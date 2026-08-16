import os

from backend.scrapers.base import load_fixture
from backend.services import google_cse

SOURCE = "forum"
# own copy of the key so monkeypatch can switch this module offline on its own
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
MAX_SITES_PER_QUERY = 5   # keeps q short and stops results diluting across low-signal sites
FIXTURE = "cse_forums.json"
# seeded to match the sites the CSE itself is configured with. reddit.com is reviews_reddit's job
FORUM_SITES = {
    "electronics": ["forums.tomshardware.com", "linustechtips.com", "techpowerup.com/forums",
                    "slickdeals.net"],
    "computers": ["forums.tomshardware.com", "linustechtips.com", "anandtech.com",
                  "techpowerup.com/forums", "overclock.net"],
    "audio": ["head-fi.org", "avforums.com", "rtings.com"],
    "tv": ["rtings.com", "avforums.com"],
    "phones": ["forums.macrumors.com", "rtings.com"],
    "photography": ["dpreview.com/forums"],
    "appliances": ["rtings.com", "slickdeals.net"],
}
DEFAULT_FORUM_SITES = ["rtings.com", "forums.tomshardware.com", "slickdeals.net"]


def build_forum_query(query: str, category: str | None) -> str:
    forums = FORUM_SITES.get(category or "", DEFAULT_FORUM_SITES)
    sites = " OR ".join(f"site:{site}" for site in forums[:MAX_SITES_PER_QUERY])
    return f"{query} review ({sites})"


# None when there are no results: nothing to persist and nothing to score
async def gather(query: str, category: str | None) -> dict | None:
    # no key configured: parse the saved fixture instead of spending quota
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return google_cse.build_review(SOURCE, google_cse.parse_items(load_fixture(FIXTURE)))
    payload = await google_cse.search(build_forum_query(query, category))
    return google_cse.build_review(SOURCE, google_cse.parse_items(payload))
