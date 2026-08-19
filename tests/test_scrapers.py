import pytest

from backend.scrapers import amazon, bestbuy, microcenter, target
from backend.scrapers.base import load_fixture, load_fixture_text
from backend.scrapers.browser import looks_blocked, page_text
from backend.services import trace

# every retailer prints the star rating on its search page, and for Best Buy and Amazon that
# is the only page of theirs that reliably loads, so the two rating keys are part of the
# search contract rather than something only the product page can answer
SEARCH_KEYS = {"name", "url", "price", "in_stock", "store_id", "distance_miles",
               "rating", "review_count"}
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


def test_amazon_rating_distribution():
    distribution = REVIEWS["amazon"]["rating_distribution"]
    assert set(distribution) == {"1", "2", "3", "4", "5"}
    assert sum(distribution.values()) == pytest.approx(1.0, abs=0.02)


# only Amazon publishes a star breakdown; the contract stays uniform
@pytest.mark.parametrize("retailer", ["bestbuy", "target"])
def test_no_distribution_from_the_others(retailer):
    assert REVIEWS[retailer]["rating_distribution"] is None


# both product pages already carry a model number, which is the strongest identity hint the
# judgment call gets
def test_model_numbers_come_from_the_existing_spec_parsers():
    assert SPECS["bestbuy"]["Model Number"] == "A1383H11-1"
    assert SPECS["amazon"]["Model Number"] == "C2046S"


# documented gaps: Target publishes no model number, and Best Buy's tiles carry none either,
# so cross-retailer identity has to come off the titles
def test_target_publishes_no_model_number():
    assert "Model Number" not in SPECS["target"]


def test_bestbuy_tiles_carry_no_model_number():
    for row in SEARCH_RESULTS["bestbuy"]:
        assert "model" not in " ".join(str(value) for value in row.values()).lower()


# what the LLM spec fallback is handed: the product page with the markup stripped
def test_page_text_is_plain_text():
    text = page_text(AMAZON_PRODUCT)
    assert text and "<" not in text


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


# the three failure kinds, on the saved captures and on literal pages. this is the tell the
# "no products" reply used to hide: a challenge page and a real page that would not parse are
# different problems with different fixes
SEARCH_OUTCOME_CASES = {
    "bestbuy capture": (bestbuy, BESTBUY_SEARCH, SEARCH_RESULTS["bestbuy"], trace.OK),
    "amazon capture": (amazon, AMAZON_SEARCH, SEARCH_RESULTS["amazon"], trace.OK),
    # PerimeterX/Akamai interstitials: short, or carrying the retailer's block wording
    "bestbuy challenge page": (bestbuy, "too short to be a real page", [], trace.BLOCKED),
    "amazon throttle page": (amazon, "x" * 5000 + "Sorry! Something went wrong!", [],
                             trace.BLOCKED),
    # a full page that parsed to nothing: the virtualized grid never hydrated, or the selectors
    # broke. either way it is not an empty shelf
    "bestbuy unhydrated grid": (bestbuy, "x" * 500000, [], trace.SELECTORS_RETURNED_NOTHING),
    # the retailer's own no-results wording, which is a real answer
    "bestbuy no results": (bestbuy, "x" * 5000 + "0 items", [], trace.OK_BUT_EMPTY),
    "amazon no results": (amazon, "x" * 5000 + "No results for gaming mouse", [],
                          trace.OK_BUT_EMPTY),
}


@pytest.mark.parametrize("case", SEARCH_OUTCOME_CASES)
def test_search_outcomes(case):
    module, html, rows, expected = SEARCH_OUTCOME_CASES[case]
    assert module.search_outcome(html, rows) == expected


def test_tcin_from_url():
    assert target.tcin_from_url("https://www.target.com/p/anker/-/A-91803760") == "91803760"
    assert target.tcin_from_url("https://www.target.com/s?searchTerm=x") is None


# the whole point of reading the rating off the search page: these are the pages that load.
# Best Buy's product page is Akamai-blocked and Amazon's is the first to be throttled, so a
# rating that needed a product page was a rating we mostly never got
@pytest.mark.parametrize("retailer", SEARCH_RESULTS)
def test_search_rows_carry_the_star_rating(retailer):
    rated = [row for row in SEARCH_RESULTS[retailer] if row["rating"] is not None]
    assert rated, f"{retailer} search parsed no ratings"
    for row in rated:
        assert 0.0 < row["rating"] <= 5.0
        assert row["review_count"] is None or row["review_count"] >= 0


# the abbreviated "(84.7K)" on the tile would lose 700 reviews; the aria-label is exact
def test_amazon_review_count_is_the_exact_number():
    counts = [row["review_count"] for row in SEARCH_RESULTS["amazon"]
              if row["review_count"]]
    assert 84753 in counts


# target sends 0.0 stars from 0 reviews for a product nobody has rated. taken at face value
# that is the worst-rated product in the set rather than an unrated one
def test_targets_zero_star_unrated_products_read_as_no_rating():
    unrated = {"tcin": "1", "item": {"product_description": {"title": "New Thing"}},
               "price": {"current_retail": 9.99},
               "ratings_and_reviews": {"statistics": {"rating": {"average": 0.0, "count": 0}}}}
    row = target.parse_search({"data": {"search": {"products": [unrated]}}})[0]
    assert row["rating"] is None
    assert row["review_count"] is None


# Micro Center is server-rendered and puts name, price and rating on the search tile, so one
# page load is the whole story: no product page, no hydration wait
def test_microcenter_search_parses_the_tile():
    rows = microcenter.parse_search(load_fixture_text("microcenter_search.html"))
    assert len(rows) == 24
    assert set(rows[0]) == SEARCH_KEYS
    for row in rows:
        assert row["name"]
        assert row["url"].startswith("https://www.microcenter.com/product/")
        assert row["price"] is None or isinstance(row["price"], float)
        assert row["rating"] is None or 0.0 < row["rating"] <= 5.0


# the tile's own href list starts with a sign-in redirect and a "#", so taking the first
# anchor would send the spec fetch to the login page
def test_microcenter_takes_the_product_link_not_the_first_link():
    tile = """<li class="product_wrapper">
      <a href="https://account.microcenter.com/auth/signin/?RedirectUrl=x">fav</a>
      <a href="#">compare</a>
      <a class="x" data-name="A Keyboard" data-price="59.99" href="/product/698072/a-keyboard">go</a>
    </li>"""
    row = microcenter.parse_search(tile)[0]
    assert row["url"] == "https://www.microcenter.com/product/698072/a-keyboard"


# same guard as the other scrapers: this url is handed to page.goto() for specs
def test_microcenter_drops_an_offsite_product_link():
    tile = """<li class="product_wrapper">
      <a data-name="X" data-price="1.00" href="https://evil.example.com/product/1/x">go</a>
    </li>"""
    assert microcenter.parse_search(tile) == []


# no spec table on the page: specs are label/value div pairs under group headings. and unlike
# Best Buy or Amazon this page actually loads for us, so these are specs we really get
def test_microcenter_product_page_parses_specs_and_rating():
    html = load_fixture_text("microcenter_product.html")
    specs = microcenter.parse_specs(html)
    assert specs["Type"] == "Mechanical"
    # the manufacturer part number is the strongest hint two listings are the same product
    assert specs["Mfr Part#"]
    assert microcenter.parse_reviews(html)["rating"] == 5.0
