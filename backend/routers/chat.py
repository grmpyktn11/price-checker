import json
import logging
from datetime import datetime
from typing import Literal

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Conversation, Item, Listing, PriceHistory, utcnow
from backend.routers.profile import get_or_create_profile
from backend.services import criteria as criteria_service
from backend.services import reviews_store, trace
from backend.services.narration import TOP_N, narrate, primary_review
from backend.services.pipeline import run_pipeline
from backend.services.ranking import RankedProduct

router = APIRouter(prefix="/api", tags=["chat"])

TITLE_MAX_CHARS = 80   # conversation list only, the full first message stays in history
# reddit summaries run long; a card shows the gist and links out for the rest
SOURCE_SUMMARY_CHARS = 600

logger = logging.getLogger(__name__)


# the id is client-generated, so a first message creates the row
def get_or_create_conversation(db: Session, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        conversation = Conversation(id=conversation_id, history_json="[]", results_json="[]")
        db.add(conversation)
        db.commit()
    return conversation


def load_json(text: str | None, default):
    return json.loads(text) if text else default


# onupdate only fires when a column actually changes, and a re-sent identical history would
# not, so the timestamp is set explicitly
def save_conversation(db: Session, conversation: Conversation, history: list[dict],
                      item_criteria: dict | None = None,
                      results: list[dict] | None = None) -> None:
    conversation.history_json = json.dumps(history)
    if item_criteria is not None:
        conversation.criteria_json = json.dumps(item_criteria)
    if results is not None:
        conversation.results_json = json.dumps(results)
    conversation.updated_at = utcnow()
    db.commit()


# everything /chat/decision needs, and nothing else: buy_now quotes name/url/retailer, watch
# writes the listing fields plus the reviews rows. the ranking scores are not stored - the
# client already has them from the /chat/message response
def decision_record(ranked: RankedProduct) -> dict:
    return {
        "name": ranked.product.get("name"),
        "url": ranked.product.get("url"),
        "price": ranked.product.get("price"),
        "in_stock": ranked.product.get("in_stock"),
        "store_id": ranked.product.get("store_id"),
        "distance_miles": ranked.product.get("distance_miles"),
        "retailer": ranked.retailer,
        "reviews": ranked.reviews,
    }


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
    # source of the quoted rating: "amazon" is first-party, "amazon_inherited" was attributed
    # from another listing the model judged to be the same product
    rating_source: str | None
    final_score: float
    spec_match: float
    review_score: float
    price_score: float
    distance_score: float
    nice_to_have_score: float
    specs_inherited_from: str | None   # retailer these specs were attributed from, if any
    video_url: str | None             # a review video, only for products research reached
    # other listings of this same product - other colours, or the same model at another
    # retailer - folded into this one so it is recommended once. usually empty
    variants: list[dict]
    # what each source actually said about this product, so the score can be read rather than
    # trusted. only the researched top few carry reddit/youtube rows
    sources: list[dict]
    sentiment: str | None             # the model's read of the discussion, when it was researched


class MessageOut(BaseModel):
    type: str                        # followup | results
    question: str | None = None      # followup only
    narration: str | None = None     # results only
    products: list[ProductOut] | None = None   # results only
    # results only. false means no retailer answered, so an empty products list is a search
    # failure rather than a statement that nothing matched
    retailers_answered: bool | None = None
    debug: dict | None = None        # results only, the full pipeline trace


class DecisionIn(BaseModel):
    conversation_id: str
    product_id: int
    decision: Literal["buy_now", "watch"]


class DecisionOut(BaseModel):
    decision: str
    url: str | None                  # purchase link
    item_id: int | None = None       # watch only
    message: str


# the youtube row is attached only to products the research stage reached, so most
# products have no video and the client hides the control
def video_url(ranked: RankedProduct) -> str | None:
    for row in ranked.reviews:
        if row.get("source") == "youtube" and row.get("url"):
            return row["url"]
    return None


# one row per source that said something about this product, trimmed to what a card shows.
# summary_text is the evidence the ranking used, so showing it is how a shopper checks the
# reasoning rather than taking the score on faith
def sources(ranked: RankedProduct) -> list[dict]:
    rows = []
    for row in ranked.reviews:
        summary = row.get("summary_text")
        if not summary and row.get("rating") is None:
            continue
        rows.append({
            "source": row.get("source"),
            "url": row.get("url"),
            "rating": row.get("rating"),
            "review_count": row.get("review_count"),
            "mention_count": row.get("mention_count"),
            "summary": (summary or "")[:SOURCE_SUMMARY_CHARS] or None,
        })
    return rows


# specs and the full spec dict are not serialized: nothing displays them and they are large
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
        rating_source=review.get("source"),
        final_score=ranked.final_score,
        spec_match=ranked.spec_match,
        review_score=ranked.review_score,
        price_score=ranked.price_score,
        distance_score=ranked.distance_score,
        nice_to_have_score=ranked.nice_to_have_score,
        specs_inherited_from=ranked.specs_inherited_from,
        video_url=video_url(ranked),
        variants=ranked.variants,
        sources=sources(ranked),
        sentiment=ranked.sentiment,
    )


