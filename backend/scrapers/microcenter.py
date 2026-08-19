import logging
import re
from urllib.parse import quote_plus, urljoin, urlparse

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

RETAILER = "microcenter"
BASE = "https://www.microcenter.com"
SEARCH_URL = "https://www.microcenter.com/search/search_results.aspx?Ntt={query}"
BLOCK_MARKERS = ("access denied", "pardon our interruption", "are you a robot",
                 "request unsuccessful", "incapsula")
NO_RESULTS_MARKERS = ("did not match any products", "0 results", "no results were found")
# server-rendered: the tiles are in the first response, so there is nothing to wait for
SEARCH_WAIT_SELECTOR = None
TILE_SELECTOR = "li.product_wrapper"
# name and price live on the analytics anchors inside the tile, not on the tile itself, and
# every one of them carries the same pair. reading the attributes beats reading the visible
# text, which is "Our price $ 59.99" and has a member-price variant next to it
TILE_DATA_SELECTOR = "[data-name][data-price]"
# the Bazaarvoice inline widget, which renders as "4.4 (44)"
RATING_SELECTOR = "[class*=bv_inline_rating]"
RATING_RE = re.compile(r"([\d.]+)\s*\((\d[\d,]*)\)")
# the stock line doubles as the fulfilment estimate. these mean it cannot be bought now
OUT_OF_STOCK_MARKERS = ("sold out", "out of stock", "not available")
# the product page has no spec table: specs are div.spec-body pairs grouped under headings
# like "Keyboard Specifications". the group heading is dropped - the field names are already
# unique and the pipeline matches on the name alone
SPEC_SELECTOR = "div.spec-body"

logger = logging.getLogger(__name__)


# "Our price $ 59.99" is the visible text, but the tile also carries data-price. prefer the
# attribute: it is the number without the currency, the label or the member-price variant
def tile_data(tile):
    return tile.select_one(TILE_DATA_SELECTOR)


def parse_price(data) -> float | None:
    raw = (data.get("data-price") or "") if data else ""
    try:
        return float(raw)
    except ValueError:
        return None


# "4.4 (44)" -> 4.4 from 44 reviews. Micro Center prints this on the search tile, so a
# matching product carries a rating without a product-page load
def parse_tile_rating(tile) -> dict:
    block = tile.select_one(RATING_SELECTOR)
    match = RATING_RE.search(block.get_text(" ", strip=True)) if block else None
    if not match:
        return {"rating": None, "review_count": None}
    return {"rating": float(match.group(1)),
            "review_count": int(match.group(2).replace(",", ""))}


# the stock line reads "Usually ships in 5-7 business days" or names a store. None when the
# tile said nothing, which is not the same as out of stock
def parse_stock(tile) -> bool | None:
    block = tile.select_one(".stock")
    if not block:
        return None
    text = block.get_text(" ", strip=True).lower()
    if any(marker in text for marker in OUT_OF_STOCK_MARKERS):
        return False
    return True if "ship" in text or "stock" in text or "store" in text else None


# urljoin constrains neither scheme nor host, and get_specs hands this url to page.goto(),
# so an off-site href in the scraped markup would be followed. keep it on microcenter.com
def product_url(href: str) -> str | None:
    url = urljoin(BASE, href)
    return url if urlparse(url).netloc == urlparse(BASE).netloc else None


# the first /product/<id>/<slug> link on the tile. the wishlist and compare links point
# elsewhere, so the href is matched on shape rather than taken by position
def parse_link(tile) -> str | None:
    for anchor in tile.select("a[href]"):
        href = anchor["href"]
        if re.match(r"^/product/\d+/", href):
            return product_url(href)
    return None


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tile in soup.select(TILE_SELECTOR):
        data = tile_data(tile)
        name = data.get("data-name") if data else None
        url = parse_link(tile)
        # a tile with no name or no product link is a promo slot, not a product
        if not name or not url:
            continue
        rows.append({
            "name": name.strip(),
            "url": url,
            "price": parse_price(data),
            "in_stock": parse_stock(tile),
            # search is web inventory; the per-store stock is behind a store picker
            "store_id": None,
            "distance_miles": None,
            **parse_tile_rating(tile),
        })
    return rows


def parse_specs(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    specs = {}
    for row in soup.select(SPEC_SELECTOR):
        cells = row.find_all(recursive=False)
        if len(cells) != 2:
            continue
        name = cells[0].get_text(" ", strip=True)
        value = cells[1].get_text(" ", strip=True)
        if name and value and name not in specs:
            specs[name] = value
    return specs


# the product page repeats the same Bazaarvoice widget the tile carries
def parse_reviews(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    found = parse_tile_rating(soup)
    if found["rating"] is None:
        return {}
    return {
        **found,
        # Micro Center publishes neither a verified-purchase ratio nor a star breakdown
        "verified_ratio": None,
        "rating_distribution": None,
    }


def search_outcome(html: str, rows: list[dict]) -> str:
    return trace.search_outcome(looks_blocked(html, BLOCK_MARKERS),
                                looks_empty(html, NO_RESULTS_MARKERS), len(rows))


class MicroCenterScraper(ScraperBase):
    # store_ids is unused: search returns web inventory. per-store stock needs a store cookie,
    # and no endpoint publishes it without one
    async def search(self, query: str, store_ids: list[str] | None = None) -> list[dict]:
        url = SEARCH_URL.format(query=quote_plus(query))
        html = await fetch_html(url, SEARCH_WAIT_SELECTOR)
        rows = [] if looks_blocked(html, BLOCK_MARKERS) else parse_search(html)
        outcome = search_outcome(html, rows)
        trace.record_search(RETAILER, url, outcome, len(rows), page_chars=len(html))
        if outcome != trace.OK:
            logger.warning("%s search: %s (page %d chars)", RETAILER, outcome, len(html))
        return rows

    async def get_specs(self, product_url_: str) -> dict:
        html = await fetch_product_html(product_url_)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on %s", RETAILER, product_url_)
            return {}
        return parse_specs(html)

    async def get_reviews(self, product_url_: str) -> dict:
        html = await fetch_product_html(product_url_)
        if looks_blocked(html, BLOCK_MARKERS):
            logger.warning("%s blocked on %s", RETAILER, product_url_)
            return {}
        return parse_reviews(html)

    async def get_page_text(self, product_url_: str) -> str:
        html = await fetch_product_html(product_url_)
        return "" if looks_blocked(html, BLOCK_MARKERS) else page_text(html)
