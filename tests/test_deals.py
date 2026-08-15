from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db import Base
from backend.models import Item, Listing, PriceHistory
from backend.services.deals import evaluate_deal

# naive UTC, matching what SQLite returns on read
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# one item + one listing, then the given (days_ago, price) rows
def seed(db, history, target_price=None):
    item = Item(name="portable charger", target_price=target_price, status="watching")
    db.add(item)
    db.flush()
    listing = Listing(item_id=item.id, retailer="bestbuy", url="https://example.com/1")
    db.add(listing)
    db.flush()
    for days_ago, price in history:
        db.add(
            PriceHistory(
                listing_id=listing.id, price=price, recorded_at=NOW - timedelta(days=days_ago)
            )
        )
    db.commit()
    return listing.id


def test_target_hit_wins_over_price_drop(db):
    listing_id = seed(db, [(10, 120.0), (1, 90.0)], target_price=100.0)
    assert evaluate_deal(db, listing_id) == "target_hit"


def test_price_drop_at_ninety_day_low(db):
    listing_id = seed(db, [(20, 120.0), (10, 110.0), (1, 100.0)])
    assert evaluate_deal(db, listing_id) == "price_drop"


def test_price_drop_below_rolling_average(db):
    listing_id = seed(db, [(80, 60.0), (20, 100.0), (10, 100.0), (5, 100.0), (1, 80.0)])
    assert evaluate_deal(db, listing_id) == "price_drop"


def test_no_deal(db):
    listing_id = seed(db, [(80, 50.0), (20, 100.0), (10, 100.0), (1, 100.0)])
    assert evaluate_deal(db, listing_id) is None


def test_no_history(db):
    listing_id = seed(db, [])
    assert evaluate_deal(db, listing_id) is None


def test_only_rows_older_than_thirty_days(db):
    listing_id = seed(db, [(80, 100.0), (60, 90.0), (40, 95.0)])
    assert evaluate_deal(db, listing_id) is None
