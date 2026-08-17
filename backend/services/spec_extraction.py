import logging
import os

import anthropic

from backend.services.criteria import parse_json_reply

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1000
MAX_PAGE_CHARS = 12000   # roughly 3k tokens; product pages run far longer than this
CANNED_SPECS = {}        # no key: no fallback, the product keeps its empty spec dict

SYSTEM_PROMPT = """Extract product specifications from this page text. Return a single JSON
object mapping spec name to value as a string, verbatim including units. Use these names when
the page contains the corresponding spec: {fields}. Omit anything not stated on the page. Do
not infer, convert units, or guess. No other text."""

logger = logging.getLogger(__name__)


# the same flat str -> str shape parse_specs returns, so a recovered spec dict is
# indistinguishable from a parsed one downstream. values are never coerced to numbers
def parse_specs_reply(text: str) -> dict:
    parsed = parse_json_reply(text)
    if not isinstance(parsed, dict):
        logger.warning("spec extraction returned no json object: %s", text[:500])
        return {}
    return {key: value for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip()}


# LLM call #2. fires only when get_specs returned {} from a page we did reach: a blocked page
# returns "" page text, so a captcha is never sent to Claude
async def extract(page_text: str, wanted_fields: list[str]) -> dict:
    if not page_text:
        return {}
    if not ANTHROPIC_API_KEY:
        return dict(CANNED_SPECS)
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT.format(fields=", ".join(wanted_fields)),
            messages=[{"role": "user", "content": page_text[:MAX_PAGE_CHARS]}],
        )
    # a fallback that fails leaves the candidate exactly where it was: with no specs
    except Exception as error:
        logger.warning("spec extraction failed: %s", error)
        return {}
    return parse_specs_reply(response.content[0].text)
