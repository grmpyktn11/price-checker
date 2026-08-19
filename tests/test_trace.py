import httpx
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.scrapers import target as target_scraper
from backend.services import trace

# nothing here makes a request or calls a model: a trace is assembled from literal outcomes,
# which is exactly what the pipeline hands it


@pytest.fixture(autouse=True)
def fresh_trace():
    # the ring buffer and the context var are process-wide, so each test starts clean
    trace._recent.clear()
    trace.start("gaming mouse", {"name": "gaming mouse", "min_review_count": 5})
    yield
    trace._recent.clear()


# --- the three failure kinds stay three ---

@pytest.mark.parametrize(
    "blocked,empty_page,rows,expected",
    [
        (False, False, 3, trace.OK),
        # a real answer that says it has nothing: the query is the problem, not the scraper
        (False, True, 0, trace.OK_BUT_EMPTY),
        # a real page with no "no results" wording that parsed to nothing: the parser broke
        (False, False, 0, trace.SELECTORS_RETURNED_NOTHING),
        # a bot wall is a block whatever else the page says
        (True, True, 0, trace.BLOCKED),
        (True, False, 0, trace.BLOCKED),
    ],
)
def test_search_outcome(blocked, empty_page, rows, expected):
    assert trace.search_outcome(blocked, empty_page, rows) == expected


# the night this was built: three retailers, three different failures, one useless "no products"
def test_three_retailers_can_fail_three_different_ways():
    trace.record_search("target", "https://redsky/plp", trace.BLOCKED, 0, http_status=403)
    trace.record_search("amazon", "https://amazon/s?k=x", trace.BLOCKED, 0, page_chars=1800)
    trace.record_search("bestbuy", "https://bestbuy/search", trace.SELECTORS_RETURNED_NOTHING,
                        0, page_chars=1_800_000)
    for retailer in ("target", "amazon", "bestbuy"):
        trace.retailer(retailer, ms=1000, candidates=0)

    data = trace.finish(0)
    assert [row["outcome"] for row in data["retailers"]] == [
        trace.BLOCKED, trace.BLOCKED, trace.SELECTORS_RETURNED_NOTHING
    ]
    # the size is the tell: 1.8KB is a challenge page, 1.8MB is a real page that failed to parse
    assert [row["page_chars"] for row in data["retailers"]] == [None, 1800, 1_800_000]
    assert data["retailers_answered"] is False


def test_every_outcome_carries_a_readable_detail():
    trace.record_search("bestbuy", "u", trace.SELECTORS_RETURNED_NOTHING, 0, page_chars=1_800_000)
    trace.retailer("bestbuy", ms=1, candidates=0)
    assert trace.finish(0)["retailers"][0]["detail"] == \
        trace.OUTCOME_DETAIL[trace.SELECTORS_RETURNED_NOTHING]


# an empty result set is an answer, so the run did search even though it found nothing
def test_an_empty_result_set_still_counts_as_answered():
    trace.record_search("target", "u", trace.OK_BUT_EMPTY, 0, http_status=200)
    trace.retailer("target", ms=10, candidates=0)
    assert trace.finish(0)["retailers_answered"] is True


# the scraper raised before it could report anything
def test_a_retailer_that_raised_is_an_error_row():
    trace.retailer("amazon", ms=5, candidates=0, error="TimeoutError: browser launch failed")
    row = trace.finish(0)["retailers"][0]
    assert row["outcome"] == trace.ERROR
    assert row["search_url"] is None
    assert row["error"] == "TimeoutError: browser launch failed"


# --- claiming ---

# the main amazon search belongs to the retailer row; the review-lookup searches that follow
# are a different stage and must not overwrite it
def test_later_searches_are_left_for_the_stage_that_ran_them():
    trace.record_search("amazon", "search", trace.OK, 3, page_chars=900_000)
    trace.retailer("amazon", ms=100, candidates=3)
    trace.record_search("amazon", "review-lookup", trace.BLOCKED, 0, page_chars=1200)

    left_over = trace.unclaimed_searches()
    assert [row["search_url"] for row in left_over] == ["review-lookup"]
    assert trace.finish(0)["retailers"][0]["search_url"] == "search"


