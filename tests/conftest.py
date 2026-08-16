import pytest

from backend.scrapers import amazon, bestbuy, target
from backend.services import (
    criteria,
    email,
    google_cse,
    narration,
    nice_to_have,
    reviews_forums,
    reviews_reddit,
    reviews_youtube,
    sentiment,
    spec_extraction,
)

# the whole suite is written against canned/fixture mode: deterministic, free, offline.
# a developer with real keys in .env would otherwise make live calls from the tests.
# keys are read into module constants at import time, so setting env vars here is too
# late - the constants themselves have to be patched.
@pytest.fixture(autouse=True)
def canned_mode(monkeypatch):
    monkeypatch.setattr(criteria, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(narration, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(nice_to_have, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(sentiment, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(spec_extraction, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(reviews_reddit, "GOOGLE_CSE_API_KEY", "")
    monkeypatch.setattr(reviews_forums, "GOOGLE_CSE_API_KEY", "")
    monkeypatch.setattr(reviews_youtube, "YOUTUBE_API_KEY", "")
    # belt and braces: even if a guard is ever missed, the shared CSE call refuses to run
    monkeypatch.setattr(google_cse, "GOOGLE_CSE_API_KEY", "")
    # no test may send an email: the render path still runs, the send returns False
    monkeypatch.setattr(email, "RESEND_API_KEY", "")
    monkeypatch.setattr(email, "USER_EMAIL", "")
    monkeypatch.setattr(bestbuy, "LIVE_SCRAPE", "")
    monkeypatch.setattr(target, "LIVE_SCRAPE", "")
    monkeypatch.setattr(amazon, "LIVE_SCRAPE", "")
