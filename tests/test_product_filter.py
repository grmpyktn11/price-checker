import asyncio
import json

import pytest

from backend.services.pipeline import (
    AMAZON_REVIEW_LOOKUPS_PER_RUN,
    AMAZON_REVIEW_SEARCHES_PER_RUN,
    amazon_review_tiles,
    judgment_payload,
    lookup_missing_reviews,
)
from backend.services.product_filter import NEUTRAL_FIT, parse_reply, requirements
from backend.services.ranking import (
    INHERITED_RATING_PENALTY,
    SPEC_MATCH_INHERITED_PENALTY,
    RankedProduct,
    compute_review_score,
    inherit_reviews,
    inherit_specs,
)

# nothing here calls the model. the tests below are either pure transformations of a reply
# handed in as a literal, or the pure joins the group ids drive. how the model actually judges
# a product is verified by talking to it, not by asserting against a stubbed answer
BESTBUY_TITLE = "Anker - Power Bank (20K, 87W, Built-In USB-C Cable) - Black"
AMAZON_TITLE = "Anker Power Bank 20000mAh 87W Built-In USB-C Cable"
AMAZON_URL = "https://www.amazon.com/dp/B0C2046S"
AMAZON_SPECS = {"Battery Capacity": "20000 milliamp hours", "Model Number": "C2046S"}
AMAZON_ROW = {"source": "amazon", "rating": 4.2, "review_count": 226, "verified_ratio": None,
              "rating_distribution": None}
AMAZON_REVIEW_DATA = {"rating": 4.2, "review_count": 226, "verified_ratio": None,
                      "rating_distribution": None}
NEUTRAL = {"qualifies": True, "spec_fit": NEUTRAL_FIT, "nice_fit": NEUTRAL_FIT, "group": None,
           "display_name": None, "reason": ""}


def make_candidate(retailer, name, specs=None, reviews=None, group=None):
    return RankedProduct(
        product={"name": name, "url": "u", "price": 99.99, "distance_miles": None},
        retailer=retailer,
        specs=dict(specs or {}),
        reviews=list(reviews or []),
        spec_match=1.0,
        review_score=0.0,
        nice_to_have_score=0.0,
        distance_score=0.5,
        group=group,
    )


def reply_for(rows):
    return json.dumps({"products": rows})


# --- what is sent ---

# the model is asked about the product, not about budget or radius, which are ranking terms
def test_requirements_carry_the_product_criteria():
    assert requirements({"name": "keyboard", "keywords": ["yellow switches"],
                         "must_haves": [{"field": "Layout", "op": "contains", "value": "75%"}],
                         "budget_max": 150.0, "radius_miles": 25}) == {
        "product": "keyboard",
        "keywords": ["yellow switches"],
        "required_specs": [{"field": "Layout", "op": "contains", "value": "75%"}],
        "preferred_specs": [],
        "nice_to_haves": [],
    }


def test_judgment_payload_is_the_listing_as_the_shopper_sees_it():
    product = {"name": BESTBUY_TITLE, "price": 59.99, "url": "u1", "in_stock": True}
    assert judgment_payload("bestbuy", product, AMAZON_SPECS) == {
        "retailer": "bestbuy", "title": BESTBUY_TITLE, "price": 59.99, "url": "u1",
        "specs": AMAZON_SPECS,
    }


# --- reading a reply: a transformation of literal text, nothing simulated ---

def test_a_full_reply_is_read_field_by_field():
    assert parse_reply(reply_for([
        {"index": 0, "qualifies": True, "spec_fit": 0.8, "nice_fit": 0.4, "group": "g1"},
        {"index": 1, "qualifies": False, "spec_fit": 0.1, "nice_fit": 0.2, "group": "g2",
         "reason": "10,000mAh, not the 20,000 asked for"},
    ]), 2) == [
        {"qualifies": True, "spec_fit": 0.8, "nice_fit": 0.4, "group": "g1",
         "display_name": None, "reason": ""},
        {"qualifies": False, "spec_fit": 0.1, "nice_fit": 0.2, "group": "g2",
         "display_name": None, "reason": "10,000mAh, not the 20,000 asked for"},
    ]


def test_a_code_fenced_reply_is_read():
    fenced = "```json\n" + reply_for(
        [{"index": 0, "qualifies": True, "spec_fit": 1.0, "nice_fit": 0.0, "group": "g1"}]
    ) + "\n```"
    assert parse_reply(fenced, 1)[0]["spec_fit"] == 1.0


