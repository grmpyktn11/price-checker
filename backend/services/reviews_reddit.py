import os

from backend.scrapers.base import load_fixture
from backend.services import google_cse

SOURCE = "reddit"
# Reddit API access was applied for and denied, so this is Google CSE with site:reddit.com.
# no praw, ever, and the spec's time_filter=year has no CSE equivalent: results are not
# date-bounded. own copy of the key so monkeypatch can switch this module offline on its own
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
MAX_SUBREDDITS = 4   # one query with an OR group; five queries would be five budget units
FIXTURE = "cse_reddit.json"
# category -> subreddits, extended by hand as categories are added (spec.md, Review Sources)
CATEGORY_SUBREDDIT_MAP = {
    "electronics": ["r/electronics", "r/gadgets", "r/UsbCHardware", "r/batteries"],
    "computers": ["r/buildapc", "r/laptops", "r/hardware", "r/monitors"],
    "audio": ["r/headphones", "r/audiophile", "r/BudgetAudiophile"],
    "tv": ["r/hometheater", "r/4kTV"],
    "phones": ["r/Android", "r/iphone", "r/smartphones"],
    "photography": ["r/photography", "r/cameras"],
    "appliances": ["r/appliances", "r/BuyItForLife"],
}
DEFAULT_SUBREDDITS = ["r/BuyItForLife", "r/ProductReviews"]   # unknown category


def build_reddit_query(query: str, category: str | None) -> str:
    subreddits = CATEGORY_SUBREDDIT_MAP.get(category or "", DEFAULT_SUBREDDITS)
    sites = " OR ".join(f"site:reddit.com/{sub}" for sub in subreddits[:MAX_SUBREDDITS])
    return f"{query} review ({sites})"


# None when there are no results: nothing to persist and nothing to score
async def gather(query: str, category: str | None) -> dict | None:
    # no key configured: parse the saved fixture instead of spending quota
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return google_cse.build_review(SOURCE, google_cse.parse_items(load_fixture(FIXTURE)))
    payload = await google_cse.search(build_reddit_query(query, category))
    return google_cse.build_review(SOURCE, google_cse.parse_items(payload))
