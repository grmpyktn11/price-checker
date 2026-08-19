import asyncio
import copy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import scheduler
from backend.db import Base
from backend.models import Alert, Item, Listing, PriceHistory, Profile, utcnow
from backend.services import email
from backend.services.ranking import RankedProduct
from sample_criteria import SAMPLE_CRITERIA


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    # autoflush off, same as the app's SessionLocal: a pending row the code
    # never flushes must not be made visible by the test setup
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


# the pipeline needs coordinates before it will run for an item
@pytest.fixture
def seeded_profile(db):
    db.add(Profile(id=1, lat=37.7749, lon=-122.4194, display_address="San Francisco, CA"))
    db.commit()


# the jobs open their own session; point that at the test database
@pytest.fixture
def job_db(db, monkeypatch):
    monkeypatch.setattr(scheduler, "job_session", lambda: db)
    return db


# records every send instead of making one
@pytest.fixture
def sent(monkeypatch):
    captured = []

    # the recipient is captured too: it now comes from the profile, not a module constant
    async def fake_send(subject, html_body, to=None):
        captured.append((subject, html_body, to))
        return True

    monkeypatch.setattr(email, "send_email", fake_send)
    return captured


def make_item(db, target_price=None, criteria=None):
    item = Item(
        name="portable charger",
        criteria_json=json.dumps(criteria or copy.deepcopy(SAMPLE_CRITERIA)),
        target_price=target_price,
        radius_miles=25,
        status="watching",
    )
    db.add(item)
    db.commit()
    return item


def candidate(price, url="https://example.com/1", retailer="bestbuy"):
    return RankedProduct(
        product={"name": "charger", "url": url, "price": price, "in_stock": True,
                 "store_id": None, "distance_miles": None},
        retailer=retailer,
        specs={},
        reviews=[],
        spec_match=0.0,
        review_score=0.0,
        nice_to_have_score=0.0,
        distance_score=0.0,
    )


def test_upsert_inserts_listing_and_first_price(db):
    item = make_item(db)
    listing = scheduler.upsert_listing(db, item.id, candidate(100.0))
    db.commit()
    assert listing.price == 100.0
    assert db.query(Listing).count() == 1
    assert db.query(PriceHistory).count() == 1


def test_upsert_same_price_writes_no_history_row(db):
    item = make_item(db)
    scheduler.upsert_listing(db, item.id, candidate(100.0))
    scheduler.upsert_listing(db, item.id, candidate(100.0))
    db.commit()
    assert db.query(Listing).count() == 1
    assert db.query(PriceHistory).count() == 1


def test_upsert_price_change_writes_history_row(db):
    item = make_item(db)
    scheduler.upsert_listing(db, item.id, candidate(100.0))
    scheduler.upsert_listing(db, item.id, candidate(80.0))
    db.commit()
    assert db.query(Listing).count() == 1
    assert [row.price for row in db.query(PriceHistory).order_by(PriceHistory.id)] == [100.0, 80.0]


# a different store_id is a different row under the unique key
def test_upsert_keys_on_store_id(db):
    item = make_item(db)
    scheduler.upsert_listing(db, item.id, candidate(100.0))
    with_store = candidate(100.0)
    with_store.product["store_id"] = "482"
    scheduler.upsert_listing(db, item.id, with_store)
    db.commit()
    assert db.query(Listing).count() == 2


def test_upsert_skips_product_without_url(db):
    item = make_item(db)
    no_url = candidate(100.0)
    no_url.product["url"] = None
    assert scheduler.upsert_listing(db, item.id, no_url) is None


def test_record_alert_does_not_stack_pending_duplicates(db):
    item = make_item(db)
    listing = scheduler.upsert_listing(db, item.id, candidate(100.0))
    db.commit()
    assert scheduler.record_alert(db, item.id, listing.id, "price_drop") is not None
    assert scheduler.record_alert(db, item.id, listing.id, "price_drop") is None
    assert db.query(Alert).count() == 1


# once the pending one has been sent, the next detection is a new alert again
def test_record_alert_after_send(db):
    item = make_item(db)
    listing = scheduler.upsert_listing(db, item.id, candidate(100.0))
    alert = scheduler.record_alert(db, item.id, listing.id, "price_drop")
    alert.sent_at = utcnow()
    db.commit()
    assert scheduler.record_alert(db, item.id, listing.id, "price_drop") is not None


@pytest.mark.live
def test_scrape_item_writes_listings_and_history(db, seeded_profile):
    item = make_item(db)
    result = asyncio.run(scheduler.scrape_item(db, item))
    assert result["listings_seen"] >= 1
    assert db.query(Listing).count() >= 1
    assert db.query(PriceHistory).count() >= 1


# first scrape of a new listing is always at its own all-time low, so deals fire
@pytest.mark.live
def test_scrape_item_records_alerts(db, seeded_profile):
    item = make_item(db)
    asyncio.run(scheduler.scrape_item(db, item))
    assert db.query(Alert).count() >= 1


@pytest.mark.live
def test_target_hit_is_sent_immediately(db, seeded_profile, sent):
    # any real price is under this, so every listing is a target hit
    item = make_item(db, target_price=100000.0)
    result = asyncio.run(scheduler.scrape_item(db, item))
    assert result["emails_sent"] >= 1
    assert sent[0][0].startswith("Shopper: target price hit")
    hits = db.query(Alert).filter(Alert.reason == "target_hit").all()
    assert hits and all(alert.sent_at is not None for alert in hits)


# everything that is not a target hit waits for the digest
@pytest.mark.live
def test_price_drop_is_not_sent_immediately(db, seeded_profile, sent):
    item = make_item(db)
    asyncio.run(scheduler.scrape_item(db, item))
    assert sent == []
    assert db.query(Alert).filter(Alert.sent_at.is_(None)).count() >= 1


