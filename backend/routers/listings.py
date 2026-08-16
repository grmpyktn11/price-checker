from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Listing, PriceHistory, Review
from backend.routers.items import get_item_or_404

router = APIRouter(prefix="/api", tags=["listings"])


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_id: int
    retailer: str | None
    store_id: str | None
    store_name: str | None
    distance_miles: float | None
    url: str | None
    price: float | None
    in_stock: bool | None
    shipping_days_est: int | None
    scraped_at: datetime | None


class PricePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    listing_id: int
    price: float | None
    recorded_at: datetime | None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str | None
    rating: float | None
    review_count: int | None
    verified_ratio: float | None
    rating_distribution_json: str | None
    authenticity_flag: str | None
    url: str | None
    summary_text: str | None
    fetched_at: datetime | None


# cheapest first: the watchlist's "best option" is the top row
@router.get("/items/{item_id}/listings", response_model=list[ListingOut])
def list_listings(item_id: int, db: Session = Depends(get_db)):
    get_item_or_404(db, item_id)
    return (
        db.query(Listing)
        .filter(Listing.item_id == item_id)
        .order_by(Listing.price.is_(None), Listing.price)
        .all()
    )


# every listing of the item in one series, oldest first; the chart groups by listing_id
@router.get("/items/{item_id}/price-history", response_model=list[PricePointOut])
def list_price_history(item_id: int, db: Session = Depends(get_db)):
    get_item_or_404(db, item_id)
    return (
        db.query(PriceHistory)
        .join(Listing, Listing.id == PriceHistory.listing_id)
        .filter(Listing.item_id == item_id)
        .order_by(PriceHistory.recorded_at)
        .all()
    )


@router.get("/items/{item_id}/reviews", response_model=list[ReviewOut])
def list_reviews(item_id: int, db: Session = Depends(get_db)):
    get_item_or_404(db, item_id)
    return db.query(Review).filter(Review.item_id == item_id).order_by(Review.source).all()
