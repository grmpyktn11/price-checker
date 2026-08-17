import logging

from backend.scrapers.amazon import AmazonScraper
from backend.scrapers.bestbuy import BestBuyScraper
from backend.scrapers.target import TargetScraper
from backend.services import (
    product_filter,
    reviews_reddit,
    reviews_store,
    reviews_youtube,
    sentiment,
    spec_extraction,
)
from backend.services.ranking import (
    INHERITED_SUFFIX,
    RankedProduct,
    apply_authenticity_flags,
    assign_price_scores,
    build_query,
    compute_distance_score,
    compute_final_score,
    compute_review_score,
    inherit_reviews,
    inherit_specs,
    inherited_row,
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
#     "nice_to_haves": ["compact", "looks sleek"],   # subjective, scored by product_filter
#     "budget_max": 150.0,                 # ranking penalty on price_score, NOT a filter, may be None
#     "target_price": 99.0,                # deals.py only, may be None
#     "fulfillment_preference": "either",  # pickup | shipping | either, unused in Phase 2
#     "radius_miles": 25,
#     "min_review_count": 100,
# }

# one instance, also used on its own as the review-lookup authority below
AMAZON = AmazonScraper()
SCRAPERS = [
    ("bestbuy", BestBuyScraper()),
    ("target", TargetScraper()),
    ("amazon", AMAZON),
]

# live, each search returns ~24 products and each detail page is a browser launch, so both
# numbers are hard caps. per Playwright retailer that is 1 search page + 3 product pages
# (get_specs, get_reviews and get_page_text share one load through the 60s cache) = 4 loads.
# the two numbers match on purpose: a candidate past the detail cutoff is dropped outright
# whenever min_review_count is set, so a lower lookup cap bought nothing.
MAX_PRODUCTS_PER_RETAILER = 3
DETAIL_LOOKUPS_PER_RETAILER = 3
# capped: a retailer whose selectors broke must not send one 12k-char page per product on
# every rescan forever
SPEC_EXTRACTION_PER_RUN = 3
# the review lookup is one Amazon search per reviewless candidate plus one product page for
# each search result the model then puts in that candidate's group, and Amazon rate-limits
# hard, so both are capped per run. worst case Amazon requests per run: 1 search + 3 product
# pages for Amazon's own candidates, plus 2 searches + 2 product pages here = 8. the lookup
# product pages are new urls, so browser.py's single-entry cache never serves them
AMAZON_REVIEW_SEARCHES_PER_RUN = 2
AMAZON_REVIEW_TILES_PER_SEARCH = 3
AMAZON_REVIEW_LOOKUPS_PER_RUN = 2

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


# what LLM call #2 is asked to look for. Model Number is always included because it is the
# strongest hint the judgment call gets that two listings are the same product
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
# each candidate gets its own list, because review inheritance inserts into it
async def gather_reviews(retailer: str, scraper, product: dict, external: list[dict]) -> list[dict]:
    data = await scraper.get_reviews(product["url"])
    if not data:
        return [*external]
    return [{"source": retailer, **data}, *external]


# any row with a rating counts: first-party, or one inherit_reviews just attributed
def has_rating(candidate: RankedProduct) -> bool:
    return any(row.get("rating") is not None for row in candidate.reviews)


# what the model is shown per product: the title carries most of the signal, the specs are
# whatever this retailer happened to publish
def judgment_payload(retailer: str, product: dict, specs: dict) -> dict:
    return {
        "retailer": retailer,
        "title": product.get("name"),
        "price": product.get("price"),
        "url": product.get("url"),
        "specs": specs,
    }


# one Amazon search per candidate with no rating, capped. the tiles are judged in the same
# batched call as the candidates, so a tile only ever donates when the model puts it in a
# candidate's group. this runs before that call, so a search can be spent on a candidate the
# model then drops - the alternative is a second model call
async def amazon_review_tiles(candidates: list[RankedProduct], scraper) -> list[dict]:
    tiles = []
    searches_left = AMAZON_REVIEW_SEARCHES_PER_RUN
    for candidate in candidates:
        # an Amazon candidate with no rating means its own product page already failed, so a
        # second search would almost certainly fail too and would starve the cap
        if candidate.retailer == "amazon" or has_rating(candidate):
            continue
        if searches_left <= 0:
            logger.info("amazon review search cap reached, skipping %s",
                        candidate.product["name"])
            continue
        searches_left -= 1
        try:
            found = await scraper.search(candidate.product["name"])
        except Exception:
            # a blocked or broken search is just no reviews for that candidate. no retry
            logger.exception("amazon review search failed for %s", candidate.product["name"])
            continue
        tiles.extend(found[:AMAZON_REVIEW_TILES_PER_SEARCH])
    return tiles


# the one model call per run. it answers three questions at once - does this product meet the
# requirements, how well does it fit them, and which listings are the same product - and it
# answers them for the Amazon review tiles in the same breath.
# returns the survivors plus the tiles keyed by group, which is how the review lookup finds
# the Amazon listing for a candidate's product
async def judge_candidates(item_criteria: dict, candidates: list[RankedProduct],
                           tiles: list[dict]) -> tuple[list[RankedProduct], dict[str, dict]]:
    products = [judgment_payload(c.retailer, c.product, c.specs) for c in candidates]
    products += [judgment_payload("amazon", tile, {}) for tile in tiles]
    assessments = await product_filter.assess(item_criteria, products)

    survivors = []
    for candidate, assessment in zip(candidates, assessments):
        candidate.group = assessment["group"]
        candidate.spec_match = assessment["spec_fit"]
        candidate.nice_to_have_score = assessment["nice_fit"]
        if not assessment["qualifies"]:
            logger.info("skip %s: does not meet the requirements", candidate.product["name"])
            continue
        survivors.append(candidate)

    tiles_by_group: dict[str, dict] = {}
    for tile, assessment in zip(tiles, assessments[len(candidates):]):
        group = assessment["group"]
        # first tile in a group wins: the search returned them in relevance order
        if group and group not in tiles_by_group:
            tiles_by_group[group] = tile
    return survivors, tiles_by_group


# Amazon is the review authority for every retailer, not just its own listings: Best Buy and
# Target publish nothing, so without this any non-zero min_review_count deletes them. runs
# after inherit_reviews, so no page load is spent on a candidate that already has a rating
async def lookup_missing_reviews(candidates: list[RankedProduct],
                                 tiles_by_group: dict[str, dict], scraper) -> None:
    lookups_left = AMAZON_REVIEW_LOOKUPS_PER_RUN
    for candidate in candidates:
        tile = tiles_by_group.get(candidate.group) if candidate.group else None
        if tile is None or has_rating(candidate):
            continue
        if lookups_left <= 0:
            logger.info("amazon review lookup cap reached, skipping %s",
                        candidate.product["name"])
            continue
        lookups_left -= 1
        try:
            data = await scraper.get_reviews(tile["url"])
        except Exception:
            # a blocked or broken lookup is just no reviews for that candidate. no retry
            logger.exception("amazon review lookup failed for %s", tile["url"])
            continue
        if not data or data.get("rating") is None:
            continue
        row = {"source": "amazon", **data, "url": tile["url"]}
        candidate.reviews.insert(0, inherited_row(row, INHERITED_SUFFIX))
        logger.info("rating looked up on amazon: %r -> %s", candidate.product["name"],
                    data["rating"])


# runs after inherit_reviews, so a candidate that inherited a rating is judged on that
# review count rather than on zero
async def filter_on_reviews(candidates: list[RankedProduct], min_review_count: int,
                            external_sentiment: str | None) -> list[RankedProduct]:
    survivors = []
    for candidate in candidates:
        counts = [(r.get("review_count") or 0) for r in candidate.reviews]
        review_count = max(counts) if counts else 0
        if review_count < min_review_count:
            logger.info("skip %s: %s reviews", candidate.product["name"], review_count)
            continue
        apply_authenticity_flags(candidate.reviews, external_sentiment)
        candidate.review_score = compute_review_score(candidate.reviews)
        survivors.append(candidate)
    return survivors


# db and item_id are optional: a first chat search has neither and must still work. with both,
# the staleness cache and review persistence are active; without either, external sources are
# fetched fresh and nothing is written
async def run_pipeline(item_criteria: dict, lat: float, lon: float, radius_mi: int,
                       db=None, item_id: int | None = None) -> list[RankedProduct]:
    query = build_query(item_criteria)
    min_review_count = item_criteria["min_review_count"]
    wanted_fields = wanted_spec_fields(item_criteria)

    external = await gather_external_reviews(item_criteria, db, item_id)
    external_sentiment = (await sentiment.classify(external))["sentiment"]

    # no drops happen inside the loop beyond the detail cutoff: qualification and both
    # inheritance passes are judged over the whole candidate set at once, so filtering has to
    # wait until it is complete
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
                    if min_review_count:
                        logger.info("skip %s: no detail lookup, review count unverifiable",
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

    # the tiles are gathered first so they ride along in the single judgment call
    tiles = await amazon_review_tiles(candidates, AMAZON)
    candidates, tiles_by_group = await judge_candidates(item_criteria, candidates, tiles)
    inherit_specs(candidates)
    inherit_reviews(candidates)
    # last resort, and the only step that can help when Amazon returned nothing for this query:
    # read the Amazon page for candidates still without any rating
    await lookup_missing_reviews(candidates, tiles_by_group, AMAZON)
    candidates = await filter_on_reviews(candidates, min_review_count, external_sentiment)

    # price is scored across the whole set, so it can only be done once everything is gathered
    assign_price_scores(candidates, item_criteria.get("budget_max"))
    for candidate in candidates:
        candidate.final_score = compute_final_score(candidate)
    if db is not None and item_id:
        reviews_store.save_reviews(db, item_id, external)
    return sorted(candidates, key=lambda c: c.final_score, reverse=True)
