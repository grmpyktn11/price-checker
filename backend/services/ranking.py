import logging
import re
from dataclasses import dataclass
from math import log10

logger = logging.getLogger(__name__)

FULL_CONFIDENCE_REVIEW_COUNT = 1000   # review count where rating is trusted at face value
NEUTRAL_SCORE = 0.5                   # used wherever a signal is missing entirely
MODEL_KEY_MIN_LENGTH = 3
MODEL_KEY_BLOCKLIST = ("na", "n/a", "none", "unknown", "doesnotapply", "notapplicable")
# an inherited review row keeps the donor's retailer in its source plus a suffix recording
# which identity rule matched: "<origin>_inherited" for exact model-number equality, and
# "<origin>_title_inherited" for the weaker title match that spec inheritance used. both end
# in _inherited, so "is this row first-party" stays one endswith check
INHERITED_SUFFIX = "_inherited"
TITLE_INHERITED_SUFFIX = "_title_inherited"
SPEC_MATCH_INHERITED_PENALTY = 0.9    # title identity is weaker evidence than a model number
# a title-matched rating gets the same 0.9 haircut as a title-matched spec_match, for the same
# reason: same evidence, so it must not be trusted more than the specs it arrived with
TITLE_INHERITED_RATING_PENALTY = 0.9
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
    # the donor object itself, run-local and never serialized: attribute_reviews needs it to
    # inherit the same donor's rating on the same title evidence
    specs_donor: "RankedProduct | None" = None


# name plus keywords, lowercased, whitespace collapsed. must_haves are filters, not search terms
def build_query(criteria: dict) -> str:
    parts = [criteria["name"], *criteria.get("keywords", [])]
    return re.sub(r"\s+", " ", " ".join(parts)).strip().lower()


