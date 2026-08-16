import json
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.models import Review, utcnow

# external sentiment moves slowly, and retailer ratings are refetched free with the page we
# already load. without this cache four rescans a day per item would exhaust the CSE tier
REVIEW_STALENESS_DAYS = 7
EXTERNAL_SOURCES = ("reddit", "forum", "youtube")


def row_to_dict(row: Review) -> dict:
    return {
        "source": row.source,
        "rating": row.rating,
        "review_count": row.review_count,
        "verified_ratio": row.verified_ratio,
        "rating_distribution": json.loads(row.rating_distribution_json)
                               if row.rating_distribution_json else None,
        "url": row.url,
        "summary_text": row.summary_text,
        "authenticity_flag": row.authenticity_flag,
    }


# a non-empty result lets the pipeline skip all three external fetches: zero CSE queries and
# zero YouTube units for that run
def load_fresh_external(db: Session, item_id: int,
                        max_age_days: int = REVIEW_STALENESS_DAYS) -> list[dict]:
    cutoff = utcnow() - timedelta(days=max_age_days)
    rows = (
        db.query(Review)
        .filter(Review.item_id == item_id)
        .filter(Review.source.in_(EXTERNAL_SOURCES))
        .filter(Review.fetched_at >= cutoff)
        .all()
    )
    return [row_to_dict(row) for row in rows]


# delete then insert, so there is exactly one row per source per item. no history table for
# reviews; the spec does not ask for one
def save_reviews(db: Session, item_id: int, reviews: list[dict]) -> None:
    sources = [review["source"] for review in reviews]
    if not sources:
        return
    (db.query(Review)
       .filter(Review.item_id == item_id, Review.source.in_(sources))
       .delete(synchronize_session=False))
    for review in reviews:
        distribution = review.get("rating_distribution")
        db.add(Review(
            item_id=item_id,
            source=review["source"],
            rating=review.get("rating"),
            review_count=review.get("review_count"),
            verified_ratio=review.get("verified_ratio"),
            rating_distribution_json=json.dumps(distribution) if distribution else None,
            authenticity_flag=review.get("authenticity_flag"),
            url=review.get("url"),
            summary_text=review.get("summary_text"),
        ))
    db.commit()
