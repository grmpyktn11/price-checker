import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

# how one retailer search ended. the three failure kinds each need a different fix - a bot wall
# needs different headers, a parse of nothing needs new selectors, an empty result set needs a
# different query - so they are never collapsed into one "failed"
OK = "OK"                                                   # rows came back
OK_BUT_EMPTY = "OK_BUT_EMPTY"                               # real answer, genuinely no matches
BLOCKED = "BLOCKED"                                         # captcha/403/challenge page
SELECTORS_RETURNED_NOTHING = "SELECTORS_RETURNED_NOTHING"   # real page, parser found no rows
ERROR = "ERROR"                                             # raised before anything was parsed
# not an outcome: the live-progress state for a search that has not come back yet
SEARCHING = "SEARCHING"
# outcomes where the retailer actually answered the question. anything else means the search
# did not happen, which is not the same as "nothing matched"
ANSWERED = (OK, OK_BUT_EMPTY)
# plain-English meaning of each outcome, carried in the trace so it can be displayed without
# reading any backend code
OUTCOME_DETAIL = {
    OK: "the search answered and product rows were parsed out of it",
    OK_BUT_EMPTY: "the search answered and said it has no matching products",
    BLOCKED: "a bot wall answered instead of the search: captcha, 403 or a challenge page",
    SELECTORS_RETURNED_NOTHING: "a real page loaded but the parser found no product rows in it",
    ERROR: "the request raised before anything could be parsed",
}

MAX_TRACES = 5   # in memory only, no table, cleared on restart

_recent: deque = deque(maxlen=MAX_TRACES)
# traces of runs still in flight, by caller-supplied key, so a client can poll what its own
# search is doing. a finished run leaves this and lands in _recent instead
_live: dict = {}
# per asyncio task, so one request's trace can never be another's. deliberately not reset at
# finish: the handler that ran the pipeline reads its own trace back with current()
_current: ContextVar = ContextVar("current_trace", default=None)


def elapsed_ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


# pure: which of the three outcomes a search page ended in. blocked wins, then rows, then the
# page's own "no results" wording decides between an empty result set and a broken parser
def search_outcome(blocked: bool, empty_page: bool, rows: int) -> str:
    if blocked:
        return BLOCKED
    if rows:
        return OK
    return OK_BUT_EMPTY if empty_page else SELECTORS_RETURNED_NOTHING


class Trace:
    def __init__(self, query: str, criteria: dict):
        self.started = time.monotonic()
        self.key: str | None = None   # set when the caller wants live progress
        self.stage_name: str | None = None
        # every retailer this run intends to search. with the searches running concurrently
        # nothing reports for ~25 seconds, and a waiting screen that lists nothing for 25
        # seconds looks broken rather than busy
        self.expected: list[str] = []
        # reported by whichever scraper ran, claimed by the pipeline stage that asked for it
        self.searches: list[dict] = []
        self.data = {
            "trace_id": uuid.uuid4().hex[:8],
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": query,
            "criteria": criteria,
            "retailers": [],        # one row per retailer search, with its outcome
            "review_lookup": {},    # the extra Amazon searches spent on ratings
            "stores": {},           # the Places lookup
            "product_filter": {},   # the one qualification call
            "candidates": [],       # what survived to ranking, with what evidence
            "research": [],         # per product: reddit, youtube
            "youtube": {},          # whether the too_close decision spent any quota
            "drops": [],            # every candidate removed, with a readable reason
            "stages_ms": {},
            "total_ms": 0,
            "retailers_answered": False,
            "products_returned": 0,
        }


def current() -> Trace | None:
    return _current.get()


def start(query: str, criteria: dict, key: str | None = None) -> Trace:
    trace = Trace(query, criteria)
    trace.key = key
    if key:
        _live[key] = trace
    _current.set(trace)
    return trace


def finish(products_returned: int) -> dict | None:
    trace = _current.get()
    if trace is None:
        return None
    # dropped before the data is filled in, so a poll that races the last line of the run
    # gets "no run in flight" and the client stops polling rather than showing a stale stage
    _live.pop(trace.key, None)
    data = trace.data
    data["products_returned"] = products_returned
    data["total_ms"] = elapsed_ms(trace.started)
    data["retailers_answered"] = any(row.get("outcome") in ANSWERED for row in data["retailers"])
    _recent.append(data)
    return data


def last() -> dict | None:
    return _recent[-1] if _recent else None


def recent() -> list[dict]:
    return list(_recent)


