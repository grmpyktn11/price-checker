import json
import logging
import os

import anthropic

from backend.services.criteria import parse_json_reply

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2000
NEUTRAL_FIT = 0.5

SYSTEM_PROMPT = """You screen retail search results for a shopper.

The user message is JSON: the shopper's requirements, and a numbered list of products with the
retailer, title, price and whatever specs that retailer published. Most retailers publish none
of the specs the requirements name, so the title is usually the only evidence there is. Read it.

Reply with a single JSON object and nothing else:
{"products": [{"index": 0, "qualifies": true, "spec_fit": 0.0, "nice_fit": 0.0, "group": "g1",
"reason": ""}]}

- qualifies: the strict check, and only the strict check.
  * A stated quantity is strict. 20,000mAh, at least 140W, under 2 pounds, 4 ports: a listing
    that states a different one does not qualify. Read the number out of the title when there
    is no spec table. Worked example, capacity: against a 20,000mAh requirement, "Portable
    Charger 2,000mAh" does not qualify and neither does a 10,000mAh one - that is a different
    product, not a near miss.
  * The same quantity is written many ways. "20,000mAh", "20000 mAh", "20K" and "20,000
    milliamp hours" are all twenty thousand and all qualify. A different scale never does:
    2,000 is not 20,000.
  * Silence about a stated quantity is a failure. If the requirement names a number and
    neither the title nor the specs say anything about it, the product does not qualify.
  * false as well when the listing is a different kind of product, or an accessory or spare
    part for one: a pack of switches is not a keyboard, a cable is not a power bank.
  * Vague or subjective wording - "yellow switches", "compact", "good for travel" - is never a
    reason to disqualify. It belongs in the fit scores instead. Only the strict checks above
    decide this field.
- spec_fit: 0.0-1.0, how well it matches required_specs, preferred_specs and any vague wording
  in the keywords. 0.5 when the listing says nothing either way.
- nice_fit: 0.0-1.0, the same judgement for nice_to_haves. 0.5 when there are none, or when the
  listing says nothing either way.
- group: a short id you invent, shared by listings of the SAME product at different retailers -
  same manufacturer, same model, same variant. A different capacity, size, switch type, colour
  or generation is a different product and gets its own group. Every product gets a group, even
  when it is alone in it.
- reason: one short phrase saying why, when qualifies is false. "" when it qualifies. This is
  shown to the shopper as the reason the product was dropped, so name the specific mismatch.

Judge every index exactly once. No prose, no markdown."""

logger = logging.getLogger(__name__)


# only the parts of the criteria that describe the product itself: budget and radius are
# ranking terms, not things a title can pass or fail
def requirements(criteria: dict) -> dict:
    return {
        "product": criteria.get("name"),
        "keywords": criteria.get("keywords", []),
        "required_specs": criteria.get("must_haves", []),
        "preferred_specs": criteria.get("preferred_specs", []),
        "nice_to_haves": criteria.get("nice_to_haves", []),
    }


# what a product the model said nothing usable about gets: it qualifies, on neutral scores and
# in no group. silence is not evidence against a product, and dropping it would be a filter
# nobody asked for
def neutral_assessment() -> dict:
    return {"qualifies": True, "spec_fit": NEUTRAL_FIT, "nice_fit": NEUTRAL_FIT, "group": None,
            "reason": ""}


def clamped_fit(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return NEUTRAL_FIT
    return max(0.0, min(1.0, float(value)))


# field by field, so one bad key does not cost the product its whole assessment
def read_assessment(row: dict) -> dict:
    return {
        # only an explicit false drops a product
        "qualifies": row.get("qualifies") is not False,
        "spec_fit": clamped_fit(row.get("spec_fit")),
        "nice_fit": clamped_fit(row.get("nice_fit")),
        "group": str(row["group"]) if row.get("group") else None,
        # only ever displayed, so a missing or odd value is just an empty string
        "reason": str(row.get("reason") or ""),
    }


# index-aligned with the products that were sent. a partial or malformed reply leaves the rest
# neutral rather than dropping them
def parse_reply(text: str, count: int) -> list[dict]:
    assessments = [neutral_assessment() for _ in range(count)]
    rows = (parse_json_reply(text) or {}).get("products")
    if not isinstance(rows, list):
        logger.warning("product filter returned no products list: %s", text[:500])
        return assessments
    judged = 0
    for row in rows:
        index = row.get("index") if isinstance(row, dict) else None
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < count:
            assessments[index] = read_assessment(row)
            judged += 1
    if judged != count:
        logger.warning("product filter judged %d of %d products", judged, count)
    return assessments


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


# one call for the whole candidate set, never one per product or per pair: qualification, both
# fit scores and cross-retailer identity are all read off the same titles at once.
# products are {retailer, title, price, url, specs}; the returned list is index-aligned with it
async def assess(criteria: dict, products: list[dict]) -> list[dict]:
    if not products:
        return []
    payload = {
        "requirements": requirements(criteria),
        "products": [{"index": index, **product} for index, product in enumerate(products)],
    }
    try:
        text = await call_model(payload)
    # a transport failure is not a verdict on any product: everything qualifies, nothing groups
    except Exception as error:
        logger.warning("product filter call failed: %s", error)
        return [neutral_assessment() for _ in products]
    return parse_reply(text, len(products))
