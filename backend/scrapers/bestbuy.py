import logging
import re
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from backend.scrapers.base import ScraperBase
from backend.scrapers.browser import (
    fetch_html,
    fetch_product_html,
    looks_blocked,
    looks_empty,
    page_text,
)
from backend.services import trace

RETAILER = "bestbuy"
BASE = "https://www.bestbuy.com"
SEARCH_URL = "https://www.bestbuy.com/site/searchpage.jsp?st={query}"
# Best Buy API access was applied for and denied. Do not reintroduce BESTBUY_API_KEY.
BLOCK_MARKERS = ("access denied", "reference #18", "_sec/cp_challenge",
                 "are you a robot", "pardon our interruption")
# what the zero-results page says. a page with none of these that still parsed to no rows is
# a broken parser, not an empty shelf
NO_RESULTS_MARKERS = ("no results found", "0 items", "did not match any")
# wait for a tile that has hydrated, not just for the shell: the grid renders empty
# li.product-list-item elements first, and parsing those returns nothing at all
SEARCH_WAIT_SELECTOR = "li.product-list-item h3.product-title"

logger = logging.getLogger(__name__)


# /sku/<digits> on the current urls, skuId query param or <digits>.p on older ones
def sku_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    sku = parse_qs(parsed.query).get("skuId")
    if sku:
        return sku[0]
    match = re.search(r"/sku/(\d+)|/(\d+)\.p", parsed.path)
    if not match:
        return None
    return match.group(1) or match.group(2)


# "$1,299.99" -> 1299.99
def parse_price(raw: str) -> float | None:
    digits = raw.replace("$", "").replace(",", "").strip()
    try:
        return float(digits)
    except ValueError:
        return None


# urljoin constrains neither scheme nor host, and get_specs hands this url straight to
# page.goto(), so an off-site href in the scraped markup would be followed. keep it on
# bestbuy.com and drop the tile otherwise
def product_url(href: str) -> str | None:
    url = urljoin(BASE, href)
    return url if urlparse(url).netloc == urlparse(BASE).netloc else None


# "Rating 4.4 out of 5 stars with 32 reviews", on the search tile itself. this is the only
# rating Best Buy gives us at all - the product page that carries it is Akamai-blocked, which
# is why every Best Buy card used to read "no rating found"
TILE_RATING_RE = re.compile(r"Rating ([\d.]+) out of 5 stars with ([\d,]+) review")


def parse_tile_rating(tile) -> dict:
    block = tile.select_one(".c-ratings-reviews")
    match = TILE_RATING_RE.search(block.get_text(" ", strip=True)) if block else None
    if not match:
        return {"rating": None, "review_count": None}
    return {"rating": float(match.group(1)),
            "review_count": int(match.group(2).replace(",", ""))}


# search is national inventory: no store, no distance
def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tile in soup.select("li.product-list-item[data-product-id]"):
        link = tile.select_one("a.product-list-item-link")
        title = tile.select_one("h3.product-title")
        # the grid is virtualized: tiles below the fold are empty shells, not products
        if not link or not link.has_attr("href") or not title:
            continue
        url = product_url(link["href"])
        if not url:
            logger.warning("%s tile linked off-site: %s", RETAILER, link["href"])
            continue
        price = tile.select_one('[data-testid="price-block-customer-price"] span')
        cart = tile.select_one('[data-testid^="plp-add-to-cart-"]')
        rows.append({
            "name": title.get("title") or title.get_text(" ", strip=True),
            "url": url,
            "price": parse_price(price.get_text(strip=True)) if price else None,
            # no add-to-cart button rendered at all means the tile never told us
            "in_stock": None if cart is None else "sold out" not in cart.get_text(" ", strip=True).lower(),
            "store_id": None,
            "distance_miles": None,
            **parse_tile_rating(tile),
        })
    return rows


# the key specs list on the product page; the full table sits behind a "See all
# specifications" link and is not worth an extra interaction.
# UNPROVEN LIVE: bestbuy_product.html was captured from an already-warm browser session.
# the headless path in browser.py has never successfully loaded a Best Buy product page -
# Akamai blocks it - so this parser and parse_reviews below are exercised by the fixture
# only. green tests here do not mean live Best Buy specs work.
def parse_specs(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    specs = {}
    for row in soup.select("#key-specs-list div.items-center"):
        cells = row.find_all("div", recursive=False)
        if len(cells) != 2:
            continue
        name = cells[0].get_text(" ", strip=True)
        value = cells[1].get_text(" ", strip=True)
        if name and value:
            specs[name] = value
    return specs


# the aggregate block reads "Rating 4.7 out of 5 stars with 166 reviews"
def parse_reviews(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    summary = soup.select_one(".c-ratings-reviews .visually-hidden")
    if not summary:
        return {}
    match = re.search(r"([\d.]+) out of 5 stars with ([\d,]+) review", summary.get_text(" ", strip=True))
    if not match:
        return {}
    return {
        "rating": float(match.group(1)),
        "review_count": int(match.group(2).replace(",", "")),
        # Best Buy publishes no verified-purchase ratio and no star breakdown
        "verified_ratio": None,
        "rating_distribution": None,
    }


# pure: which of the trace outcomes this search page ended in
def search_outcome(html: str, rows: list[dict]) -> str:
    return trace.search_outcome(looks_blocked(html, BLOCK_MARKERS),
                                looks_empty(html, NO_RESULTS_MARKERS), len(rows))


class BestBuyScraper(ScraperBase):
    # store_ids is unused: the search page is national inventory, and the Stores API needed
    # the denied key
    async def search(self, query: str, store_ids: list[str] | None = None) -> list[dict]:
        url = SEARCH_URL.format(query=quote_plus(query))
        html = await fetch_html(url, SEARCH_WAIT_SELECTOR)
        # LLM call #5's search-page fallback is deferred to Phase 7 or later: an invented url
        # would become part of the listings unique key and corrupt watchlist identity
        rows = [] if looks_blocked(html, BLOCK_MARKERS) else parse_search(html)
        outcome = search_outcome(html, rows)
        trace.record_search(RETAILER, url, outcome, len(rows), page_chars=len(html))
        if outcome != trace.OK:
            logger.warning("%s search: %s (page %d chars)", RETAILER, outcome, len(html))
        return rows

    async def get_specs(self, product_url: str) -> dict:
        html = await fetch_product_html(product_url)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on %s", RETAILER, product_url)
            return {}
        return parse_specs(html)

    async def get_reviews(self, product_url: str) -> dict:
        html = await fetch_product_html(product_url)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on %s", RETAILER, product_url)
            return {}
        return parse_reviews(html)

    # same cached html get_specs just fetched, so no extra page load. "" when blocked, which
    # is the normal live outcome here and is what keeps a challenge page out of the LLM
    async def get_page_text(self, product_url: str) -> str:
        html = await fetch_product_html(product_url)
        if looks_blocked(html, BLOCK_MARKERS):
            return ""
        return page_text(html)

    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        # the Stores API needed a key that was denied; scraping the store locator is out of scope
        raise NotImplementedError
