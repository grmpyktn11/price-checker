import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.models import Project, ProjectItem
from backend.services import project_run
from backend.services.project_run import MAX_PROJECT_ITEMS, progress, run_project

def criteria_for(name):
    return {"name": name, "radius_miles": 25, "min_review_count": 5, "keywords": [],
            "must_haves": [], "preferred_specs": [], "nice_to_haves": [], "budget_max": None}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # the runner opens its own session, as the scheduler's jobs do
    monkeypatch.setattr(project_run, "SessionLocal", Session)
    session = Session()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def clean_runs():
    yield
    project_run._runs.clear()


# the runner commits on its own session, so the test's session must drop what it cached
# before it can see the result
def reread(db):
    db.expire_all()
    return db.query(ProjectItem).order_by(ProjectItem.id).all()


def make_project(db, item_count):
    project = Project(name="Home lab", source="paste")
    db.add(project)
    db.flush()
    for index in range(item_count):
        db.add(ProjectItem(project_id=project.id, name=f"thing {index}", why="because",
                           criteria_json=json.dumps(criteria_for(f"thing {index}")),
                           quantity=1, essential=True, selected=True, status="pending"))
    db.commit()
    ids = [item.id for item in db.query(ProjectItem).order_by(ProjectItem.id)]
    return project.id, ids


# a stand-in for ProductOut: the runner only ever calls model_dump on it
class FakeOut:
    def __init__(self, index):
        self.index = index

    def model_dump(self):
        return {"product_id": self.index, "name": "a product"}


def fake_product_out(index, ranked):
    return FakeOut(index)


def patch_pipeline(monkeypatch, behaviour):
    calls = []

    async def fake(item_criteria, lat, lon, radius, **kwargs):
        calls.append({"name": item_criteria["name"], **kwargs})
        return await behaviour(item_criteria)

    monkeypatch.setattr(project_run, "run_pipeline", fake)
    return calls


async def three_products(item_criteria):
    return ["a", "b", "c"]


def test_a_run_searches_every_ticked_item(db, monkeypatch):
    project_id, ids = make_project(db, 3)
    calls = patch_pipeline(monkeypatch, three_products)

    asyncio.run(run_project(project_id, ids, 1.0, 2.0, fake_product_out))

    assert [call["name"] for call in calls] == ["thing 0", "thing 1", "thing 2"]
    items = reread(db)
    assert [item.status for item in items] == ["done"] * 3
    assert json.loads(items[0].results_json)[0]["name"] == "a product"


# five reddit searches per item, on a source that already 429s, is how one project run gets
# everything blocked partway. a project search spends no reddit or youtube quota at all
def test_a_project_search_skips_the_research_stage(db, monkeypatch):
    project_id, ids = make_project(db, 1)
    calls = patch_pipeline(monkeypatch, three_products)

    asyncio.run(run_project(project_id, ids, 1.0, 2.0, fake_product_out))

    assert calls[0]["research_top_n"] == 0


def test_the_run_is_capped(db, monkeypatch):
    project_id, ids = make_project(db, MAX_PROJECT_ITEMS + 3)
    calls = patch_pipeline(monkeypatch, three_products)

    asyncio.run(run_project(project_id, ids, 1.0, 2.0, fake_product_out))

    assert len(calls) == MAX_PROJECT_ITEMS


# one bad item must not end the project, the same rule the scheduler follows for the watchlist
def test_one_failing_item_does_not_stop_the_rest(db, monkeypatch):
    project_id, ids = make_project(db, 3)

    async def fail_the_middle_one(item_criteria):
        if item_criteria["name"] == "thing 1":
            raise RuntimeError("amazon blocked")
        return ["a"]

    patch_pipeline(monkeypatch, fail_the_middle_one)
    asyncio.run(run_project(project_id, ids, 1.0, 2.0, fake_product_out))

    items = reread(db)
    assert [item.status for item in items] == ["done", "failed", "done"]
    assert "amazon blocked" in items[1].error
    # the failure is recorded against the item, not left as a silent empty result
    assert items[1].results_json is None


# the reason this progress store exists rather than riding on trace._live: that dict is popped
# in trace.finish, so between one item ending and the next starting a poller would see
# "not running" and stop. this asserts the gap never appears
def test_progress_stays_running_between_items(db, monkeypatch):
    project_id, ids = make_project(db, 3)
    seen = []

    async def record_progress_between_items(item_criteria):
        seen.append(progress(project_id)["status"])
        return ["a"]

    patch_pipeline(monkeypatch, record_progress_between_items)
    asyncio.run(run_project(project_id, ids, 1.0, 2.0, fake_product_out))

    assert seen == ["running"] * 3
    assert progress(project_id)["status"] == "done"


def test_progress_reports_each_item_as_it_finishes(db, monkeypatch):
    project_id, ids = make_project(db, 2)
    states = []

    async def record(item_criteria):
        states.append([row["state"] for row in progress(project_id)["items"]])
        return ["a", "b"]

    patch_pipeline(monkeypatch, record)
    asyncio.run(run_project(project_id, ids, 1.0, 2.0, fake_product_out))

    assert states == [["searching", "pending"], ["done", "searching"]]
    final = progress(project_id)
    assert [row["state"] for row in final["items"]] == ["done", "done"]
    assert [row["products_found"] for row in final["items"]] == [2, 2]


def test_progress_is_none_for_a_project_that_never_ran():
    assert progress(999) is None


def test_only_this_projects_items_are_searched(db, monkeypatch):
    project_id, ids = make_project(db, 2)
    other_id, other_ids = make_project(db, 2)
    calls = patch_pipeline(monkeypatch, three_products)

    asyncio.run(run_project(project_id, ids + other_ids, 1.0, 2.0, fake_product_out))

    assert len(calls) == 2
    assert all(item.status == "pending" for item in reread(db)
               if item.project_id == other_id)
