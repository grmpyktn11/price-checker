from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Alert, Item, Listing

router = APIRouter(prefix="/api", tags=["alerts"])

MAX_ALERTS = 200   # single user, alert history table; no paging in the spec


class AlertOut(BaseModel):
    id: int
    item_id: int | None
    item_name: str | None
    listing_id: int | None
    retailer: str | None
    url: str | None
    price: float | None
    reason: str | None
    sent_at: datetime | None


# alerts has no created_at column, so id order is the only chronology available
@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(db: Session = Depends(get_db)):
    rows = (
        db.query(Alert, Item.name, Listing.retailer, Listing.url, Listing.price)
        .outerjoin(Item, Item.id == Alert.item_id)
        .outerjoin(Listing, Listing.id == Alert.listing_id)
        .order_by(Alert.id.desc())
        .limit(MAX_ALERTS)
        .all()
    )
    return [
        AlertOut(
            id=alert.id,
            item_id=alert.item_id,
            item_name=item_name,
            listing_id=alert.listing_id,
            retailer=retailer,
            url=url,
            price=price,
            reason=alert.reason,
            sent_at=alert.sent_at,
        )
        for alert, item_name, retailer, url, price in rows
    ]
