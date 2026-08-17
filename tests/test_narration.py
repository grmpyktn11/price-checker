import asyncio

import pytest

from backend.services import trace
from backend.services.narration import (
    any_retailer_answered,
    canned_narration,
    narrate,
    retailers_down_narration,
)
from backend.services.ranking import RankedProduct

CRITERIA = {"name": "portable charger"}


def build(name, price, final_score):
    return RankedProduct(
        product={"name": name, "url": "https://example.com/1", "price": price},
        retailer="bestbuy",
        specs={},
        reviews=[],
        spec_match=0.5,
        review_score=0.5,
        nice_to_have_score=0.5,
        distance_score=0.5,
        final_score=final_score,
    )


def test_canned_narration_two_products():
    ranked = [build("Anker 737", 129.99, 0.712), build("Belkin BoostCharge", 39.99, 0.66)]
    assert canned_narration(CRITERIA, ranked) == (
        "Found 2 options for portable charger. "
        "Best match: Anker 737 at $129.99 from bestbuy.\n"
        "1. Anker 737 - $129.99 - bestbuy - score 0.71\n"
        "2. Belkin BoostCharge - $39.99 - bestbuy - score 0.66"
    )


def test_canned_narration_empty():
    assert canned_narration(CRITERIA, []) == (
        "No products matched your criteria for portable charger."
    )


def test_canned_narration_missing_price():
    line = canned_narration(CRITERIA, [build("Anker 737", None, 0.5)])
    assert "price unavailable" in line


# --- a failed search is not an empty shelf ---

# every retailer failed, so nothing was learned about the market. the old reply here was
# "No products matched your criteria", which is a claim the run never earned
def test_retailers_down_narration_names_what_failed():
    text = retailers_down_narration(CRITERIA, {
        "bestbuy": trace.SELECTORS_RETURNED_NOTHING,
        "target": trace.BLOCKED,
        "amazon": trace.BLOCKED,
    })
    assert text == (
        "The search for portable charger did not run: bestbuy returned a page we could not "
        "read, target blocked us, amazon blocked us. That is a retailer failure, not a finding "
        "about what exists - nothing here says the product is unavailable. Try again in a few "
        "minutes."
    )


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        ({"bestbuy": trace.BLOCKED, "target": trace.ERROR}, False),
        # an empty result set is an answer: the shelf really is empty for this query
        ({"bestbuy": trace.BLOCKED, "target": trace.OK_BUT_EMPTY}, True),
        ({"bestbuy": trace.OK}, True),
        ({}, False),
    ],
)
def test_any_retailer_answered(outcomes, expected):
    assert any_retailer_answered(outcomes) == expected


# the short circuit: no model call, no "nothing matched"
def test_narrate_reports_the_failure_instead_of_calling_the_model():
    text = asyncio.run(narrate(CRITERIA, [], {"target": trace.BLOCKED, "amazon": trace.BLOCKED}))
    assert text == retailers_down_narration(CRITERIA, {"target": trace.BLOCKED,
                                                       "amazon": trace.BLOCKED})