# --- drops ---

def test_drops_name_the_stage_and_the_reason():
    trace.drop("product_filter", "Charger 10,000mAh", "target",
               "10,000mAh, not the 20,000 asked for")
    trace.drop("review_floor", "No Name Mouse", "bestbuy",
               "0 reviews or mentions found, below the 5 the criteria ask for")
    assert trace.finish(0)["drops"] == [
        {"stage": "product_filter", "name": "Charger 10,000mAh", "retailer": "target",
         "reason": "10,000mAh, not the 20,000 asked for"},
        {"stage": "review_floor", "name": "No Name Mouse", "retailer": "bestbuy",
         "reason": "0 reviews or mentions found, below the 5 the criteria ask for"},
    ]


# --- research ---

def test_research_rows_are_filled_in_as_sources_are_searched():
    trace.append("research", {"rank": 1, "name": "Razer Viper", "reddit_posts": 4,
                              "reddit_retried": True, "youtube": False})
    trace.update_research(0, {"youtube": True, "youtube_videos": 5})
    assert trace.finish(0)["research"][0] == {
        "rank": 1, "name": "Razer Viper", "reddit_posts": 4, "reddit_retried": True,
        "youtube": True, "youtube_videos": 5,
    }


# --- timings and storage ---

def test_stages_are_timed():
    with trace.stage("collect_candidates"):
        pass
    data = trace.finish(0)
    assert data["stages_ms"]["collect_candidates"] >= 0
    assert data["total_ms"] >= 0


def test_only_the_last_few_traces_are_kept():
    for index in range(trace.MAX_TRACES + 3):
        trace.start(f"query {index}", {})
        trace.finish(0)
    assert len(trace.recent()) == trace.MAX_TRACES
    assert trace.last()["query"] == f"query {trace.MAX_TRACES + 2}"


def test_retailer_outcomes_is_what_narration_reads():
    trace.record_search("target", "u", trace.BLOCKED, 0)
    trace.retailer("target", ms=1, candidates=0)
    assert trace.retailer_outcomes(trace.finish(0)) == {"target": trace.BLOCKED}
    assert trace.retailer_outcomes(None) == {}


# the pipeline is also run by the scheduler and by scripts, which record nothing
def test_recording_outside_a_run_is_a_no_op():
    trace._current.set(None)
    trace.record_search("amazon", "u", trace.OK, 1)
    trace.drop("review_floor", "x", "amazon", "reason")
    trace.retailer("amazon", ms=1, candidates=0)
    with trace.stage("collect_candidates"):
        pass
    assert trace.finish(0) is None
    assert trace.last() is None


# --- the outcomes the scrapers themselves report ---

def test_target_tells_an_empty_shelf_from_a_changed_response_shape():
    assert target_scraper.has_search_block({"data": {"search": {"products": []}}})
    # redsky answered, but not with the block the parser reads
    assert not target_scraper.has_search_block({"data": {}})


@pytest.mark.parametrize("status,expected", [(403, trace.BLOCKED), (429, trace.BLOCKED),
                                             (500, trace.ERROR), (None, trace.ERROR)])
def test_target_maps_its_status_codes(status, expected):
    request = httpx.Request("GET", "https://redsky.target.com/x")
    if status is None:
        error = httpx.ConnectError("connection reset", request=request)
    else:
        error = httpx.HTTPStatusError(
            "blocked", request=request, response=httpx.Response(status, request=request)
        )
    target_scraper.record_search_failure("https://redsky.target.com/x", error)
    trace.retailer("target", ms=1, candidates=0)
    row = trace.finish(0)["retailers"][0]
    assert row["outcome"] == expected
    assert row["http_status"] == status
    # httpx puts the whole request url in the error text and the redsky key rides in its
    # query string, so neither the detail nor the traced url may carry it
    assert target_scraper.API_KEY not in row["detail"]
    assert target_scraper.API_KEY not in row["search_url"]


