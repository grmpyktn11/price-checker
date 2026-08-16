import logging
import os
from datetime import date

import httpx

ENDPOINT = "https://www.googleapis.com/customsearch/v1"
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
RESULTS_PER_QUERY = 10   # CSE max for one call; a second page would cost a second query
DAILY_BUDGET = 80        # free tier is 100/day; 20 held back for check scripts and retries
TIMEOUT_SECONDS = 10
MAX_SUMMARY_CHARS = 2000   # per source, so three sources feed at most 6000 chars into LLM call #4
# 403 bodies carry the reason in text; a plain 403 is something else and is not treated as quota
QUOTA_MARKERS = ("dailylimitexceeded", "ratelimitexceeded", "quotaexceeded")

# in-process only: a restart forgives the day's spend, which is why DAILY_BUDGET is 80 not 100.
# persisting it needs a table the spec does not have
_SPENT = {"date": None, "count": 0}

logger = logging.getLogger(__name__)


# rolls the counter over on the first call of a new day. today is injectable so tests can
# drive the rollover without touching the clock; production passes nothing
def budget_left(today: date | None = None) -> int:
    today = today or date.today()
    if _SPENT["date"] != today:
        _SPENT.update({"date": today, "count": 0})
    return max(0, DAILY_BUDGET - _SPENT["count"])


# pure: the four fields either CSE source needs
def parse_items(payload: dict) -> list[dict]:
    return [
        {
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "snippet": item.get("snippet", ""),
            "display_link": item.get("displayLink", ""),
        }
        for item in payload.get("items") or []
        if item.get("link")
    ]


# the external review dict, one shape for every source. rating/review_count/verified_ratio are
# None on purpose: a count of Google hits is not a count of reviews, so min_review_count must
# not see it. the hit count lives in mention_count, which nothing filters on
def build_review(source: str, items: list[dict]) -> dict | None:
    if not items:
        return None
    text = " | ".join(f"{item['title']}: {item['snippet']}" for item in items)
    return {
        "source": source,
        "rating": None,
        "review_count": None,
        "verified_ratio": None,
        "rating_distribution": None,
        "url": items[0]["link"],
        "summary_text": text[:MAX_SUMMARY_CHARS],
        "mention_count": len(items),
        "authenticity_flag": "ok",
    }


# {} on budget exhausted, quota refusal, any HTTP error, or a body that is not json. no
# retries, no backoff: the pipeline already tolerates a missing source
async def search(query: str) -> dict:
    # belt and braces: even if a caller's guard is missed, no key means no request
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return {}
    if budget_left() <= 0:
        logger.warning("cse daily budget exhausted (%d), skipping", DAILY_BUDGET)
        return {}
    _SPENT["count"] += 1
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": RESULTS_PER_QUERY,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(ENDPOINT, params=params)
    except httpx.HTTPError as error:
        logger.warning("cse request failed: %s", error)
        return {}
    if response.status_code != 200:
        # google's own quota is gone: burn the local counter so the rest of the day skips free
        if response.status_code == 429 or any(
            marker in response.text.lower() for marker in QUOTA_MARKERS
        ):
            logger.warning("cse quota refused by google (%d)", response.status_code)
            _SPENT["count"] = DAILY_BUDGET
        else:
            logger.warning("cse returned %d", response.status_code)
        return {}
    try:
        return response.json()
    # a 200 that is not json (a proxy or captive-portal page) must degrade like any other
    # failure, not raise out of the pipeline
    except ValueError:
        logger.warning("cse returned a non-json body")
        return {}
