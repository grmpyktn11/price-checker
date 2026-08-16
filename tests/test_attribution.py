import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.models import Review
from backend.services import reviews_store
from backend.services.attribution import (
    attribute_specs,
    brand_token,
    distinctive_shared,
    numbers_conflict,
    same_product,
)
from backend.services.ranking import (
    RankedProduct,
    attribute_reviews,
    compute_review_score,
    model_key,
    passes_must_haves,
)

# real fixture strings, as literals: these tests must not depend on fixture contents
BESTBUY_TITLE = "Anker - Power Bank (20K, 87W, Built-In USB-C Cable) - Black"
AMAZON_TITLE = "Anker Power Bank 20000mAh 87W Built-In USB-C Cable"
SAMSUNG_TITLE = "Samsung - Magnetic Wireless Battery Pack - Gray"
BRANDLESS_TITLE = "Portable Charger with Wall Plug, Slim USB C Power Bank"
AMAZON_SPECS = {"Battery Capacity": "20000 milliamp hours", "Model Number": "C2046S"}
AMAZON_ROW = {"source": "amazon", "rating": 4.2, "review_count": 226, "verified_ratio": None,
              "rating_distribution": None}


def make_candidate(retailer, name, specs=None, reviews=None):
    return RankedProduct(
        product={"name": name, "price": 99.99, "distance_miles": None},
        retailer=retailer,
        specs=dict(specs or {}),
        reviews=list(reviews or []),
        spec_match=0.0,
        review_score=0.0,
        nice_to_have_score=0.0,
        distance_score=0.5,
    )


def amazon_donor(name=AMAZON_TITLE, specs=None):
    return make_candidate("amazon", name, specs or AMAZON_SPECS, [dict(AMAZON_ROW)])


# --- part 1: review identity, exact model number ---

def test_exact_model_match_inherits():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, {"Model Number": "C2046S"})
    donor = amazon_donor()
    attribute_reviews([taker, donor])
    row = taker.reviews[0]
    assert row["rating"] == 4.2
    assert row["review_count"] == 226
    assert row["source"] == "amazon_inherited"
    assert row["inherited_from_retailer"] == "amazon"
    # a copy, so the two rows can carry different authenticity flags
    assert row is not donor.reviews[0]
    assert donor.reviews[0]["source"] == "amazon"


def test_no_model_match_inherits_nothing():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, {"Model Number": "A1383H11-1"})
    attribute_reviews([taker, amazon_donor()])
    assert taker.reviews == []


@pytest.mark.parametrize(
    "left,right",
    [("A1383H11-1", "A1383H11-2"), ("C2046S", "C2046"), ("C2046S", "C2046T"), ("X100", "X1000")],
)
def test_variant_model_numbers_stay_distinct(left, right):
    assert model_key({"Model Number": left}) != model_key({"Model Number": right})


@pytest.mark.parametrize(
    "left,right", [("a1383h11-1", "A1383H11 1"), ("A-1383", "A1383")]
)
def test_separators_and_case_normalize_away(left, right):
    assert model_key({"Model Number": left}) == model_key({"Model Number": right})


@pytest.mark.parametrize("specs", [{}, {"Model Number": "N/A"}, {"Model Number": "AB"}])
def test_unusable_model_keys(specs):
    assert model_key(specs) is None


def test_first_party_rating_is_never_replaced():
    own = {"source": "bestbuy", "rating": 3.1, "review_count": 12}
    taker = make_candidate("bestbuy", BESTBUY_TITLE, {"Model Number": "C2046S"}, [own])
    attribute_reviews([taker, amazon_donor()])
    assert taker.reviews == [own]


def test_the_donor_with_more_reviews_wins():
    small = make_candidate("target", "x", AMAZON_SPECS,
                           [{"source": "target", "rating": 2.0, "review_count": 5}])
    taker = make_candidate("bestbuy", BESTBUY_TITLE, {"Model Number": "C2046S"})
    attribute_reviews([taker, small, amazon_donor()])
    assert taker.reviews[0]["review_count"] == 226


