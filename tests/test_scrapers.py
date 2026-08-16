import pytest

from backend.scrapers import amazon, bestbuy, target
from backend.scrapers.base import load_fixture, load_fixture_text
from backend.scrapers.browser import looks_blocked

SEARCH_KEYS = {"name", "url", "price", "in_stock", "store_id", "distance_miles"}
BIDI_MARKS = ("‎", "‏")

BESTBUY_SEARCH = load_fixture_text("bestbuy_search.html")
# captured from a warm browser session: the live headless path is blocked before it
# reaches a Best Buy product page, so these two parsers are fixture-only for now
BESTBUY_PRODUCT = load_fixture_text("bestbuy_product.html")
AMAZON_SEARCH = load_fixture_text("amazon_search.html")
AMAZON_PRODUCT = load_fixture_text("amazon_product.html")
TARGET_SEARCH = load_fixture("target_search.json")
TARGET_PDP = load_fixture("target_pdp.json")
TARGET_STORES = load_fixture("target_stores.json")

SEARCH_RESULTS = {
    "bestbuy": bestbuy.parse_search(BESTBUY_SEARCH),
    "amazon": amazon.parse_search(AMAZON_SEARCH),
    "target": target.parse_search(TARGET_SEARCH),
}
SPECS = {
    "bestbuy": bestbuy.parse_specs(BESTBUY_PRODUCT),
    "amazon": amazon.parse_specs(AMAZON_PRODUCT),
    "target": target.parse_specs(TARGET_PDP),
}
REVIEWS = {
    "bestbuy": bestbuy.parse_reviews(BESTBUY_PRODUCT),
    "amazon": amazon.parse_reviews(AMAZON_PRODUCT),
    "target": target.parse_reviews(TARGET_PDP),
}


@pytest.mark.parametrize("retailer", SEARCH_RESULTS)
def test_search_rows_match_the_contract(retailer):
    rows = SEARCH_RESULTS[retailer]
    assert rows
    for row in rows:
        assert set(row) == SEARCH_KEYS
        assert row["name"]
        assert row["url"].startswith("https://")
        assert row["price"] is None or isinstance(row["price"], float)


def test_amazon_urls_are_built_from_the_asin():
    for row in SEARCH_RESULTS["amazon"]:
        assert row["url"].startswith("https://www.amazon.com/dp/")
        assert "/ref=" not in row["url"]


def test_bestbuy_urls_carry_a_sku():
    for row in SEARCH_RESULTS["bestbuy"]:
        assert bestbuy.sku_from_url(row["url"]).isdigit()


@pytest.mark.parametrize("retailer", SPECS)
def test_specs_are_clean_strings(retailer):
    specs = SPECS[retailer]
    assert specs
    for name, value in specs.items():
        assert isinstance(name, str) and isinstance(value, str)
        assert "<" not in value
        for mark in BIDI_MARKS:
            assert mark not in name and mark not in value


@pytest.mark.parametrize("retailer", REVIEWS)
def test_reviews_are_numbers(retailer):
    data = REVIEWS[retailer]
    assert 0.0 <= data["rating"] <= 5.0
    assert isinstance(data["review_count"], int)
    # no MVP source publishes a verified-purchase ratio
    assert data["verified_ratio"] is None


def test_target_stores_have_string_ids():
    stores = target.parse_stores(TARGET_STORES)
    assert stores
    for store in stores:
        assert isinstance(store["store_id"], str)
        assert isinstance(store["distance_miles"], float)


def test_looks_blocked():
    assert looks_blocked("too short to be a real page", bestbuy.BLOCK_MARKERS)
    assert looks_blocked("x" * 5000 + "Access Denied", bestbuy.BLOCK_MARKERS)
    assert looks_blocked("x" * 5000 + "Sorry! Something went wrong!", amazon.BLOCK_MARKERS)
    assert not looks_blocked(BESTBUY_SEARCH, bestbuy.BLOCK_MARKERS)
    assert not looks_blocked(AMAZON_SEARCH, amazon.BLOCK_MARKERS)


def test_tcin_from_url():
    assert target.tcin_from_url("https://www.target.com/p/anker/-/A-91803760") == "91803760"
    assert target.tcin_from_url("https://www.target.com/s?searchTerm=x") is None
