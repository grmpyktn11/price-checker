import logging
import re
from urllib.parse import quote_plus

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

RETAILER = "amazon"
BASE = "https://www.amazon.com"
SEARCH_URL = "https://www.amazon.com/s?k={query}"
# the last two are the 503 "Dogs of Amazon" throttle page, seen live during this build
BLOCK_MARKERS = ("enter the characters you see below", "/errors/validatecaptcha",
                 "not a robot", "robot check", "automated access",
                 "sorry! something went wrong", "dogs of amazon")
SEARCH_WAIT_SELECTOR = ".s-result-item"
# what the zero-results page says. a page with none of these that still parsed to no rows is
# a broken parser, not an empty shelf
NO_RESULTS_MARKERS = ("no results for", "did not match any products",
                      "try checking your spelling")
# spec tables, most specific layout first; all are th/td or td/td row pairs
SPEC_SELECTORS = (
    "#productOverview_feature_div tr",
    "table.a-keyvalue tr",
    "#productDetails_techSpec_section_1 tr",
)
# invisible bidi marks Amazon embeds in spec text
BIDI_MARKS = ("‎", "‏")

logger = logging.getLogger(__name__)


def strip_bidi(text: str) -> str:
    for mark in BIDI_MARKS:
        text = text.replace(mark, "")
    return text.strip()


# "25" + "18" -> 25.18; the spec names only .a-price-whole, which drops the cents
def parse_price(tile) -> float | None:
    whole = tile.select_one(".a-price-whole")
    if not whole:
        return None
    fraction = tile.select_one(".a-price-fraction")
    cents = fraction.get_text(strip=True) if fraction else "0"
    digits = whole.get_text(strip=True).replace(",", "").rstrip(".")
    try:
        return float(f"{digits}.{cents}")
    except ValueError:
        return None


# .s-result-item alone also matches ad shells and layout spacers, so read the tiles with
# a data-asin instead. the tile href is a per-load tracking url and is useless as the
# listings unique key, so the url is built from the stable ASIN.
def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tile in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = tile["data-asin"]
        # the asin is interpolated into the product url that get_specs later navigates to,
        # so require the plain alphanumeric shape rather than trusting the attribute
        if not asin.isalnum():
            continue
        name = tile.select_one("h2 span")
        price = parse_price(tile)
        rows.append({
            "name": name.get_text(" ", strip=True) if name else None,
            "url": f"{BASE}/dp/{asin}",
            "price": price,
            # search tiles carry no stock status; a buyable price means buyable
            "in_stock": price is not None,
            "store_id": None,
            "distance_miles": None,
        })
    return rows


# merge the spec layouts, first non-empty wins per key
def parse_specs(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    specs = {}
    for selector in SPEC_SELECTORS:
        for row in soup.select(selector):
            cells = row.select("th, td")
            if len(cells) != 2:
                continue
            name = strip_bidi(cells[0].get_text(" ", strip=True))
            value = strip_bidi(cells[1].get_text(" ", strip=True))
            if name and value and name not in specs:
                specs[name] = value
    return specs


# "71 percent of reviews have 5 stars" -> {"5": 0.71, ...}. None when the table is absent
def parse_distribution(soup) -> dict | None:
    distribution = {}
    for link in soup.select("#histogramTable a[aria-label]"):
        match = re.search(r"(\d+) percent of reviews have (\d) star", link["aria-label"])
        if match:
            distribution[match.group(2)] = int(match.group(1)) / 100
    return distribution or None


def parse_reviews(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    # .a-icon-alt appears dozens of times on the page, so scope it to the aggregate block
    icon = soup.select_one("#averageCustomerReviews .a-icon-alt") or soup.select_one(".a-icon-alt")
    count = soup.select_one('[data-hook="total-review-count"]') or soup.select_one("#acrCustomerReviewCount")
    rating_match = re.search(r"([\d.]+) out of 5", icon.get_text(strip=True)) if icon else None
    count_match = re.search(r"([\d,]+)", count.get_text(strip=True)) if count else None
    if not rating_match and not count_match:
        return {}
    return {
        "rating": float(rating_match.group(1)) if rating_match else None,
        "review_count": int(count_match.group(1).replace(",", "")) if count_match else None,
        # Amazon's aggregate block does not expose a verified-purchase ratio
        "verified_ratio": None,
        # the only star breakdown any MVP source publishes; feeds the skew heuristic
        "rating_distribution": parse_distribution(soup),
    }


# pure: which of the trace outcomes this search page ended in
def search_outcome(html: str, rows: list[dict]) -> str:
    return trace.search_outcome(looks_blocked(html, BLOCK_MARKERS),
                                looks_empty(html, NO_RESULTS_MARKERS), len(rows))


class AmazonScraper(ScraperBase):
    # store_ids is accepted and unused: Amazon has no stores
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

    # no extra page load: the 60s cache still holds the page get_specs just fetched.
    # "" on a blocked page, so a captcha is never sent to the LLM spec fallback
    async def get_page_text(self, product_url: str) -> str:
        html = await fetch_product_html(product_url)
        if looks_blocked(html, BLOCK_MARKERS):
            return ""
        return page_text(html)

    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        raise NotImplementedError