# --- the endpoint the debug panel refreshes from ---

@pytest.fixture
def client():
    return TestClient(app)


def test_debug_last_404s_before_any_search(client):
    trace._recent.clear()
    assert client.get("/api/debug/last").status_code == 404


def test_debug_last_returns_the_most_recent_finished_trace(client):
    trace.record_search("bestbuy", "https://bestbuy/search", trace.BLOCKED, 0, page_chars=1500)
    trace.retailer("bestbuy", ms=900, candidates=0)
    finished = trace.finish(0)
    body = client.get("/api/debug/last").json()
    assert body["trace_id"] == finished["trace_id"]
    assert body["retailers"][0]["outcome"] == trace.BLOCKED


# the waiting screen reads the same trace the run is filling in, so it can never disagree
# with the debug panel that shows up afterwards
def test_live_progress_reports_the_run_in_flight():
    trace.start("rgb mouse", {}, key="conv-1")
    with trace.stage("collect_candidates"):
        trace.record_search("target", "u", trace.OK, 4)
        trace.retailer("target", ms=10, candidates=3)
        live = trace.live("conv-1")
    assert live["stage"] == "collect_candidates"
    assert live["retailers"] == [
        {"retailer": "target", "outcome": trace.OK, "candidates_kept": 3}
    ]
    assert live["elapsed_ms"] >= 0


def test_only_the_asking_conversation_sees_its_own_run():
    trace.start("rgb mouse", {}, key="conv-1")
    assert trace.live("conv-2") is None


# the client stops polling on this, so a finished run must not keep reporting a stale stage
def test_a_finished_run_is_no_longer_live():
    trace.start("rgb mouse", {}, key="conv-1")
    trace.finish(3)
    assert trace.live("conv-1") is None


# the scheduler passes no key: nothing is watching a rescan, and an unkeyed run must not
# leak into some other conversation's progress
def test_a_run_with_no_key_is_never_live():
    trace.start("rescan", {})
    assert trace.live(None) is None
    trace.finish(0)


# with the retailers running concurrently nothing reports for ~25 seconds. a waiting screen
# that lists nothing for 25 seconds reads as broken, so every expected retailer shows up on
# the first poll and is replaced by its real outcome as it lands
def test_expected_retailers_show_as_searching_before_they_report():
    trace.start("mouse", {}, key="conv-1")
    trace.expect_retailers(["bestbuy", "target", "amazon", "microcenter"])

    live = trace.live("conv-1")
    assert [row["retailer"] for row in live["retailers"]] == [
        "bestbuy", "target", "amazon", "microcenter"
    ]
    assert {row["outcome"] for row in live["retailers"]} == {trace.SEARCHING}

    trace.record_search("target", "u", trace.OK, 4)
    trace.retailer("target", ms=10, candidates=3)
    live = trace.live("conv-1")
    by_name = {row["retailer"]: row for row in live["retailers"]}
    assert by_name["target"]["outcome"] == trace.OK
    assert by_name["target"]["candidates_kept"] == 3
    # the order is the order they were started, not the order they finished
    assert [row["retailer"] for row in live["retailers"]][0] == "bestbuy"
    assert by_name["bestbuy"]["outcome"] == trace.SEARCHING


# a retailer that reported without being expected is still listed rather than dropped
def test_an_unexpected_retailer_still_appears():
    trace.start("mouse", {}, key="conv-1")
    trace.expect_retailers(["bestbuy"])
    trace.record_search("target", "u", trace.BLOCKED, 0)
    trace.retailer("target", ms=1, candidates=0)
    names = [row["retailer"] for row in trace.live("conv-1")["retailers"]]
    assert names == ["bestbuy", "target"]


# SEARCHING is a live-progress state, never a recorded outcome, so it must not count as an
# answer when narration decides whether the search actually ran
def test_searching_is_not_an_answered_outcome():
    assert trace.SEARCHING not in trace.ANSWERED
