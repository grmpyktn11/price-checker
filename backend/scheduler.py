import asyncio
import json
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.models import Alert, Item, Listing, PriceHistory, utcnow
from backend.routers.profile import get_or_create_profile
from backend.services import email
from backend.services.deals import evaluate_deal
from backend.services.pipeline import run_pipeline
from backend.services.ranking import RankedProduct

SCRAPE_INTERVAL_HOURS = 6   # spec.md, Scheduler Jobs
REVIEW_CHECK_HOUR = 3       # daily
DIGEST_HOUR = 8             # daily
DEFAULT_RADIUS_MILES = 25   # only used when an item somehow has none stored

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


# every job runs on a scheduler thread while requests are being served on other threads. a
# session is not thread-safe, so a job opens its own and never touches a request's session
def job_session() -> Session:
    return SessionLocal()


def watched_items(db: Session) -> list[Item]:
    return db.query(Item).filter(Item.status == "watching").all()


# the pipeline needs coordinates; without a profile location there is nothing to scan
def profile_location(db: Session) -> tuple[float, float] | None:
    profile = get_or_create_profile(db)
    if profile.lat is None or profile.lon is None:
        return None
    return profile.lat, profile.lon


async def rank_for_item(db: Session, item: Item) -> list[RankedProduct]:
    location = profile_location(db)
    if location is None:
        logger.warning("no profile location, skipping item %s", item.id)
        return []
    item_criteria = json.loads(item.criteria_json)
    return await run_pipeline(
        item_criteria,
        location[0],
        location[1],
        item.radius_miles or DEFAULT_RADIUS_MILES,
        db=db,
        item_id=item.id,
    )


# upsert on the listings unique key. price change is the only thing that writes price_history
def upsert_listing(db: Session, item_id: int, candidate: RankedProduct) -> Listing | None:
    url = candidate.product.get("url")
    # the unique key is meaningless without a url and a later rescan could not re-find it
    if not url:
        return None
    price = candidate.product.get("price")
    listing = (
        db.query(Listing)
        .filter_by(
            item_id=item_id,
            retailer=candidate.retailer,
            store_id=candidate.product.get("store_id"),
            url=url,
        )
        .one_or_none()
    )
    if listing is None:
        listing = Listing(
            item_id=item_id,
            retailer=candidate.retailer,
            store_id=candidate.product.get("store_id"),
            store_name=None,
            distance_miles=candidate.product.get("distance_miles"),
            url=url,
            price=price,
            in_stock=candidate.product.get("in_stock"),
            shipping_days_est=None,
        )
        db.add(listing)
        db.flush()
    else:
        price_changed = price is not None and price != listing.price
        listing.price = price if price is not None else listing.price
        listing.in_stock = candidate.product.get("in_stock")
        listing.distance_miles = candidate.product.get("distance_miles")
        listing.scraped_at = utcnow()
        # unchanged price writes no row: history is a series of changes, not of scans
        if not price_changed:
            return listing
    if listing.price is not None:
        db.add(PriceHistory(listing_id=listing.id, price=listing.price))
        # flushed here, not at commit: the session runs with autoflush off, and evaluate_deal
        # queries price_history straight after this call
        db.flush()
    return listing


# one pending alert per listing+reason. without this a flat price at its 90-day low would add
# a fresh row every 6 hours and the digest would repeat itself
def record_alert(db: Session, item_id: int, listing_id: int, reason: str) -> Alert | None:
    pending = (
        db.query(Alert)
        .filter(Alert.listing_id == listing_id, Alert.reason == reason, Alert.sent_at.is_(None))
        .first()
    )
    if pending:
        return None
    alert = Alert(item_id=item_id, listing_id=listing_id, reason=reason)
    db.add(alert)
    db.flush()
    return alert


# what email.py renders: alert plus the item and listing it points at
def alert_row(db: Session, alert: Alert) -> dict:
    item = db.get(Item, alert.item_id)
    listing = db.get(Listing, alert.listing_id)
    return {
        "item_name": item.name if item else None,
        "reason": alert.reason,
        "price": listing.price if listing else None,
        "target_price": item.target_price if item else None,
        "retailer": listing.retailer if listing else None,
        "store_name": listing.store_name if listing else None,
        "url": listing.url if listing else None,
    }


