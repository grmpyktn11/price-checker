import pytest

from backend.services.pipeline import filter_on_specs
from backend.services.ranking import (
    MIXED_SIGNAL_PENALTY,
    NEUTRAL_SCORE,
    SKEWED_DISTRIBUTION_PENALTY,
    SPEC_MATCH_INHERITED_PENALTY,
    RankedProduct,
    apply_authenticity_flags,
    assign_price_scores,
    build_query,
    compute_distance_score,
    compute_final_score,
    compute_review_score,
    compute_spec_match,
    distribution_is_skewed,
    find_spec_value,
    first_number,
    over_budget_penalty,
    passes_must_haves,
)

# the real Amazon fixture curve: a strong product, not a suspicious one
FIXTURE_DISTRIBUTION = {"5": 0.71, "4": 0.09, "3": 0.05, "2": 0.04, "1": 0.11}
SKEWED_DISTRIBUTION = {"5": 0.88, "4": 0.02, "3": 0.01, "2": 0.02, "1": 0.07}
AMAZON_ROW = {"source": "amazon", "rating": 4.7, "review_count": 1843, "verified_ratio": None,
              "rating_distribution": None}
EXTERNAL_ROWS = [
    {"source": source, "rating": None, "review_count": None, "verified_ratio": None,
     "summary_text": "text", "mention_count": 9, "authenticity_flag": "ok"}
    for source in ("reddit", "youtube")
]

# retailer spec strings, inline: these tests are about ranking math, not about any scraper
SPECS = {
    "Battery Capacity": "24,000 milliamp hours",
    "Product Weight": "1.4 pounds",
    "Number of USB Ports": "3",
    "Pass-Through Charging": "Yes",
    "Display Type": "Smart digital display",
}
PREFERRED_SPECS = [
    {"field": "Number of USB Ports", "op": ">=", "value": 3},
    {"field": "Product Weight", "op": "<=", "value": 1.0},
]

# real keys taken from the fixtures, in the order the scrapers emit them. Best Buy's set is
# complete at 6; Target and Amazon are trimmed from 18 and 33 to the keys these cases
# probe, relative order kept. copied, not imported, so these tests stay off the scrapers
BESTBUY_SPECS = {
    "Brand": "Anker",
    "Model Number": "A1383H11-1",
    "Product Name": "Power Bank (20K, 87W, Built-In USB-C Cable)",
    "Color": "Black",
    "Capacity": "20000 milliampere hours",
    "Battery Chemistry": "Lithium-ion",
}
TARGET_SPECS = {
    "Dimensions (Overall)": "2.36 Inches (H) x 4.92 Inches (W) x 6.89 Inches (D)",
    "Weight": "1.95 Pounds",
    "Battery Capacity": "24000 (mAh)",
    "Wattage Output": "140 Watts",
    "Battery": "1 Non-Universal Lithium Ion",
    "Generous Capacity, Exceptional Portability": "Empower your charging with 24,000 mAh",
}
AMAZON_SPECS = {
    "Battery Capacity": "20000 milliamp_hours",
    "Number of Ports": "5",
    "Output Wattage": "22.5 watts",
    "Item Dimensions L x W x Thickness": '5.91"L x 2.83"W x 1.09"Th',
    "Item Dimensions": "5.91 x 2.83 x 1.09 inches",
    "Battery Weight": "148 g",
    "Battery Cell Type": "Lithium Polymer",
}


# minimal candidate; only the fields the function under test reads matter
def make_candidate(price, spec_match=0.0, review_score=0.0):
    return RankedProduct(
        product={"name": "x", "price": price, "distance_miles": None},
        retailer="bestbuy",
        specs={},
        reviews=[],
        spec_match=spec_match,
        review_score=review_score,
        nice_to_have_score=0.5,
        distance_score=0.5,
    )


def test_build_query():
    assert build_query({"name": "portable charger", "keywords": ["usb-c", "140w"]}) == (
        "portable charger usb-c 140w"
    )
    assert build_query({"name": "Portable Charger"}) == "portable charger"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("24,000 milliamp hours", 24000.0),
        ("2.2 inches", 2.2),
        ("140 watts", 140.0),
        ("Smart digital display", None),
    ],
)
def test_first_number(raw, expected):
    assert first_number(raw) == expected


@pytest.mark.parametrize(
    "must_haves,expected",
    [
        ([{"field": "Battery Capacity", "op": ">=", "value": 20000}], True),
        ([{"field": "Product Weight", "op": "<=", "value": 1.0}], False),
        ([{"field": "Pass-Through Charging", "op": "contains", "value": "yes"}], True),
        ([{"field": "Waterproof", "op": "exists"}], False),
        ([], True),
    ],
)
def test_passes_must_haves(must_haves, expected):
    assert passes_must_haves(SPECS, must_haves) is expected


