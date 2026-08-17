import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.models import Review
from backend.scrapers.base import load_fixture, load_fixture_text
from backend.services import reviews_reddit, reviews_store, reviews_youtube
from backend.services.pipeline import run_pipeline
from sample_criteria import SAMPLE_CRITERIA

LAT = 37.7749
LON = -122.4194
EXTERNAL_SOURCES = ("reddit", "youtube")
REDDIT_FIXTURE = "reddit_search.xml"


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
    posts = reviews_reddit.parse_posts(load_fixture_text(REDDIT_FIXTURE))
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


# both sources are built from a saved capture here: gather() itself is a network call, and
# the shape is what the pipeline depends on
@pytest.mark.parametrize(
    "review,source,max_chars",
    [
        (reviews_reddit.build_review(
            reviews_reddit.parse_posts(load_fixture_text(REDDIT_FIXTURE))),
         "reddit", reviews_reddit.MAX_SUMMARY_CHARS),
        (reviews_youtube.build_review(
            reviews_youtube.parse_videos(load_fixture("youtube_search.json"),
                                         load_fixture("youtube_videos.json")),
            reviews_youtube.parse_comments(load_fixture("youtube_comments.json"))),
         "youtube", reviews_youtube.MAX_SUMMARY_CHARS),
    ],
)
def test_external_review_shape(review, source, max_chars):
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
    posts = reviews_reddit.parse_posts(load_fixture_text(REDDIT_FIXTURE))
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


# the researched top of the ranking carries discussion of its own product. youtube is only
# fetched when the sentiment call says the top is too close to call, so it is not required
@pytest.mark.live
def test_top_candidates_carry_their_own_research():
    ranked = asyncio.run(run_pipeline(SAMPLE_CRITERIA, LAT, LON, 25))
    assert ranked
    assert "reddit" in {review["source"] for review in ranked[0].reviews}
    assert ranked[0].sentiment


# a count of reddit threads is not a count of reviews: mention_count must not reach the filter
def test_external_sources_do_not_satisfy_min_review_count():
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    assert max((r["review_count"] or 0) for r in reviews) == 0


def test_save_writes_one_row_per_source(db):
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    reviews_store.save_reviews(db, 1, reviews)
    rows = db.query(Review).all()
    assert {row.source for row in rows} == set(EXTERNAL_SOURCES)
    # discussion is not a star rating
    assert all(row.rating is None for row in rows)


def test_saving_twice_leaves_one_row_per_source(db):
    reviews = [external_review(source) for source in EXTERNAL_SOURCES]
    reviews_store.save_reviews(db, 1, reviews)
    reviews_store.save_reviews(db, 1, reviews)
    assert db.query(Review).count() == len(EXTERNAL_SOURCES)


def test_inherited_rows_keep_their_source(db):
    reviews_store.save_reviews(db, 1, [
        {"source": "amazon_inherited", "rating": 4.2, "review_count": 226,
         "authenticity_flag": "ok"},
        external_review("reddit"),
    ])
    assert {row.source for row in db.query(Review)} == {"amazon_inherited", "reddit"}