# called by the scrapers, so a search reports what the site did without changing its signature.
# a no-op outside a pipeline run, which is what scripts and unit tests are
def record_search(retailer: str, url: str, outcome: str, raw_rows: int,
                  page_chars: int | None = None, http_status: int | None = None,
                  detail: str | None = None) -> None:
    trace = _current.get()
    if trace is None:
        return
    trace.searches.append({
        "retailer": retailer,
        "search_url": url,
        "outcome": outcome,
        "http_status": http_status,
        # a 2KB page is a block page, a 1.8MB one is a real page that failed to parse
        "page_chars": page_chars,
        "raw_rows": raw_rows,
        "detail": detail or OUTCOME_DETAIL.get(outcome, ""),
    })


# the stage that ran the search takes its row; whatever is left belongs to a later stage
def claim_search(retailer: str) -> dict | None:
    trace = _current.get()
    if trace is None:
        return None
    for index, row in enumerate(trace.searches):
        if row["retailer"] == retailer:
            return trace.searches.pop(index)
    return None


def unclaimed_searches() -> list[dict]:
    trace = _current.get()
    if trace is None:
        return []
    rows, trace.searches = trace.searches, []
    return rows


# a retailer that raised before reporting anything: the pipeline saw an exception, the site
# never got as far as an outcome
def missing_search_row(retailer: str) -> dict:
    return {
        "retailer": retailer,
        "search_url": None,
        "outcome": ERROR,
        "http_status": None,
        "page_chars": None,
        "raw_rows": 0,
        "detail": OUTCOME_DETAIL[ERROR],
    }


# one row per retailer: what the site did, plus what the run then kept from it
def retailer(name: str, ms: int, candidates: int, error: str | None = None) -> None:
    trace = _current.get()
    if trace is None:
        return
    row = {**(claim_search(name) or missing_search_row(name)),
           "ms": ms, "candidates_kept": candidates, "error": error}
    trace.data["retailers"].append(row)


def note(section: str, value) -> None:
    trace = _current.get()
    if trace is None:
        return
    trace.data[section] = value


def append(section: str, row: dict) -> None:
    trace = _current.get()
    if trace is None:
        return
    trace.data[section].append(row)


# one product's research row, filled in further when a later source is searched for it
def update_research(index: int, fields: dict) -> None:
    trace = _current.get()
    if trace is None:
        return
    rows = trace.data["research"]
    if 0 <= index < len(rows):
        rows[index].update(fields)


def drop(stage: str, name: str | None, retailer_name: str, reason: str) -> None:
    trace = _current.get()
    if trace is None:
        return
    trace.data["drops"].append({
        "stage": stage, "name": name, "retailer": retailer_name, "reason": reason
    })


# ms per named stage, so a 60s search can be attributed
@contextmanager
def stage(name: str):
    started = time.monotonic()
    entered = _current.get()
    if entered is not None:
        entered.stage_name = name
    try:
        yield
    finally:
        trace = _current.get()
        if trace is not None:
            trace.data["stages_ms"][name] = elapsed_ms(started)


# what a run still in flight has done so far. everything here is already accumulating on the
# trace as the pipeline works, so this is a read, not a second bookkeeping path that could
# disagree with the debug trace. None once the run finishes, which is the client's stop signal
def live(key: str) -> dict | None:
    trace = _live.get(key)
    if trace is None:
        return None
    data = trace.data
    done = {row["retailer"]: row for row in data["retailers"]}
    # every expected retailer appears immediately as SEARCHING and is replaced by its real
    # outcome as it lands. anything that reported without being expected is still listed
    names = trace.expected + [name for name in done if name not in trace.expected]
    return {
        "stage": trace.stage_name,
        "elapsed_ms": elapsed_ms(trace.started),
        "retailers": [
            {"retailer": name,
             "outcome": done[name]["outcome"] if name in done else SEARCHING,
             "candidates_kept": done[name].get("candidates_kept") if name in done else None}
            for name in names
        ],
        "qualified": data["product_filter"].get("qualified"),
        "products_in": data["product_filter"].get("products_in"),
        "researched": len(data["research"]),
    }


# how a retailer's main search ended, while the run is still going. the review lookup uses
# this to not spend more searches on a retailer that already answered with a bot wall
# the retailers this run will search, in the order they were started
def expect_retailers(names: list[str]) -> None:
    trace = _current.get()
    if trace is not None:
        trace.expected = list(names)


def outcome_so_far(retailer_name: str) -> str | None:
    trace = _current.get()
    if trace is None:
        return None
    for row in trace.data["retailers"]:
        if row["retailer"] == retailer_name:
            return row["outcome"]
    return None


# {retailer: outcome} for the three main searches, which is what narration needs to tell a
# failed search from an empty one
def retailer_outcomes(data: dict | None) -> dict[str, str]:
    if not data:
        return {}
    return {row["retailer"]: row["outcome"] for row in data.get("retailers", [])}
