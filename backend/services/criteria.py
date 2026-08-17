import copy
import json
import logging
import os

import anthropic

from backend.services.ranking import first_number

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1000
DEFAULT_RADIUS_MILES = 25
DEFAULT_MIN_REVIEW_COUNT = 5   # enough to drop listings with no feedback at all, nothing more
LIST_FIELDS = ("keywords", "must_haves", "preferred_specs", "nice_to_haves")
VALID_OPS = (">=", "<=", "==", "contains", "exists")
COMPARISON_OPS = (">=", "<=", "==")
RULE_LISTS = ("must_haves", "preferred_specs")
# one question per op, so the user is told which side of the comparison is missing
RULE_QUESTIONS = {
    ">=": 'How much "{field}" do you need, at minimum? Give a number with its unit.',
    "<=": 'What is the most "{field}" you will accept? Give a number with its unit.',
    "==": 'What exact "{field}" do you need? Give a number.',
}
FALLBACK_RULE_QUESTION = 'What should "{field}" be? Describe the requirement in one line.'
UNUSABLE_FIELD_QUESTION = ("One of your requirements did not come through clearly. "
                           "Can you restate what it needs to have?")

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
  "min_review_count": 5
}}

Rules:
- op is one of >=, <=, ==, contains, exists.
- field is the spec name as a retailer prints it on a product page, e.g. "Battery Capacity".
- value for >=, <=, == is a number in the unit the retailer prints; no unit conversion happens later.
- must_haves are hard filters, preferred_specs are soft preferences, nice_to_haves are subjective phrases.
- budget_max and target_price may be null. Ask at most one question per reply.
- min_review_count is 5 unless the user actually asks for a review threshold. Do not invent one.
- a must_have drops every product whose spec table does not carry that field, and most
  retailers publish only a handful of specs. So use must_haves ONLY for measurable specs a
  retailer really prints, like capacity, wattage, size or weight. Attributes that live in the
  product title instead - switch type, colour, model line, edition, "yellow switches",
  "mechanical" - belong in keywords, and in nice_to_haves if they are subjective. Putting one
  of those in must_haves returns nothing at all."""

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


# a comparison needs a number. "20,000" is a formatting quirk and is repaired in place;
# null, empty, and booleans are rejected rather than guessed at
def usable_comparison_value(rule: dict) -> bool:
    value = rule.get("value")
    if value is None or value == "" or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    number = first_number(str(value))
    if number is None:
        return False
    rule["value"] = number
    return True


# a rule the matcher cannot evaluate: missing field, unknown op, or a comparison with no
# value. returns the question for the first bad rule, or None when every rule is usable
def bad_rule_question(criteria: dict) -> str | None:
    for list_name in RULE_LISTS:
        for rule in criteria.get(list_name) or []:
            field = rule.get("field") if isinstance(rule, dict) else None
            if not isinstance(field, str) or not field.strip():
                return UNUSABLE_FIELD_QUESTION
            op = rule.get("op")
            if op not in VALID_OPS:
                return FALLBACK_RULE_QUESTION.format(field=field)
            if op in COMPARISON_OPS and not usable_comparison_value(rule):
                return RULE_QUESTIONS[op].format(field=field)
            # "exists" legitimately has no value
            if op == "contains" and not str(rule.get("value") or "").strip():
                return FALLBACK_RULE_QUESTION.format(field=field)
    return None


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


# an unusable rule is a conversation problem, not an outage: ask instead of ranking on it
def criteria_or_followup(criteria_dict: dict) -> dict:
    question = bad_rule_question(criteria_dict)
    if question:
        logger.warning("criteria contained an unusable rule: %s", question)
        return {"type": "followup", "question": question}
    return {"type": "criteria", "criteria": criteria_dict}


async def extract(history: list[dict], message: str) -> dict:
    # no key configured: ask once, then return the saved criteria. counts turns, reads nothing
    if not ANTHROPIC_API_KEY:
        if not history:
            return {"type": "followup", "question": CANNED_QUESTION}
        # deep copy: normalize and the rule validator both edit in place, and the
        # validator reaches into the nested rule dicts a shallow copy still shares
        return criteria_or_followup(normalize(copy.deepcopy(CANNED_CRITERIA)))

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
    return criteria_or_followup(normalize(parsed["criteria"]))
