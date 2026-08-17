import asyncio

import pytest

from backend.services.sentiment import (
    MAX_DISCUSSION_CHARS,
    NEUTRAL_ASSESSMENT,
    assess,
    build_products,
    contradicts,
    parse_reply,
)


@pytest.mark.parametrize(
    "sentiment,rating,expected",
    [
        ("negative", 4.8, True),
        ("positive", 3.0, True),
        # mixed is what community discussion normally looks like: it flags nothing
        ("mixed", 4.9, False),
        ("unknown", 4.9, False),
        ("negative", None, False),
        ("negative", 3.0, False),
        (None, 4.9, False),
    ],
)
def test_contradicts(sentiment, rating, expected):
    assert contradicts(sentiment, rating) is expected


def test_parse_reply_fenced():
    reply = ('```json\n{"products": [{"index": 0, "sentiment": "mixed", "confidence": 0.6,'
             ' "summary": "ok"}], "too_close": []}\n```')
    parsed = parse_reply(reply, 1)
    assert parsed["products"] == [{"sentiment": "mixed", "confidence": 0.6, "summary": "ok"}]
    assert parsed["too_close"] == []


# a decisive reply names nobody; a close call names the products it cannot separate
def test_parse_reply_too_close():
    reply = ('{"products": [{"index": 0, "sentiment": "positive", "confidence": 0.5},'
             ' {"index": 1, "sentiment": "positive", "confidence": 0.5}],'
             ' "too_close": [0, 1]}')
    assert parse_reply(reply, 2)["too_close"] == [0, 1]


# an index the call never sent must not be able to spend a YouTube search
def test_parse_reply_drops_unknown_too_close_indexes():
    reply = '{"products": [{"index": 0, "sentiment": "mixed"}], "too_close": [0, 7, "x"]}'
    assert parse_reply(reply, 1)["too_close"] == [0]


# a product the model skipped or judged with an invalid label stays unknown, not dropped
@pytest.mark.parametrize("reply", ["not json at all", '{"products": "nope"}',
                                   '{"products": [{"index": 0, "sentiment": "great"}]}'])
def test_parse_reply_garbage_is_neutral(reply):
    parsed = parse_reply(reply, 2)
    assert parsed["products"] == [NEUTRAL_ASSESSMENT, NEUTRAL_ASSESSMENT]
    assert parsed["too_close"] == []


def test_build_products_truncates_discussion():
    products = build_products([{"name": "Anker 737", "rating": 4.7, "discussion": "a" * 9000}])
    assert products[0]["index"] == 0
    assert len(products[0]["discussion"]) == MAX_DISCUSSION_CHARS


def test_assess_with_nothing_to_read():
    assert asyncio.run(assess([])) == {"products": [], "too_close": []}
