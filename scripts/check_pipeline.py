import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.scrapers.bestbuy import BESTBUY_API_KEY  # noqa: E402
from backend.services.pipeline import run_pipeline  # noqa: E402

CRITERIA = {
    "name": "portable charger",
    "category": "electronics",
    "keywords": ["usb-c", "140w"],
    "must_haves": [
        {"field": "Battery Capacity", "op": ">=", "value": 20000},
        {"field": "Pass-Through Charging", "op": "contains", "value": "yes"},
    ],
    "preferred_specs": [
        {"field": "Number of USB Ports", "op": ">=", "value": 3},
        {"field": "Product Weight", "op": "<=", "value": 1.0},
    ],
    "nice_to_haves": ["compact", "looks sleek"],
    "budget_max": 150.0,
    "target_price": 99.0,
    "fulfillment_preference": "either",
    "radius_miles": 25,
    "min_review_count": 100,
}
LAT = 37.7749
LON = -122.4194
RADIUS_MI = 25

# INFO so the pipeline's per-product skip lines show up inline
logging.basicConfig(level=logging.INFO)


def show(rank: int, result):
    price = result.product["price"]
    budget_max = CRITERIA.get("budget_max")
    over = " (over budget)" if budget_max and price and price > budget_max else ""
    print(f"\n{rank}. {result.product['name']} - ${price} [{result.retailer}]{over}")
    print(f"   final_score:        {result.final_score:.3f}")
    print(f"   spec_match:         {result.spec_match:.3f}")
    print(f"   review_score:       {result.review_score:.3f}")
    print(f"   price_score:        {result.price_score:.3f}")
    print(f"   distance_score:     {result.distance_score:.3f}")
    print(f"   nice_to_have_score: {result.nice_to_have_score:.3f}")


async def main():
    print("MODE:", "LIVE" if BESTBUY_API_KEY else "FIXTURE")
    results = await run_pipeline(CRITERIA, LAT, LON, RADIUS_MI)
    if not results:
        print("no products passed the filters")
        return
    for rank, result in enumerate(results, start=1):
        show(rank, result)


asyncio.run(main())
