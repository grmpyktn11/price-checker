import json
import logging
import os

import anthropic

from backend.services.criteria import parse_json_reply

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1000
MAX_DISCUSSION_CHARS = 4000   # per product, so five products fit in one call
NEUTRAL_ASSESSMENT = {"sentiment": "unknown", "confidence": 0.0, "summary": ""}
VALID_SENTIMENTS = ("positive", "negative", "mixed", "unknown")
POSITIVE_RATING_FLOOR = 4.3    # a rating this high alongside negative talk is the contradiction
NEGATIVE_RATING_CEILING = 3.5

SYSTEM_PROMPT = """You are reading community discussion collected separately for each of a
shopper's top candidate products. The user message is JSON: a numbered list of products with
the product name, its star rating if any, and the discussion text found about that product.

Reply with a single JSON object and nothing else:
{"products": [{"index": 0, "sentiment": "positive" | "negative" | "mixed" | "unknown",
"confidence": 0.0-1.0, "summary": "one sentence"}], "too_close": [0, 1]}

- Judge each product only on its own discussion. Use unknown when its text is off-topic, about
  a different product, or too thin to judge. Do not invent details.
- too_close: the indexes of the top-ranked products the discussion cannot separate - the
  evidence is equally good, or equally thin, for all of them. Return [] when the discussion
  clearly favours one product over the others. Only list products you were actually shown.

Judge every index exactly once. No prose, no markdown."""

logger = logging.getLogger(__name__)


# one entry per researched product: name, its rating, and everything found about it
def build_products(products: list[dict]) -> list[dict]:
    return [
        {
            "index": index,
            "name": product.get("name"),
            "rating": product.get("rating"),
            "discussion": (product.get("discussion") or "")[:MAX_DISCUSSION_CHARS],
        }
        for index, product in enumerate(products)
    ]


def read_assessment(row: dict) -> dict:
    confidence = row.get("confidence")
    return {
        "sentiment": row["sentiment"],
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0.0,
        "summary": str(row.get("summary") or ""),
    }


# only indexes that were actually sent, so a hallucinated index cannot spend a YouTube search
def read_too_close(parsed: dict, count: int) -> list[int]:
    rows = parsed.get("too_close")
    if not isinstance(rows, list):
        return []
    return [index for index in rows
            if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < count]


# index-aligned with the products that were sent. a partial or malformed reply leaves the
# rest unknown rather than guessing at them
def parse_reply(text: str, count: int) -> dict:
    assessments = [dict(NEUTRAL_ASSESSMENT) for _ in range(count)]
    parsed = parse_json_reply(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("products"), list):
        logger.warning("sentiment returned an unusable reply: %s", text[:500])
        return {"products": assessments, "too_close": []}
    for row in parsed["products"]:
        if not isinstance(row, dict) or row.get("sentiment") not in VALID_SENTIMENTS:
            continue
        index = row.get("index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < count:
            assessments[index] = read_assessment(row)
    return {"products": assessments, "too_close": read_too_close(parsed, count)}


# LLM call #4. one call for the whole researched top of the ranking, never one per product.
# products are {name, rating, discussion}; the returned "products" list is index-aligned with
# it and "too_close" names the products the discussion could not separate
async def assess(products: list[dict]) -> dict:
    if not products:
        return {"products": [], "too_close": []}
    payload = {"products": build_products(products)}
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
    # a transport failure is not a verdict on any product: everything stays unknown, and
    # unknown separates nothing, so no YouTube quota is spent on an outage
    except Exception as error:
        logger.warning("sentiment assessment failed: %s", error)
        return {"products": [dict(NEUTRAL_ASSESSMENT) for _ in products], "too_close": []}
    return parse_reply(response.content[0].text, len(products))


# pure per-product comparison. "mixed" contradicts nothing: community discussion is usually
# mixed, and flagging that would flag nearly every product and make the signal worthless.
# the discussion was searched with this product's own name, so this means "the star rating
# sits far outside what people say about this product"
def contradicts(sentiment: str | None, rating: float | None) -> bool:
    if rating is None:
        return False
    if sentiment == "negative":
        return rating >= POSITIVE_RATING_FLOOR
    if sentiment == "positive":
        return rating <= NEGATIVE_RATING_CEILING
    return False
