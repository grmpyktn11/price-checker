import json
import logging

from backend.db import SessionLocal
from backend.models import ProjectItem, utcnow
from backend.services import trace
from backend.services.pipeline import run_pipeline

# one run at a time, and this many items in it. every item is a full four-retailer search, so
# five is already three to five minutes; more is a queue nobody watches
MAX_PROJECT_ITEMS = 5
# a project search spends no reddit or youtube quota. see run_pipeline's research_top_n
PROJECT_RESEARCH_TOP_N = 0

# progress for runs in flight, by project id. deliberately NOT trace._live: that dict is
# popped in trace.finish, so a poller watching a multi-item run would see "not running"
# in the gap between one item finishing and the next starting, and stop polling. this
# entry lives for the whole run. in memory only, gone on restart - the durable state is
# ProjectItem.status
_runs: dict[int, dict] = {}

logger = logging.getLogger(__name__)


# the key each item's own pipeline trace registers under, so the waiting screen can still show
# retailer-by-retailer detail for whichever item is searching right now
def trace_key(project_id: int) -> str:
    return f"project:{project_id}"


def is_running(project_id: int) -> bool:
    return _runs.get(project_id, {}).get("status") == "running"


# merged view: the project's own item states, plus the live trace of the item being searched
def progress(project_id: int) -> dict | None:
    run = _runs.get(project_id)
    if run is None:
        return None
    return {**run, "current_search": trace.live(trace_key(project_id))}


def start_run(project_id: int, items: list[ProjectItem]) -> dict:
    run = {
        "status": "running",
        "current_index": 0,
        "items": [{"id": item.id, "name": item.name, "state": "pending", "products_found": 0}
                  for item in items],
    }
    _runs[project_id] = run
    return run


def mark(project_id: int, index: int, **fields) -> None:
    run = _runs.get(project_id)
    if run and 0 <= index < len(run["items"]):
        run["items"][index].update(fields)
        run["current_index"] = index


# one item, on its own session state. the caller holds the try/except: a single item failing
# must not end the run
async def search_item(db, item: ProjectItem, lat: float | None, lon: float | None, project_id: int,
                      to_product_out) -> int:
    item_criteria = json.loads(item.criteria_json)
    item.status = "searching"
    db.commit()
    ranked = await run_pipeline(
        item_criteria, lat, lon, item_criteria["radius_miles"],
        progress_key=trace_key(project_id),
        research_top_n=PROJECT_RESEARCH_TOP_N,
    )
    top = ranked[:TOP_N]
    item.results_json = json.dumps(
        [to_product_out(index, row).model_dump() for index, row in enumerate(top)]
    )
    item.status = "done"
    item.error = None
    item.searched_at = utcnow()
    db.commit()
    return len(top)


# how many picks are kept per item. narration's TOP_N is 5 for a single search; a project page
# showing five options for each of five items is a wall, so a project keeps the best three
TOP_N = 3


# the whole run, sequentially. never parallel: reddit's pacing is per-run so N runs remove the
# spacing, amazon's caps are per-run so N runs multiply its request rate, and browser.py's
# product-page cache holds one entry, which two concurrent pipelines would thrash
async def run_project(project_id: int, item_ids: list[int], lat: float | None, lon: float | None,
                      to_product_out) -> None:
    db = SessionLocal()
    try:
        items = (db.query(ProjectItem)
                 .filter(ProjectItem.project_id == project_id,
                         ProjectItem.id.in_(item_ids))
                 .order_by(ProjectItem.id)
                 .limit(MAX_PROJECT_ITEMS)
                 .all())
        start_run(project_id, items)
        for index, item in enumerate(items):
            mark(project_id, index, state="searching")
            # one bad item must not stop the rest of the project
            try:
                found = await search_item(db, item, lat, lon, project_id, to_product_out)
                mark(project_id, index, state="done", products_found=found)
            except Exception as error:
                db.rollback()
                logger.exception("project %s item %s failed", project_id, item.id)
                item.status = "failed"
                item.error = str(error)[:200]
                item.searched_at = utcnow()
                db.commit()
                mark(project_id, index, state="failed")
    finally:
        run = _runs.get(project_id)
        if run:
            run["status"] = "done"
        db.close()
