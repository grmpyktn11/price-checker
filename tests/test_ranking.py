import pytest

from backend.services.ranking import (
    INHERITED_RATING_PENALTY,
    MIXED_SIGNAL_PENALTY,
    NEUTRAL_SCORE,
    SKEWED_DISTRIBUTION_PENALTY,
    RankedProduct,
    apply_authenticity_flags,
    assign_price_scores,
    collapse_variants,
    build_query,
    compute_distance_score,
    compute_final_score,
    compute_review_score,
    distribution_is_skewed,
    first_number,
    over_budget_penalty,
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

# minimal candidate; only the fields the function under test reads matter
def make_candidate(price, spec_match=0.0, review_score=0.0, group=None):
    return RankedProduct(
        product={"name": "x", "url": "https://example.com/x", "price": price,
                 "distance_miles": None},
        retailer="bestbuy",
        specs={},
        reviews=[],
        spec_match=spec_match,
        review_score=review_score,
        nice_to_have_score=0.5,
        distance_score=0.5,
        group=group,
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


# the model grouped two listings by reading their titles, which is weaker than the retailer's
# own feed, so an inherited rating is discounted
def test_inherited_rating_is_discounted():
    inherited = [{**AMAZON_ROW, "source": "amazon_inherited"}]
    assert compute_review_score(inherited) == pytest.approx(
        compute_review_score([AMAZON_ROW]) * INHERITED_RATING_PENALTY, abs=1e-9
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


# a real search returned two keyboards at $60.04 and $60.09 and scored them 1.0 and 0.0.
# five cents is not a price difference, and 20% of the ranking must not turn on it
def test_prices_within_noise_of_each_other_all_score_full():
    near = [make_candidate(60.04), make_candidate(60.09)]
    assign_price_scores(near, None)
    assert [c.price_score for c in near] == [1.0, 1.0]


# but a spread that is real still separates them, which is the whole point of the score
def test_a_real_spread_still_separates():
    spread = [make_candidate(60.0), make_candidate(120.0)]
    assign_price_scores(spread, None)
    assert [c.price_score for c in spread] == pytest.approx([1.0, 0.0], abs=1e-9)


# the tie is judged before the budget penalty, so a cluster that is all over budget is still
# scored down rather than every candidate quietly getting full marks
def test_a_tied_cluster_over_budget_is_still_penalised():
    over = [make_candidate(200.0), make_candidate(201.0)]
    assign_price_scores(over, 100.0)
    assert all(c.price_score < 0.55 for c in over)


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


# the pink and the white Womier Q61 PRO came back as two separate recommendations. they are
# one product to a shopper, so the better-scoring one is shown and the other becomes a variant
def test_colour_variants_collapse_to_one_recommendation():
    pink = make_candidate(60.04, group="g1")
    pink.product["name"] = "Womier Q61 PRO - Pink"
    pink.final_score = 0.87
    white = make_candidate(60.09, group="g1")
    white.product["name"] = "Womier Q61 PRO - White"
    white.final_score = 0.67

    kept = collapse_variants([pink, white])
    assert [c.product["name"] for c in kept] == ["Womier Q61 PRO - Pink"]
    assert kept[0].variants == [
        {"name": "Womier Q61 PRO - White", "url": white.product["url"],
         "price": 60.09, "retailer": white.retailer}
    ]


# no group means the model said nothing about identity, which is not evidence that two
# listings are the same thing. collapsing on it would silently hide real alternatives
def test_ungrouped_candidates_are_never_collapsed():
    first, second = make_candidate(10.0), make_candidate(20.0)
    assert len(collapse_variants([first, second])) == 2