def test_inherited_rows_are_distinguishable_in_the_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # autoflush off, same as the app's SessionLocal, so a missing flush fails here too
    db = sessionmaker(bind=engine, autoflush=False)()
    taker = make_candidate("bestbuy", BESTBUY_TITLE, {"Model Number": "C2046S"})
    attribute_reviews([taker, amazon_donor()])
    reviews_store.save_reviews(db, 1, taker.reviews)
    assert db.query(Review).filter(Review.source == "amazon").count() == 0
    assert db.query(Review).filter(Review.source == "amazon_inherited").count() == 1
    db.close()


# --- part 2: spec identity, titles ---

def test_matching_titles_inherit_specs():
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    donor = amazon_donor()
    attribute_specs([taker, donor])
    assert taker.specs == donor.specs
    assert taker.specs is not donor.specs
    assert taker.specs_inherited_from == "amazon"
    assert donor.specs_inherited_from is None
    taker.specs["Battery Capacity"] = "changed"
    assert donor.specs["Battery Capacity"] == "20000 milliamp hours"


def test_differing_capacity_does_not_inherit():
    a, b = "Anker 737 Power Bank 24,000 mAh", "Anker 737 Power Bank 20,000 mAh"
    assert numbers_conflict(a, b) is True
    assert same_product(a, b) is False
    taker = make_candidate("bestbuy", a)
    attribute_specs([taker, amazon_donor(b)])
    assert taker.specs == {}


def test_differing_model_digits_does_not_inherit():
    assert same_product("Anker 737 Power Bank", "Anker 733 Power Bank") is False


def test_different_brand_does_not_inherit():
    assert same_product(SAMSUNG_TITLE, AMAZON_TITLE) is False


def test_a_brandless_title_never_matches():
    assert brand_token(BRANDLESS_TITLE) is None
    assert same_product(BRANDLESS_TITLE, AMAZON_TITLE) is False
    assert same_product(AMAZON_TITLE, BRANDLESS_TITLE) is False


def test_first_party_specs_are_never_overridden():
    own = {"Battery Capacity": "20000 mAh"}
    taker = make_candidate("bestbuy", BESTBUY_TITLE, own)
    attribute_specs([taker, amazon_donor()])
    assert taker.specs == own
    assert taker.specs_inherited_from is None


def test_ambiguous_donors_resolve_to_a_miss(caplog):
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    with caplog.at_level(logging.INFO):
        attribute_specs([taker, amazon_donor(), amazon_donor()])
    assert taker.specs == {}
    assert "ambiguous spec donor" in caplog.text


def test_inheritance_is_logged_with_both_titles(caplog):
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    with caplog.at_level(logging.INFO):
        attribute_specs([taker, amazon_donor()])
    assert BESTBUY_TITLE in caplog.text and AMAZON_TITLE in caplog.text


def test_no_transitive_inheritance():
    first = make_candidate("bestbuy", BESTBUY_TITLE)
    second = make_candidate("target", BESTBUY_TITLE)
    attribute_specs([first, second, amazon_donor()])
    # exactly one of them may inherit from the single real donor, and neither from the other
    assert [c.specs_inherited_from for c in (first, second)].count("bestbuy") == 0
    assert [c.specs_inherited_from for c in (first, second)].count("target") == 0


@pytest.mark.parametrize(
    "rule,expected",
    [
        ({"field": "Battery Capacity", "op": ">=", "value": 20000}, True),
        ({"field": "Battery Capacity", "op": ">=", "value": 24000}, False),
    ],
)
def test_must_haves_behave_identically_on_inherited_specs(rule, expected):
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    donor = amazon_donor()
    attribute_specs([taker, donor])
    assert passes_must_haves(taker.specs, [rule]) is expected
    assert passes_must_haves(donor.specs, [rule]) is expected


# --- the rails in isolation ---

@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("87W", "140W", True),
        ("24,000 mAh", "20000mAh", True),
        # 20K expands to 20000, so these agree rather than conflict
        ("20K, 87W", "20000mAh 87W", False),
        # one bare pool empty is no evidence, not a conflict
        ("Anker 737", "Anker Power Bank", False),
    ],
)
def test_numbers_conflict(a, b, expected):
    assert numbers_conflict(a, b) is expected


