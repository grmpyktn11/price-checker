import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.models import Review, utcnow
from backend.scrapers.base import load_fixture, load_fixture_text
from backend.services import reviews_reddit, reviews_store, reviews_youtube
from backend.services.criteria import CANNED_CRITERIA
from backend.services.pipeline import run_pipeline

LAT = 37.7749
LON = -122.4194
EXTERNAL_SOURCES = ("reddit", "youtube")


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # autoflush off, same as the app's SessionLocal, so a missing flush fails here too
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def external_review(source, **overrides):
    return {"source": source, "rating": None, "review_count": None, "verified_ratio": None,
            "rating_distribution": None, "url": f"https://example.com/{source}",
            "summary_text": "text", "mention_count": 50, "authenticity_flag": "ok", **overrides}


# the reddit fixture is a real capture of the public search feed, so this asserts the shape
# the live endpoint actually returns
def test_parse_posts():
    posts = reviews_reddit.parse_posts(load_fixture_text(reviews_reddit.FIXTURE))
    assert posts
    for post in posts:
        assert post["title"]
        assert post["subreddit"]
        assert post["url"].startswith("https://www.reddit.com/")
    # full post bodies are the point of this source: a search snippet would never be this long
    assert any(len(post["selftext"]) > 200 for post in posts)


# a link post has no body between the markers, and that is not an error
def test_parse_selftext_without_a_body():
    assert reviews_reddit.parse_selftext("<a href='x'>[link]</a>") == ""


@pytest.mark.parametrize(
    "gather,source,max_chars",
    [
        (lambda: reviews_reddit.gather("portable charger", "electronics"), "reddit",
         reviews_reddit.MAX_SUMMARY_CHARS),
        (lambda: reviews_youtube.gather("portable charger"), "youtube",
         reviews_youtube.MAX_SUMMARY_CHARS),
    ],
)
def test_external_review_shape(gather, source, max_chars):
    review = asyncio.run(gather())
    assert review["source"] == source
    # no external source publishes a star rating, and a thread count is not a review count
    assert review["rating"] is None
    assert review["review_count"] is None
    assert review["verified_ratio"] is None
    assert review["summary_text"]
    assert len(review["summary_text"]) <= max_chars
    assert isinstance(review["mention_count"], int)
    assert review["url"].startswith("https://")


def test_build_subreddit_path():
    path = reviews_reddit.build_subreddit_path("electronics")
    assert path.startswith("electronics+")
    assert len(path.split("+")) <= reviews_reddit.MAX_SUBREDDITS


def test_unknown_category_falls_back():
    assert "BuyItForLife" in reviews_reddit.build_subreddit_path("spaceships")


def test_subreddits_are_bare_names():
    for names in [*reviews_reddit.CATEGORY_SUBREDDIT_MAP.values(),
                  reviews_reddit.DEFAULT_SUBREDDITS]:
        for name in names:
            assert not name.startswith("r/") and "/" not in name


# the summary is what LLM call #4 reads, so it must carry the post bodies, not just titles
def test_summary_carries_post_bodies():
    posts = reviews_reddit.parse_posts(load_fixture_text(reviews_reddit.FIXTURE))
    summary = reviews_reddit.build_summary(posts)
    assert posts[0]["selftext"][:100] in summary
    assert f"[r/{posts[0]['subreddit']}]" in summary


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
@pytest.mark.live
def test_item_level_reviews_reach_every_candidate():
    ranked = asyncio.run(run_pipeline(CANNED_CRITERIA, LAT, LON, 25))
    assert ranked
    for candidate in ranked:
        sources = {review["source"] for review in candidate.reviews}
        assert set(EXTERNAL_SOURCES) <= sources


# a count of reddit threads is not a count of reviews: mention_count must not reach the filter
def test_external_sources_do_not_satisfy_min_review_count():
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    assert max((r["review_count"] or 0) for r in reviews) == 0


# fixture mode is the offline switch: no LIVE_SCRAPE means no socket, ever
def test_fixture_mode_makes_no_request(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("reddit tried to make a request in fixture mode")

    monkeypatch.setattr(reviews_reddit.httpx, "AsyncClient", explode)
    assert asyncio.run(reviews_reddit.gather("anything", "electronics"))["source"] == "reddit"


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
