import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base, get_db
from backend.main import app
from backend.models import Item, Listing, Profile, ProjectItem
from backend.routers import projects as projects_router
from backend.services import claude_share, project_extract, project_run

EXTRACTED = {
    "project": "Home lab",
    "items": [
        {"name": "8-port gigabit switch", "why": "connects the nodes", "keywords": ["gigabit"],
         "category": "electronics", "budget_max": 60.0, "quantity": 1, "essential": True,
         "criteria": {"name": "8-port gigabit switch", "radius_miles": 25,
                      "min_review_count": 5, "keywords": ["gigabit"], "must_haves": [],
                      "preferred_specs": [], "nice_to_haves": [], "budget_max": 60.0}},
        {"name": "Cat6 patch cables", "why": "wiring", "keywords": [], "category": "electronics",
         "budget_max": None, "quantity": 8, "essential": False,
         "criteria": {"name": "Cat6 patch cables", "radius_miles": 25, "min_review_count": 5,
                      "keywords": [], "must_haves": [], "preferred_specs": [],
                      "nice_to_haves": [], "budget_max": None}},
    ],
}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    # no `with`: the lifespan starts the scheduler, and a second one raises ConflictingIdError
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_runs():
    yield
    project_run._runs.clear()


@pytest.fixture
def extraction(monkeypatch):
    async def fake(transcript):
        return EXTRACTED

    monkeypatch.setattr(project_extract, "extract", fake)


def with_location(db):
    db.add(Profile(lat=37.0, lon=-122.0, display_address="somewhere"))
    db.commit()


def import_one(client):
    return client.post("/api/projects/import", json={"text": "we need a switch and cables"})


def test_import_extracts_a_shopping_list(client, extraction):
    response = import_one(client)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Home lab"
    assert body["source"] == "paste"
    assert [item["name"] for item in body["items"]] == [
        "8-port gigabit switch", "Cat6 patch cables"
    ]


# essentials start ticked: unticking one is less work than ticking everything
def test_essentials_start_selected(client, extraction):
    items = import_one(client).json()["items"]
    assert [item["selected"] for item in items] == [True, False]
    assert [item["essential"] for item in items] == [True, False]


def test_import_needs_something_to_read(client, extraction):
    assert client.post("/api/projects/import", json={}).status_code == 400
    assert client.post("/api/projects/import", json={"text": "   "}).status_code == 400


# a conversation with nothing buyable in it is a real outcome, and 422 says so more usefully
# than an empty project the person then has to delete
def test_a_conversation_with_nothing_to_buy_is_a_422(client, monkeypatch):
    async def nothing(transcript):
        return {"project": "", "items": []}

    monkeypatch.setattr(project_extract, "extract", nothing)
    response = client.post("/api/projects/import", json={"text": "how do i center a div"})
    assert response.status_code == 422
    assert "no products" in response.json()["detail"]


