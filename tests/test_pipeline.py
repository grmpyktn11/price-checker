import asyncio

import pytest

from backend.services import trace
from backend.services.pipeline import (
    amazon_review_tiles,
    evidence_count,
    filter_on_reviews,
    research_payload,
    research_rows,
    tile_rank,
)
from backend.services.ranking import RankedProduct

RATING_ROW = {"source": "bestbuy", "rating": 4.5, "review_count": 120}
# a retailer that published a count, and the count is zero
ZERO_RATING_ROW = {"source": "target", "rating": None, "review_count": 0}
REDDIT_ROW = {"source": "reddit", "rating": None, "review_count": None, "mention_count": 8,
              "summary_text": "held up for two years"}
YOUTUBE_ROW = {"source": "youtube", "rating": None, "review_count": None, "mention_count": 5,
               "summary_text": "teardown video"}


def candidate(name="Anker 737", reviews=None, retailer="bestbuy"):
    return RankedProduct(
        product={"name": name, "price": 99.0, "in_stock": True},
        retailer=retailer,
        specs={},
        reviews=list(reviews or []),
        spec_match=0.0,
        review_score=0.0,
        nice_to_have_score=0.0,
        distance_score=0.5,
    )


# buyable tiles are looked up first; the sort is stable, so relevance order survives
def test_tile_rank_puts_buyable_first():
    tiles = [{"in_stock": False, "price": 10.0}, {"in_stock": True, "price": None},
             {"in_stock": True, "price": 20.0}]
    assert sorted(tiles, key=tile_rank)[0] == {"in_stock": True, "price": 20.0}


@pytest.mark.parametrize(
    "reviews,expected",
    [
        ([RATING_ROW], 120),
        # Best Buy publishes no review count: the discussion about this product is the evidence
        ([REDDIT_ROW, YOUTUBE_ROW], 8),
        ([RATING_ROW, REDDIT_ROW], 120),
        # a retailer that answered zero is a fact about the product; no rows at all is not
        ([ZERO_RATING_ROW], 0),
        ([], None),
    ],
)
def test_evidence_count(reviews, expected):
    assert evidence_count(candidate(reviews=reviews)) == expected


# a researched product clears a floor its retailer could never have cleared alone
def test_discussion_clears_the_review_floor():
    researched = candidate("Anker 737", [REDDIT_ROW])
    counted = candidate("No Name Charger", [ZERO_RATING_ROW])
    survivors = filter_on_reviews([researched, counted], 5)
    assert [c.product["name"] for c in survivors] == ["Anker 737"]


# the rgb mouse bug: Best Buy product pages are blocked, so its listings carry no review row
# at all. counting that as zero deleted the only products that actually matched the search
def test_unknown_review_count_is_not_a_drop():
    survivors = filter_on_reviews([candidate("CORSAIR M75 RGB", [])], 5)
    assert [c.product["name"] for c in survivors] == ["CORSAIR M75 RGB"]


def test_research_payload_labels_each_source():
    payload = research_payload([candidate(reviews=[RATING_ROW, REDDIT_ROW, YOUTUBE_ROW])])
    assert payload[0]["name"] == "Anker 737"
    assert payload[0]["rating"] == 4.5
    assert "[reddit] held up for two years" in payload[0]["discussion"]
    assert "[youtube] teardown video" in payload[0]["discussion"]


# only the discussion rows are stored as the item's research; the rating row is the retailer's
def test_research_rows_are_the_discussion_rows():
    rows = research_rows(candidate(reviews=[RATING_ROW, REDDIT_ROW]))
    assert [row["source"] for row in rows] == ["reddit"]


# a bot wall does not lift between one search and the next, so the review lookup must not
# spend its remaining searches on a retailer that already answered with one
def test_review_lookup_skips_a_blocked_amazon():
    trace.start("mouse", {})
    trace.record_search("amazon", "u", trace.BLOCKED, 0)
    trace.retailer("amazon", ms=1, candidates=0)

    class Boom:
        async def search(self, query):
            raise AssertionError("must not search a blocked retailer again")

    assert asyncio.run(amazon_review_tiles([candidate("Anker 737", [])], Boom())) == []
    assert trace.finish(0)["review_lookup"]["skipped"]
