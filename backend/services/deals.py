from datetime import datetime, timedelta
from statistics import mean

from sqlalchemy.orm import Session

from backend.models import Item, Listing, PriceHistory, utcnow

ROLLING_WINDOW_DAYS = 30
HISTORY_WINDOW_DAYS = 90
PRICE_DROP_RATIO = 0.9   # 10 percent under the rolling average counts as a drop


# same clock the timestamp columns are written with, so comparisons line up
def utc_cutoff(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


def get_price_history(db: Session, listing_id: int, days: int = HISTORY_WINDOW_DAYS) -> list[PriceHistory]:
    return (
        db.query(PriceHistory)
        .filter(PriceHistory.listing_id == listing_id)
        .filter(PriceHistory.recorded_at >= utc_cutoff(days))
        .order_by(PriceHistory.recorded_at)
        .all()
    )


def get_item_for_listing(db: Session, listing_id: int) -> Item | None:
    listing = db.get(Listing, listing_id)
    if not listing:
        return None
    return db.get(Item, listing.item_id)


def evaluate_deal(db: Session, listing_id: int) -> str | None:
    history = get_price_history(db, listing_id, days=HISTORY_WINDOW_DAYS)
    if not history:
        return None
    current = history[-1].price
    recent = [p.price for p in history if p.recorded_at >= utc_cutoff(ROLLING_WINDOW_DAYS)]
    all_time_min = min(p.price for p in history)
    item = get_item_for_listing(db, listing_id)

    # target_hit wins over price_drop
    if item and item.target_price and current <= item.target_price:
        return "target_hit"
    # at or below the 90-day low, not strictly a new low: a never-moved price also fires
    if current <= all_time_min:
        return "price_drop"
    if recent and current <= PRICE_DROP_RATIO * mean(recent):
        return "price_drop"
    return None
