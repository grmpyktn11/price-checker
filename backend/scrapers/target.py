import html as html_module
import logging
import re

import httpx

from backend.scrapers.base import ScraperBase

# 2026-08-16: redsky 403'd this host earlier in the day on every endpoint, and answered 200
# again later the same day on search, pdp and nearby_stores. the block was transient, so
# there is no workaround in the code: this is the plain httpx path and nothing else.
RETAILER = "target"
BASE_URL = "https://redsky.target.com/redsky_aggregations/v1/web"
# public web key lifted from target.com's own JS, not a credential and not a secret.
# it rotates occasionally: a sudden 401/404/403 from redsky means check this first.
API_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
DEFAULT_STORE_ID = "3991"   # redsky requires some store for pricing; MVP does not vary it
VISITOR_ID = "0192F0D2B1C40201B0B0C0D0E0F00001"   # redsky only wants the param present
# redsky answers a plain client with a PerimeterX captcha, and 403s an outdated user agent.
# these are the headers target.com's own page sends; keep the Chrome version current.
HEADERS = {
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    "accept": "application/json",
    "origin": "https://www.target.com",
    "referer": "https://www.target.com/",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

logger = logging.getLogger(__name__)


async def get_json(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        response = await client.get(f"{BASE_URL}/{path}", params=params)
        response.raise_for_status()
        return response.json()


# the digits after /A- in a target product url
def tcin_from_url(url: str) -> str | None:
    match = re.search(r"/A-(\d+)", url or "")
    return match.group(1) if match else None


# titles carry html entities like &#38;
def clean_text(raw: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


# plp_search_v2 no longer returns a fulfillment block, so there is no per-product store or
# distance data: in_stock reflects the purchasable-only filter the request asks for
def parse_search(payload: dict) -> list[dict]:
    products = (payload.get("data") or {}).get("search", {}).get("products", []) or []
    rows = []
    for product in products:
        tcin = product.get("tcin")
        if not tcin:
            continue
        item = product.get("item") or {}
        title = (item.get("product_description") or {}).get("title", "")
        price = (product.get("price") or {}).get("current_retail")
        rows.append({
            "name": clean_text(title),
            "url": (item.get("enrichment") or {}).get("buy_url")
                   or f"https://www.target.com/p/-/A-{tcin}",
            "price": price,
            "in_stock": price is not None,
            "store_id": None,
            "distance_miles": None,
        })
    return rows


# bullets look like "<B>Battery Capacity:</B> 20000 (mAh)"; sentences without a colon are
# marketing copy, not specs
def parse_specs(payload: dict) -> dict:
    product = (payload.get("data") or {}).get("product", {}) or {}
    description = ((product.get("item") or {}).get("product_description") or {})
    bullets = description.get("bullet_descriptions") or []
    soft = (description.get("soft_bullets") or {}).get("bullets") or []
    specs = {}
    for bullet in [*bullets, *soft]:
        text = clean_text(bullet)
        name, separator, value = text.partition(":")
        if not separator or not name.strip() or not value.strip():
            continue
        specs.setdefault(name.strip(), value.strip())
    return specs


def parse_reviews(payload: dict) -> dict:
    product = (payload.get("data") or {}).get("product", {}) or {}
    statistics = (product.get("ratings_and_reviews") or {}).get("statistics") or {}
    rating = statistics.get("rating") or {}
    if not rating:
        return {}
    return {
        "rating": rating.get("average"),
        "review_count": rating.get("count", statistics.get("review_count")),
        # Target publishes no verified-purchase ratio and no star breakdown
        "verified_ratio": None,
        "rating_distribution": None,
    }


# the pdp description text, for the LLM spec fallback. no html to strip beyond the tags the
# bullets carry, so this is the same source parse_specs reads, unparsed
def parse_page_text(payload: dict) -> str:
    product = (payload.get("data") or {}).get("product", {}) or {}
    description = ((product.get("item") or {}).get("product_description") or {})
    parts = [
        description.get("title") or "",
        description.get("downstream_description") or "",
        *(description.get("bullet_descriptions") or []),
        *((description.get("soft_bullets") or {}).get("bullets") or []),
    ]
    return " ".join(clean_text(part) for part in parts if part).strip()


def parse_stores(payload: dict) -> list[dict]:
    stores = (payload.get("data") or {}).get("nearby_stores", {}).get("stores", []) or []
    return [
        {
            # listings.store_id is TEXT
            "store_id": str(store.get("store_id")),
            "name": store.get("location_name"),
            "distance_miles": store.get("distance"),
        }
        for store in stores
    ]


class TargetScraper(ScraperBase):
    async def search(self, query: str, store_ids: list[str] | None = None) -> list[dict]:
        store_id = store_ids[0] if store_ids else DEFAULT_STORE_ID
        try:
            payload = await get_json("plp_search_v2", {
                "key": API_KEY,
                "channel": "WEB",
                "count": "24",
                "default_purchasability_filter": "true",
                "keyword": query,
                "new_search": "true",
                "offset": "0",
                "page": f"/s/{query}",
                "platform": "desktop",
                "pricing_store_id": store_id,
                "spellcheck": "true",
                "store_ids": store_id,
                "visitor_id": VISITOR_ID,
            })
        except httpx.HTTPError as error:
            logger.warning("target search failed: %s", error)
            return []
        return parse_search(payload)

    async def get_specs(self, product_url: str) -> dict:
        payload = await self.get_pdp(product_url)
        return parse_specs(payload)

    async def get_reviews(self, product_url: str) -> dict:
        payload = await self.get_pdp(product_url)
        return parse_reviews(payload)

    # one more ~200ms json call: redsky has no page html to reuse
    async def get_page_text(self, product_url: str) -> str:
        return parse_page_text(await self.get_pdp(product_url))

    # both detail calls read the same endpoint; it is a ~200ms json call with no bot
    # challenge, so there is nothing to cache
    async def get_pdp(self, product_url: str) -> dict:
        tcin = tcin_from_url(product_url)
        if not tcin:
            return {}
        try:
            return await get_json("pdp_client_v1", {
                "key": API_KEY,
                "tcin": tcin,
                "channel": "WEB",
                "is_bot": "false",
                "store_id": DEFAULT_STORE_ID,
                "pricing_store_id": DEFAULT_STORE_ID,
                "page": f"/p/A-{tcin}",
                "visitor_id": VISITOR_ID,
            })
        except httpx.HTTPError as error:
            logger.warning("target pdp failed for %s: %s", tcin, error)
            return {}

    # the only working find_nearby_stores in the app: Best Buy lost its with the denied key
    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        try:
            payload = await get_json("nearby_stores_v1", {
                "key": API_KEY,
                "place": f"{lat},{lon}",
                "within": str(radius_mi),
                "limit": "20",
                "channel": "WEB",
                "visitor_id": VISITOR_ID,
            })
        except httpx.HTTPError as error:
            logger.warning("target find_nearby_stores failed: %s", error)
            return []
        return parse_stores(payload)
