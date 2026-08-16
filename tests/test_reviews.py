import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.models import Review, utcnow
from backend.scrapers.base import load_fixture
from backend.services import google_cse, reviews_forums, reviews_reddit, reviews_store, reviews_youtube
from backend.services.criteria import CANNED_CRITERIA
from backend.services.pipeline import run_pipeline

LAT = 37.7749
LON = -122.4194
EXTERNAL_SOURCES = ("reddit", "forum", "youtube")
# the two cse_*.json fixtures are hand-built in the documented CSE response shape: the
# Custom Search API is not enabled on the project's key, so a live capture 403s
CSE_FIXTURES = ("cse_reddit.json", "cse_forums.json")


# the in-process counter must not leak between tests
@pytest.fixture
def fresh_budget():
    google_cse._SPENT.update({"date": None, "count": 0})
    yield
    google_cse._SPENT.update({"date": None, "count": 0})


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def external_review(source, **overrides):
    return {"source": source, "rating": None, "review_count": None, "verified_ratio": None,
            "rating_distribution": None, "url": f"https://example.com/{source}",
            "summary_text": "text", "mention_count": 50, "authenticity_flag": "ok", **overrides}


@pytest.mark.parametrize("filename", CSE_FIXTURES)
def test_parse_items(filename):
    items = google_cse.parse_items(load_fixture(filename))
    assert items
    for item in items:
        assert item["title"] and item["snippet"] and item["display_link"]
        assert item["link"].startswith("https://")


def test_forum_fixture_covers_several_domains():
    domains = {item["display_link"] for item in google_cse.parse_items(load_fixture("cse_forums.json"))}
    assert len(domains) >= 2


@pytest.mark.parametrize(
    "gather,source",
    [
        (lambda: reviews_reddit.gather("portable charger", "electronics"), "reddit"),
        (lambda: reviews_forums.gather("portable charger", "electronics"), "forum"),
        (lambda: reviews_youtube.gather("portable charger"), "youtube"),
    ],
)
def test_external_review_shape(gather, source):
    review = asyncio.run(gather())
    assert review["source"] == source
    # no external source publishes a star rating, and a hit count is not a review count
    assert review["rating"] is None
    assert review["review_count"] is None
    assert review["verified_ratio"] is None
    assert review["summary_text"]
    assert len(review["summary_text"]) <= google_cse.MAX_SUMMARY_CHARS
    assert isinstance(review["mention_count"], int)
    assert review["url"].startswith("https://")


def test_build_reddit_query():
    query = reviews_reddit.build_reddit_query("portable charger", "electronics")
    assert "site:reddit.com/r/" in query
    assert query.count("site:") <= reviews_reddit.MAX_SUBREDDITS


def test_unknown_category_falls_back():
    assert "r/BuyItForLife" in reviews_reddit.build_reddit_query("x", "spaceships")
    assert "rtings.com" in reviews_forums.build_forum_query("x", "spaceships")


def test_forum_sites_are_bare_domains():
    for sites in [*reviews_forums.FORUM_SITES.values(), reviews_forums.DEFAULT_FORUM_SITES]:
        for site in sites:
            assert "://" not in site


def test_forum_query_is_capped():
    query = reviews_forums.build_forum_query("x", "computers")
    assert query.count("site:") <= reviews_forums.MAX_SITES_PER_QUERY


def test_parse_videos_merges_statistics():
    videos = reviews_youtube.parse_videos(load_fixture("youtube_search.json"),
                                          load_fixture("youtube_videos.json"))
    assert videos
    for video in videos:
        assert video["url"].endswith(video["video_id"])
        assert isinstance(video["view_count"], int)


# a video with likes hidden is a real case the captured fixture happens not to contain
def test_hidden_like_count_is_none():
    search = {"items": [{"id": {"videoId": "abc123"}, "snippet": {"title": "t", "channelTitle": "c"}}]}
    videos = {"items": [{"id": "abc123", "statistics": {"viewCount": "10"}}]}
    parsed = reviews_youtube.parse_videos(search, videos)
    assert parsed[0]["like_count"] is None
    assert parsed[0]["view_count"] == 10


def test_parse_comments():
    comments = reviews_youtube.parse_comments(load_fixture("youtube_comments.json"))
    assert comments and all(isinstance(comment, str) for comment in comments)


# every candidate from every retailer carries the three item-level dicts
def test_item_level_reviews_reach_every_candidate():
    ranked = asyncio.run(run_pipeline(CANNED_CRITERIA, LAT, LON, 25))
    assert ranked
    for candidate in ranked:
        sources = {review["source"] for review in candidate.reviews}
        assert set(EXTERNAL_SOURCES) <= sources


# a count of Google hits is not a count of reviews: mention_count must not reach the filter
def test_external_sources_do_not_satisfy_min_review_count():
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    assert max((r["review_count"] or 0) for r in reviews) == 0


def test_budget_rolls_over_by_day(fresh_budget):
    assert google_cse.budget_left(date(2026, 1, 1)) == google_cse.DAILY_BUDGET
    google_cse._SPENT["count"] = google_cse.DAILY_BUDGET
    assert google_cse.budget_left(date(2026, 1, 1)) == 0
    assert google_cse.budget_left(date(2026, 1, 2)) == google_cse.DAILY_BUDGET


def test_exhausted_budget_makes_no_request(monkeypatch, fresh_budget):
    monkeypatch.setattr(google_cse, "GOOGLE_CSE_API_KEY", "key")
    monkeypatch.setattr(google_cse, "GOOGLE_CSE_ID", "cx")

    # any attempt to open a connection fails the test rather than reaching the network
    def explode(*args, **kwargs):
        raise AssertionError("cse tried to make a request with no budget left")

    monkeypatch.setattr(google_cse.httpx, "AsyncClient", explode)
    google_cse._SPENT.update({"date": date.today(), "count": google_cse.DAILY_BUDGET})
    assert asyncio.run(google_cse.search("anything")) == {}


def test_missing_key_makes_no_request(monkeypatch, fresh_budget):
    def explode(*args, **kwargs):
        raise AssertionError("cse tried to make a request with no key")

    monkeypatch.setattr(google_cse.httpx, "AsyncClient", explode)
    assert asyncio.run(google_cse.search("anything")) == {}


def test_save_and_load_round_trip(db):
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    reviews_store.save_reviews(db, 1, reviews)
    loaded = reviews_store.load_fresh_external(db, 1)
    assert {row["source"] for row in loaded} == set(EXTERNAL_SOURCES)
    assert all(row["rating"] is None for row in loaded)


def test_saving_twice_leaves_one_row_per_source(db):
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    reviews_store.save_reviews(db, 1, reviews)
    reviews_store.save_reviews(db, 1, reviews)
    assert db.query(Review).count() == len(EXTERNAL_SOURCES)


def test_stale_rows_are_not_returned(db):
    reviews_store.save_reviews(db, 1, [external_review("reddit")])
    row = db.query(Review).one()
    row.fetched_at = utcnow() - timedelta(days=8)
    db.commit()
    assert reviews_store.load_fresh_external(db, 1) == []


def test_inherited_rows_are_not_external(db):
    reviews_store.save_reviews(db, 1, [
        {"source": "amazon_inherited", "rating": 4.2, "review_count": 226,
         "authenticity_flag": "ok"},
        external_review("reddit"),
    ])
    assert [row["source"] for row in reviews_store.load_fresh_external(db, 1)] == ["reddit"]