# exclude_unset, not exclude_none: it drops the keys of the branch this response is not
# (a followup has no products) while keeping product fields that are genuinely null,
# so the client always sees store_id/distance_miles rather than a missing key
@router.post("/chat/message", response_model=MessageOut, response_model_exclude_unset=True)
async def post_message(body: MessageIn, db: Session = Depends(get_db)) -> MessageOut:
    conversation = get_or_create_conversation(db, body.conversation_id)
    history = load_json(conversation.history_json, [])
    try:
        result = await criteria_service.extract(history, body.message)
    # transport failure, not a bad reply: nothing useful to say back to the user
    except anthropic.APIError as error:
        logger.warning("criteria extraction failed: %s", error)
        raise HTTPException(502, "criteria extraction failed")

    # appended after extraction so the new message is not duplicated in the prompt
    history.append({"role": "user", "content": body.message})

    if result["type"] == "followup":
        history.append({"role": "assistant", "content": result["question"]})
        save_conversation(db, conversation, history)
        return MessageOut(type="followup", question=result["question"])

    item_criteria = result["criteria"]
    profile = get_or_create_profile(db)
    if profile.lat is None or profile.lon is None:
        # the turn we just appended is never saved: leaving a user message with no assistant
        # reply would put two user turns in a row in the next prompt
        raise HTTPException(400, "Set your location first: PATCH /api/profile/location")

    ranked = await run_pipeline(
        item_criteria, profile.lat, profile.lon, item_criteria["radius_miles"],
        progress_key=body.conversation_id,
    )
    # the trace this run just recorded, on this task's context var
    current_trace = trace.current()
    debug = current_trace.data if current_trace else None
    top = ranked[:TOP_N]
    narration = await narrate(item_criteria, top, trace.retailer_outcomes(debug))
    history.append({"role": "assistant", "content": narration})
    save_conversation(db, conversation, history, item_criteria,
                      [decision_record(r) for r in top])
    return MessageOut(
        type="results",
        narration=narration,
        products=[to_product_out(index, r) for index, r in enumerate(top)],
        # no trace means nothing recorded the searches, not that they failed
        retailers_answered=debug["retailers_answered"] if debug else True,
        debug=debug,
    )


@router.post("/chat/decision", response_model=DecisionOut, response_model_exclude_unset=True)
def post_decision(body: DecisionIn, db: Session = Depends(get_db)) -> DecisionOut:
    conversation = db.get(Conversation, body.conversation_id)
    if conversation is None:
        raise HTTPException(404, "conversation not found")
    results = load_json(conversation.results_json, [])
    if not results:
        raise HTTPException(404, "no results in this conversation yet")
    if not 0 <= body.product_id < len(results):
        raise HTTPException(404, "unknown product_id")

    chosen = results[body.product_id]
    if body.decision == "buy_now":
        return DecisionOut(
            decision="buy_now",
            url=chosen["url"],
            message=f"Buy {chosen['name']} at {chosen['retailer']}.",
        )

    # the listings unique key is meaningless without a url, and a rescan cannot re-find it
    if chosen.get("url") is None:
        raise HTTPException(400, "product has no url")
    item = watch_product(db, load_json(conversation.criteria_json, {}), chosen)
    return DecisionOut(
        decision="watch",
        url=chosen["url"],
        item_id=item.id,
        message=f"Watching {chosen['name']}.",
    )


class ConversationSummary(BaseModel):
    id: str
    title: str            # first user message, truncated
    created_at: datetime
    updated_at: datetime


class ConversationOut(BaseModel):
    id: str
    history: list[dict]   # [{"role", "content"}], oldest first
    created_at: datetime
    updated_at: datetime


def conversation_title(history: list[dict]) -> str:
    first = next((turn["content"] for turn in history if turn.get("role") == "user"), "")
    return first[:TITLE_MAX_CHARS]


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(db: Session = Depends(get_db)) -> list[ConversationSummary]:
    rows = db.query(Conversation).order_by(Conversation.updated_at.desc()).all()
    return [
        ConversationSummary(
            id=row.id,
            title=conversation_title(load_json(row.history_json, [])),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationOut:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "conversation not found")
    return ConversationOut(
        id=conversation.id,
        history=load_json(conversation.history_json, []),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


# one item, one listing, one price_history row, for the picked product only.
# finding better alternatives is the job of the separate new_alternative scan
def watch_product(db: Session, item_criteria: dict, chosen: dict) -> Item:
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
        retailer=chosen["retailer"],
        store_id=chosen.get("store_id"),
        # Best Buy search returns online rows with no store
        store_name=None,
        distance_miles=chosen.get("distance_miles"),
        url=chosen["url"],
        price=chosen.get("price"),
        in_stock=chosen.get("in_stock"),
        shipping_days_est=None,
    )
    db.add(listing)
    # flush so the listing id exists for the price_history row
    db.flush()
    if listing.price is not None:
        db.add(PriceHistory(listing_id=listing.id, price=listing.price))
    # first writer for the reviews table: the chosen product's retailer row (first-party or
    # inherited) plus the shared external rows, so a rescan can reuse them instead of quota
    reviews_store.save_reviews(db, item.id, chosen.get("reviews", []))
    db.commit()
    db.refresh(item)
    return item
