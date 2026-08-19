import logging
import re
from dataclasses import dataclass, field
from math import log10

logger = logging.getLogger(__name__)

FULL_CONFIDENCE_REVIEW_COUNT = 1000   # review count where rating is trusted at face value
NEUTRAL_SCORE = 0.5                   # used wherever a signal is missing entirely
# an inherited row keeps the donor's retailer in its source plus this suffix. one suffix, not
# two: there is one identity mechanism now - the model's grouping - so there is nothing for a
# second marker to distinguish
INHERITED_SUFFIX = "_inherited"
# the model groups listings by reading titles, which is weaker evidence than a manufacturer
# part number, so anything inherited on a group takes the same mild haircut
SPEC_MATCH_INHERITED_PENALTY = 0.9
INHERITED_RATING_PENALTY = 0.9
FIVE_STAR_DOMINANCE = 0.80            # share of 5-star reviews above which the curve is suspicious
HOLLOW_MIDDLE_MAX = 0.10              # combined 2-4 star share below which the curve is bimodal
# both penalties are judgement calls: the signals are weak, and a heavy penalty on a weak
# signal is worse than no signal at all
SKEWED_DISTRIBUTION_PENALTY = 0.75
MIXED_SIGNAL_PENALTY = 0.85


@dataclass
class RankedProduct:
    product: dict            # scraper search dict: name, url, price, in_stock, store_id, distance_miles
    retailer: str            # bestbuy | target | amazon
    specs: dict              # raw retailer strings, e.g. {"Battery Capacity": "24,000 milliamp hours"}
    reviews: list[dict]      # one dict per source: {source, rating, review_count, verified_ratio}
    spec_match: float
    review_score: float
    nice_to_have_score: float
    distance_score: float
    price_score: float = 0.0   # set after the full candidate set is known
    final_score: float = 0.0   # set just before sorting
    specs_inherited_from: str | None = None   # retailer these specs were attributed from
    # the model's group id: listings sharing one are the same product at different retailers.
    # run-local and never serialized
    group: str | None = None
    # what LLM call #4 made of this product's own reddit/youtube discussion. only the
    # researched top few carry it; everything below stays None
    sentiment: str | None = None
    sentiment_summary: str | None = None
    # the other listings of this same product - other colours, or the same thing at another
    # retailer - that collapse_variants folded into this one. only the winner carries them
    variants: list[dict] = field(default_factory=list)


# name plus keywords, lowercased, whitespace collapsed. must_haves are filters, not search terms
def build_query(criteria: dict) -> str:
    parts = [criteria["name"], *criteria.get("keywords", [])]
    return re.sub(r"\s+", " ", " ".join(parts)).strip().lower()