def test_distinctive_shared_ignores_stopwords():
    assert distinctive_shared("Anker Portable Charger Black", "Anker Power Bank Black",
                              "anker") == set()


def test_distinctive_shared_sees_scaled_numbers():
    assert "20000" in distinctive_shared(BESTBUY_TITLE, AMAZON_TITLE, "anker")


# --- the amendment: a title match may also carry the donor's rating ---

def test_title_inheritance_also_inherits_the_rating():
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    donor = amazon_donor()
    attribute_specs([taker, donor])
    attribute_reviews([taker, donor])
    row = taker.reviews[0]
    assert row["rating"] == 4.2
    # a distinct marker: title identity must not read as model-number identity
    assert row["source"] == "amazon_title_inherited"
    assert row["source"] != "amazon_inherited"
    assert row["source"].endswith("_inherited")


def test_title_inherited_specs_never_donate_a_model_number():
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    donor = amazon_donor()
    attribute_specs([taker, donor])
    # the donor is gone from this run, so the title-inheritor is the only candidate holding
    # C2046S: if it were allowed to donate, third would inherit on a laundered title match
    third = make_candidate("target", "Some Other Product", {"Model Number": "C2046S"})
    attribute_reviews([taker, third])
    assert taker.specs["Model Number"] == "C2046S"
    assert third.reviews == []


# with a real first-party holder present, the same candidate does inherit: the guard is about
# provenance, not about blocking the model-number path
def test_model_number_path_still_works_alongside_a_title_inheritor():
    taker = make_candidate("bestbuy", BESTBUY_TITLE)
    donor = amazon_donor()
    third = make_candidate("target", "Some Other Product", {"Model Number": "C2046S"})
    attribute_specs([taker, donor])
    attribute_reviews([taker, third, donor])
    assert third.reviews[0]["source"] == "amazon_inherited"
    assert taker.reviews[0]["source"] == "amazon_title_inherited"


def test_title_inherited_ratings_are_discounted():
    model_matched = [{**AMAZON_ROW, "source": "amazon_inherited"}]
    title_matched = [{**AMAZON_ROW, "source": "amazon_title_inherited"}]
    first_party = [dict(AMAZON_ROW)]
    assert compute_review_score(model_matched) == compute_review_score(first_party)
    assert compute_review_score(title_matched) == pytest.approx(
        compute_review_score(first_party) * 0.9, abs=1e-9
    )


# --- the documented limit (attribution.py, above same_product): pinned, not endorsed ---

# the titles are silent about the difference, so no rail can see it
def test_documented_limit_titles_silent_about_the_difference():
    assert same_product("Anker Power Bank 20,000mAh",
                        "Anker Zolo Power Bank 20,000mAh") is True


# --- a scale-suffixed capacity is compared against a united one ---

# "10K" is bare and "20000mAh" is capacity. without the bare-against-all comparison the pools
# never meet, the shared "87w" token carries rail 3, and a 10,000mAh product inherits the
# 20,000mAh specs and passes a "capacity >= 20000" must_have it fails
def test_scale_suffixed_capacity_conflicts_with_a_united_one():
    ten_k = "Anker - Power Bank (10K, 87W, Built-In USB-C Cable) - Black"
    assert numbers_conflict(ten_k, AMAZON_TITLE) is True
    assert same_product(ten_k, AMAZON_TITLE) is False


# the hard filter itself, end to end: the wrong donor must not reach must_haves
def test_a_mismatched_capacity_never_reaches_the_hard_filter():
    taker = make_candidate("bestbuy", "Anker - Power Bank (10K, 87W, Built-In USB-C Cable) - Black")
    attribute_specs([taker, amazon_donor()])
    assert taker.specs == {}
    assert taker.specs_inherited_from is None
    assert passes_must_haves(
        taker.specs, [{"field": "Battery Capacity", "op": ">=", "value": 20000}]
    ) is False


# the expansion still earns its keep: same stated capacity, written two ways, still matches
def test_scale_suffix_still_matches_the_same_capacity():
    assert numbers_conflict(BESTBUY_TITLE, AMAZON_TITLE) is False
    assert same_product(BESTBUY_TITLE, AMAZON_TITLE) is True
