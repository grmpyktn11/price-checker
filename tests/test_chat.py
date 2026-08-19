import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from backend.db import Base, get_db
from backend.main import app
from backend.models import Conversation, Item, Listing, PriceHistory, Profile, Review
from backend.services import trace


@pytest.fixture
def db():
    # one shared connection so the app and the test see the same in-memory database
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # autoflush off, same as the app's SessionLocal, so a missing flush fails here too
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def set_location(db, lat=37.7749, lon=-122.4194):
    db.add(Profile(id=1, lat=lat, lon=lon, display_address="San Francisco, CA"))
    db.commit()


def send(client, conversation_id, message):
    return client.post(
        "/api/chat/message", json={"conversation_id": conversation_id, "message": message}
    )


# drives a conversation to the results turn and returns that response body
def search(client):
    send(client, "c1", "i need a portable charger")
    response = send(client, "c1", "under $150, shipped is fine")
    assert response.status_code == 200
    return response.json()


def decide(client, product_id, decision, conversation_id="c1"):
    return client.post(
        "/api/chat/decision",
        json={
            "conversation_id": conversation_id,
            "product_id": product_id,
            "decision": decision,
        },
    )


def counts(db):
    return [db.query(model).count() for model in (Item, Listing, PriceHistory)]


@pytest.mark.live
def test_first_message_is_a_followup(client, db):
    set_location(db)
    body = send(client, "c1", "i need a portable charger").json()
    assert body["type"] == "followup"
    assert body["question"]
    assert "products" not in body


@pytest.mark.live
def test_second_message_returns_results(client, db):
    set_location(db)
    body = search(client)
    assert body["type"] == "results"
    assert body["narration"]
    assert len(body["products"]) >= 1
    first = body["products"][0]
    assert first["product_id"] == 0
    for score_field in (
        "final_score",
        "spec_match",
        "review_score",
        "price_score",
        "distance_score",
        "nice_to_have_score",
    ):
        assert isinstance(first[score_field], float)


# null product fields must serialize as null, not vanish: the client reads them by key
@pytest.mark.live
def test_nullable_product_fields_are_present_as_null(client, db):
    set_location(db)
    first = search(client)["products"][0]
    assert first["store_id"] is None
    assert first["distance_miles"] is None


# provenance has to reach the client, so Phase 8 can render it without a backend change
@pytest.mark.live
def test_specs_inherited_from_is_serialized(client, db):
    set_location(db)
    first = search(client)["products"][0]
    assert "specs_inherited_from" in first


# the trace a debug panel renders. a run where every retailer failed is a valid trace and
# must still come back fully formed, so nothing here asserts the search succeeded
@pytest.mark.live
def test_results_carry_the_debug_trace(client, db):
    set_location(db)
    body = search(client)
    debug = body["debug"]
    assert isinstance(body["retailers_answered"], bool)
    assert {row["retailer"] for row in debug["retailers"]} == {"bestbuy", "target", "amazon"}
    for row in debug["retailers"]:
        assert row["outcome"] in trace.OUTCOME_DETAIL
        assert row["detail"]
    assert debug["stages_ms"]["collect_candidates"] > 0
    # the same trace, without re-running a 60 second search
    assert client.get("/api/debug/last").json()["trace_id"] == debug["trace_id"]


# the reviews table gets its first rows here: the chosen product's row plus the shared
# item-level ones
@pytest.mark.live
def test_watch_persists_reviews(client, db):
    set_location(db)
    search(client)
    decide(client, 0, "watch")
    item_id = db.query(Item).one().id
    sources = {row.source for row in db.query(Review).filter(Review.item_id == item_id)}
    assert {"reddit", "youtube"} <= sources


@pytest.mark.live
def test_search_without_location_is_400(client, db):
    send(client, "c1", "i need a portable charger")
    response = send(client, "c1", "under $150")
    assert response.status_code == 400
    assert "PATCH /api/profile/location" in response.json()["detail"]


@pytest.mark.live
def test_buy_now_writes_nothing(client, db):
    set_location(db)
    body = search(client)
    response = decide(client, 0, "buy_now")
    assert response.status_code == 200
    assert response.json()["url"] == body["products"][0]["url"]
    assert counts(db) == [0, 0, 0]


@pytest.mark.live
def test_watch_writes_one_row_each(client, db):
    set_location(db)
    body = search(client)
    assert len(body["products"]) > 1   # one row each even though the search found several
    response = decide(client, 0, "watch")
    assert response.status_code == 200
    assert response.json()["item_id"]
    assert counts(db) == [1, 1, 1]
    assert db.query(Listing).one().url == body["products"][0]["url"]
    stored = json.loads(db.query(Item).one().criteria_json)
    assert stored["name"] == "portable charger"


@pytest.mark.live
def test_second_watch_creates_a_second_item(client, db):
    set_location(db)
    search(client)
    decide(client, 0, "watch")
    decide(client, 1, "watch")
    assert counts(db) == [2, 2, 2]


def test_unknown_conversation_is_404(client, db):
    assert decide(client, 0, "watch", conversation_id="nope").status_code == 404


@pytest.mark.live
def test_out_of_range_product_id_is_404(client, db):
    set_location(db)
    search(client)
    assert decide(client, 99, "watch").status_code == 404


@pytest.mark.live
def test_decision_before_results_is_404(client, db):
    set_location(db)
    send(client, "c2", "i need a portable charger")
    assert decide(client, 0, "watch", conversation_id="c2").status_code == 404


@pytest.mark.live
def test_bad_decision_value_is_422(client, db):
    set_location(db)
    search(client)
    assert decide(client, 0, "maybe").status_code == 422


# the waiting screen polls this while a search runs. running:false is its stop signal, so a
# conversation with nothing in flight must answer that rather than 404
def test_progress_is_not_running_when_nothing_is_in_flight(client):
    response = client.get("/api/chat/progress/conv-nothing")
    assert response.status_code == 200
    assert response.json() == {"running": False}


def test_progress_reports_a_run_in_flight(client):
    trace.start("rgb mouse", {}, key="conv-live")
    try:
        body = client.get("/api/chat/progress/conv-live").json()
        assert body["running"] is True
        assert "elapsed_ms" in body
    finally:
        trace.finish(0)


# built directly rather than through /chat/message: the delete path needs no model call, and
# a paid call has no place in the free suite
def make_conversation(db, conversation_id):
    db.add(Conversation(id=conversation_id, history_json='[{"role": "user", "content": "hi"}]',
                        results_json="[]"))
    db.commit()


def test_a_conversation_can_be_deleted(client, db):
    make_conversation(db, "conv-doomed")
    assert client.get("/api/conversations/conv-doomed").status_code == 200
    assert client.delete("/api/conversations/conv-doomed").json() == {"deleted": 1}
    assert client.get("/api/conversations/conv-doomed").status_code == 404


def test_deleting_an_unknown_conversation_is_a_404(client):
    assert client.delete("/api/conversations/nope").status_code == 404


def test_conversations_can_be_cleared_all_at_once(client, db):
    make_conversation(db, "conv-a")
    make_conversation(db, "conv-b")
    assert client.delete("/api/conversations").json()["deleted"] == 2
    assert client.get("/api/conversations").json() == []


# a watched product is a watchlist item of its own by the time the chat is deleted. clearing
# history must not silently stop tracking a price
def test_deleting_a_conversation_leaves_watched_items_alone(client, db):
    db.add(Item(name="a thing", status="watching"))
    db.commit()
    make_conversation(db, "conv-x")
    client.delete("/api/conversations/conv-x")
    assert db.query(Item).filter(Item.status == "watching").count() == 1