@pytest.mark.parametrize(
    "specs,field,expected",
    [
        # the reported bug: Best Buy prints a shorter name than the rule asks for
        (BESTBUY_SPECS, "Battery Capacity", "20000 milliampere hours"),
        # the second bug: neither substring direction matches, tokens do
        (AMAZON_SPECS, "Number of USB Ports", "5"),
        # exact match wins over the longer key that also contains it
        (AMAZON_SPECS, "Item Dimensions", "5.91 x 2.83 x 1.09 inches"),
        # fewest tokens beats the marketing soft bullet
        (TARGET_SPECS, "Capacity", "24000 (mAh)"),
        # Battery Weight is a different quantity, so no match at all
        (AMAZON_SPECS, "Product Weight", None),
        # punctuation and case are normalized away
        ({"Dimensions (Overall)": "x"}, "dimensions overall", "x"),
        # vague single-token field: fewest tokens, then insertion order
        (AMAZON_SPECS, "Battery", "20000 milliamp_hours"),
        (TARGET_SPECS, "Battery", "1 Non-Universal Lithium Ion"),
        # a field with no usable tokens must not match every key by empty-subset
        (TARGET_SPECS, "   ", None),
        (TARGET_SPECS, "()", None),
    ],
)
def test_find_spec_value(specs, field, expected):
    assert find_spec_value(specs, field) == expected


# end to end for the reported bug: the must_have now passes against Best Buy's Capacity key
def test_must_have_matches_a_shorter_retailer_spec_name():
    rule = [{"field": "Battery Capacity", "op": ">=", "value": 20000}]
    assert passes_must_haves(BESTBUY_SPECS, rule) is True


def test_compute_spec_match():
    assert compute_spec_match(SPECS, PREFERRED_SPECS) == pytest.approx(0.5, abs=1e-3)
    assert compute_spec_match(SPECS, []) == 1.0


@pytest.mark.parametrize(
    "rating,count,expected",
    [
        (4.7, 1843, 0.940),
        (4.4, 612, 0.853),
        (4.1, 238, 0.754),
        (None, 0, 0.500),
    ],
)
def test_compute_review_score(rating, count, expected):
    reviews = [{"source": "bestbuy", "rating": rating, "review_count": count, "verified_ratio": None}]
    assert compute_review_score(reviews) == pytest.approx(expected, abs=1e-3)


def test_compute_review_score_no_reviews():
    assert compute_review_score([]) == 0.5


@pytest.mark.parametrize(
    "distribution,expected",
    [
        (FIXTURE_DISTRIBUTION, False),
        (SKEWED_DISTRIBUTION, True),
        (None, False),
        # dominant 5-star alone is not enough: the hollow middle is the fake-review shape
        ({"5": 0.85, "4": 0.10, "3": 0.03, "2": 0.01, "1": 0.01}, False),
    ],
)
def test_distribution_is_skewed(distribution, expected):
    assert distribution_is_skewed(distribution) is expected


# measured data about this product beats an item-level sentiment signal
def test_skewed_distribution_wins_over_mixed_signal():
    row = {**AMAZON_ROW, "rating": 4.8, "rating_distribution": SKEWED_DISTRIBUTION}
    apply_authenticity_flags([row], "negative")
    assert row["authenticity_flag"] == "skewed_distribution"


def test_mixed_signal_flag():
    row = {**AMAZON_ROW, "rating": 4.8}
    apply_authenticity_flags([row], "negative")
    assert row["authenticity_flag"] == "mixed_signal"


# external rows carry no rating, so there is nothing to be suspicious about
def test_external_rows_are_always_ok():
    rows = [dict(row) for row in EXTERNAL_ROWS]
    apply_authenticity_flags(rows, "negative")
    assert {row["authenticity_flag"] for row in rows} == {"ok"}


@pytest.mark.parametrize(
    "flag,penalty",
    [("skewed_distribution", SKEWED_DISTRIBUTION_PENALTY),
     ("mixed_signal", MIXED_SIGNAL_PENALTY)],
)
def test_authenticity_penalties(flag, penalty):
    base = compute_review_score([dict(AMAZON_ROW)])
    flagged = compute_review_score([{**AMAZON_ROW, "authenticity_flag": flag}])
    assert flagged == pytest.approx(base * penalty, abs=1e-9)


def test_external_rows_cannot_move_the_score():
    assert compute_review_score([AMAZON_ROW, *EXTERNAL_ROWS]) == compute_review_score([AMAZON_ROW])


def test_no_retailer_row_is_neutral():
    assert compute_review_score(EXTERNAL_ROWS) == NEUTRAL_SCORE


