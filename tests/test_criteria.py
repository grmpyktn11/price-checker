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


# the model returns explicit nulls, not missing keys, for fields it has no value for
def test_normalize_replaces_nulls():
    filled = normalize({"name": "x", "radius_miles": None, "min_review_count": None})
    assert filled["radius_miles"] == 25
    assert filled["min_review_count"] == 0


# regression: a null min_review_count reached "review_count < min_review_count" and the
# TypeError was swallowed by the per-retailer except, so the run returned nothing
def test_null_min_review_count_still_ranks():
    item_criteria = normalize({"name": "portable charger", "min_review_count": None})
    ranked = asyncio.run(
        run_pipeline(item_criteria, LAT, LON, item_criteria["radius_miles"])
    )
    assert ranked


# same class: a null keywords crashed build_query and a null must_haves crashed the filter
def test_normalize_replaces_null_lists():
    filled = normalize({"name": "x", "keywords": None, "must_haves": None})
    assert filled["keywords"] == []
    assert filled["must_haves"] == []


def test_null_lists_still_rank():
    item_criteria = normalize(
        {"name": "portable charger", "keywords": None, "must_haves": None}
    )
    ranked = asyncio.run(
        run_pipeline(item_criteria, LAT, LON, item_criteria["radius_miles"])
    )
    assert ranked


def test_parse_json_reply_fenced():
    assert parse_json_reply('```json\n{"type": "followup"}\n```') == {"type": "followup"}


def test_parse_json_reply_in_prose():
    assert parse_json_reply('Sure, here it is: {"a": 1} hope that helps') == {"a": 1}


def test_parse_json_reply_garbage():
    assert parse_json_reply("no json at all") is None
