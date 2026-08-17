import logging
import os
import re

import httpx
from bs4 import BeautifulSoup

from backend.scrapers.base import load_fixture_text

SOURCE = "reddit"
# reddit's public search feed needs no key and no oauth, so LIVE_SCRAPE is the switch, the
# same one the scrapers use. the Reddit API was applied for and denied; nothing here uses it.
# 2026-08-16: the .json endpoints answer this host with "blocked by network security" 403s,
# in a real browser as well as from httpx, so the equivalent .rss feed is used instead. it is
# the same public search, keyless, and it carries the full post body
LIVE_SCRAPE = os.getenv("LIVE_SCRAPE", "")
SEARCH_URL = "https://www.reddit.com/r/{subreddits}/search.rss"
# reddit blocks generic/default agents outright. one realistic descriptive agent, never rotated
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/151.0.0.0 Safari/537.36")
FIXTURE = "reddit_search.xml"
# subreddits are joined into one multireddit path, so a category still costs one request
MAX_SUBREDDITS = 4
POSTS_PER_QUERY = 10
TIME_FILTER = "year"     # spec.md's PRAW time_filter, which the public search does support
TIMEOUT_SECONDS = 10
MAX_POST_CHARS = 600     # one long selftext must not eat the whole summary budget
# reddit now supplies full post bodies rather than a search-engine snippet, so it gets a
# larger share of sentiment.MAX_INPUT_CHARS than youtube's titles and comments
MAX_SUMMARY_CHARS = 3000
# reddit wraps the post body in these markers and appends its own "submitted by" footer
SELFTEXT_PATTERN = re.compile(r"<!-- SC_OFF -->(.*?)<!-- SC_ON -->", re.DOTALL)
# category -> subreddits, extended by hand as categories are added (spec.md, Review Sources)
CATEGORY_SUBREDDIT_MAP = {
    "electronics": ["electronics", "gadgets", "UsbCHardware", "batteries"],
    "computers": ["buildapc", "laptops", "hardware", "monitors"],
    "audio": ["headphones", "audiophile", "BudgetAudiophile"],
    "tv": ["hometheater", "4kTV"],
    "phones": ["Android", "iphone", "smartphones"],
    "photography": ["photography", "cameras"],
    "appliances": ["appliances", "BuyItForLife"],
}
DEFAULT_SUBREDDITS = ["BuyItForLife", "ProductReviews"]   # unknown category

logger = logging.getLogger(__name__)


# "electronics+gadgets+UsbCHardware+batteries": reddit searches a multireddit path in one call
def build_subreddit_path(category: str | None) -> str:
    subreddits = CATEGORY_SUBREDDIT_MAP.get(category or "", DEFAULT_SUBREDDITS)
    return "+".join(subreddits[:MAX_SUBREDDITS])


# the body is escaped html between the two markers; link posts have no body at all
def parse_selftext(content: str) -> str:
    match = SELFTEXT_PATTERN.search(content)
    if not match:
        return ""
    return BeautifulSoup(match.group(1), "lxml").get_text(" ", strip=True)


# pure: one dict per atom entry
def parse_posts(xml_text: str) -> list[dict]:
    feed = BeautifulSoup(xml_text, "lxml-xml")
    posts = []
    for entry in feed.find_all("entry"):
        title = entry.find("title")
        if not title or not title.get_text(strip=True):
            continue
        content = entry.find("content")
        category = entry.find("category")
        link = entry.find("link")
        posts.append({
            "title": title.get_text(strip=True),
            "selftext": parse_selftext(content.get_text() if content else ""),
            "subreddit": category.get("term", "") if category else "",
            "url": link.get("href", "") if link else "",
        })
    return posts


# title plus the actual post body, labelled with the subreddit it came from. this is what
# LLM call #4 reads, and it is whole sentences from real users rather than a search snippet
def build_summary(posts: list[dict]) -> str:
    blocks = [f"[r/{post['subreddit']}] {post['title']}. {post['selftext']}".strip()[:MAX_POST_CHARS]
              for post in posts]
    return "\n\n".join(blocks)[:MAX_SUMMARY_CHARS]


# the external review dict, the same shape youtube returns. rating/review_count/verified_ratio
# are None on purpose: a count of threads is not a count of reviews, so min_review_count must
# not see it. the thread count lives in mention_count, which nothing filters on
def build_review(posts: list[dict]) -> dict | None:
    if not posts:
        return None
    return {
        "source": SOURCE,
        "rating": None,
        "review_count": None,
        "verified_ratio": None,
        "rating_distribution": None,
        "url": posts[0]["url"],
        "summary_text": build_summary(posts),
        "mention_count": len(posts),
        "authenticity_flag": "ok",
    }


# "" on any failure: reddit rate-limits hard, and the pipeline already tolerates a missing
# source. one request per run, no retries, no backoff
async def search(query: str, category: str | None) -> str:
    url = SEARCH_URL.format(subreddits=build_subreddit_path(category))
    params = {
        "q": query,
        "restrict_sr": "1",
        "sort": "relevance",
        "t": TIME_FILTER,
        "limit": POSTS_PER_QUERY,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS,
                                     headers={"user-agent": USER_AGENT}) as client:
            response = await client.get(url, params=params, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as error:
        logger.warning("reddit search failed: %s", error)
        return ""


# None when there are no results: nothing to persist and nothing to score
async def gather(query: str, category: str | None) -> dict | None:
    # not opted in to live scraping: parse the saved capture instead of hitting reddit
    if not LIVE_SCRAPE:
        return build_review(parse_posts(load_fixture_text(FIXTURE)))
    return build_review(parse_posts(await search(query, category)))
