import asyncio
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
    stores,
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

# each search returns ~24 products and each detail page is a browser launch, so this is a hard
# cap. per Playwright retailer that is 1 search page + 3 product pages (get_specs, get_reviews
# and get_page_text share one load through the 60s cache) = 4 loads
MAX_PRODUCTS_PER_RETAILER = 3
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
# how many of the ranked products get researched individually. reddit is keyless and free but
# rate-limits, so the searches are capped and paced
RESEARCH_TOP_N = 5
REDDIT_PAUSE_SECONDS = 2.0
# measured 2026-08-17: reddit 429s this host on most searches and answers the same query
# seconds later, so one retry after a longer wait roughly doubles the yield. one, not a
# backoff loop: five products must not turn into twenty requests
REDDIT_RETRY_PAUSE_SECONDS = 4.0
# a YouTube search costs 100 of ~10000 daily quota units, so it is only spent when the
# sentiment call says the top of the ranking is too close to call, and only on the top two
YOUTUBE_TOP_N = 2

logger = logging.getLogger(__name__)


# ScraperBase defines find_nearby_stores for everyone, so hasattr cannot detect Amazon's opt-out
async def nearby_store_ids(scraper, lat: float, lon: float, radius_mi: int) -> list[str] | None:
    try:
        stores_found = await scraper.find_nearby_stores(lat, lon, radius_mi)
    except NotImplementedError:
        return None
    return [store["store_id"] for store in stores_found]


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


# the retailer's own rating row, or nothing. external discussion is not gathered here: it is
# per product and only the top of the ranking earns it, further down
async def gather_reviews(retailer: str, scraper, product: dict) -> list[dict]:
    data = await scraper.get_reviews(product["url"])
    return [{"source": retailer, **data}] if data else []


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


# the one product-filter call per run. it answers three questions at once - does this product
# meet the requirements, how well does it fit them, and which listings are the same product -
# and it answers them for the Amazon review tiles in the same breath.
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


# every retailer, capped, with specs and the retailer's own rating for each candidate
async def collect_candidates(item_criteria: dict, lat: float, lon: float,
                             radius_mi: int) -> list[RankedProduct]:
    query = build_query(item_criteria)
    wanted_fields = wanted_spec_fields(item_criteria)
    # one Places lookup per location for the whole run, not per product
    store_distances = await stores.nearest_stores(lat, lon)

    candidates = []
    spec_extractions = 0
    for retailer, scraper in SCRAPERS:
        # one broken retailer must not kill the run
        try:
            store_ids = await nearby_store_ids(scraper, lat, lon, radius_mi)
            # relevance order decides which products survive the cap; the cheap tile signal
            # only decides which are looked up first
            found = (await scraper.search(query, store_ids))[:MAX_PRODUCTS_PER_RETAILER]
            for product in sorted(found, key=tile_rank):
                specs = await scraper.get_specs(product["url"])
                # first-party recovery: a page we did reach but could not parse. runs before
                # inheritance, and what it returns counts as first-party
                if not specs and spec_extractions < SPEC_EXTRACTION_PER_RUN:
                    page_text = await scraper.get_page_text(product["url"])
                    if page_text:
                        spec_extractions += 1
                        specs = await spec_extraction.extract(page_text, wanted_fields)
                candidates.append(
                    RankedProduct(
                        product=product,
                        retailer=retailer,
                        specs=specs,
                        reviews=await gather_reviews(retailer, scraper, product),
                        spec_match=0.0,
                        review_score=0.0,
                        nice_to_have_score=0.0,
                        distance_score=compute_distance_score(
                            store_distances.get(retailer), radius_mi
                        ),
                    )
                )
        except Exception:
            # keep the broad catch so one dead retailer does not kill a multi-retailer run,
            # but log the traceback: a code bug must not read like a retailer outage
            logger.exception("%s failed", retailer)
    return candidates


# reddit and youtube rows carry no rating, so they only reach the score through the
# authenticity flag; the rating rows are what compute_review_score actually reads
def score_candidate(candidate: RankedProduct) -> None:
    apply_authenticity_flags(candidate.reviews, candidate.sentiment)
    candidate.review_score = compute_review_score(candidate.reviews)


# price is scored across the whole set, so it can only be done once everything is gathered
def rank(candidates: list[RankedProduct], budget_max: float | None) -> list[RankedProduct]:
    assign_price_scores(candidates, budget_max)
    for candidate in candidates:
        score_candidate(candidate)
        candidate.final_score = compute_final_score(candidate)
    return sorted(candidates, key=lambda c: c.final_score, reverse=True)


