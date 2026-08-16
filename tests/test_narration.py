from backend.services.narration import canned_narration
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
