import logging

from backend.scrapers.amazon import AmazonScraper
from backend.scrapers.bestbuy import BestBuyScraper
from backend.scrapers.target import TargetScraper
from backend.services import nice_to_have
from backend.services.ranking import (
    RankedProduct,
    assign_price_scores,
    build_query,
    compute_distance_score,
    compute_final_score,
    compute_review_score,
    compute_spec_match,
    passes_must_haves,
)

# the criteria dict this module consumes, as criteria.py (LLM call #1) will emit it:
# {
#     "name": "portable charger",          # main search noun, required
#     "category": "electronics",           # used later by the subreddit/forum maps
#     "keywords": ["usb-c", "140w"],       # extra search terms, may be empty
#     "must_haves": [                      # hard filter, all must pass
#         {"field": "Battery Capacity", "op": ">=", "value": 20000},
#     ],
#     "preferred_specs": [                 # soft, drives spec_match, may be empty
#         {"field": "Number of USB Ports", "op": ">=", "value": 3},
#     ],
#     "nice_to_haves": ["compact", "looks sleek"],   # subjective, LLM call #3 scores these
#     "budget_max": 150.0,                 # ranking penalty on price_score, NOT a filter, may be None
#     "target_price": 99.0,                # deals.py only, may be None
#     "fulfillment_preference": "either",  # pickup | shipping | either, unused in Phase 2
#     "radius_miles": 25,
#     "min_review_count": 100,
# }

SCRAPERS = [
    ("bestbuy", BestBuyScraper()),
    ("target", TargetScraper()),
    ("amazon", AmazonScraper()),
]

# live, each search returns ~24 products and each detail page is a browser launch, so both
# numbers are hard caps. per Playwright retailer that is 1 search page + 3 product pages
# (get_specs and get_reviews share one load through the 60s cache) = 4 loads, so 8 across
# Best Buy and Amazon, roughly 40 seconds. Target's json calls are not page loads.
# the two numbers match on purpose: a candidate past the detail cutoff is dropped outright
# whenever must_haves or min_review_count is set, so a lower lookup cap bought nothing.
MAX_PRODUCTS_PER_RETAILER = 3
DETAIL_LOOKUPS_PER_RETAILER = 3

logger = logging.getLogger(__name__)


# ScraperBase defines find_nearby_stores for everyone, so hasattr cannot detect Amazon's opt-out
async def nearby_store_ids(scraper, lat: float, lon: float, radius_mi: int) -> list[str] | None:
    try:
        stores = await scraper.find_nearby_stores(lat, lon, radius_mi)
    except NotImplementedError:
        return None
    return [store["store_id"] for store in stores]


# cheap pre-spec signal from the search tile: buyable products are worth a detail page load
# first. the sort is stable, so everything else keeps the retailer's relevance order - price
# does not decide, since a "capacity >= 20000" style rule and cheapest-first pull opposite ways
def tile_rank(product: dict) -> tuple:
    return (product.get("in_stock") is False, product.get("price") is None)


async def gather_reviews(retailer: str, scraper, product: dict) -> list[dict]:
    data = await scraper.get_reviews(product["url"])
    if not data:
        return []
    # Reddit/forum/YouTube entries get appended here in their phase
    return [{"source": retailer, **data}]


async def run_pipeline(
    item_criteria: dict, lat: float, lon: float, radius_mi: int
) -> list[RankedProduct]:
    query = build_query(item_criteria)
    must_haves = item_criteria.get("must_haves", [])
    preferred_specs = item_criteria.get("preferred_specs", [])
    nice_to_haves = item_criteria.get("nice_to_haves", [])
    min_review_count = item_criteria["min_review_count"]

    candidates = []
    for retailer, scraper in SCRAPERS:
        # one broken retailer must not kill the run
        try:
            store_ids = await nearby_store_ids(scraper, lat, lon, radius_mi)
            # relevance order decides which products survive the cap; the cheap tile signal
            # only decides which of those are worth a detail page load
            found = (await scraper.search(query, store_ids))[:MAX_PRODUCTS_PER_RETAILER]
            for position, product in enumerate(sorted(found, key=tile_rank)):
                # below the top few: no detail page load, so specs and reviews stay unknown
                if position >= DETAIL_LOOKUPS_PER_RETAILER:
                    if must_haves or min_review_count:
                        logger.info("skip %s: no detail lookup, filters unverifiable",
                                    product["name"])
                        continue
                    specs, reviews = {}, []
                else:
                    specs = await scraper.get_specs(product["url"])
                    if not specs:
                        # LLM call #2 (spec_extraction) goes here once a scraper returns raw page text
                        logger.info("skip %s: no specs", product["name"])
                        continue
                    if not passes_must_haves(specs, must_haves):
                        logger.info("skip %s: failed must_haves", product["name"])
                        continue
                    reviews = await gather_reviews(retailer, scraper, product)
                    review_count = max((r["review_count"] or 0) for r in reviews) if reviews else 0
                    if review_count < min_review_count:
                        logger.info("skip %s: %s reviews", product["name"], review_count)
                        continue
                candidates.append(
                    RankedProduct(
                        product=product,
                        retailer=retailer,
                        specs=specs,
                        reviews=reviews,
                        spec_match=compute_spec_match(specs, preferred_specs),
                        review_score=compute_review_score(reviews),
                        nice_to_have_score=await nice_to_have.score(product, nice_to_haves),
                        distance_score=compute_distance_score(
                            product["distance_miles"], radius_mi
                        ),
                    )
                )
        except Exception:
            # keep the broad catch so one dead retailer does not kill a multi-retailer run,
            # but log the traceback: a code bug must not read like a retailer outage
            logger.exception("%s failed", retailer)

    # price is scored across the whole set, so it can only be done once everything is gathered
    assign_price_scores(candidates, item_criteria.get("budget_max"))
    for candidate in candidates:
        candidate.final_score = compute_final_score(candidate)
    return sorted(candidates, key=lambda c: c.final_score, reverse=True)