def test_a_bad_share_url_is_rejected_before_fetching(client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not fetch a url that failed the host check")

    monkeypatch.setattr(claude_share, "fetch_html", boom)
    response = client.post("/api/projects/import",
                           json={"share_url": "https://evil.example.com/share/x"})
    assert response.status_code == 400


def test_a_share_url_import_records_where_it_came_from(client, extraction, monkeypatch):
    async def transcript(url):
        return "a long conversation about building a home lab" * 20

    monkeypatch.setattr(claude_share, "fetch_transcript", transcript)
    body = client.post("/api/projects/import",
                       json={"share_url": "https://claude.ai/share/abc"}).json()
    assert body["source"] == "share_link"
    assert body["source_url"] == "https://claude.ai/share/abc"


def test_the_project_survives_a_reload(client, extraction, db):
    project_id = import_one(client).json()["id"]
    # results written by a run that has since ended, as the runner writes them
    item = db.query(ProjectItem).filter(ProjectItem.project_id == project_id).first()
    item.results_json = json.dumps([{"product_id": 0, "name": "a switch"}])
    item.status = "done"
    db.commit()

    body = client.get(f"/api/projects/{project_id}").json()
    assert body["results"][str(item.id)][0]["name"] == "a switch"


def test_unknown_project_is_a_404(client):
    assert client.get("/api/projects/999").status_code == 404
    assert client.post("/api/projects/999/search", json={"item_ids": [1]}).status_code == 404


# no location is not an error: the run starts anyway, online-only
def test_search_without_a_location_still_starts(client, extraction, monkeypatch):
    seen = {}

    async def fake_run(project_id, item_ids, lat, lon, to_product_out):
        seen.update({"lat": lat, "lon": lon})

    monkeypatch.setattr(project_run, "run_project", fake_run)
    body = import_one(client).json()
    response = client.post(f"/api/projects/{body['id']}/search",
                           json={"item_ids": [body["items"][0]["id"]]})
    assert response.status_code == 202
    assert seen == {"lat": None, "lon": None}


def test_search_starts_a_run(client, extraction, db, monkeypatch):
    with_location(db)
    started = {}

    async def fake_run(project_id, item_ids, lat, lon, to_product_out):
        started.update({"project_id": project_id, "item_ids": item_ids})

    monkeypatch.setattr(project_run, "run_project", fake_run)
    body = import_one(client).json()
    ids = [item["id"] for item in body["items"]]

    response = client.post(f"/api/projects/{body['id']}/search", json={"item_ids": ids})
    assert response.status_code == 202
    assert response.json()["searching"] == ids


def test_search_caps_the_item_count(client, extraction, db, monkeypatch):
    with_location(db)
    monkeypatch.setattr(project_run, "MAX_PROJECT_ITEMS", 1)
    monkeypatch.setattr(projects_router.project_run, "MAX_PROJECT_ITEMS", 1)

    async def fake_run(*args, **kwargs):
        pass

    monkeypatch.setattr(project_run, "run_project", fake_run)
    body = import_one(client).json()
    ids = [item["id"] for item in body["items"]]

    payload = client.post(f"/api/projects/{body['id']}/search",
                          json={"item_ids": ids}).json()
    assert payload["searching"] == ids[:1]
    assert payload["skipped"] == ids[1:]


def test_search_rejects_items_from_another_project(client, extraction, db, monkeypatch):
    with_location(db)

    async def fake_run(*args, **kwargs):
        pass

    monkeypatch.setattr(project_run, "run_project", fake_run)
    body = import_one(client).json()
    response = client.post(f"/api/projects/{body['id']}/search", json={"item_ids": [9999]})
    assert response.status_code == 404


def test_a_second_run_is_refused_while_one_is_in_flight(client, extraction, db, monkeypatch):
    with_location(db)
    body = import_one(client).json()
    project_run._runs[body["id"]] = {"status": "running", "current_index": 0, "items": []}

    response = client.post(f"/api/projects/{body['id']}/search",
                           json={"item_ids": [body["items"][0]["id"]]})
    assert response.status_code == 409


# running:false is the client's stop signal, the same contract as /api/chat/progress, so a
# project that never ran must answer it rather than 404
def test_progress_is_not_running_for_an_idle_project(client):
    assert client.get("/api/projects/1/progress").json() == {"running": False}


def test_progress_reports_a_run_in_flight(client, extraction):
    body = import_one(client).json()
    project_run._runs[body["id"]] = {
        "status": "running", "current_index": 0,
        "items": [{"id": 1, "name": "switch", "state": "searching", "products_found": 0}],
    }
    payload = client.get(f"/api/projects/{body['id']}/progress").json()
    assert payload["running"] is True
    assert payload["items"][0]["state"] == "searching"


def test_delete_removes_the_items_too(client, extraction, db):
    body = import_one(client).json()
    assert client.delete(f"/api/projects/{body['id']}").json()["deleted"] == 3
    assert db.query(ProjectItem).filter(ProjectItem.project_id == body["id"]).count() == 0


def searched_item(client, db, url="https://www.bestbuy.com/x"):
    project_id = import_one(client).json()["id"]
    item = db.query(ProjectItem).filter(ProjectItem.project_id == project_id).first()
    item.results_json = json.dumps([{
        "product_id": 0, "name": "A switch", "url": url, "price": 59.99,
        "in_stock": True, "retailer": "bestbuy", "store_id": None, "distance_miles": None,
    }])
    item.status = "done"
    db.commit()
    return project_id, item.id


# a project pick becomes an ordinary watchlist item, so the scheduler rescans it and alerts
# on it like anything else
def test_tracking_a_pick_creates_a_watchlist_item(client, extraction, db):
    project_id, item_id = searched_item(client, db)
    response = client.post(f"/api/projects/{project_id}/items/{item_id}/track",
                           json={"product_id": 0})
    assert response.status_code == 200
    watched = db.get(Item, response.json()["item_id"])
    assert watched.status == "watching"
    assert watched.name == "8-port gigabit switch"
    assert db.query(Listing).filter(Listing.item_id == watched.id).count() == 1


# the listings unique key is meaningless without a url and a rescan could not re-find it
def test_tracking_a_pick_with_no_url_is_refused(client, extraction, db):
    project_id, item_id = searched_item(client, db, url=None)
    response = client.post(f"/api/projects/{project_id}/items/{item_id}/track",
                           json={"product_id": 0})
    assert response.status_code == 400


def test_tracking_an_unknown_product_is_a_404(client, extraction, db):
    project_id, item_id = searched_item(client, db)
    assert client.post(f"/api/projects/{project_id}/items/{item_id}/track",
                       json={"product_id": 7}).status_code == 404


# item ids are global, so the project in the path has to actually own the item
def test_tracking_an_item_from_another_project_is_a_404(client, extraction, db):
    _, item_id = searched_item(client, db)
    other_project = import_one(client).json()["id"]
    assert client.post(f"/api/projects/{other_project}/items/{item_id}/track",
                       json={"product_id": 0}).status_code == 404