# one reddit search per product, on that product's own name, paced: reddit rate-limits and
# this is the only place the app searches it more than once. a product reddit has nothing on
# simply gets no discussion row
async def research_reddit(candidates: list[RankedProduct], category: str | None) -> None:
    for position, candidate in enumerate(candidates):
        if position:
            await asyncio.sleep(REDDIT_PAUSE_SECONDS)
        name = candidate.product["name"]
        review = await reviews_reddit.gather(name, category)
        # nothing back is usually a 429 rather than an unknown product, so try once more
        if review is None:
            await asyncio.sleep(REDDIT_RETRY_PAUSE_SECONDS)
            review = await reviews_reddit.gather(name, category)
        if review:
            candidate.reviews.append(review)


async def research_youtube(candidates: list[RankedProduct]) -> None:
    for candidate in candidates:
        review = await reviews_youtube.gather(candidate.product["name"])
        if review:
            candidate.reviews.append(review)


# everything found about this one product, in the shape sentiment.assess reads
def research_payload(candidates: list[RankedProduct]) -> list[dict]:
    payload = []
    for candidate in candidates:
        rated = [r.get("rating") for r in candidate.reviews if r.get("rating") is not None]
        discussion = "\n\n".join(f"[{r['source']}] {r['summary_text']}"
                                 for r in candidate.reviews if r.get("summary_text"))
        payload.append({
            "name": candidate.product.get("name"),
            "rating": rated[0] if rated else None,
            "discussion": discussion,
        })
    return payload


def apply_assessment(candidates: list[RankedProduct], assessments: list[dict]) -> None:
    for candidate, assessment in zip(candidates, assessments):
        candidate.sentiment = assessment["sentiment"]
        candidate.sentiment_summary = assessment["summary"]


# the point of the app: the top few products are researched one by one rather than sharing
# one item-level lookup. one sentiment call in the common case, two when it says the top is
# too close to call - and only then is any YouTube quota spent
async def research_top(candidates: list[RankedProduct], category: str | None) -> None:
    await research_reddit(candidates, category)
    assessment = await sentiment.assess(research_payload(candidates))
    if assessment["too_close"]:
        logger.info("top of the ranking is too close to call: %s", assessment["too_close"])
        await research_youtube(candidates[:YOUTUBE_TOP_N])
        assessment = await sentiment.assess(research_payload(candidates))
    apply_assessment(candidates, assessment["products"])


# star ratings and discussion both count: Best Buy product pages are blocked and publish no
# review count at all, so a product with real reddit/youtube threads about it clears the floor
# on those instead. a candidate with neither is the one that gets dropped
def evidence_count(candidate: RankedProduct) -> int:
    counts = [(row.get("review_count") or 0) for row in candidate.reviews]
    mentions = [(row.get("mention_count") or 0) for row in candidate.reviews]
    return max([0, *counts, *mentions])


def filter_on_reviews(candidates: list[RankedProduct],
                      min_review_count: int) -> list[RankedProduct]:
    survivors = []
    for candidate in candidates:
        count = evidence_count(candidate)
        if count < min_review_count:
            logger.info("skip %s: %s reviews or mentions", candidate.product["name"], count)
            continue
        survivors.append(candidate)
    return survivors


# the reddit/youtube rows for one product, which is what the reviews table stores as the
# item's discussion. rating rows are the retailer's and are saved by the caller instead
def research_rows(candidate: RankedProduct) -> list[dict]:
    return [row for row in candidate.reviews if row.get("mention_count") is not None]


# db and item_id are optional: a first chat search has neither and must still work. with both,
# the winner's research is persisted; without either, nothing is written
async def run_pipeline(item_criteria: dict, lat: float, lon: float, radius_mi: int,
                       db=None, item_id: int | None = None) -> list[RankedProduct]:
    budget_max = item_criteria.get("budget_max")
    candidates = await collect_candidates(item_criteria, lat, lon, radius_mi)

    # no drops happen before this: qualification and both inheritance passes are judged over
    # the whole candidate set at once, so filtering has to wait until it is complete.
    # the tiles are gathered first so they ride along in the single judgment call
    tiles = await amazon_review_tiles(candidates, AMAZON)
    candidates, tiles_by_group = await judge_candidates(item_criteria, candidates, tiles)
    inherit_specs(candidates)
    inherit_reviews(candidates)
    # last resort, and the only step that can help when Amazon returned nothing for this query:
    # read the Amazon page for candidates still without any rating
    await lookup_missing_reviews(candidates, tiles_by_group, AMAZON)

    # cheap pass first, so the research is spent on the products that are actually in contention
    ranked = rank(candidates, budget_max)
    await research_top(ranked[:RESEARCH_TOP_N], item_criteria.get("category"))
    # re-rank: the research moved review_score through the per-product authenticity flags
    ranked = filter_on_reviews(rank(ranked, budget_max), item_criteria["min_review_count"])

    if db is not None and item_id and ranked:
        reviews_store.save_reviews(db, item_id, research_rows(ranked[0]))
    return ranked
