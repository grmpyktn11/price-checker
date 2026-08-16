import pytest

from backend.scrapers import amazon, bestbuy, target
from backend.services import criteria, narration

# the whole suite is written against canned/fixture mode: deterministic, free, offline.
# a developer with real keys in .env would otherwise make live calls from the tests.
# keys are read into module constants at import time, so setting env vars here is too
# late - the constants themselves have to be patched.
@pytest.fixture(autouse=True)
def canned_mode(monkeypatch):
    monkeypatch.setattr(criteria, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(narration, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(bestbuy, "LIVE_SCRAPE", "")
    monkeypatch.setattr(target, "LIVE_SCRAPE", "")
    monkeypatch.setattr(amazon, "LIVE_SCRAPE", "")
