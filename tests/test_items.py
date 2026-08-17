import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base, get_db
from backend.main import app
from backend.models import Alert, Item, Listing, PriceHistory, Profile, Review

BASIC_ITEM = {
    "name": "portable charger",
    "category": "electronics",
    "criteria": {
        "keywords": ["usb-c"],
        "must_haves": [{"field": "Battery Capacity", "op": ">=", "value": 20000}],
    },
    "budget_max": 150.0,
    "target_price": 99.0,
    "min_review_count": 100,
}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # autoflush off, same as the app's SessionLocal: a pending row the code
    # never flushes must not be made visible by the test setup
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def set_location(db):
    db.add(Profile(id=1, lat=37.7749, lon=-122.4194, display_address="San Francisco, CA"))
    db.commit()


def create(client, **overrides):
    return client.post("/api/items", json={**BASIC_ITEM, **overrides})


def test_manual_add_stores_columns_and_criteria(client, db):
    response = create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "watching"
    assert body["target_price"] == 99.0
    stored = json.loads(body["criteria_json"])
    # the flat fields are mirrored into criteria_json so a rescan sees the same values
    assert stored["name"] == "portable charger"
    assert stored["budget_max"] == 150.0
    assert stored["must_haves"][0]["value"] == 20000


# criteria.py fills the fields run_pipeline indexes directly
def test_manual_add_normalizes_missing_lists(client, db):
    stored = json.loads(create(client, criteria={}).json()["criteria_json"])
    assert stored["nice_to_haves"] == []
    assert stored["radius_miles"] == 25


# same validator the chat path runs: a null-valued rule must not reach ranking
def test_manual_add_rejects_a_rule_with_no_value(client, db):
    response = create(client, criteria={"must_haves": [
        {"field": "Battery Capacity", "op": ">=", "value": None}
    ]})
    assert response.status_code == 422
    assert "Battery Capacity" in response.json()["detail"]
    assert db.query(Item).count() == 0


def test_manual_add_rejects_an_unknown_op(client, db):
    response = create(client, criteria={"must_haves": [
        {"field": "Battery Capacity", "op": "roughly", "value": 20000}
    ]})
    assert response.status_code == 422
    assert db.query(Item).count() == 0


def test_manual_add_repairs_a_formatted_number(client, db):
    stored = json.loads(create(client, criteria={"must_haves": [
        {"field": "Battery Capacity", "op": ">=", "value": "20,000 mAh"}
    ]}).json()["criteria_json"])
    assert stored["must_haves"][0]["value"] == 20000


def test_list_items(client, db):
    create(client)
    create(client, name="usb hub")
    body = client.get("/api/items").json()
    assert [item["name"] for item in body] == ["usb hub", "portable charger"]


def test_patch_updates_column_and_criteria(client, db):
    item_id = create(client).json()["id"]
    response = client.patch(f"/api/items/{item_id}", json={"target_price": 79.0})
    assert response.status_code == 200
    assert response.json()["target_price"] == 79.0
    assert json.loads(response.json()["criteria_json"])["target_price"] == 79.0


def test_patch_archives(client, db):
    item_id = create(client).json()["id"]
    assert client.patch(f"/api/items/{item_id}", json={"status": "archived"}).json()["status"] == (
        "archived"
    )


def test_patch_rejects_an_unknown_status(client, db):
    item_id = create(client).json()["id"]
    assert client.patch(f"/api/items/{item_id}", json={"status": "paused"}).status_code == 422


def test_patch_validates_new_rules(client, db):
    item_id = create(client).json()["id"]
    response = client.patch(f"/api/items/{item_id}", json={"criteria": {"must_haves": [
        {"field": "Weight", "op": "<=", "value": None}
    ]}})
    assert response.status_code == 422


def test_patch_unknown_item_is_404(client, db):
    assert client.patch("/api/items/999", json={"target_price": 1.0}).status_code == 404


def test_delete_removes_children(client, db):
    item_id = create(client).json()["id"]
    listing = Listing(item_id=item_id, retailer="bestbuy", url="https://example.com/1", price=10.0)
    db.add(listing)
    db.flush()
    db.add(PriceHistory(listing_id=listing.id, price=10.0))
    db.add(Alert(item_id=item_id, listing_id=listing.id, reason="price_drop"))
    db.add(Review(item_id=item_id, source="reddit"))
    db.commit()

    assert client.delete(f"/api/items/{item_id}").status_code == 200
    for model in (Item, Listing, PriceHistory, Alert, Review):
        assert db.query(model).count() == 0


def test_delete_unknown_item_is_404(client, db):
    assert client.delete("/api/items/999").status_code == 404


@pytest.mark.live
def test_rescan_writes_listings(client, db):
    set_location(db)
    item_id = create(client).json()["id"]
    body = client.post(f"/api/items/{item_id}/rescan").json()
    assert body["item_id"] == item_id
    assert body["listings_seen"] >= 1
    assert db.query(Listing).filter(Listing.item_id == item_id).count() >= 1
    assert db.query(PriceHistory).count() >= 1


# no email is configured in the suite, so nothing is sent even when a target is hit
@pytest.mark.live
def test_rescan_sends_no_email_without_a_key(client, db):
    set_location(db)
    item_id = create(client, target_price=100000.0).json()["id"]
    assert client.post(f"/api/items/{item_id}/rescan").json()["emails_sent"] == 0


def test_rescan_unknown_item_is_404(client, db):
    assert client.post("/api/items/999/rescan").status_code == 404


@pytest.mark.live
def test_listings_price_history_and_reviews(client, db):
    set_location(db)
    item_id = create(client).json()["id"]
    client.post(f"/api/items/{item_id}/rescan")

    listings = client.get(f"/api/items/{item_id}/listings").json()
    assert listings and listings[0]["url"]
    # cheapest first
    prices = [row["price"] for row in listings if row["price"] is not None]
    assert prices == sorted(prices)

    history = client.get(f"/api/items/{item_id}/price-history").json()
    assert {row["listing_id"] for row in history} <= {row["id"] for row in listings}

    reviews = client.get(f"/api/items/{item_id}/reviews").json()
    assert {"reddit", "youtube"} <= {row["source"] for row in reviews}


def test_listings_for_unknown_item_is_404(client, db):
    for path in ("listings", "price-history", "reviews"):
        assert client.get(f"/api/items/999/{path}").status_code == 404


def test_alerts_are_newest_first_with_item_and_listing_fields(client, db):
    item_id = create(client).json()["id"]
    listing = Listing(item_id=item_id, retailer="bestbuy", url="https://example.com/1", price=10.0)
    db.add(listing)
    db.flush()
    db.add(Alert(item_id=item_id, listing_id=listing.id, reason="price_drop"))
    db.add(Alert(item_id=item_id, listing_id=listing.id, reason="target_hit"))
    db.commit()

    body = client.get("/api/alerts").json()
    assert [row["reason"] for row in body] == ["target_hit", "price_drop"]
    assert body[0]["item_name"] == "portable charger"
    assert body[0]["retailer"] == "bestbuy"
    assert body[0]["price"] == 10.0
    assert body[0]["sent_at"] is None
