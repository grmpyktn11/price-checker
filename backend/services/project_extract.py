import json
import logging
import os

import anthropic

from backend.services.criteria import normalize, parse_json_reply

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2000
# a long planning conversation costs tokens on every import and the shopping list is usually
# settled by the end, so the tail is what gets sent
MAX_TRANSCRIPT_CHARS = 60000
# a planning chat can name a lot of parts; past this the list stops being a thing anyone reads
MAX_ITEMS = 20

SYSTEM_PROMPT = """You read a conversation where somebody planned a project, and pull out the
things they would have to buy.

The user message is JSON with the transcript. Reply with a single JSON object and nothing else:
{"project": "Home lab", "items": [{"index": 0, "name": "8-port gigabit switch",
"why": "connects the nodes", "keywords": ["8 port", "gigabit"], "category": "electronics",
"budget_max": 60, "quantity": 1, "essential": true}]}

- project: a short name for what is being built. Four words at most.
- items: one row per physical product the person would have to buy, in the order the
  conversation settled on them.
  * Only things a shop sells. Not "time", not "patience", not "a free weekend", not a skill
    they need to learn. Not software, subscriptions, services, warranties or shipping.
    A conversation about building a PC lists the case and the PSU, not "an afternoon".
  * Only what they still need. If the conversation says they already own it, or decided
    against it, leave it out.
  * Consumables and cables count. They are products.
  * If the same thing is named several ways across the conversation, emit it once.
- name: what to type into a shop's search box. "8-port gigabit switch", not "the switch we
  talked about" and not "TP-Link TL-SG108 8-Port Gigabit Unmanaged Switch (or similar)".
  Include a brand and model only if the conversation actually settled on one.
- why: one short phrase, in the conversation's own terms, saying what it is for. This is
  shown to the person so they can tell which item is which.
- keywords: the specifics that were agreed - capacity, wattage, size, port count, colour.
  Empty list when none were.
- category: a broad one, used to pick where to look for discussion. electronics, furniture,
  kitchen, tools, outdoor, or "" when none fit.
- budget_max: a number, if the conversation named a price or a cap for this item. null
  otherwise. Never invent one, and never split a total budget across items yourself.
- quantity: how many, when the conversation said. 1 otherwise.
- essential: true when the project does not work without it, false for a nice-to-have the
  conversation floated.

If the conversation never gets to anything buyable, reply {"project": "", "items": []}.
No prose, no markdown."""

logger = logging.getLogger(__name__)


# the tail, not the head: a planning conversation converges, and the last word on what to buy
# is the one worth reading
def trim(transcript: str) -> str:
    return transcript[-MAX_TRANSCRIPT_CHARS:]


def clean_text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def clean_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def clean_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(entry, 60) for entry in value if clean_text(entry, 60)][:8]


# quantity is shown and multiplied into nothing, so an odd value is just 1
def clean_quantity(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return min(value, 99)


# the criteria dict run_pipeline consumes. normalize() supplies radius_miles and
# min_review_count, which the pipeline indexes directly rather than .get-ing
def to_criteria(row: dict) -> dict:
    return normalize({
        "name": row["name"],
        "category": row["category"] or None,
        "keywords": row["keywords"],
        "must_haves": [],
        "preferred_specs": [],
        "nice_to_haves": [],
        "budget_max": row["budget_max"],
        "target_price": None,
        "fulfillment_preference": "either",
        "radius_miles": None,
        "min_review_count": None,
    })


# field by field, so one bad key costs a value rather than the whole row. a row with no name
# is dropped: the name is the search query, and there is nothing to search without it
def read_item(row: dict) -> dict | None:
    if not isinstance(row, dict):
        return None
    name = clean_text(row.get("name"), 120)
    if not name:
        return None
    item = {
        "name": name,
        "why": clean_text(row.get("why"), 200),
        "keywords": clean_list(row.get("keywords")),
        "category": clean_text(row.get("category"), 40),
        "budget_max": clean_number(row.get("budget_max")),
        "quantity": clean_quantity(row.get("quantity")),
        # only an explicit false makes something optional
        "essential": row.get("essential") is not False,
    }
    item["criteria"] = to_criteria(item)
    return item


def parse_reply(text: str) -> dict:
    payload = parse_json_reply(text)
    if not isinstance(payload, dict):
        logger.warning("project extract returned no object: %s", text[:500])
        return {"project": "", "items": []}
    rows = payload.get("items")
    if not isinstance(rows, list):
        logger.warning("project extract returned no items list: %s", text[:500])
        return {"project": clean_text(payload.get("project"), 80), "items": []}
    items = [item for item in (read_item(row) for row in rows) if item]
    return {"project": clean_text(payload.get("project"), 80), "items": items[:MAX_ITEMS]}


# the SDK call on its own, so the rest of the module is pure and the tests can double it
async def call_model(payload: dict) -> str:
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    return response.content[0].text


# one call for the whole transcript. an empty list is a real answer - plenty of conversations
# are not about buying anything - so a failure returns the same shape rather than raising
async def extract(transcript: str) -> dict:
    if not transcript.strip():
        return {"project": "", "items": []}
    try:
        text = await call_model({"transcript": trim(transcript)})
    except Exception as error:
        logger.warning("project extract call failed: %s", error)
        return {"project": "", "items": []}
    return parse_reply(text)
