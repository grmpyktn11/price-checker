import asyncio

from backend.services.criteria import extract, normalize, parse_json_reply
from backend.services.pipeline import run_pipeline
from backend.services.ranking import RankedProduct

LAT = 37.7749
LON = -122.4194
HISTORY = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]


def test_first_turn_is_a_followup():
    result = asyncio.run(extract([], "anything"))
    assert result["type"] == "followup"
    assert result["question"]


def test_later_turn_returns_criteria():
    result = asyncio.run(extract(HISTORY, "anything"))
    assert result["type"] == "criteria"


def test_criteria_shape():
    item_criteria = asyncio.run(extract(HISTORY, "anything"))["criteria"]
    assert item_criteria["name"]
    assert item_criteria["radius_miles"] is not None
    assert item_criteria["min_review_count"] is not None
    for rule in [*item_criteria["must_haves"], *item_criteria["preferred_specs"]]:
        assert "field" in rule and "op" in rule


# catches drift between criteria.py and pipeline.py
def test_criteria_runs_through_the_pipeline():
    item_criteria = asyncio.run(extract(HISTORY, "anything"))["criteria"]
    ranked = asyncio.run(
        run_pipeline(item_criteria, LAT, LON, item_criteria["radius_miles"])
    )
    assert ranked
    assert all(isinstance(result, RankedProduct) for result in ranked)


def test_normalize_fills_defaults():
    filled = normalize({"name": "x"})
    assert filled["radius_miles"] == 25
    assert filled["min_review_count"] == 0


def test_parse_json_reply_fenced():
    assert parse_json_reply('```json\n{"type": "followup"}\n```') == {"type": "followup"}


def test_parse_json_reply_in_prose():
    assert parse_json_reply('Sure, here it is: {"a": 1} hope that helps') == {"a": 1}


def test_parse_json_reply_garbage():
    assert parse_json_reply("no json at all") is None