# a reply we cannot read says nothing about any product, so nothing is dropped on it. this is
# the broken-transport/broken-reply case, and it is NOT the same as the model deciding a
# product falls short - that arrives as an explicit qualifies: false above
@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        '{"products": "nonsense"}',
        "{}",
        # verdicts for products that do not exist
        reply_for([{"index": 7, "qualifies": False}, {"index": -1, "qualifies": False}]),
        # rows that are not objects
        reply_for(["x", 3]),
    ],
)
def test_an_unusable_reply_drops_nothing(text):
    assert parse_reply(text, 2) == [NEUTRAL, NEUTRAL]


# the model judged one of two products: the other keeps the neutral, qualifying assessment
def test_a_partial_reply_leaves_the_rest_qualifying():
    assessments = parse_reply(reply_for(
        [{"index": 1, "qualifies": False, "spec_fit": 0.9, "nice_fit": 0.9, "group": "g2"}]
    ), 2)
    assert assessments[0] == NEUTRAL
    assert assessments[1]["qualifies"] is False


# one bad field must not cost a product its whole assessment
@pytest.mark.parametrize(
    "row,expected",
    [
        ({"index": 0, "spec_fit": 5, "nice_fit": -2}, (1.0, 0.0)),
        ({"index": 0, "spec_fit": "high", "nice_fit": True}, (NEUTRAL_FIT, NEUTRAL_FIT)),
        ({"index": 0}, (NEUTRAL_FIT, NEUTRAL_FIT)),
    ],
)
def test_unusable_scores_fall_back_to_neutral(row, expected):
    first = parse_reply(reply_for([row]), 1)[0]
    assert (first["spec_fit"], first["nice_fit"]) == expected
    assert first["qualifies"] is True


def test_an_empty_group_is_no_group():
    assert parse_reply(reply_for([{"index": 0, "group": ""}]), 1)[0]["group"] is None


# --- group ids drive inheritance ---

def test_specs_are_inherited_within_a_group():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    donor = make_candidate("amazon", AMAZON_TITLE, AMAZON_SPECS, group="g1")
    inherit_specs([taker, donor])
    assert taker.specs == donor.specs
    assert taker.specs is not donor.specs
    assert taker.specs_inherited_from == "amazon"
    assert donor.specs_inherited_from is None
    # weaker evidence than the retailer's own page, so the soft score is discounted
    assert taker.spec_match == pytest.approx(SPEC_MATCH_INHERITED_PENALTY, abs=1e-9)


@pytest.mark.parametrize("taker_group,donor_group", [("g1", "g2"), (None, "g1"), ("g1", None)])
def test_specs_are_not_inherited_across_groups(taker_group, donor_group):
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group=taker_group)
    donor = make_candidate("amazon", AMAZON_TITLE, AMAZON_SPECS, group=donor_group)
    inherit_specs([taker, donor])
    assert taker.specs == {}
    assert taker.spec_match == 1.0


def test_first_party_specs_are_never_overridden():
    own = {"Battery Capacity": "20000 mAh"}
    taker = make_candidate("bestbuy", BESTBUY_TITLE, own, group="g1")
    inherit_specs([taker, make_candidate("amazon", AMAZON_TITLE, AMAZON_SPECS, group="g1")])
    assert taker.specs == own
    assert taker.specs_inherited_from is None


def test_ratings_are_inherited_within_a_group():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    donor = make_candidate("amazon", AMAZON_TITLE, reviews=[dict(AMAZON_ROW)], group="g1")
    inherit_reviews([taker, donor])
    row = taker.reviews[0]
    assert row["rating"] == 4.2
    assert row["source"] == "amazon_inherited"
    assert row["inherited_from_retailer"] == "amazon"
    # a copy, so the two rows can carry different authenticity flags
    assert row is not donor.reviews[0]
    assert compute_review_score(taker.reviews) == pytest.approx(
        compute_review_score([dict(AMAZON_ROW)]) * INHERITED_RATING_PENALTY, abs=1e-9
    )


def test_ratings_are_not_inherited_across_groups():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    inherit_reviews([taker, make_candidate("amazon", AMAZON_TITLE,
                                           reviews=[dict(AMAZON_ROW)], group="g2")])
    assert taker.reviews == []


def test_first_party_rating_is_never_replaced():
    own = {"source": "bestbuy", "rating": 3.1, "review_count": 12}
    taker = make_candidate("bestbuy", BESTBUY_TITLE, reviews=[own], group="g1")
    inherit_reviews([taker, make_candidate("amazon", AMAZON_TITLE,
                                           reviews=[dict(AMAZON_ROW)], group="g1")])
    assert taker.reviews == [own]