# a model-number match is the same physical product, so the rating is not discounted
def test_model_inherited_rating_is_not_discounted():
    inherited = [{**AMAZON_ROW, "source": "amazon_inherited"}]
    assert compute_review_score(inherited) == compute_review_score([AMAZON_ROW])


# title identity is weaker evidence than a model number, so the soft score says so
def test_inherited_specs_are_discounted_in_spec_match():
    first_party = make_candidate(99.99)
    inherited = make_candidate(99.99)
    for candidate in (first_party, inherited):
        candidate.specs = SPECS
    inherited.specs_inherited_from = "amazon"
    filter_on_specs([first_party, inherited], [], PREFERRED_SPECS)
    assert inherited.spec_match == pytest.approx(
        first_party.spec_match * SPEC_MATCH_INHERITED_PENALTY, abs=1e-9
    )


@pytest.mark.parametrize(
    "distance,radius,expected",
    [(None, 25, 0.5), (0, 25, 1.0), (5, 25, 0.8), (25, 25, 0.0), (40, 25, 0.0), (5, 0, 0.5)],
)
def test_compute_distance_score(distance, radius, expected):
    assert compute_distance_score(distance, radius) == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize(
    "price,budget_max,expected",
    [
        (129.99, 150.0, 1.000),
        (150.00, 150.0, 1.000),
        (165.00, 150.0, 0.909),
        (189.99, 150.0, 0.790),
        (300.00, 150.0, 0.500),
        (300.00, None, 1.000),
    ],
)
def test_over_budget_penalty(price, budget_max, expected):
    assert over_budget_penalty(price, budget_max) == pytest.approx(expected, abs=1e-3)


def test_assign_price_scores_no_budget():
    candidates = [make_candidate(p) for p in (129.99, 39.99, 99.99, 24.99)]
    assign_price_scores(candidates, None)
    scores = [c.price_score for c in candidates]
    assert scores == pytest.approx([0.0, 0.857, 0.286, 1.0], abs=1e-3)


def test_assign_price_scores_with_budget():
    candidates = [make_candidate(p) for p in (189.99, 99.99, 39.99)]
    assign_price_scores(candidates, 150.0)
    scores = [c.price_score for c in candidates]
    assert scores == pytest.approx([0.0, 0.6, 1.0], abs=1e-3)


# a budget only changes the score of candidates above it
def test_budget_does_not_change_under_budget_scores():
    prices = (129.99, 39.99, 99.99, 24.99)
    without = [make_candidate(p) for p in prices]
    with_budget = [make_candidate(p) for p in prices]
    assign_price_scores(without, None)
    assign_price_scores(with_budget, 150.0)
    assert [c.price_score for c in with_budget] == pytest.approx(
        [c.price_score for c in without], abs=1e-9
    )


def test_assign_price_scores_edge_cases():
    single = [make_candidate(99.99)]
    assign_price_scores(single, None)
    assert single[0].price_score == 1.0

    equal = [make_candidate(50.0), make_candidate(50.0)]
    assign_price_scores(equal, None)
    assert [c.price_score for c in equal] == [1.0, 1.0]

    # unpriced candidates are skipped and keep 0.0
    mixed = [make_candidate(None), make_candidate(10.0), make_candidate(20.0)]
    assign_price_scores(mixed, None)
    assert [c.price_score for c in mixed] == pytest.approx([0.0, 1.0, 0.0], abs=1e-3)


def test_compute_final_score_no_budget():
    anker = make_candidate(129.99, spec_match=0.5, review_score=0.940)
    belkin = make_candidate(39.99, spec_match=0.0, review_score=0.853)
    # the other two fixture prices set the span the price scores are normalized over
    candidates = [anker, belkin, make_candidate(99.99), make_candidate(24.99)]
    assign_price_scores(candidates, None)
    assert compute_final_score(anker) == pytest.approx(0.510, abs=1e-3)
    assert compute_final_score(belkin) == pytest.approx(0.485, abs=1e-3)


# budget_max is a penalty, not a filter: the over-budget product still ranks first
def test_over_budget_product_still_wins():
    anker = make_candidate(189.99, spec_match=1.0, review_score=0.940)
    mophie = make_candidate(99.99, spec_match=0.5, review_score=0.754)
    belkin = make_candidate(39.99, spec_match=0.0, review_score=0.853)
    candidates = [anker, mophie, belkin]
    assign_price_scores(candidates, 150.0)
    for candidate in candidates:
        candidate.final_score = compute_final_score(candidate)
    ranked = sorted(candidates, key=lambda c: c.final_score, reverse=True)

    assert ranked[0] is anker
    assert [c.final_score for c in ranked] == pytest.approx([0.685, 0.584, 0.513], abs=1e-3)
