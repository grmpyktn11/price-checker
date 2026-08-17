import logging

from backend.scrapers.amazon import AmazonScraper
from backend.scrapers.bestbuy import BestBuyScraper
from backend.scrapers.target import TargetScraper
from backend.services import (
    attribution,
    nice_to_have,
    reviews_reddit,
    reviews_store,
    reviews_youtube,
    sentiment,
    spec_extraction,
)
from backend.services.ranking import (
    SPEC_MATCH_INHERITED_PENALTY,
    RankedProduct,
    apply_authenticity_flags,
    assign_price_scores,
    attribute_reviews,
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
#     "category": "electronics",           # picks the subreddit list
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
# (get_specs, get_reviews and get_page_text share one load through the 60s cache) = 4 loads.
# the two numbers match on purpose: a candidate past the detail cutoff is dropped outright
# whenever must_haves or min_review_count is set, so a lower lookup cap bought nothing.
MAX_PRODUCTS_PER_RETAILER = 3
DETAIL_LOOKUPS_PER_RETAILER = 3
# capped: a retailer whose selectors broke must not send one 12k-char page per product on
# every rescan forever
SPEC_EXTRACTION_PER_RUN = 3

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


# what LLM call #2 is asked to look for. Model Number is always included so a page recovered
# by the fallback can still take part in review attribution
def wanted_spec_fields(item_criteria: dict) -> list[str]:
    rules = [*item_criteria.get("must_haves", []), *item_criteria.get("preferred_specs", [])]
    fields = [rule["field"] for rule in rules if rule.get("field")]
    return list(dict.fromkeys([*fields, "Model Number"]))


# reddit and youtube are fetched once per run and keyed on the item query, not per product:
# per-product would be 9 reddit requests and 900 YouTube units per run. the honest cost is
# that the external signal is item-level, not product-level
async def gather_external_reviews(item_criteria: dict, db=None, item_id: int | None = None
                                  ) -> list[dict]:
    # a watched item with rows under a week old costs zero quota. a first chat search has no
    # item row yet, so it always fetches
    if db is not None and item_id:
        cached = reviews_store.load_fresh_external(db, item_id)
        if cached:
            logger.info("external reviews served from cache for item %s", item_id)
            return cached
    query = build_query(item_criteria)
    category = item_criteria.get("category")
    gathered = [
        await reviews_reddit.gather(query, category),
        await reviews_youtube.gather(query),
    ]
    return [review for review in gathered if review]


# the retailer row first, then the same three item-level dicts every candidate gets. the
# external dicts are shared objects across candidates - only retailer rows are mutated - but
# each candidate gets its own list, because attribution inserts into it
async def gather_reviews(retailer: str, scraper, product: dict, external: list[dict]) -> list[dict]:
    data = await scraper.get_reviews(product["url"])
    if not data:
        return [*external]
    return [{"source": retailer, **data}, *external]


# spec inheritance has already run, so a candidate is filtered on the specs it now has
def filter_on_specs(candidates: list[RankedProduct], must_haves: list[dict],
                    preferred_specs: list[dict]) -> list[RankedProduct]:
    survivors = []
    for candidate in candidates:
        name = candidate.product["name"]
        # only a spec-based must_have needs a spec table. with no must_haves there is nothing
        # to verify, so dropping a retailer whose product page we cannot read (best buy) would
        # discard a real match for no reason
        if not candidate.specs and must_haves:
            logger.info("skip %s: no specs", name)
            continue
        if not passes_must_haves(candidate.specs, must_haves):
            logger.info("skip %s: failed must_haves", name)
            continue
        candidate.spec_match = compute_spec_match(candidate.specs, preferred_specs)
        if candidate.specs_inherited_from:
            candidate.spec_match *= SPEC_MATCH_INHERITED_PENALTY
        survivors.append(candidate)
    return survivors


# runs after attribute_reviews, so a candidate that inherited a rating is judged on that
# review count rather than on zero
async def filter_on_reviews(candidates: list[RankedProduct], min_review_count: int,
                            external_sentiment: str | None, nice_to_haves: list[str]
                            ) -> list[RankedProduct]:
    survivors = []
    for candidate in candidates:
        counts = [(r.get("review_count") or 0) for r in candidate.reviews]
        review_count = max(counts) if counts else 0
        if review_count < min_review_count:
            logger.info("skip %s: %s reviews", candidate.product["name"], review_count)
            continue
        apply_authenticity_flags(candidate.reviews, external_sentiment)
        candidate.review_score = compute_review_score(candidate.reviews)
        candidate.nice_to_have_score = await nice_to_have.score(candidate.product, nice_to_haves)
        survivors.append(candidate)
    return survivors


# db and item_id are optional: a first chat search has neither and must still work. with both,
# the staleness cache and review persistence are active; without either, external sources are
# fetched fresh and nothing is written
async def run_pipeline(item_criteria: dict, lat: float, lon: float, radius_mi: int,
                       db=None, item_id: int | None = None) -> list[RankedProduct]:
    query = build_query(item_criteria)
    must_haves = item_criteria.get("must_haves", [])
    preferred_specs = item_criteria.get("preferred_specs", [])
    nice_to_haves = item_criteria.get("nice_to_haves", [])
    min_review_count = item_criteria["min_review_count"]
    wanted_fields = wanted_spec_fields(item_criteria)

    external = await gather_external_reviews(item_criteria, db, item_id)
    external_sentiment = (await sentiment.classify(external))["sentiment"]

    # no drops happen inside the loop beyond the detail cutoff: both attribution passes are
    # joins across the whole candidate set, so filtering has to wait until it is complete
    candidates = []
    spec_extractions = 0
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
                    specs, reviews = {}, [*external]
                else:
                    specs = await scraper.get_specs(product["url"])
                    # first-party recovery: a page we did reach but could not parse. runs
                    # before inheritance, and what it returns counts as first-party
                    if not specs and spec_extractions < SPEC_EXTRACTION_PER_RUN:
                        page_text = await scraper.get_page_text(product["url"])
                        if page_text:
                            spec_extractions += 1
                            specs = await spec_extraction.extract(page_text, wanted_fields)
                    reviews = await gather_reviews(retailer, scraper, product, external)
                candidates.append(
                    RankedProduct(
                        product=product,
                        retailer=retailer,
                        specs=specs,
                        reviews=reviews,
                        spec_match=0.0,
                        review_score=0.0,
                        nice_to_have_score=0.0,
                        distance_score=compute_distance_score(
                            product["distance_miles"], radius_mi
                        ),
                    )
                )
        except Exception:
            # keep the broad catch so one dead retailer does not kill a multi-retailer run,
            # but log the traceback: a code bug must not read like a retailer outage
            logger.exception("%s failed", retailer)

    # order matters: inheritance before the no-specs drop, or a candidate is gone before it
    # can inherit, and before must_haves, so it is filtered on the specs it now has
    attribution.attribute_specs(candidates)
    candidates = filter_on_specs(candidates, must_haves, preferred_specs)
    attribute_reviews(candidates)
    candidates = await filter_on_reviews(candidates, min_review_count, external_sentiment,
                                         nice_to_haves)

    # price is scored across the whole set, so it can only be done once everything is gathered
    assign_price_scores(candidates, item_criteria.get("budget_max"))
    for candidate in candidates:
        candidate.final_score = compute_final_score(candidate)
    if db is not None and item_id:
        reviews_store.save_reviews(db, item_id, external)
    return sorted(candidates, key=lambda c: c.final_score, reverse=True)