# spec.md: target hits are sent at detection time, everything else waits for the digest
async def send_immediately(db: Session, alert: Alert) -> bool:
    row = alert_row(db, alert)
    sent = await email.send_email(email.immediate_subject(row), email.render_digest([row]))
    if sent:
        alert.sent_at = utcnow()
        db.commit()
    return sent


# the whole per-item scrape: rank, upsert, deal check, immediate target-hit send. shared by
# scrape_job and POST /api/items/{id}/rescan
async def scrape_item(db: Session, item: Item) -> dict:
    ranked = await rank_for_item(db, item)
    new_alerts = []
    for candidate in ranked:
        listing = upsert_listing(db, item.id, candidate)
        if listing is None:
            continue
        reason = evaluate_deal(db, listing.id)
        if not reason:
            continue
        alert = record_alert(db, item.id, listing.id, reason)
        if alert:
            new_alerts.append(alert)
    db.commit()

    emails_sent = 0
    for alert in new_alerts:
        if alert.reason == "target_hit" and await send_immediately(db, alert):
            emails_sent += 1
    return {
        "item_id": item.id,
        "listings_seen": len(ranked),
        "alerts": [{"id": a.id, "reason": a.reason, "listing_id": a.listing_id} for a in new_alerts],
        "emails_sent": emails_sent,
    }


async def run_scrape_job() -> None:
    db = job_session()
    try:
        for item in watched_items(db):
            # one bad item must not stop the rest of the watchlist
            try:
                result = await scrape_item(db, item)
                logger.info("scrape_job item %s: %s", item.id, result)
            except Exception:
                db.rollback()
                logger.exception("scrape_job failed for item %s", item.id)
    finally:
        db.close()


# the pipeline never filtered on known urls, so "broader" is the same call: the diff is here,
# against the urls already stored for this item
async def check_new_alternatives(db: Session, item: Item) -> list[Alert]:
    known_urls = {url for (url,) in db.query(Listing.url).filter(Listing.item_id == item.id)}
    ranked = await rank_for_item(db, item)
    new_alerts = []
    for candidate in ranked:
        if candidate.product.get("url") in known_urls:
            continue
        listing = upsert_listing(db, item.id, candidate)
        if listing is None:
            continue
        alert = record_alert(db, item.id, listing.id, "new_alternative")
        if alert:
            new_alerts.append(alert)
    db.commit()
    return new_alerts


async def run_review_check_job() -> None:
    db = job_session()
    try:
        for item in watched_items(db):
            try:
                alerts = await check_new_alternatives(db, item)
                logger.info("review_check_job item %s: %s new alternatives", item.id, len(alerts))
            except Exception:
                db.rollback()
                logger.exception("review_check_job failed for item %s", item.id)
    finally:
        db.close()


async def run_digest_job() -> int:
    db = job_session()
    try:
        pending = db.query(Alert).filter(Alert.sent_at.is_(None)).order_by(Alert.id).all()
        if not pending:
            logger.info("digest_job: nothing pending")
            return 0
        rows = [alert_row(db, alert) for alert in pending]
        # a failed send leaves sent_at null, so the alerts roll into tomorrow's digest
        if not await email.send_email(email.digest_subject(rows), email.render_digest(rows)):
            return 0
        sent_at = utcnow()
        for alert in pending:
            alert.sent_at = sent_at
        db.commit()
        return len(pending)
    finally:
        db.close()


# APScheduler runs jobs on plain threads, so each entry point owns its event loop
def scrape_job() -> None:
    asyncio.run(run_scrape_job())


def review_check_job() -> None:
    asyncio.run(run_review_check_job())


def digest_job() -> None:
    asyncio.run(run_digest_job())


def start_scheduler() -> None:
    scheduler.add_job(scrape_job, "interval", hours=SCRAPE_INTERVAL_HOURS, id="scrape")
    scheduler.add_job(review_check_job, "cron", hour=REVIEW_CHECK_HOUR, id="review_check")
    scheduler.add_job(digest_job, "cron", hour=DIGEST_HOUR, id="digest")
    scheduler.start()
    logger.info("scheduler started")