# lowercase, punctuation to spaces, split. "Dimensions (Overall)" -> ("dimensions", "overall")
def normalize_spec_name(name: str) -> tuple[str, ...]:
    return tuple(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


# retailers print the same spec under different names: Best Buy's "Capacity" is Amazon's
# "Battery Capacity". exact normalized match first, then any key whose tokens are a subset
# or superset of the field's, fewest tokens winning and insertion order breaking ties
# (scrapers build specs in page order, so real spec tables come before marketing bullets).
# limits, accepted:
# - no stemming: "Port" will not match "Ports"
# - a vague one-word field ("Battery") has several plausible answers and picks one
# - specs that merely share a token can still match wrongly; fail-closed only guards misses
def find_spec_value(specs: dict, field: str) -> str | None:
    wanted = normalize_spec_name(field)
    # a field with no usable tokens would be a subset of every key
    if not wanted:
        return None
    keys = {key: tokens for key in specs if (tokens := normalize_spec_name(key))}
    for key, tokens in keys.items():
        if tokens == wanted:
            return str(specs[key])
    pool = [key for key, tokens in keys.items()
            if set(tokens) <= set(wanted) or set(tokens) >= set(wanted)]
    if not pool:
        return None
    chosen = min(pool, key=lambda key: len(keys[key]))
    if len(pool) > 1:
        logger.debug("spec field %r matched %s, chose %r", field, pool, chosen)
    return str(specs[chosen])


# first number in the string, commas stripped. "24,000 milliamp hours" -> 24000.0
def first_number(raw: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


# limits, by design and not worked around:
# - no unit conversion; the criteria value must use the unit the retailer prints
# - only the first number in the string is read ("5.1 x 2.1 inches" -> 5.1)
# - cross-retailer spec names are matched on tokens by find_spec_value, not on meaning
def spec_passes(specs: dict, rule: dict) -> bool:
    raw = find_spec_value(specs, rule["field"])
    # fail closed: a missing spec is never satisfied
    if not raw:
        return False
    op = rule["op"]
    if op == "exists":
        return True
    if op == "contains":
        return str(rule["value"]).lower() in raw.lower()
    number = first_number(raw)
    if number is None:
        return False
    target = float(rule["value"])
    if op == ">=":
        return number >= target
    if op == "<=":
        return number <= target
    if op == "==":
        return number == target
    return False


# hard filter: every rule must pass, empty list passes
def passes_must_haves(specs: dict, must_haves: list[dict]) -> bool:
    return all(spec_passes(specs, rule) for rule in must_haves)


# soft criteria: fraction of preferred specs satisfied. nothing preferred -> no penalty
def compute_spec_match(specs: dict, preferred_specs: list[dict]) -> float:
    if not preferred_specs:
        return 1.0
    satisfied = sum(1 for rule in preferred_specs if spec_passes(specs, rule))
    return satisfied / len(preferred_specs)


# uppercase, drop whitespace and hyphens, nothing else: no trailing-letter stripping, no
# prefix matching, no edit distance. "a1383h11-1" -> "A1383H111", and A1383H11-2 stays different
def model_key(specs: dict) -> str | None:
    raw = find_spec_value(specs, "Model Number")
    if not raw:
        return None
    key = re.sub(r"[\s-]+", "", raw).upper()
    if len(key) < MODEL_KEY_MIN_LENGTH or raw.strip().lower() in MODEL_KEY_BLOCKLIST:
        return None
    return key


# the candidate's own retailer row, which is the only row that can carry a first-party rating
def first_party_rating_row(candidate: RankedProduct) -> dict | None:
    for row in candidate.reviews:
        if row.get("source") == candidate.retailer and row.get("rating") is not None:
            return row
    return None


# a copy, not the shared object: the two rows can end up with different authenticity flags
def inherited_row(row: dict, suffix: str) -> dict:
    return {**row, "source": f"{row['source']}{suffix}", "inherited_from_retailer": row["source"]}


# star ratings are product-level, not listing-level, so a candidate with no first-party rating
# may take another candidate's when both describe the same product. two identity strengths:
# exact model number (the strict rule), and the same title match that supplied its specs
def attribute_reviews(candidates: list[RankedProduct]) -> None:
    donors: dict[str, dict] = {}
    for candidate in candidates:
        # inherited specs carry the donor's model number: reading it here would launder a
        # title match into the exact-model-number rule, so inheritors never donate this way
        if candidate.specs_inherited_from:
            continue
        key, row = model_key(candidate.specs), first_party_rating_row(candidate)
        if not key or not row:
            continue
        best = donors.get(key)
        # whichever source has the most reviews wins a key collision
        if best is None or (row.get("review_count") or 0) > (best.get("review_count") or 0):
            donors[key] = row
    for candidate in candidates:
        if first_party_rating_row(candidate):
            continue
        if candidate.specs_inherited_from:
            inherit_from_spec_donor(candidate)
            continue
        key = model_key(candidate.specs)
        row = donors.get(key) if key else None
        if row:
            candidate.reviews.insert(0, inherited_row(row, INHERITED_SUFFIX))


# same identity claim as the specs it already inherited, and marked distinctly so the weaker
# evidence is visible in stored data
def inherit_from_spec_donor(candidate: RankedProduct) -> None:
    donor = candidate.specs_donor
    row = first_party_rating_row(donor) if donor else None
    if row:
        candidate.reviews.insert(0, inherited_row(row, TITLE_INHERITED_SUFFIX))


# a high 5-star share alone is normal for a good product; the hollow middle is the actual
# fake-review shape, so both conditions must hold
def distribution_is_skewed(distribution: dict | None) -> bool:
    if not distribution:
        return False
    five = distribution.get("5") or 0.0
    middle = sum(distribution.get(star) or 0.0 for star in ("2", "3", "4"))
    return five >= FIVE_STAR_DOMINANCE and middle <= HOLLOW_MIDDLE_MAX


# mutates authenticity_flag in place. precedence: skewed_distribution wins over mixed_signal,
# because it is measured data about this exact product while the sentiment signal is
# item-level and weaker. nothing ever writes suspicious_velocity: no source supplies a
# listing age, so the velocity heuristic has no input
def apply_authenticity_flags(reviews: list[dict], external_sentiment: str | None) -> None:
    # imported here, not at module level: sentiment -> criteria -> ranking is an import cycle
    from backend.services.sentiment import contradicts

    for row in reviews:
        rating = row.get("rating")
        if rating is None:
            row["authenticity_flag"] = "ok"
        elif distribution_is_skewed(row.get("rating_distribution")):
            row["authenticity_flag"] = "skewed_distribution"
        elif contradicts(external_sentiment, rating):
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
    # a model-number-inherited rating is not discounted (same physical product), a
    # title-inherited one is: the evidence is a marketing title, not a manufacturer identifier
    if str(primary.get("source", "")).endswith(TITLE_INHERITED_SUFFIX):
        score *= TITLE_INHERITED_RATING_PENALTY
    return max(0.0, min(1.0, score))


# online-only listings have no distance: neither near nor far, so neutral rather than worst
def compute_distance_score(distance_miles: float | None, radius_miles: int) -> float:
    if distance_miles is None or not radius_miles:
        return NEUTRAL_SCORE
    return max(0.0, 1.0 - distance_miles / radius_miles)


# ratio penalty: 10 percent over budget costs 9 percent of the price score, 2x over costs half
def over_budget_penalty(price: float, budget_max: float | None) -> float:
    if not budget_max or price <= budget_max:
        return 1.0
    return budget_max / price


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
    for candidate in priced:
        price = candidate.product["price"]
        raw_score = 1.0 if span == 0 else (max_price - price) / span
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
