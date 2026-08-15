import pytest

from backend.scrapers.base import load_fixture
from backend.scrapers.bestbuy import parse_details
from backend.services.ranking import (
    RankedProduct,
    assign_price_scores,
    build_query,
    compute_distance_score,
    compute_final_score,
    compute_review_score,
    compute_spec_match,
    first_number,
    over_budget_penalty,
    passes_must_haves,
)

SPECS = parse_details(load_fixture("bestbuy_details.json"))
PREFERRED_SPECS = [
    {"field": "Number of USB Ports", "op": ">=", "value": 3},
    {"field": "Product Weight", "op": "<=", "value": 1.0},
]


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
