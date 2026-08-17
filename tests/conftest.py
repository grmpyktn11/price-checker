import pytest

from backend.scrapers import amazon, bestbuy, target
from backend.services import (
    criteria,
    email,
    narration,
    reviews_reddit,
    reviews_youtube,
    sentiment,
    spec_extraction,
)

# every other source is put in canned/fixture mode: keys are read into module constants at
# import time, so setting env vars here is too late - the constants themselves have to be
# patched. product_filter is deliberately absent: it has no offline path and the suite never
# fakes the model, so any test that runs the pipeline makes the real judgment call.
@pytest.fixture(autouse=True)
def canned_mode(monkeypatch):
    monkeypatch.setattr(criteria, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(narration, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(sentiment, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(spec_extraction, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(reviews_youtube, "YOUTUBE_API_KEY", "")
    # reddit is keyless, so LIVE_SCRAPE is its switch, same as the scrapers
    monkeypatch.setattr(reviews_reddit, "LIVE_SCRAPE", "")
    # no test may send an email: the render path still runs, the send returns False
    monkeypatch.setattr(email, "RESEND_API_KEY", "")
    monkeypatch.setattr(email, "USER_EMAIL", "")
    monkeypatch.setattr(bestbuy, "LIVE_SCRAPE", "")
    monkeypatch.setattr(target, "LIVE_SCRAPE", "")
    monkeypatch.setattr(amazon, "LIVE_SCRAPE", "")
