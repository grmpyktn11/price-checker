import asyncio
import json

import pytest

from backend.services import claude_share, project_extract
from backend.services.project_extract import extract, parse_reply, to_criteria, trim

REPLY = json.dumps({
    "project": "Home lab",
    "items": [
        {"index": 0, "name": "8-port gigabit switch", "why": "connects the nodes",
         "keywords": ["8 port", "gigabit"], "category": "electronics",
         "budget_max": 60, "quantity": 1, "essential": True},
        {"index": 1, "name": "Cat6 patch cables", "why": "wiring the rack",
         "keywords": ["cat6", "3ft"], "category": "electronics",
         "budget_max": None, "quantity": 8, "essential": False},
    ],
})


def test_parses_the_shopping_list():
    result = parse_reply(REPLY)
    assert result["project"] == "Home lab"
    assert [item["name"] for item in result["items"]] == [
        "8-port gigabit switch", "Cat6 patch cables"
    ]
    assert result["items"][1]["quantity"] == 8
    assert result["items"][1]["essential"] is False


# every row has to come out ready for run_pipeline, which indexes min_review_count and whose
# caller reads radius_miles. normalize() supplies both; a hand-built dict would not
def test_every_item_is_a_usable_criteria_dict():
    for item in parse_reply(REPLY)["items"]:
        criteria = item["criteria"]
        assert criteria["min_review_count"] is not None
        assert criteria["radius_miles"] is not None
        assert criteria["name"] == item["name"]
        assert criteria["must_haves"] == []


# a row with no name has nothing to search for, so it is not a row
def test_rows_with_no_name_are_dropped():
    reply = json.dumps({"project": "x", "items": [
        {"name": "", "why": "nothing"},
        {"name": "   ", "why": "nothing"},
        {"name": "a real thing", "why": "something"},
    ]})
    assert [i["name"] for i in parse_reply(reply)["items"]] == ["a real thing"]


# a malformed reply is not a crash: the import is one model call and the person is waiting
@pytest.mark.parametrize("text", ["", "not json at all", "[]", '{"project": "x"}',
                                  '{"items": "not a list"}'])
def test_a_malformed_reply_degrades_to_an_empty_list(text):
    result = parse_reply(text)
    assert result["items"] == []
    assert isinstance(result["project"], str)


def test_rows_that_are_not_objects_are_skipped():
    reply = json.dumps({"project": "x", "items": ["a string", 7, None,
                                                  {"name": "real"}]})
    assert [i["name"] for i in parse_reply(reply)["items"]] == ["real"]


# the model is told to send numbers; anything else is not a budget
@pytest.mark.parametrize("value,expected", [
    (60, 60.0), (60.5, 60.5), (0, None), (-5, None),
    ("60", None), (True, None), (None, None),
])
def test_budget_only_survives_as_a_positive_number(value, expected):
    reply = json.dumps({"project": "x", "items": [{"name": "thing", "budget_max": value}]})
    assert parse_reply(reply)["items"][0]["budget_max"] == expected


@pytest.mark.parametrize("value,expected", [(3, 3), (0, 1), (-2, 1), (True, 1),
                                            ("4", 1), (None, 1), (500, 99)])
def test_quantity_falls_back_to_one(value, expected):
    reply = json.dumps({"project": "x", "items": [{"name": "thing", "quantity": value}]})
    assert parse_reply(reply)["items"][0]["quantity"] == expected


# only an explicit false makes something optional, so a missing key does not quietly untick
# an item the project actually needs
def test_essential_defaults_to_true():
    reply = json.dumps({"project": "x", "items": [
        {"name": "a"}, {"name": "b", "essential": False}, {"name": "c", "essential": None},
    ]})
    assert [i["essential"] for i in parse_reply(reply)["items"]] == [True, False, True]


def test_the_list_is_capped():
    rows = [{"name": f"thing {n}"} for n in range(project_extract.MAX_ITEMS + 5)]
    reply = json.dumps({"project": "x", "items": rows})
    assert len(parse_reply(reply)["items"]) == project_extract.MAX_ITEMS


# a planning conversation converges, so the last word on what to buy is the part to keep
def test_a_long_transcript_is_trimmed_from_the_end():
    transcript = "start" + ("x" * project_extract.MAX_TRANSCRIPT_CHARS) + "the actual list"
    trimmed = trim(transcript)
    assert len(trimmed) == project_extract.MAX_TRANSCRIPT_CHARS
    assert trimmed.endswith("the actual list")


def test_an_empty_transcript_never_calls_the_model(monkeypatch):
    def boom(payload):
        raise AssertionError("must not call the model for an empty transcript")

    monkeypatch.setattr(project_extract, "call_model", boom)
    assert asyncio.run(extract("   "))["items"] == []


# a transport failure returns the same shape as "nothing to buy here" rather than raising:
# the router turns an empty list into a readable 422, and a 500 says nothing useful
def test_a_transport_failure_returns_an_empty_list(monkeypatch):
    async def boom(payload):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(project_extract, "call_model", boom)
    assert asyncio.run(extract("we need a switch")) == {"project": "", "items": []}