# first number in the string, commas stripped. "24,000 milliamp hours" -> 24000.0
def first_number(raw: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


# the candidate's own retailer row, which is the only row that can carry a first-party rating
def first_party_rating_row(candidate: RankedProduct) -> dict | None:
    for row in candidate.reviews:
        if row.get("source") == candidate.retailer and row.get("rating") is not None:
            return row
    return None


# a copy, not the shared object: the two rows can end up with different authenticity flags
def inherited_row(row: dict, suffix: str) -> dict:
    return {**row, "source": f"{row['source']}{suffix}", "inherited_from_retailer": row["source"]}


# retailers publish specs unevenly, so a candidate whose own product page gave nothing takes
# the specs of another listing the model put in its group. fills empty dicts only: a candidate
# with even one first-party spec is never touched, and an inheritor never donates onward
def inherit_specs(candidates: list[RankedProduct]) -> None:
    # reversed, so the first candidate in the group wins rather than the last
    donors = {c.group: c for c in reversed(candidates) if c.group and c.specs}
    for candidate in candidates:
        donor = donors.get(candidate.group) if candidate.group else None
        if candidate.specs or donor is None:
            continue
        candidate.specs = dict(donor.specs)
        candidate.specs_inherited_from = donor.retailer
        # the identity is the model reading two titles, so the soft score says so
        candidate.spec_match *= SPEC_MATCH_INHERITED_PENALTY
        logger.info("specs inherited: %r <- %r [%s]", candidate.product.get("name"),
                    donor.product.get("name"), donor.retailer)


# star ratings are product-level, not listing-level, so a candidate with no first-party rating
# takes the best-supported rating in its group
def inherit_reviews(candidates: list[RankedProduct]) -> None:
    donors: dict[str, dict] = {}
    for candidate in candidates:
        row = first_party_rating_row(candidate)
        if not candidate.group or not row:
            continue
        best = donors.get(candidate.group)
        # whichever source has the most reviews wins the group
        if best is None or (row.get("review_count") or 0) > (best.get("review_count") or 0):
            donors[candidate.group] = row
    for candidate in candidates:
        if first_party_rating_row(candidate) or not candidate.group:
            continue
        row = donors.get(candidate.group)
        if row:
            candidate.reviews.insert(0, inherited_row(row, INHERITED_SUFFIX))


# one listing per product. the model groups the same model across retailers and across
# colours, and a shopper asking for a keyboard does not want the pink and the white one
# offered as two separate recommendations - they want the better one, and to know the other
# colour exists. the winner is whichever scored highest; the rest become its variants.
# ungrouped candidates are always kept: no group means the model said nothing about identity,
# which is not evidence that two listings are the same thing
def collapse_variants(ranked: list[RankedProduct]) -> list[RankedProduct]:
    winners: dict[str, RankedProduct] = {}
    kept = []
    for candidate in ranked:
        if not candidate.group:
            kept.append(candidate)
            continue
        winner = winners.get(candidate.group)
        if winner is None:
            winners[candidate.group] = candidate
            kept.append(candidate)
            continue
        winner.variants.append({
            "name": candidate.product.get("name"),
            "url": candidate.product.get("url"),
            "price": candidate.product.get("price"),
            "retailer": candidate.retailer,
        })
    return kept


# a high 5-star share alone is normal for a good product; the hollow middle is the actual
# fake-review shape, so both conditions must hold
def distribution_is_skewed(distribution: dict | None) -> bool:
    if not distribution:
        return False
    five = distribution.get("5") or 0.0
    middle = sum(distribution.get(star) or 0.0 for star in ("2", "3", "4"))
    return five >= FIVE_STAR_DOMINANCE and middle <= HOLLOW_MIDDLE_MAX


# mutates authenticity_flag in place. precedence: skewed_distribution wins over mixed_signal,
# because it is the star breakdown of this exact listing while the sentiment signal is a
# reading of prose about the product. nothing ever writes suspicious_velocity: no source
# supplies a listing age, so the velocity heuristic has no input
def apply_authenticity_flags(reviews: list[dict], product_sentiment: str | None) -> None:
    # imported here, not at module level: sentiment -> criteria -> ranking is an import cycle
    from backend.services.sentiment import contradicts

    for row in reviews:
        rating = row.get("rating")
        if rating is None:
            row["authenticity_flag"] = "ok"
        elif distribution_is_skewed(row.get("rating_distribution")):
            row["authenticity_flag"] = "skewed_distribution"
        elif contradicts(product_sentiment, rating):
            row["authenticity_flag"] = "mixed_signal"
        else:
            row["authenticity_flag"] = "ok"


# velocity anomaly is still not implemented: nothing supplies a listing age, and scraped_at
# is when we first saw the listing, not when it was created
def compute_review_score(reviews: list[dict]) -> float:
    rated = [r for r in reviews if r.get("rating") is not None]
    # missing feed, not a bad product
    if not rated:
        return NEUTRAL_SCORE
    primary = max(rated, key=lambda r: r.get("review_count") or 0)
    count = primary.get("review_count") or 0
    rating_component = primary["rating"] / 5.0
    confidence = min(1.0, log10(1 + count) / log10(1 + FULL_CONFIDENCE_REVIEW_COUNT))
    # shrink toward neutral so a 5.0 from 3 reviews cannot beat a 4.7 from 1843
    score = confidence * rating_component + (1 - confidence) * NEUTRAL_SCORE
    verified_ratio = primary.get("verified_ratio")
    # no MVP source populates this: none of the three retailers publish a verified ratio
    if verified_ratio is not None and verified_ratio < 0.7:
        score *= verified_ratio / 0.7
    flag = primary.get("authenticity_flag")
    if flag == "skewed_distribution":
        score *= SKEWED_DISTRIBUTION_PENALTY
    elif flag == "mixed_signal":
        score *= MIXED_SIGNAL_PENALTY
    # an inherited rating rests on the model calling two listings the same product, which is
    # weaker than the retailer's own feed, so it is discounted like an inherited spec_match
    if str(primary.get("source", "")).endswith(INHERITED_SUFFIX):
        score *= INHERITED_RATING_PENALTY
    return max(0.0, min(1.0, score))


# distance_miles is how far the retailer's NEAREST store is (services/stores.py), not where
# this product is: no retailer publishes per-product store stock. linear falloff - a store on
# top of you scores 1.0, one at the edge of the radius 0.0. a retailer with no store nearby,
# and a failed Places lookup, both pass None and score neutral, so an outage cannot reorder
def compute_distance_score(distance_miles: float | None, radius_miles: int) -> float:
    if distance_miles is None or not radius_miles:
        return NEUTRAL_SCORE
    return max(0.0, 1.0 - distance_miles / radius_miles)


# ratio penalty: 10 percent over budget costs 9 percent of the price score, 2x over costs half
def over_budget_penalty(price: float, budget_max: float | None) -> float:
    if not budget_max or price <= budget_max:
        return 1.0
    return budget_max / price


# a spread this small, as a fraction of the cheapest candidate, is not a real price difference.
# without it the span is stretched across noise: two keyboards at $60.04 and $60.09 scored
# 1.0 and 0.0, a hundred points of the price weight for five cents
MEANINGFUL_SPREAD = 0.05


# budget_max is a penalty, never a filter: over-budget candidates stay in the set and in the span.
# scores are relative to this run's candidate set, so they are not comparable across runs.
# deals.py works on absolute prices, so nothing alert-related depends on them.
def assign_price_scores(candidates: list[RankedProduct], budget_max: float | None) -> None:
    priced = [c for c in candidates if c.product.get("price") is not None]
    if not priced:
        return
    prices = [c.product["price"] for c in priced]
    min_price, max_price = min(prices), max(prices)
    span = max_price - min_price
    # every candidate costs about the same, so price cannot separate them and must not pretend to
    tied = min_price <= 0 or span / min_price < MEANINGFUL_SPREAD
    for candidate in priced:
        price = candidate.product["price"]
        raw_score = 1.0 if tied else (max_price - price) / span
        candidate.price_score = raw_score * over_budget_penalty(price, budget_max)


# spec weights, verbatim from the spec
def compute_final_score(ranked_product: RankedProduct) -> float:
    return (
        0.35 * ranked_product.spec_match
        + 0.25 * ranked_product.review_score
        + 0.20 * ranked_product.price_score
        + 0.10 * ranked_product.distance_score
        + 0.10 * ranked_product.nice_to_have_score
    )
