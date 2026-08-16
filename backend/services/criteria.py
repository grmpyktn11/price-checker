import json
import logging
import os

import anthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1000
DEFAULT_RADIUS_MILES = 25
DEFAULT_MIN_REVIEW_COUNT = 0   # only filter on reviews when the user actually asked for it
LIST_FIELDS = ("keywords", "must_haves", "preferred_specs", "nice_to_haves")

CANNED_QUESTION = "What is your budget, and do you need it shipped or available for pickup?"
MALFORMED_QUESTION = "Sorry, I did not catch that. Can you rephrase what you are looking for?"

# fixture in Python form, used when no key is configured. same shape pipeline.py documents
CANNED_CRITERIA = {
    "name": "portable charger",
    "category": "electronics",
    "keywords": ["usb-c", "140w"],
    "must_haves": [
        {"field": "Battery Capacity", "op": ">=", "value": 20000},
    ],
    "preferred_specs": [
        {"field": "Number of USB Ports", "op": ">=", "value": 3},
        {"field": "Product Weight", "op": "<=", "value": 1.0},
    ],
    "nice_to_haves": ["compact", "looks sleek"],
    "budget_max": 150.0,
    "target_price": 99.0,
    "fulfillment_preference": "either",
    "radius_miles": 25,
    "min_review_count": 100,
}

SYSTEM_PROMPT = """You extract shopping criteria from a conversation.

Reply with a single JSON object and nothing else. Use one of exactly two shapes.

If anything essential is missing or ambiguous - the product, the budget, a vague spec, a
model/trim that changes the price - ask one question:
{"type": "followup", "question": "..."}

Otherwise return the criteria:
{"type": "criteria", "criteria": {
  "name": "portable charger",
  "category": "electronics",
  "keywords": ["usb-c"],
  "must_haves": [{"field": "Battery Capacity", "op": ">=", "value": 20000}],
  "preferred_specs": [{"field": "Number of USB Ports", "op": ">=", "value": 3}],
  "nice_to_haves": ["compact"],
  "budget_max": 150.0,
  "target_price": 99.0,
  "fulfillment_preference": "either",
  "radius_miles": 25,
  "min_review_count": 100
}}

Rules:
- op is one of >=, <=, ==, contains, exists.
- field is the spec name as a retailer prints it on a product page, e.g. "Battery Capacity".
- value for >=, <=, == is a number in the unit the retailer prints; no unit conversion happens later.
- must_haves are hard filters, preferred_specs are soft preferences, nice_to_haves are subjective phrases.
- budget_max and target_price may be null. Ask at most one question per reply."""

logger = logging.getLogger(__name__)


# only fill what run_pipeline indexes directly; everything else passes through untouched.
# the model emits explicit nulls, not missing keys, for fields it has no value for, so both
# have to be replaced: run_pipeline compares min_review_count and iterates LIST_FIELDS,
# and neither works on None
def normalize(raw: dict) -> dict:
    if raw.get("radius_miles") is None:
        raw["radius_miles"] = DEFAULT_RADIUS_MILES
    if raw.get("min_review_count") is None:
        raw["min_review_count"] = DEFAULT_MIN_REVIEW_COUNT
    for field in LIST_FIELDS:
        if raw.get(field) is None:
            raw[field] = []
    return raw


# tolerate a code fence or a sentence wrapped around the object
def parse_json_reply(text: str) -> dict | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        if stripped.startswith("json"):
            stripped = stripped[len("json"):]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(stripped[start:end + 1])
    except ValueError:
        return None


# stored history already uses Anthropic's role/content shape, so no translation is needed
def build_messages(history: list[dict], message: str) -> list[dict]:
    return [*history, {"role": "user", "content": message}]


# a followup needs a question, criteria needs a name; anything else is unusable
def is_valid(parsed: dict | None) -> bool:
    if not parsed:
        return False
    if parsed.get("type") == "followup":
        return bool(parsed.get("question"))
    if parsed.get("type") == "criteria":
        return bool((parsed.get("criteria") or {}).get("name"))
    return False


async def extract(history: list[dict], message: str) -> dict:
    # no key configured: ask once, then return the saved criteria. counts turns, reads nothing
    if not ANTHROPIC_API_KEY:
        if not history:
            return {"type": "followup", "question": CANNED_QUESTION}
        # copy so a caller editing the returned dict cannot corrupt the constant
        return {"type": "criteria", "criteria": normalize(dict(CANNED_CRITERIA))}

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=build_messages(history, message),
    )
    text = response.content[0].text
    parsed = parse_json_reply(text)
    # a bad model reply is a conversation problem, not an outage: keep the chat alive
    if not is_valid(parsed):
        logger.warning("criteria extraction returned malformed json: %s", text[:500])
        return {"type": "followup", "question": MALFORMED_QUESTION}
    if parsed["type"] == "followup":
        return {"type": "followup", "question": parsed["question"]}
    return {"type": "criteria", "criteria": normalize(parsed["criteria"])}