def test_criteria_carries_the_budget_through():
    criteria = to_criteria({"name": "switch", "category": "electronics",
                            "keywords": ["gigabit"], "budget_max": 60.0})
    assert criteria["budget_max"] == 60.0
    assert criteria["keywords"] == ["gigabit"]


# the url reaches page.goto(), so anything not on claude.ai is refused before it is fetched.
# without this, "import from a link" is an open proxy into whatever the server can reach
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost:8000/api/profile",
    "file:///c:/windows/win.ini",
    "javascript:alert(1)",
    "https://claude.ai.evil.example.com/share/x",
    "https://evil.example.com/share/x",
    "https://notclaude.ai/share/x",
])
def test_only_claude_ai_share_urls_are_accepted(url):
    assert claude_share.is_share_url(url) is False


@pytest.mark.parametrize("url", ["https://claude.ai/share/abc-123",
                                 "https://www.claude.ai/share/abc-123"])
def test_a_real_share_url_is_accepted(url):
    assert claude_share.is_share_url(url) is True


def test_fetching_a_non_share_url_raises_before_any_fetch(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not fetch a url that failed the host check")

    monkeypatch.setattr(claude_share, "fetch_html", boom)
    with pytest.raises(claude_share.NotAShareUrl):
        asyncio.run(claude_share.fetch_transcript("https://evil.example.com/share/x"))


# a revoked or private link renders a shell. that is not a transcript, and passing it to the
# model would spend a call to be told there is nothing to buy
def test_a_page_with_no_transcript_is_reported_not_extracted(monkeypatch):
    async def shell(url, wait_for):
        return "<html><body><main>Sign in</main></body></html>"

    monkeypatch.setattr(claude_share, "fetch_html", shell)
    with pytest.raises(claude_share.ShareUnreadable):
        asyncio.run(claude_share.fetch_transcript("https://claude.ai/share/gone"))


# measured 2026-08-19: claude.ai is behind Cloudflare, which serves our headless browser an
# interstitial instead of the conversation. it is ~370 characters, so the old length check let
# it through to the model, which truthfully said there were no products in it - and the person
# was told their conversation had nothing to buy when it had never been read
CLOUDFLARE_PAGE = (
    "<html><body><main>Just a moment... claude.ai Performing security verification "
    "This website uses a security service to protect against malicious bots. This page is "
    "displayed while the website verifies you are not a bot. Enable JavaScript and cookies "
    "to continue Ray ID: a2d9217e5b78e5f6 Performance and Security by Cloudflare"
    "</main></body></html>"
)


def test_a_bot_challenge_is_reported_as_a_block_not_an_empty_conversation(monkeypatch):
    async def challenge(url, wait_for):
        return CLOUDFLARE_PAGE

    monkeypatch.setattr(claude_share, "fetch_html", challenge)
    with pytest.raises(claude_share.ShareBlocked) as caught:
        asyncio.run(claude_share.fetch_transcript("https://claude.ai/share/abc"))
    # the message must not send someone off checking a link that is perfectly fine
    assert "Nothing is wrong with your link" in str(caught.value)


def test_a_sign_in_wall_says_the_chat_is_still_private(monkeypatch):
    async def wall(url, wait_for):
        return "<html><body><main>Sign in to continue to Claude</main></body></html>"

    monkeypatch.setattr(claude_share, "fetch_html", wall)
    with pytest.raises(claude_share.ShareUnreadable) as caught:
        asyncio.run(claude_share.fetch_transcript("https://claude.ai/share/abc"))
    assert "private" in str(caught.value)


# a challenge is not "unreadable link": they are separate types because they need separate
# advice, and a caller must be able to tell them apart
def test_a_block_is_not_reported_as_an_unreadable_link(monkeypatch):
    async def challenge(url, wait_for):
        return CLOUDFLARE_PAGE

    monkeypatch.setattr(claude_share, "fetch_html", challenge)
    with pytest.raises(claude_share.ShareBlocked):
        asyncio.run(claude_share.fetch_transcript("https://claude.ai/share/abc"))
    assert not issubclass(claude_share.ShareBlocked, claude_share.ShareUnreadable)


# a page that is neither blocked nor a sign-in wall but far too short for a transcript
def test_a_short_page_is_still_an_unreadable_link(monkeypatch):
    async def stub(url, wait_for):
        return "<html><body><main>Nothing here</main></body></html>"

    monkeypatch.setattr(claude_share, "fetch_html", stub)
    with pytest.raises(claude_share.ShareUnreadable):
        asyncio.run(claude_share.fetch_transcript("https://claude.ai/share/abc"))


def test_a_real_length_transcript_gets_through(monkeypatch):
    async def real(url, wait_for):
        body = "we need a keyboard and a mouse and a monitor. " * 60
        return f"<html><body><main>{body}</main></body></html>"

    monkeypatch.setattr(claude_share, "fetch_html", real)
    text = asyncio.run(claude_share.fetch_transcript("https://claude.ai/share/abc"))
    assert len(text) >= claude_share.MIN_TEXT_CHARS
