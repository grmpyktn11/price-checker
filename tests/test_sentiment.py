import asyncio

import pytest

from backend.services.sentiment import (
    CANNED_SENTIMENT,
    MAX_INPUT_CHARS,
    build_input,
    classify,
    contradicts,
    parse_sentiment_reply,
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


def test_parse_sentiment_reply_fenced():
    reply = '```json\n{"sentiment": "mixed", "confidence": 0.6, "summary": "ok"}\n```'
    assert parse_sentiment_reply(reply) == {"sentiment": "mixed", "confidence": 0.6,
                                            "summary": "ok"}


def test_parse_sentiment_reply_bare_object():
    parsed = parse_sentiment_reply('{"sentiment": "positive", "confidence": 1, "summary": "s"}')
    assert parsed["sentiment"] == "positive"
    assert parsed["confidence"] == 1.0


@pytest.mark.parametrize("reply", ["not json at all", '{"sentiment": "great"}', "{}"])
def test_parse_sentiment_reply_garbage(reply):
    assert parse_sentiment_reply(reply) == CANNED_SENTIMENT


def test_classify_with_nothing_to_read():
    assert asyncio.run(classify([])) == CANNED_SENTIMENT


def test_build_input_labels_and_truncates():
    reviews = [{"source": "reddit", "summary_text": "a" * 5000},
               {"source": "forum", "summary_text": "b" * 5000}]
    text = build_input(reviews)
    assert text.startswith("[reddit]")
    assert len(text) == MAX_INPUT_CHARS