def test_the_donor_with_more_reviews_wins_the_group():
    small = make_candidate("target", "x", reviews=[{"source": "target", "rating": 2.0,
                                                    "review_count": 5}], group="g1")
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    inherit_reviews([taker, small, make_candidate("amazon", AMAZON_TITLE,
                                                  reviews=[dict(AMAZON_ROW)], group="g1")])
    assert taker.reviews[0]["review_count"] == 226


# --- the amazon review lookup, once the groups are known ---

# stands in for AmazonScraper: the two methods the lookup calls, and it records them
class FakeAmazon:
    def __init__(self, results, review_data=None):
        self.results = results
        self.review_data = AMAZON_REVIEW_DATA if review_data is None else review_data
        self.searches = []
        self.review_urls = []

    async def search(self, query, store_ids=None):
        self.searches.append(query)
        return list(self.results)

    async def get_reviews(self, product_url):
        self.review_urls.append(product_url)
        return dict(self.review_data)


def amazon_tile(name=AMAZON_TITLE, url=AMAZON_URL):
    return {"name": name, "url": url, "price": 99.99, "in_stock": True,
            "store_id": None, "distance_miles": None}


def test_a_grouped_tile_gives_a_reviewless_candidate_a_rating():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    scraper = FakeAmazon([amazon_tile()])
    asyncio.run(lookup_missing_reviews([taker], {"g1": amazon_tile()}, scraper))
    row = taker.reviews[0]
    assert row["rating"] == 4.2
    assert row["source"] == "amazon_inherited"
    assert row["inherited_from_retailer"] == "amazon"
    assert scraper.review_urls == [AMAZON_URL]


# no group in common means the model did not call them the same product: no page load
@pytest.mark.parametrize("tiles_by_group", [{}, {"g2": amazon_tile()}])
def test_an_ungrouped_tile_never_donates(tiles_by_group):
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    scraper = FakeAmazon([amazon_tile()])
    asyncio.run(lookup_missing_reviews([taker], tiles_by_group, scraper))
    assert taker.reviews == []
    assert scraper.review_urls == []


def test_the_lookup_cap_is_respected():
    takers = [make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
              for _ in range(AMAZON_REVIEW_LOOKUPS_PER_RUN + 2)]
    scraper = FakeAmazon([amazon_tile()])
    asyncio.run(lookup_missing_reviews(takers, {"g1": amazon_tile()}, scraper))
    assert len(scraper.review_urls) == AMAZON_REVIEW_LOOKUPS_PER_RUN
    assert sum(1 for taker in takers if taker.reviews) == AMAZON_REVIEW_LOOKUPS_PER_RUN


# a blocked product page returns {}
def test_a_blocked_lookup_degrades_to_no_reviews():
    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    asyncio.run(lookup_missing_reviews([taker], {"g1": amazon_tile()},
                                       FakeAmazon([amazon_tile()], {})))
    assert taker.reviews == []


# playwright can still raise past the scraper, e.g. if the browser fails to launch
def test_a_raising_lookup_never_propagates():
    class Exploding(FakeAmazon):
        async def get_reviews(self, product_url):
            raise RuntimeError("browser launch failed")

    taker = make_candidate("bestbuy", BESTBUY_TITLE, group="g1")
    asyncio.run(lookup_missing_reviews([taker], {"g1": amazon_tile()}, Exploding([])))
    assert taker.reviews == []


@pytest.mark.parametrize(
    "candidate",
    [
        # first-party data: never overwritten, and never worth a request
        make_candidate("bestbuy", BESTBUY_TITLE, group="g1",
                       reviews=[{"source": "bestbuy", "rating": 3.1, "review_count": 12}]),
        # already inherited a rating from another candidate in this run
        make_candidate("bestbuy", BESTBUY_TITLE, group="g1",
                       reviews=[{"source": "amazon_inherited", "rating": 4.9,
                                 "review_count": 900}]),
        # an amazon candidate with no rating means its own page already failed
        make_candidate("amazon", AMAZON_TITLE, group="g1"),
    ],
)
def test_candidates_that_are_never_searched_for(candidate):
    scraper = FakeAmazon([amazon_tile()])
    asyncio.run(amazon_review_tiles([candidate], scraper))
    assert scraper.searches == []


def test_the_search_cap_is_respected():
    takers = [make_candidate("bestbuy", BESTBUY_TITLE)
              for _ in range(AMAZON_REVIEW_SEARCHES_PER_RUN + 2)]
    scraper = FakeAmazon([amazon_tile()])
    asyncio.run(amazon_review_tiles(takers, scraper))
    assert scraper.searches == [BESTBUY_TITLE] * AMAZON_REVIEW_SEARCHES_PER_RUN
