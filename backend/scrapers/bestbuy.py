import logging
import os
import re
from urllib.parse import parse_qs, quote, urlparse

import httpx

from backend.scrapers.base import ScraperBase, load_fixture

BASE_URL = "https://api.bestbuy.com/v1"
BESTBUY_API_KEY = os.getenv("BESTBUY_API_KEY", "")
RETAILER = "bestbuy"

logger = logging.getLogger(__name__)


# sent on every live call
def base_params() -> dict:
    return {"apiKey": BESTBUY_API_KEY, "format": "json"}


async def get_json(url: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# skuId query param first, else the digits before ".p" in the path
def sku_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    sku = parse_qs(parsed.query).get("skuId")
    if sku:
        return sku[0]
    match = re.search(r"/(\d+)\.p", parsed.path)
    return match.group(1) if match else None


# search returns online inventory only; per-store availability is not wired up
def parse_search(payload: dict) -> list[dict]:
    return [
        {
            "name": product.get("name"),
            "url": product.get("url"),
            "price": product.get("salePrice"),
            "in_stock": product.get("onlineAvailability"),
            "store_id": None,
            "distance_miles": None,
        }
        for product in payload.get("products", [])
    ]


# details is a list of {name, value}; raw strings, normalization happens in the pipeline
def parse_details(payload: dict) -> dict:
    products = payload.get("products", [])
    if not products:
        return {}
    details = products[0].get("details") or []
    return {detail["name"]: detail["value"] for detail in details}


def parse_reviews(payload: dict) -> dict:
    products = payload.get("products", [])
    if not products:
        return {}
    return {
        "rating": products[0].get("customerReviewAverage"),
        "review_count": products[0].get("customerReviewCount"),
        # Best Buy exposes no verified-purchase ratio
        "verified_ratio": None,
    }


def parse_stores(payload: dict) -> list[dict]:
    return [
        {
            # listings.store_id is TEXT
            "store_id": str(store.get("storeId")),
            "name": store.get("longName") or store.get("name"),
            "distance_miles": store.get("distance"),
        }
        for store in payload.get("stores", [])
    ]


class BestBuyScraper(ScraperBase):
    # store_ids is unused: the products endpoint is online inventory only
    async def search(self, query: str, store_ids: list[str] | None = None) -> list[dict]:
        # no key configured: parse the saved fixture instead of calling the API
        if not BESTBUY_API_KEY:
            return parse_search(load_fixture("bestbuy_response.json"))
        try:
            # encode the query: it goes into the path, where unescaped ) or & would alter the filter
            search = quote(query, safe="")
            payload = await get_json(f"{BASE_URL}/products(search={search})", base_params())
        except httpx.HTTPError as error:
            logger.warning("bestbuy search failed: %s", error)
            return []
        return parse_search(payload)

    async def get_specs(self, product_url: str) -> dict:
        if not BESTBUY_API_KEY:
            return parse_details(load_fixture("bestbuy_details.json"))
        sku = sku_from_url(product_url)
        if not sku:
            return {}
        try:
            payload = await get_json(
                f"{BASE_URL}/products({sku})", {**base_params(), "show": "details"}
            )
        except httpx.HTTPError as error:
            logger.warning("bestbuy get_specs failed: %s", error)
            return {}
        return parse_details(payload)

    async def get_reviews(self, product_url: str) -> dict:
        if not BESTBUY_API_KEY:
            return parse_reviews(load_fixture("bestbuy_details.json"))
        sku = sku_from_url(product_url)
        if not sku:
            return {}
        try:
            payload = await get_json(
                f"{BASE_URL}/products({sku})",
                {**base_params(), "show": "customerReviewAverage,customerReviewCount"},
            )
        except httpx.HTTPError as error:
            logger.warning("bestbuy get_reviews failed: %s", error)
            return {}
        return parse_reviews(payload)

    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        if not BESTBUY_API_KEY:
            return parse_stores(load_fixture("bestbuy_stores.json"))
        try:
            payload = await get_json(
                f"{BASE_URL}/stores(area({lat},{lon},{radius_mi}))", base_params()
            )
        except httpx.HTTPError as error:
            logger.warning("bestbuy find_nearby_stores failed: %s", error)
            return []
        return parse_stores(payload)
