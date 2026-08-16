import json
import logging
import os

import anthropic

from backend.services.ranking import RankedProduct

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 500
TOP_N = 5   # products narrated and returned to the client

SYSTEM_PROMPT = """You are summarizing shopping search results for the person who asked.
Write 2-4 plain sentences: what the best option is and why, then anything worth flagging
(over budget, few reviews, out of stock). Reference products by name. No markdown, no lists,
no emojis. Do not invent details that are not in the JSON."""

logger = logging.getLogger(__name__)


def format_price(price: float | None) -> str:
    return "price unavailable" if price is None else f"${price:.2f}"


# entry with the most reviews, so the rating quoted is the best supported one
def primary_review(ranked: RankedProduct) -> dict:
    if not ranked.reviews:
        return {}
    return max(ranked.reviews, key=lambda r: r.get("review_count") or 0)


# compact json for the model: specs is a large raw blob and would dominate the prompt
def summarize(ranked: list[RankedProduct]) -> list[dict]:
    rows = []
    for result in ranked:
        review = primary_review(result)
        rows.append(
            {
                "name": result.product.get("name"),
                "retailer": result.retailer,
                "price": result.product.get("price"),
                "in_stock": result.product.get("in_stock"),
                "final_score": round(result.final_score, 2),
                "spec_match": round(result.spec_match, 2),
                "review_score": round(result.review_score, 2),
                "price_score": round(result.price_score, 2),
                "rating": review.get("rating"),
                "review_count": review.get("review_count"),
            }
        )
    return rows


# deterministic template over the real ranked results, no adjectives, no reasoning
def canned_narration(criteria: dict, ranked: list[RankedProduct]) -> str:
    name = criteria.get("name")
    if not ranked:
        return f"No products matched your criteria for {name}."
    best = ranked[0]
    header = (
        f"Found {len(ranked)} options for {name}. "
        f"Best match: {best.product.get('name')} at {format_price(best.product.get('price'))} "
        f"from {best.retailer}."
    )
    lines = [
        f"{index}. {result.product.get('name')} - {format_price(result.product.get('price'))}"
        f" - {result.retailer} - score {result.final_score:.2f}"
        for index, result in enumerate(ranked, start=1)
    ]
    return "\n".join([header, *lines])


async def narrate(criteria: dict, ranked: list[RankedProduct]) -> str:
    if not ANTHROPIC_API_KEY:
        return canned_narration(criteria, ranked)
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps({"criteria": criteria, "results": summarize(ranked)}),
                }
            ],
        )
        return response.content[0].text.strip()
    # opposite policy from criteria.py on purpose: narration is cosmetic, a found search must not 500
    except Exception as error:
        logger.warning("narration failed, falling back to template: %s", error)
        return canned_narration(criteria, ranked)
