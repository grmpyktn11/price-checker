import json
import logging
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Item, Listing, PriceHistory
from backend.routers.profile import get_or_create_profile
from backend.services import criteria as criteria_service
from backend.services import reviews_store
from backend.services.narration import TOP_N, narrate, primary_review
from backend.services.pipeline import run_pipeline
from backend.services.ranking import RankedProduct

router = APIRouter(prefix="/api", tags=["chat"])

MAX_CONVERSATIONS = 50   # single user; drop the oldest so a long-running process cannot grow forever

logger = logging.getLogger(__name__)


# scratch state: only the "watch" decision produces anything durable, so no table for this
@dataclass
class Conversation:
    history: list[dict] = field(default_factory=list)   # [{"role", "content"}], oldest first
    criteria: dict | None = None                        # set once extraction completes
    results: list[RankedProduct] = field(default_factory=list)   # indexed by product_id


CONVERSATIONS: dict[str, Conversation] = {}


# dicts keep insertion order, so the first key is the oldest conversation
def get_conversation(conversation_id: str) -> Conversation:
    if conversation_id not in CONVERSATIONS:
        if len(CONVERSATIONS) >= MAX_CONVERSATIONS:
            del CONVERSATIONS[next(iter(CONVERSATIONS))]
        CONVERSATIONS[conversation_id] = Conversation()
    return CONVERSATIONS[conversation_id]


class MessageIn(BaseModel):
    conversation_id: str
    message: str


class ProductOut(BaseModel):
    product_id: int          # index into this conversation's last results, used by /chat/decision
    name: str | None
    url: str | None
    price: float | None
    in_stock: bool | None
    retailer: str
    store_id: str | None
    distance_miles: float | None
    rating: float | None
    review_count: int | None
    final_score: float
    spec_match: float
    review_score: float
    price_score: float
    distance_score: float
    nice_to_have_score: float
    specs_inherited_from: str | None   # retailer these specs were attributed from, if any


class MessageOut(BaseModel):
    type: str                        # followup | results
    question: str | None = None      # followup only
    narration: str | None = None     # results only
    products: list[ProductOut] | None = None   # results only


class DecisionIn(BaseModel):
    conversation_id: str
    product_id: int
    decision: Literal["buy_now", "watch"]


class DecisionOut(BaseModel):
    decision: str
    url: str | None                  # purchase link
    item_id: int | None = None       # watch only
    message: str


# specs and the full reviews list are not serialized: nothing displays them and they are large
def to_product_out(product_id: int, ranked: RankedProduct) -> ProductOut:
    review = primary_review(ranked)
    return ProductOut(
        product_id=product_id,
        name=ranked.product.get("name"),
        url=ranked.product.get("url"),
        price=ranked.product.get("price"),
        in_stock=ranked.product.get("in_stock"),
        retailer=ranked.retailer,
        store_id=ranked.product.get("store_id"),
        distance_miles=ranked.product.get("distance_miles"),
        rating=review.get("rating"),
        review_count=review.get("review_count"),
        final_score=ranked.final_score,
        spec_match=ranked.spec_match,
        review_score=ranked.review_score,
        price_score=ranked.price_score,
        distance_score=ranked.distance_score,
        nice_to_have_score=ranked.nice_to_have_score,
        specs_inherited_from=ranked.specs_inherited_from,
    )


