import asyncio
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.scrapers import amazon, bestbuy, target  # noqa: E402
from backend.scrapers.base import FIXTURES_DIR  # noqa: E402
from backend.scrapers.browser import fetch_html, looks_blocked  # noqa: E402

QUERY = "portable charger"
LAT = 37.7749
LON = -122.4194
RADIUS_MI = 25


# script and style bodies are megabytes of noise the parsers never read
def strip_scripts(html: str) -> str:
    return re.sub(r"<(script|style)\b[^>]*>.*?</\1>", r"<\1></\1>", html, flags=re.S | re.I)


def write(filename: str, text: str) -> None:
    (FIXTURES_DIR / filename).write_text(text, encoding="utf-8")
    print(f"{filename}: {len(text)} chars")


# a captcha or throttle page must never replace a good fixture
async def save_html(filename: str, url: str, wait_for: str, markers: tuple[str, ...]) -> None:
    html = await fetch_html(url, wait_for)
    if looks_blocked(html, markers):
        print(f"{filename} skipped: blocked on {url} ({len(html)} chars)")
        return
    write(filename, strip_scripts(html))


def review_count(product: dict) -> int:
    statistics = (product.get("ratings_and_reviews") or {}).get("statistics") or {}
    return (statistics.get("rating") or {}).get("count") or 0


async def save_target() -> None:
    scraper = target.TargetScraper()
    search = await target.get_json("plp_search_v2", {
        "key": target.API_KEY, "channel": "WEB", "count": "24",
        "default_purchasability_filter": "true", "keyword": QUERY, "new_search": "true",
        "offset": "0", "page": f"/s/{QUERY}", "platform": "desktop",
        "pricing_store_id": target.DEFAULT_STORE_ID, "spellcheck": "true",
        "store_ids": target.DEFAULT_STORE_ID, "visitor_id": target.VISITOR_ID,
    })
    write("target_search.json", json.dumps(search, indent=1))

    # the most-reviewed hit: fixture mode reuses this one page for every product, so a
    # well-reviewed one lets the canned review-count filter pass
    products = search["data"]["search"]["products"]
    best = max(products, key=review_count)
    url = target.parse_search({"data": {"search": {"products": [best]}}})[0]["url"]
    write("target_pdp.json", json.dumps(await scraper.get_pdp(url), indent=1))

    stores = await target.get_json("nearby_stores_v1", {
        "key": target.API_KEY, "place": f"{LAT},{LON}", "within": str(RADIUS_MI),
        "limit": "20", "channel": "WEB", "visitor_id": target.VISITOR_ID,
    })
    write("target_stores.json", json.dumps(stores, indent=1))


async def save_bestbuy() -> None:
    await save_html("bestbuy_search.html",
                    bestbuy.SEARCH_URL.format(query=QUERY.replace(" ", "+")),
                    bestbuy.SEARCH_WAIT_SELECTOR, bestbuy.BLOCK_MARKERS)
    rows = bestbuy.parse_search((FIXTURES_DIR / "bestbuy_search.html").read_text(encoding="utf-8"))
    if not rows:
        print("bestbuy_product.html skipped: the search page returned no rows")
        return
    await save_html("bestbuy_product.html", rows[0]["url"], "#key-specs-list",
                    bestbuy.BLOCK_MARKERS)


async def save_amazon() -> None:
    await save_html("amazon_search.html",
                    amazon.SEARCH_URL.format(query=QUERY.replace(" ", "+")),
                    amazon.SEARCH_WAIT_SELECTOR, amazon.BLOCK_MARKERS)
    rows = amazon.parse_search((FIXTURES_DIR / "amazon_search.html").read_text(encoding="utf-8"))
    if not rows:
        print("amazon_product.html skipped: the search page returned no rows")
        return
    await save_html("amazon_product.html", rows[0]["url"], "#averageCustomerReviews",
                    amazon.BLOCK_MARKERS)


async def main():
    if not target.LIVE_SCRAPE:
        print("set LIVE_SCRAPE=1 in .env: this tool only writes fixtures from live responses")
        return
    await save_target()
    await save_bestbuy()
    await save_amazon()


asyncio.run(main())
