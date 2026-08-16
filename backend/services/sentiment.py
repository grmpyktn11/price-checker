import logging
import os

import anthropic

from backend.services.criteria import parse_json_reply

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 300
MAX_INPUT_CHARS = 6000   # 3 sources x google_cse.MAX_SUMMARY_CHARS
CANNED_SENTIMENT = {"sentiment": "unknown", "confidence": 0.0, "summary": ""}
VALID_SENTIMENTS = ("positive", "negative", "mixed", "unknown")
POSITIVE_RATING_FLOOR = 4.3    # a rating this high alongside negative talk is the contradiction
NEGATIVE_RATING_CEILING = 3.5

SYSTEM_PROMPT = """You are reading community discussion collected for a shopping query.
Classify overall sentiment about this kind of product. Reply with one JSON object:
{"sentiment": "positive" | "negative" | "mixed" | "unknown", "confidence": 0.0-1.0,
"summary": "one sentence"}. Use unknown when the text is off-topic or too thin to judge.
Do not invent details."""

logger = logging.getLogger(__name__)


# labelled by source so the model knows what it is reading, then truncated as a whole
def build_input(external_reviews: list[dict]) -> str:
    blocks = [f"[{review['source']}] {review.get('summary_text') or ''}"
              for review in external_reviews]
    return "\n\n".join(blocks)[:MAX_INPUT_CHARS]


def parse_sentiment_reply(text: str) -> dict:
    parsed = parse_json_reply(text)
    if not isinstance(parsed, dict) or parsed.get("sentiment") not in VALID_SENTIMENTS:
        logger.warning("sentiment returned an unusable reply: %s", text[:500])
        return dict(CANNED_SENTIMENT)
    confidence = parsed.get("confidence")
    return {
        "sentiment": parsed["sentiment"],
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0.0,
        "summary": str(parsed.get("summary") or ""),
    }


# LLM call #4. one call per run, not per product: the external text is identical for every
# candidate, so up to 9 calls would be 9 copies of the same question
async def classify(external_reviews: list[dict]) -> dict:
    text = build_input(external_reviews)
    if not text.strip() or not ANTHROPIC_API_KEY:
        return dict(CANNED_SENTIMENT)
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as error:
        logger.warning("sentiment classification failed: %s", error)
        return dict(CANNED_SENTIMENT)
    return parse_sentiment_reply(response.content[0].text)


# pure per-product comparison. "mixed" contradicts nothing: community discussion is usually
# mixed, and flagging that would flag nearly every product and make the signal worthless.
# the text was retrieved with the item query, not this product's model, so this means "the
# star rating sits far outside what the community says about this class of product"
def contradicts(sentiment: str | None, rating: float | None) -> bool:
    if rating is None:
        return False
    if sentiment == "negative":
        return rating >= POSITIVE_RATING_FLOOR
    if sentiment == "positive":
        return rating <= NEGATIVE_RATING_CEILING
    return False