def test_scrape_job_skips_archived_items(job_db, seeded_profile):
    item = make_item(job_db)
    item.status = "archived"
    job_db.commit()
    asyncio.run(scheduler.run_scrape_job())
    assert job_db.query(Listing).count() == 0


# no profile location is not a reason to skip: the rescan runs online-only with a neutral
# distance score, same as a chat search
def test_scrape_job_runs_without_location(job_db, monkeypatch):
    item = make_item(job_db)
    scanned = []

    async def fake_scrape(db, scanned_item):
        scanned.append(scanned_item.id)
        return {}

    monkeypatch.setattr(scheduler, "scrape_item", fake_scrape)
    asyncio.run(scheduler.run_scrape_job())
    assert scanned == [item.id]


@pytest.mark.live
def test_review_check_flags_unseen_products(db, seeded_profile):
    item = make_item(db)
    alerts = asyncio.run(scheduler.check_new_alternatives(db, item))
    assert alerts
    assert {alert.reason for alert in alerts} == {"new_alternative"}


# a url already stored for the item is not a new alternative
@pytest.mark.live
def test_review_check_ignores_known_urls(db, seeded_profile):
    item = make_item(db)
    asyncio.run(scheduler.check_new_alternatives(db, item))
    db.query(Alert).delete()
    db.commit()
    assert asyncio.run(scheduler.check_new_alternatives(db, item)) == []


def test_digest_sends_pending_and_marks_them(job_db, sent):
    item = make_item(job_db)
    listing = scheduler.upsert_listing(job_db, item.id, candidate(100.0))
    scheduler.record_alert(job_db, item.id, listing.id, "price_drop")
    job_db.commit()
    assert asyncio.run(scheduler.run_digest_job()) == 1
    assert job_db.query(Alert).filter(Alert.sent_at.is_(None)).count() == 0
    assert "portable charger" in sent[0][1]


def test_digest_with_nothing_pending_sends_nothing(job_db, sent):
    assert asyncio.run(scheduler.run_digest_job()) == 0
    assert sent == []


# no key configured: send_email returns False, so the alert stays pending for the next run
def test_failed_send_leaves_alerts_pending(job_db):
    item = make_item(job_db)
    listing = scheduler.upsert_listing(job_db, item.id, candidate(100.0))
    scheduler.record_alert(job_db, item.id, listing.id, "price_drop")
    job_db.commit()
    assert asyncio.run(scheduler.run_digest_job()) == 0
    assert job_db.query(Alert).filter(Alert.sent_at.is_(None)).count() == 1


# a rescan sees every candidate the pipeline ranked. with four retailers that was ~20 urls the
# item had never stored, and every one became an alert - 21 "new alternative" notices for a
# single usb hub in one email. an alternative is only news if it beats what you already found
def patch_rank(monkeypatch, candidates):
    async def fake(db, item):
        return candidates

    monkeypatch.setattr(scheduler, "rank_for_item", fake)


def test_only_cheaper_alternatives_raise_an_alert(db, monkeypatch):
    item = make_item(db)
    scheduler.upsert_listing(db, item.id, candidate(50.0, url="https://example.com/known"))
    db.commit()

    patch_rank(monkeypatch, [
        candidate(70.0, url="https://example.com/dearer"),
        candidate(40.0, url="https://example.com/cheaper"),
        candidate(None, url="https://example.com/unpriced"),
    ])
    alerts = asyncio.run(scheduler.check_new_alternatives(db, item))

    assert len(alerts) == 1
    assert db.get(Listing, alerts[0].listing_id).price == 40.0


# the listing is still stored either way: it is real inventory and the watchlist should show
# it. only the alert is withheld
def test_the_dearer_alternatives_are_still_stored(db, monkeypatch):
    item = make_item(db)
    scheduler.upsert_listing(db, item.id, candidate(50.0, url="https://example.com/known"))
    db.commit()

    patch_rank(monkeypatch, [candidate(70.0, url="https://example.com/dearer")])
    asyncio.run(scheduler.check_new_alternatives(db, item))

    urls = {url for (url,) in db.query(Listing.url).filter(Listing.item_id == item.id)}
    assert "https://example.com/dearer" in urls


def test_new_alternatives_are_capped_per_item(db, monkeypatch):
    item = make_item(db)
    scheduler.upsert_listing(db, item.id, candidate(90.0, url="https://example.com/known"))
    db.commit()

    patch_rank(monkeypatch, [candidate(float(n), url=f"https://example.com/{n}")
                             for n in range(10, 10 + 8)])
    alerts = asyncio.run(scheduler.check_new_alternatives(db, item))

    assert len(alerts) == scheduler.MAX_NEW_ALTERNATIVES_PER_ITEM


# nothing priced yet means there is no bar to clear, so a first find is worth knowing about
def test_with_no_prices_stored_anything_new_alerts(db, monkeypatch):
    item = make_item(db)
    patch_rank(monkeypatch, [candidate(70.0, url="https://example.com/first")])
    assert len(asyncio.run(scheduler.check_new_alternatives(db, item))) == 1


# the debug panel triggers the same job functions the scheduler does, so an unknown name must
# not silently do nothing
def test_debug_job_names_match_the_scheduled_ones():
    from backend.routers.debug import JOBS, SLOW_JOBS

    assert set(JOBS) == {"scrape", "review_check", "digest"}
    # the two that re-search every watched item take minutes and must not block a request
    assert set(SLOW_JOBS) == {"scrape", "review_check"}
    assert "digest" not in SLOW_JOBS
    for runner in JOBS.values():
        assert callable(runner)
