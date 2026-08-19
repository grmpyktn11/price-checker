import asyncio
import logging
import time

from backend.scrapers.amazon import AmazonScraper
from backend.scrapers.bestbuy import BestBuyScraper
from backend.scrapers.microcenter import MicroCenterScraper
from backend.scrapers.target import TargetScraper
from backend.services import (
    product_filter,
    reviews_reddit,
    reviews_store,
    reviews_youtube,
    sentiment,
    spec_extraction,
    stores,
    trace,
)
from backend.services.ranking import (
    INHERITED_SUFFIX,
    RankedProduct,
    apply_authenticity_flags,
    assign_price_scores,
    build_query,
    collapse_variants,
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
    ("microcenter", MicroCenterScraper()),
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


# the per-run cap on LLM spec extraction, shared by the concurrent retailers. a plain int
# would be copied into each closure and every retailer would get the full budget
class SpecExtractionBudget:
    def __init__(self, limit: int) -> None:
        self.left = limit

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True

    # the page turned out to be unreadable, so nothing was spent
    def give_back(self) -> None:
        self.left += 1


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


# {rating, review_count} the search tile already carried, or nothing. Best Buy and Amazon both
# print these on the search page, which is the page that actually loads for us
def tile_reviews(product: dict) -> dict:
    rating = product.get("rating")
    return {} if rating is None else {"rating": rating,
                                      "review_count": product.get("review_count")}


# the retailer's own rating row, or nothing. external discussion is not gathered here: it is
# per product and only the top of the ranking earns it, further down.
# the product page is tried first because it also carries the star distribution, but its
# absence is no longer the end of it: a blocked product page falls back to the numbers the
# search tile already gave us, which is why a Best Buy card can show a rating at all
async def gather_reviews(retailer: str, scraper, product: dict) -> list[dict]:
    data = await scraper.get_reviews(product["url"]) or tile_reviews(product)
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
    # measured: two more searches into a bot wall cost 20s of a 107s run and returned nothing.
    # the wall does not lift between one search and the next
    if trace.outcome_so_far("amazon") == trace.BLOCKED:
        logger.info("amazon search was blocked this run, skipping the review lookup")
        trace.note("review_lookup", {"searches": trace.unclaimed_searches(), "tiles_kept": 0,
                                     "searches_left": searches_left,
                                     "skipped": "amazon search was blocked this run"})
        return tiles
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
    # these are extra Amazon searches, so they get their own section rather than overwriting
    # the main amazon search row
    trace.note("review_lookup", {
        "searches": trace.unclaimed_searches(),
        "tiles_kept": len(tiles),
        "searches_left": searches_left,
    })
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
    started = time.monotonic()
    assessments = await product_filter.assess(item_criteria, products)

    survivors = []
    for candidate, assessment in zip(candidates, assessments):
        candidate.group = assessment["group"]
        candidate.spec_match = assessment["spec_fit"]
        candidate.nice_to_have_score = assessment["nice_fit"]
        if not assessment["qualifies"]:
            logger.info("skip %s: does not meet the requirements", candidate.product["name"])
            # the model's own words when it gave any, so the drop is explainable
            trace.drop("product_filter", candidate.product.get("name"), candidate.retailer,
                       assessment.get("reason")
                       or "the model judged it does not meet the requirements")
            continue
        survivors.append(candidate)
    trace.note("product_filter", {
        "products_in": len(products),
        "candidates_in": len(candidates),
        "review_tiles_in": len(tiles),
        "qualified": len(survivors),
        "rejected": len(candidates) - len(survivors),
        "ms": trace.elapsed_ms(started),
    })

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
    started = time.monotonic()
    store_distances = await stores.nearest_stores(lat, lon)
    trace.note("stores", {
        "source": "google places text search",
        "distance_miles": store_distances,
        # a retailer with stores that Places did not return: no store nearby, or a failed
        # lookup. either way its candidates score a neutral distance
        "not_found": [r for r in stores.STORE_QUERIES if r not in store_distances],
        "ms": trace.elapsed_ms(started),
    })

    # shared across the concurrent retailers so the whole run stays inside the cap. asyncio is
    # single-threaded and take() never awaits, so the check and the decrement cannot interleave
    budget = SpecExtractionBudget(SPEC_EXTRACTION_PER_RUN)

    async def collect_from(retailer: str, scraper) -> list[RankedProduct]:
        started = time.monotonic()
        found_here: list[RankedProduct] = []
        error = None
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
                if not specs and budget.take():
                    page_text = await scraper.get_page_text(product["url"])
                    if page_text:
                        specs = await spec_extraction.extract(page_text, wanted_fields)
                    else:
                        budget.give_back()
                found_here.append(
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
        except Exception as failure:
            # keep the broad catch so one dead retailer does not kill a multi-retailer run,
            # but log the traceback: a code bug must not read like a retailer outage
            logger.exception("%s failed", retailer)
            error = f"{type(failure).__name__}: {failure}"
        trace.retailer(retailer, ms=trace.elapsed_ms(started),
                       candidates=len(found_here), error=error)
        return found_here

    # concurrently, not one after another. measured sequentially: best buy 27s + target 4s +
    # amazon 13s + micro center 18s = 63s of a 115s search, spent mostly waiting. they are
    # four different hosts, so nothing is rate-limited by running them together - the pacing
    # that matters is per-retailer, and each retailer's own products are still serial
    trace.expect_retailers([retailer for retailer, _ in SCRAPERS])
    per_retailer = await asyncio.gather(*(collect_from(r, s) for r, s in SCRAPERS))
    # flattened in SCRAPERS order, not completion order, so a run is reproducible
    return [candidate for group in per_retailer for candidate in group]


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
    ordered = sorted(candidates, key=lambda c: c.final_score, reverse=True)
    # after sorting, so the highest-scoring listing of a product is the one that survives
    return collapse_variants(ordered)


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
        retried = review is None
        if retried:
            await asyncio.sleep(REDDIT_RETRY_PAUSE_SECONDS)
            review = await reviews_reddit.gather(name, category)
        if review:
            candidate.reviews.append(review)
        trace.append("research", {
            "rank": position + 1,
            "name": name,
            "retailer": candidate.retailer,
            "reddit_posts": (review or {}).get("mention_count", 0),
            "reddit_retried": retried,
            "youtube": False,
        })


# candidates are the top few in ranked order, so the index is also the research row's index
async def research_youtube(candidates: list[RankedProduct]) -> None:
    for index, candidate in enumerate(candidates):
        review = await reviews_youtube.gather(candidate.product["name"])
        if review:
            candidate.reviews.append(review)
        trace.update_research(index, {
            "youtube": True,
            "youtube_videos": (review or {}).get("mention_count", 0),
        })


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
    with trace.stage("research_reddit"):
        await research_reddit(candidates, category)
    assessment = await sentiment.assess(research_payload(candidates))
    too_close = assessment["too_close"]
    if too_close:
        logger.info("top of the ranking is too close to call: %s", too_close)
        with trace.stage("research_youtube"):
            await research_youtube(candidates[:YOUTUBE_TOP_N])
        assessment = await sentiment.assess(research_payload(candidates))
    trace.note("youtube", {
        "triggered": bool(too_close),
        # the sentiment call's own verdict: which ranked positions its discussion could not
        # separate. empty means the reddit research was decisive and no quota was spent
        "too_close_positions": [index + 1 for index in too_close],
        "reason": ("the discussion could not separate the top of the ranking" if too_close
                   else "the discussion separated the ranking, so no youtube quota was spent"),
    })
    apply_assessment(candidates, assessment["products"])


# star ratings and discussion both count: Best Buy product pages are blocked and publish no
# review count at all, so a product with real reddit/youtube threads about it clears the floor
# on those instead.
# None, not 0, when no source reported anything. a blocked product page and a product nobody
# has reviewed produce the same empty list here, and only the second is a fact about the
# product - see filter_on_reviews
def evidence_count(candidate: RankedProduct) -> int | None:
    reported = [row[field] for row in candidate.reviews
                for field in ("review_count", "mention_count")
                if row.get(field) is not None]
    return max(reported) if reported else None


# one line per candidate that reached ranking: the evidence it carries, and whether its specs
# and rating are its own or were attributed from another retailer. "spec_fields: 0" is how a
# candidate nobody published specs for shows up - it is not dropped for it, specs are inherited
def candidate_row(candidate: RankedProduct) -> dict:
    rating_row = next((r for r in candidate.reviews if r.get("rating") is not None), {})
    return {
        "name": candidate.product.get("name"),
        "retailer": candidate.retailer,
        "price": candidate.product.get("price"),
        "spec_fields": len(candidate.specs),
        "specs_inherited_from": candidate.specs_inherited_from,
        "rating": rating_row.get("rating"),
        "rating_source": rating_row.get("source"),
        "evidence_count": evidence_count(candidate),
        "same_product_group": candidate.group,
    }


def filter_on_reviews(candidates: list[RankedProduct],
                      min_review_count: int) -> list[RankedProduct]:
    survivors = []
    for candidate in candidates:
        count = evidence_count(candidate)
        # nobody published a count for this product, so there is nothing to be below the floor.
        # dropping here would delete the best match for a retailer whose pages we cannot read,
        # which is exactly what happened to the only RGB mice in an "rgb mouse" search
        if count is None:
            survivors.append(candidate)
            continue
        if count < min_review_count:
            logger.info("skip %s: %s reviews or mentions", candidate.product["name"], count)
            trace.drop("review_floor", candidate.product.get("name"), candidate.retailer,
                       f"{count} reviews or mentions found, "
                       f"below the {min_review_count} the criteria ask for")
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
                       db=None, item_id: int | None = None,
                       progress_key: str | None = None,
                       research_top_n: int = RESEARCH_TOP_N) -> list[RankedProduct]:
    # progress_key is the conversation, so that conversation can poll its own run. the
    # scheduler passes none: nothing is watching a rescan
    trace.start(build_query(item_criteria), item_criteria, key=progress_key)
    budget_max = item_criteria.get("budget_max")
    with trace.stage("collect_candidates"):
        candidates = await collect_candidates(item_criteria, lat, lon, radius_mi)

    # no drops happen before this: qualification and both inheritance passes are judged over
    # the whole candidate set at once, so filtering has to wait until it is complete.
    # the tiles are gathered first so they ride along in the single judgment call
    with trace.stage("amazon_review_tiles"):
        tiles = await amazon_review_tiles(candidates, AMAZON)
    with trace.stage("product_filter"):
        candidates, tiles_by_group = await judge_candidates(item_criteria, candidates, tiles)
    inherit_specs(candidates)
    inherit_reviews(candidates)
    # last resort, and the only step that can help when Amazon returned nothing for this query:
    # read the Amazon page for candidates still without any rating
    with trace.stage("lookup_missing_reviews"):
        await lookup_missing_reviews(candidates, tiles_by_group, AMAZON)
    trace.note("candidates", [candidate_row(c) for c in candidates])

    # cheap pass first, so the research is spent on the products that are actually in contention
    ranked = rank(candidates, budget_max)
    # research_top_n=0 skips reddit and youtube entirely. a project search does that: five
    # reddit searches per item, on a source that already 429s most requests, is how one
    # project run gets everything blocked partway through. ranking still has the retailer
    # star ratings, which now come off the search tile
    if research_top_n:
        with trace.stage("research_top"):
            await research_top(ranked[:research_top_n], item_criteria.get("category"))
    # re-rank: the research moved review_score through the per-product authenticity flags
    ranked = filter_on_reviews(rank(ranked, budget_max), item_criteria["min_review_count"])

    if db is not None and item_id and ranked:
        reviews_store.save_reviews(db, item_id, research_rows(ranked[0]))
    trace.finish(len(ranked))
    return ranked
