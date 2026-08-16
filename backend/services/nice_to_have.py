import json
import logging
import os

import anthropic

from backend.services.criteria import parse_json_reply

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 200
NO_PREFERENCES_SCORE = 1.0   # nothing asked for cannot be missed, same as compute_spec_match
CANNED_SCORE = 0.5           # no key: neutral, identical for every product, cannot reorder

SYSTEM_PROMPT = """Score how well this product matches each subjective preference, 0.0 to 1.0,
judging only from the title. 0.5 when the title says nothing either way. Reply with a single
JSON object: {"scores": {"<preference>": 0.0}} and nothing else."""

logger = logging.getLogger(__name__)


# the model scores, deterministic code aggregates: average the requested preferences only,
# ignoring extra keys the model invented
def parse_score_reply(text: str, nice_to_haves: list[str]) -> float:
    parsed = parse_json_reply(text) or {}
    scores = parsed.get("scores")
    if not isinstance(scores, dict):
        logger.warning("nice-to-have scoring returned no scores object: %s", text[:500])
        return CANNED_SCORE
    values = []
    for preference in nice_to_haves:
        value = scores.get(preference)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(max(0.0, min(1.0, float(value))))
    if not values:
        logger.warning("nice-to-have scoring named none of the requested preferences")
        return CANNED_SCORE
    return sum(values) / len(values)


# LLM call #3. title and price only: "cute" and "sleek" are not spec judgements and
# a spec blob would dominate the prompt
async def score(product: dict, nice_to_haves: list[str]) -> float:
    if not nice_to_haves:
        return NO_PREFERENCES_SCORE
    if not ANTHROPIC_API_KEY:
        return CANNED_SCORE
    payload = {
        "title": product.get("name"),
        "price": product.get("price"),
        "preferences": nice_to_haves,
    }
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
    # a subjective score is the smallest term in the ranking: never fail a search over it
    except Exception as error:
        logger.warning("nice-to-have scoring failed: %s", error)
        return CANNED_SCORE
    return parse_score_reply(response.content[0].text, nice_to_haves)
