import logging
import os
import re
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from backend.scrapers.base import ScraperBase, load_fixture_text
from backend.scrapers.browser import fetch_html, fetch_product_html, looks_blocked

RETAILER = "bestbuy"
BASE = "https://www.bestbuy.com"
SEARCH_URL = "https://www.bestbuy.com/site/searchpage.jsp?st={query}"
LIVE_SCRAPE = os.getenv("LIVE_SCRAPE", "")
# Best Buy API access was applied for and denied. Do not reintroduce BESTBUY_API_KEY.
BLOCK_MARKERS = ("access denied", "reference #18", "_sec/cp_challenge",
                 "are you a robot", "pardon our interruption")
SEARCH_WAIT_SELECTOR = "li.product-list-item"

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
        # Best Buy publishes no verified-purchase ratio
        "verified_ratio": None,
    }


class BestBuyScraper(ScraperBase):
    # store_ids is unused: the search page is national inventory, and the Stores API needed
    # the denied key
    async def search(self, query: str, store_ids: list[str] | None = None) -> list[dict]:
        # not opted in to live scraping: parse the saved fixture instead of hitting the site
        if not LIVE_SCRAPE:
            return parse_search(load_fixture_text("bestbuy_search.html"))
        html = await fetch_html(SEARCH_URL.format(query=quote_plus(query)), SEARCH_WAIT_SELECTOR)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on search", RETAILER)
            return []
        rows = parse_search(html)
        if not rows:
            # LLM call #5's fallback extraction goes here in Phase 6, with page text from this html
            logger.warning("%s search selectors returned nothing (page %d chars) - selectors may "
                           "have broken", RETAILER, len(html))
        return rows

    async def get_specs(self, product_url: str) -> dict:
        if not LIVE_SCRAPE:
            return parse_specs(load_fixture_text("bestbuy_product.html"))
        html = await fetch_product_html(product_url)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on %s", RETAILER, product_url)
            return {}
        return parse_specs(html)

    async def get_reviews(self, product_url: str) -> dict:
        if not LIVE_SCRAPE:
            return parse_reviews(load_fixture_text("bestbuy_product.html"))
        html = await fetch_product_html(product_url)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on %s", RETAILER, product_url)
            return {}
        return parse_reviews(html)

    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        # the Stores API needed a key that was denied; scraping the store locator is out of scope
        raise NotImplementedError