# exclude_unset, not exclude_none: it drops the keys of the branch this response is not
# (a followup has no products) while keeping product fields that are genuinely null,
# so the client always sees store_id/distance_miles rather than a missing key
@router.post("/chat/message", response_model=MessageOut, response_model_exclude_unset=True)
async def post_message(body: MessageIn, db: Session = Depends(get_db)) -> MessageOut:
    conversation = get_conversation(body.conversation_id)
    try:
        result = await criteria_service.extract(conversation.history, body.message)
    # transport failure, not a bad reply: nothing useful to say back to the user
    except anthropic.APIError as error:
        logger.warning("criteria extraction failed: %s", error)
        raise HTTPException(502, "criteria extraction failed")

    # appended after extraction so the new message is not duplicated in the prompt
    conversation.history.append({"role": "user", "content": body.message})

    if result["type"] == "followup":
        conversation.history.append({"role": "assistant", "content": result["question"]})
        return MessageOut(type="followup", question=result["question"])

    item_criteria = result["criteria"]
    profile = get_or_create_profile(db)
    if profile.lat is None or profile.lon is None:
        # drop the turn we just appended: leaving a user message with no assistant reply
        # would put two user turns in a row in the next prompt
        conversation.history.pop()
        raise HTTPException(400, "Set your location first: PATCH /api/profile/location")

    ranked = await run_pipeline(
        item_criteria, profile.lat, profile.lon, item_criteria["radius_miles"]
    )
    conversation.criteria = item_criteria
    conversation.results = ranked[:TOP_N]
    narration = await narrate(item_criteria, conversation.results)
    conversation.history.append({"role": "assistant", "content": narration})
    return MessageOut(
        type="results",
        narration=narration,
        products=[to_product_out(index, r) for index, r in enumerate(conversation.results)],
    )


@router.post("/chat/decision", response_model=DecisionOut, response_model_exclude_unset=True)
def post_decision(body: DecisionIn, db: Session = Depends(get_db)) -> DecisionOut:
    conversation = CONVERSATIONS.get(body.conversation_id)
    if conversation is None:
        raise HTTPException(404, "conversation not found or expired")
    if not conversation.results:
        raise HTTPException(404, "no results in this conversation yet")
    if not 0 <= body.product_id < len(conversation.results):
        raise HTTPException(404, "unknown product_id")

    chosen = conversation.results[body.product_id]
    if body.decision == "buy_now":
        return DecisionOut(
            decision="buy_now",
            url=chosen.product["url"],
            message=f"Buy {chosen.product['name']} at {chosen.retailer}.",
        )

    # the listings unique key is meaningless without a url, and a rescan cannot re-find it
    if chosen.product.get("url") is None:
        raise HTTPException(400, "product has no url")
    item = watch_product(db, conversation.criteria, chosen)
    return DecisionOut(
        decision="watch",
        url=chosen.product["url"],
        item_id=item.id,
        message=f"Watching {chosen.product['name']}.",
    )


# one item, one listing, one price_history row, for the picked product only.
# finding better alternatives is the job of the separate new_alternative scan
def watch_product(db: Session, item_criteria: dict, chosen: RankedProduct) -> Item:
    item = Item(
        name=item_criteria["name"],
        category=item_criteria.get("category"),
        criteria_json=json.dumps(item_criteria),
        budget_max=item_criteria.get("budget_max"),
        target_price=item_criteria.get("target_price"),
        fulfillment_preference=item_criteria.get("fulfillment_preference"),
        radius_miles=item_criteria.get("radius_miles"),
        min_review_count=item_criteria.get("min_review_count"),
        status="watching",
    )
    db.add(item)
    db.flush()
    listing = Listing(
        item_id=item.id,
        retailer=chosen.retailer,
        store_id=chosen.product.get("store_id"),
        # Best Buy search returns online rows with no store
        store_name=None,
        distance_miles=chosen.product.get("distance_miles"),
        url=chosen.product["url"],
        price=chosen.product.get("price"),
        in_stock=chosen.product.get("in_stock"),
        shipping_days_est=None,
    )
    db.add(listing)
    # flush so the listing id exists for the price_history row
    db.flush()
    if listing.price is not None:
        db.add(PriceHistory(listing_id=listing.id, price=listing.price))
    # first writer for the reviews table: the chosen product's retailer row (first-party or
    # inherited) plus the shared external rows, so a rescan can reuse them instead of quota
    reviews_store.save_reviews(db, item.id, chosen.reviews)
    db.commit()
    db.refresh(item)
    return item
