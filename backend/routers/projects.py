import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Project, ProjectItem
from backend.routers.chat import load_json, to_product_out, watch_product
from backend.routers.profile import get_or_create_profile
from backend.services import claude_share, project_extract, project_run

router = APIRouter(prefix="/api", tags=["projects"])

logger = logging.getLogger(__name__)


class ImportIn(BaseModel):
    text: str | None = None          # the conversation, pasted
    share_url: str | None = None     # a claude.ai share link


class ProjectItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str | None
    why: str | None
    quantity: int | None
    essential: bool | None
    selected: bool | None
    status: str | None
    error: str | None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str | None
    source: str | None
    source_url: str | None


class ProjectDetailOut(ProjectOut):
    items: list[ProjectItemOut]
    # ProductOut-shaped dicts per item id, so the page can render cards after a reload
    results: dict[int, list[dict]]


class SearchIn(BaseModel):
    item_ids: list[int]


class TrackIn(BaseModel):
    product_id: int      # index into that item's results_json


class TrackOut(BaseModel):
    item_id: int         # the watchlist Item this created
    url: str | None
    message: str


def get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def project_items(db: Session, project_id: int) -> list[ProjectItem]:
    return (db.query(ProjectItem)
            .filter(ProjectItem.project_id == project_id)
            .order_by(ProjectItem.id)
            .all())


def detail(db: Session, project: Project) -> ProjectDetailOut:
    items = project_items(db, project.id)
    return ProjectDetailOut(
        id=project.id,
        name=project.name,
        source=project.source,
        source_url=project.source_url,
        items=[ProjectItemOut.model_validate(item) for item in items],
        results={item.id: load_json(item.results_json, []) for item in items},
    )


# the transcript, from whichever of the two ways the client offered it
async def read_transcript(body: ImportIn) -> tuple[str, str, str | None]:
    if body.share_url:
        try:
            return await claude_share.fetch_transcript(body.share_url), "share_link", body.share_url
        except claude_share.NotAShareUrl as error:
            raise HTTPException(400, str(error))
        except (claude_share.ShareBlocked, claude_share.ShareUnreadable) as error:
            raise HTTPException(502, str(error))
        # a browser launch failure is not the user's fault and not a 400
        except Exception as error:
            logger.warning("claude share fetch failed: %s", error)
            raise HTTPException(502, "could not load that share link")
    if body.text and body.text.strip():
        return body.text, "paste", None
    raise HTTPException(400, "send either text or share_url")


@router.post("/projects/import", response_model=ProjectDetailOut, status_code=201)
async def import_project(body: ImportIn, db: Session = Depends(get_db)) -> ProjectDetailOut:
    transcript, source, source_url = await read_transcript(body)
    extracted = await project_extract.extract(transcript)
    if not extracted["items"]:
        raise HTTPException(422, "no products to buy were found in that conversation")

    project = Project(name=extracted["project"] or "Untitled project",
                      source=source, source_url=source_url)
    db.add(project)
    db.flush()
    for row in extracted["items"]:
        db.add(ProjectItem(
            project_id=project.id,
            name=row["name"],
            why=row["why"],
            criteria_json=json.dumps(row["criteria"]),
            quantity=row["quantity"],
            essential=row["essential"],
            # essentials start ticked: they are what the project needs, and unticking is
            # less work than ticking everything
            selected=row["essential"],
            status="pending",
        ))
    db.commit()
    db.refresh(project)
    return detail(db, project)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return db.query(Project).order_by(Project.updated_at.desc()).all()


@router.get("/projects/{project_id}", response_model=ProjectDetailOut)
def read_project(project_id: int, db: Session = Depends(get_db)) -> ProjectDetailOut:
    return detail(db, get_project(db, project_id))


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)) -> dict:
    project = get_project(db, project_id)
    removed = db.query(ProjectItem).filter(ProjectItem.project_id == project.id).delete()
    db.delete(project)
    db.commit()
    return {"deleted": removed + 1}


# returns immediately: the run is minutes long, so the client polls /progress for it
@router.post("/projects/{project_id}/search", status_code=202)
async def start_search(project_id: int, body: SearchIn,
                       db: Session = Depends(get_db)) -> dict:
    project = get_project(db, project_id)
    if project_run.is_running(project.id):
        raise HTTPException(409, "this project is already searching")
    if not body.item_ids:
        raise HTTPException(400, "tick at least one item")

    # no location set is fine: the run is online-only with neutral distance scores
    profile = get_or_create_profile(db)

    known = {item.id for item in project_items(db, project.id)}
    chosen = [item_id for item_id in body.item_ids if item_id in known]
    if not chosen:
        raise HTTPException(404, "none of those items belong to this project")
    capped = chosen[:project_run.MAX_PROJECT_ITEMS]

    # the ticks are persisted before the run starts, so a reload mid-run still shows what
    # was asked for
    for item in project_items(db, project.id):
        item.selected = item.id in capped
        if item.id in capped:
            item.status = "pending"
    db.commit()

    # a task, not BackgroundTasks: this outlives the response by minutes and must start now
    # rather than after it. the runner opens its own session, as the scheduler's jobs do
    asyncio.create_task(project_run.run_project(
        project.id, capped, profile.lat, profile.lon, to_product_out
    ))
    return {"searching": capped, "skipped": chosen[len(capped):]}


# tracking a project pick creates an ordinary watchlist item, so the scheduler rescans it and
# alerts on it like anything else. the chat decision endpoint cannot serve this: it looks its
# product up in a Conversation, and a project is not one
@router.post("/projects/{project_id}/items/{item_id}/track", response_model=TrackOut)
def track_product(project_id: int, item_id: int, body: TrackIn,
                  db: Session = Depends(get_db)) -> TrackOut:
    get_project(db, project_id)
    item = db.get(ProjectItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, "item not found in this project")
    results = load_json(item.results_json, [])
    if not 0 <= body.product_id < len(results):
        raise HTTPException(404, "unknown product_id")

    chosen = results[body.product_id]
    # the listings unique key is meaningless without a url, and a rescan cannot re-find it
    if chosen.get("url") is None:
        raise HTTPException(400, "product has no url")
    watched = watch_product(db, json.loads(item.criteria_json), chosen)
    return TrackOut(item_id=watched.id, url=chosen["url"],
                    message=f"Watching {chosen.get('name')}.")


# {"running": false} once the run ends, which is the client's stop signal - the same contract
# as /api/chat/progress
@router.get("/projects/{project_id}/progress")
def read_progress(project_id: int) -> dict:
    live = project_run.progress(project_id)
    if live is None or live["status"] != "running":
        return {"running": False, **(live or {})}
    return {"running": True, **live}
