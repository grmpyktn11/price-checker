import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Alert, Item, Listing, PriceHistory, Review
from backend.scheduler import scrape_item
from backend.services.criteria import DEFAULT_RADIUS_MILES, bad_rule_question, normalize

router = APIRouter(prefix="/api", tags=["items"])

VALID_STATUSES = ("watching", "archived")
# columns a manual add or a PATCH may set, and which are mirrored into criteria_json so the
# rescan pipeline sees the same values the columns hold
CRITERIA_COLUMNS = (
    "name",
    "category",
    "budget_max",
    "target_price",
    "fulfillment_preference",
    "radius_miles",
    "min_review_count",
)


class ItemIn(BaseModel):
    name: str
    category: str | None = None
    # the rule/preference half of the criteria object: keywords, must_haves, preferred_specs,
    # nice_to_haves. the flat fields below are the half that also lives in columns
    criteria: dict = {}
    budget_max: float | None = None
    target_price: float | None = None
    fulfillment_preference: str = "either"
    radius_miles: int = DEFAULT_RADIUS_MILES
    min_review_count: int = 0


class ItemPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    criteria: dict | None = None
    budget_max: float | None = None
    target_price: float | None = None
    fulfillment_preference: str | None = None
    radius_miles: int | None = None
    min_review_count: int | None = None
    status: str | None = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str | None
    category: str | None
    criteria_json: str | None
    budget_max: float | None
    target_price: float | None
    fulfillment_preference: str | None
    radius_miles: int | None
    min_review_count: int | None
    status: str | None


def get_item_or_404(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    return item


# a manual add skips chat, so nothing has validated its rules yet. same validator the chat
# path runs, so an unusable rule cannot reach ranking through the back door
def validated_criteria(item_criteria: dict) -> dict:
    item_criteria = normalize(item_criteria)
    question = bad_rule_question(item_criteria)
    if question:
        raise HTTPException(422, question)
    return item_criteria


# the flat fields win over anything with the same name inside criteria: the columns are what
# the rest of the app reads
def build_criteria(body: ItemIn) -> dict:
    merged = dict(body.criteria)
    merged.update({column: getattr(body, column) for column in CRITERIA_COLUMNS})
    return validated_criteria(merged)


@router.get("/items", response_model=list[ItemOut])
def list_items(db: Session = Depends(get_db)):
    return db.query(Item).order_by(Item.id.desc()).all()


@router.get("/items/{item_id}", response_model=ItemOut)
def read_item(item_id: int, db: Session = Depends(get_db)):
    return get_item_or_404(db, item_id)


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemIn, db: Session = Depends(get_db)):
    item_criteria = build_criteria(body)
    item = Item(
        criteria_json=json.dumps(item_criteria),
        status="watching",
        **{column: getattr(body, column) for column in CRITERIA_COLUMNS},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, body: ItemPatch, db: Session = Depends(get_db)):
    item = get_item_or_404(db, item_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("status") and changes["status"] not in VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {VALID_STATUSES}")

    # rebuild the stored criteria from what is there now plus the change, then validate it
    # the same way a manual add is validated
    item_criteria = json.loads(item.criteria_json) if item.criteria_json else {}
    item_criteria.update(changes.pop("criteria", None) or {})
    item_criteria.update({key: value for key, value in changes.items() if key in CRITERIA_COLUMNS})
    item.criteria_json = json.dumps(validated_criteria(item_criteria))
    for key, value in changes.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


# SQLite does not enforce the foreign keys by default, so the children are removed by hand
# rather than left as orphan rows
@router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = get_item_or_404(db, item_id)
    listing_ids = [row.id for row in db.query(Listing.id).filter(Listing.item_id == item_id)]
    if listing_ids:
        (db.query(PriceHistory)
           .filter(PriceHistory.listing_id.in_(listing_ids))
           .delete(synchronize_session=False))
    for model in (Alert, Listing, Review):
        db.query(model).filter(model.item_id == item_id).delete(synchronize_session=False)
    db.delete(item)
    db.commit()
    return {"deleted": item_id}


# same scrape scrape_job runs, for one item, synchronously
@router.post("/items/{item_id}/rescan")
async def rescan_item(item_id: int, db: Session = Depends(get_db)):
    item = get_item_or_404(db, item_id)
    return await scrape_item(db, item)
