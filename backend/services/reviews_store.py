import json

from sqlalchemy.orm import Session

from backend.models import Review


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
