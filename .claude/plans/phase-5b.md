# Phase 5b Plan — targeted fixes for what Phase 5 surfaced

Not a feature phase. Four items, all narrow:

1. **Persistent warm browser profile** (user-approved) — Best Buy product pages are unreachable from a cold context.
2. **Fuzzy spec-name matching** (user-approved) — retailers print the same spec under different names.
3. **Target store/fulfillment data** (user-asked) — timeboxed experiment, additive, may end in "no".
4. **Carry-over decision** — `DETAIL_LOOKUPS_PER_RETAILER` vs `MAX_PRODUCTS_PER_RETAILER`.

Nothing else. No review sources, no LLM calls #2/#3/#4, no scheduler, no frontend, no new retailer.
Phase 5's binding user decisions stay in force except where item 4 explicitly revises one.

---

## 0. File list

| File | Action | Item |
|---|---|---|
| `backend/scrapers/browser.py` | edit | 1 — persistent context, launch lock, profile dir |
| `scripts/warm_profile.py` | new | 1 — manual, headed, one-time profile seeding |
| `.env.example` | edit | 1 — add `BROWSER_PROFILE_DIR` |
| `.gitignore` | edit | 1 — defensive `browser-profile/` entry |
| `backend/services/ranking.py` | edit | 2 — `find_spec_value` rewrite, two helpers |
| `tests/test_ranking.py` | edit | 2 — matching cases using real fixture keys |
| `backend/scrapers/target.py` | edit | 3 — additive fulfillment path only, if it works |
| `tests/fixtures/target_fulfillment.json` | new, conditional | 3 — only if step 1/2/3 succeeds |
| `tests/test_scrapers.py` | edit | 3 — `parse_fulfillment` test, if it exists |
| `backend/services/pipeline.py` | edit | 4 — one constant |
| `.claude/plans/phase-5b.md` | this file | — |

Do **not** create: a config module, a `stealth.py`, a proxy helper, a retry helper, a browser
pool class, `scheduler.py`, any router, any frontend file.

---

# ITEM 1 — Persistent warm browser profile

## 1.1 The problem, restated as observed

Every Best Buy **product page** navigation from a cold Playwright context fails with
`net::ERR_HTTP2_PROTOCOL_ERROR` — headless and headed, so it is not a headless-detection issue.
`browser.py` catches it, returns `""`, and `looks_blocked` reports a block. Search pages
sometimes load; product pages do not. The same URLs load normally in a long-lived browser
profile that has cookies and history for the site. `bestbuy_product.html` was captured that way,
which is why `bestbuy.parse_specs` / `parse_reviews` carry an UNPROVEN LIVE comment.

Hypothesis being tested: Akamai is rejecting **sessions with no state**, not the automation.
A returning browser with a real profile is the ordinary-behaviour answer to that.

## 1.2 The deviation from spec.md, stated

`spec.md`, `amazon.py` (Tier B) section:

> One browser instance per call, closed immediately after (no persistent browser process)

Phase 5b keeps **the process half of that rule and breaks the state half**:

- Still one browser instance per call, closed immediately in a `finally`. No module-level
  browser, no pool, no `atexit`, no process outliving the call.
- The **profile on disk** (cookies, localStorage, history, cache) now persists between calls.

Why the user approved it: without it Best Buy contributes nothing at all, and the alternative
ways to get past the block — stealth plugins, proxies, UA rotation, retry escalation — are all
explicitly out of scope and were rejected in Phase 5 §12. A browser that remembers the sites you
visited is what every real browser does; that is the whole change.

Recorded in `browser.py` as a comment at `PROFILE_DIR`, in the shape Phase 5 used for the
denied Best Buy API, so nobody "restores" it later.

## 1.3 Open/close per call, reusing the on-disk profile — recommended

Two candidate shapes:

**A. Per-call `launch_persistent_context`, closed at the end of the call. RECOMMENDED.**
Chromium starts, reads the profile from disk (cookies, history, storage), does the work, exits.
Next call starts a new process against the same directory.

**B. One `launch_persistent_context` kept open for the process lifetime.**
Chromium runs continuously alongside uvicorn.

Pick A. Justification, in order:

1. **It tests the actual hypothesis.** The claim is that the block targets sessions with no
   cookies/history. Those live on disk and survive the close. If A does not work but B does,
   the real requirement was a live TLS/HTTP2 connection, which is a different and much larger
   claim — and one worth reporting rather than engineering around.
2. **Deviation stays minimal.** A keeps the spec's process rule verbatim. B abandons it.
3. **Lifecycle cost.** B needs startup/shutdown hooks in `main.py`, crash recovery (a dead
   context must be relaunched mid-request), and leaves a Chromium process on the VPS for the
   9,999 seconds between 6-hourly scrape jobs. A needs none of that.
4. **Block resistance.** A profile that reconnects periodically from a fresh process is closer
   to human behaviour than a browser tab open for six hours. B is not obviously better here and
   is definitely more machinery.
5. **Concurrency.** Both need serialization (§1.5), but B's shared context also shares pages,
   cookies mid-write, and the single-entry product cache across two concurrent runs.

Cost accepted: A keeps the ~0.5-1.5s Chromium launch per call. Already in the ~2 minute budget
from Phase 5 §4. Do not optimize it.

## 1.4 Where the profile lives

Outside the repo tree, always. Not `./browser-profile`, not `tests/`, not anywhere `git status`
looks.

```
BROWSER_PROFILE_DIR env var, default: Path.home() / ".price-checker" / "browser-profile"
```

- Works unchanged on Windows (`C:\Users\<user>\.price-checker\browser-profile`) and on the
  Linux VPS (`/root/.price-checker/browser-profile` or the service user's home). One line,
  no platform branching.
- `PROFILE_DIR.mkdir(parents=True, exist_ok=True)` once at import. Playwright creates a fresh
  profile in an empty directory, so a missing dir is not an error — it is just cold.
- `.gitignore` gets `browser-profile/` as belt-and-braces for anyone who points the env var at
  the repo. The default already sits outside it.
- Profile size grows (cache, service workers). Not managed this phase. If it becomes a problem
  the answer is to delete the directory, which costs one re-warm.

`.env.example` gains one commented line. Blank env var is still a working default, so the
"blank `.env` means fixtures" rule is untouched — `LIVE_SCRAPE` remains the only network switch.

## 1.5 Concurrency

Chromium takes an exclusive OS-level lock on a `user_data_dir` (`SingletonLock`). A second
launch against the same directory fails or silently forks a temp profile. Both are bad, and
Phase 7's APScheduler runs `scrape_job` in the **same process and same event loop** as chat
requests, so a scheduled rescan overlapping a chat search is a real, expected collision.

**In-process: one module-level `asyncio.Lock` in `browser.py`, held around the whole of
`fetch_html`** — launch through close. Every Playwright fetch in the app goes through
`fetch_html`, so one lock covers everything.

Consequences, all acceptable and to be commented:
- Two concurrent pipeline runs serialize on browser work. Wall time adds; nothing breaks.
  `run_pipeline` is already sequential per retailer, so this changes nothing for a single run.
- The lock doubles as rate limiting: no two page loads ever overlap, which is the request
  pattern a single human browser produces anyway.
- `fetch_product_html`'s 60s single-entry cache stays as-is, outside the lock's concern.

**Cross-process is NOT solved and must not be.** Running `scripts/check_bestbuy.py` while
uvicorn is live is two processes on one profile. Handle it as an error message, not a lock file:
catch the launch error, log
`browser profile is in use by another process; run one scraper at a time`, return `""` (which
`looks_blocked` already reports as a block). No file locks, no PID files, no second profile dir.

## 1.6 Warming — the profile does need seeding

An empty `user_data_dir` is exactly as cold as today's fresh context. Reuse alone only helps
once there is something to reuse. Two ways to get it:

- **Automatic homepage hop before every product load.** Rejected for now: it doubles page loads
  on the hot path, and it is a guess. If manual seeding proves the profile is the fix, an
  automatic warm-up can be reconsidered with data.
- **`scripts/warm_profile.py`, manual, run once. CHOSEN.**

`warm_profile.py` (dev tool, never imported, same shape as the other `scripts/`):
- Launches `launch_persistent_context(PROFILE_DIR, headless=False)` — always headed, ignores
  `PLAYWRIGHT_HEADLESS`, because a human is meant to interact with it.
- Opens `https://www.bestbuy.com`, then `https://www.amazon.com`, then waits for the operator
  to press Enter before closing.
- Prints instructions: accept the cookie banner, dismiss the store/location prompt, click into
  one category, do one search, open one product page. Then Enter.
- Closes the context cleanly so the profile is flushed to disk.

That is the entire mechanism. No cookie injection, no scripted clicking, no headless warm-up
loop.

## 1.7 Exactly what the coder must test, in order

Record the result of each. Steps 1 and 2 are the experiment; the rest characterize it.

1. **Baseline.** Delete `BROWSER_PROFILE_DIR`. `LIVE_SCRAPE=1 python scripts/check_bestbuy.py`
   headless. Expect the same `ERR_HTTP2_PROTOCOL_ERROR` / block as today. If this passes, the
   block was never about session state and item 1 stops here — report that.
2. **The key test.** Run `scripts/warm_profile.py`, do the manual clicks, close. Then rerun
   `check_bestbuy.py` **headless**. Does `get_specs` return a non-empty dict for a live product
   URL?
3. **Is it the disk profile or the live session?** Repeat step 2 in a fresh Python process,
   10+ minutes later. Same result means the on-disk profile is doing the work, which is what
   shape A depends on.
4. **Volume.** Five different product URLs in one run. Does the block return at N=3, N=5?
   Record the number.
5. **Amazon.** Same profile, `check_amazon.py`, 10+ consecutive requests. Does the 503
   "Dogs of Amazon" page still appear after ~6? Record where it starts.
6. **Full pipeline.** `LIVE_SCRAPE=1 python scripts/check_pipeline.py`, timed. Compare wall
   time to Phase 5's recorded figure — persistent-profile launches read more from disk and may
   be slower.

## 1.8 Scope: both retailers, one profile — recommended

Apply the persistent context to **all** Playwright fetches, i.e. Best Buy and Amazon, because
they share `fetch_html` and splitting them means two launch paths and two profiles to reason
about. One profile visiting two retailers is exactly what a real browser does.

Amazon's 503 throttle is a different mechanism (request-rate) from Akamai's cold-session block,
so a warm profile may not help it. That is fine — it costs nothing, and step 7.5 measures it.
If the throttle is unchanged, that is a recorded finding, not a reason to add backoff.

## 1.9 Hard boundary — what is forbidden

The whole point of item 1 is that this stays **ordinary browser behavior: a returning browser
with a profile**. Explicitly out of scope, this phase and any phase, without the user saying so:

- `playwright-stealth`, `undetected-chromedriver`, `puppeteer-extra` plugins, any
  fingerprint-patching library.
- Proxies of any kind, residential or datacenter.
- User-agent rotation. `browser.py` keeps its single realistic UA and nothing more.
- Retry loops, backoff, sleep escalation, or a second attempt after a block.
- Captcha solving, manual or via a service.
- Randomized mouse movement, scripted human-like scrolling, timing jitter beyond the existing
  1-3s post-navigation delay.

**If a warm profile does not unblock Best Buy, the deliverable is a report saying so.**
Best Buy stays wired in, returns empty on block, and the pipeline keeps degrading correctly to
Target plus Amazon — which Phase 5 already established is the acceptance criterion. Whether to
drop Best Buy or spend money is the user's decision.

## 1.10 `browser.py` changes, concretely

Constants added near the existing block:

```
PROFILE_DIR   from BROWSER_PROFILE_DIR env var, default ~/.price-checker/browser-profile
_LAUNCH_LOCK  module-level asyncio.Lock
```

`fetch_html(url, wait_for)` restructured:
- Acquire `_LAUNCH_LOCK` for the whole body.
- `async with async_playwright() as playwright:` unchanged.
- `context = await playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=HEADLESS, user_agent=USER_AGENT, viewport=VIEWPORT, locale="en-US")` — replaces `launch()` +
  `new_context()`. There is no separate `browser` object with a persistent context.
- Page: `launch_persistent_context` opens with a blank page already, so use
  `context.pages[0] if context.pages else await context.new_page()`. Do not open a second tab.
- Wrap the launch itself in `try/except PlaywrightError` for the profile-in-use case (§1.5) —
  log the readable message, return `""`.
- Everything else unchanged: `goto` with the existing `PlaywrightError` catch, the optional
  `wait_for`, the randomized delay, `page.content()`.
- `finally: await context.close()`. Closing the context is what flushes the profile; skipping
  it loses the cookies the whole item depends on.

`fetch_product_html` and `looks_blocked` are unchanged.

---

# ITEM 2 — Fuzzy spec-name matching

## 2.1 Correction to the premise

`find_spec_value` in the committed `ranking.py` is **not** exact-only. It already does exact
(case-insensitive) then `wanted in key.lower()` — a one-directional substring pass. The real gap
is narrower and worth naming precisely:

- **Only one direction.** `field="Battery Capacity"` against Best Buy's key `"Capacity"` checks
  `"battery capacity" in "capacity"` → False. The retailer printing a **shorter** name than the
  user's rule is the exact case that fails, and it is the reported bug.
- **No punctuation/whitespace normalization.** `"Dimensions (Overall)"`, `"Item Dimensions
  L x W x Thickness"`, `"Battery Capacity :"` all miss.
- **Raw substring is too loose in the other direction.** `field="Capacity"` currently matches the
  first key containing the letters, in dict order, with no tie-break — on Target that pool
  includes the marketing soft-bullet `"Generous Capacity, Exceptional Portability"`.

## 2.2 Real spec keys in the committed fixtures

Read from the fixtures, not invented.

**Best Buy** (`bestbuy_product.html`, `#key-specs-list`) — 6 keys:
`Brand`, `Model Number`, `Product Name`, `Color`, **`Capacity`** (`"20000 milliampere hours"`),
`Battery Chemistry`.

**Target** (`target_pdp.json`, second product) — 13 bullet keys plus 5 soft-bullet keys = 18:
`Model Compatibility`, `Dimensions (Overall)`, `Weight` (`"1.95 Pounds"`),
`Batteries Charged Simultaneously`, **`Battery Capacity`** (`"24000 (mAh)"`), `Volts`,
`Electronics Features`, `Connection Type`, `Package Quantity`, `Wattage Output`, `Rechargeable`,
`Battery`, `Warranty`, plus marketing soft bullets including
`Generous Capacity, Exceptional Portability` and `Cutting-Edge Bi-Directional Fast Charging`.

**Amazon** (`amazon_product.html`, overview + two detail tables) — 33 keys, including:
`Connector Type`, `Brand`, **`Battery Capacity`**, `Color`, `Special Feature`, `Voltage`,
`Power Source`, `Amperage`, **`Number of Ports`**, `Output Current`, `Compatible Devices`,
`Output Wattage`, `Number of Outlets`, `Item Dimensions L x W x Thickness`, `Item Dimensions`,
`Battery Weight`, `Model Name`, `Model Number`, `Number of Items`, `Manufacturer`,
`Number of Batteries`, `Battery Cell Type`, `ASIN`, `Best Sellers Rank`, `Customer Reviews`.

Note what this shows: `CANNED_CRITERIA`'s must_have `Battery Capacity >= 20000` hits Amazon and
Target exactly and **misses Best Buy entirely** — Best Buy's key is `Capacity`. And its
preferred_spec `Number of USB Ports` misses Amazon's `Number of Ports` in *both* substring
directions. Those two are the algorithm's acceptance cases.

## 2.3 Algorithm

Three passes, first hit wins. No library, no scoring, no thresholds.

Normalization, one helper:

```
normalize_spec_name(name) -> tuple of tokens
  lowercase, replace every non-alphanumeric character with a space, split on whitespace
```

`"Item Dimensions L x W x Thickness"` → `("item","dimensions","l","x","w","thickness")`
`"Dimensions (Overall)"` → `("dimensions","overall")`
`"Battery Capacity :"` → `("battery","capacity")`

Matching, in `find_spec_value(specs, field)`:

1. **Exact normalized match.** Token tuples equal → return that value immediately. This is what
   keeps `Item Dimensions` from being answered by `Item Dimensions L x W x Thickness`.
2. **Token-subset match, either direction.** Collect every key whose token *set* is a subset of
   the field's token set, or whose token set is a superset of it. Empty pool → step 3 does not
   apply, return `None`.
3. **Tie-break within the pool: fewest tokens wins; on a tie, first in `specs` insertion order.**

Insertion order is meaningful and free: the scrapers build `specs` in page order, so real spec
tables land before Target's marketing soft bullets and before Amazon's lower detail tables.

That is the entire algorithm — roughly 12 lines replacing the current 9. `spec_passes`,
`compute_spec_match`, `passes_must_haves` are untouched; they call `find_spec_value` and know
nothing about this.

Log one `logger.debug` line when the pool has more than one candidate, naming the field and the
chosen key. `ranking.py` has no logger today; adding one is fine and is the only import added.

## 2.4 Worked examples, against the real keys above

| Field asked for | Retailer | Pool | Chosen | Why |
|---|---|---|---|---|
| `Battery Capacity` | Best Buy | `Capacity` (key ⊂ field) | `Capacity` → `20000 milliampere hours` | **The reported bug, fixed.** `first_number` → 20000.0, must_have passes |
| `Battery Capacity` | Amazon | exact | `Battery Capacity` | step 1, pool never built |
| `Battery Capacity` | Target | exact | `Battery Capacity` → `24000 (mAh)` | step 1 |
| `Number of USB Ports` | Amazon | `Number of Ports` (key ⊂ field) | `Number of Ports` | **Second bug fixed**; raw substring fails both directions |
| `Number of USB Ports` | Best Buy / Target | empty | `None` | correct — neither publishes it, fail closed |
| `Capacity` | Target | `Battery Capacity` (2 tok), `Generous Capacity, Exceptional Portability` (4 tok) | `Battery Capacity` | fewest tokens; marketing bullet loses |
| `Capacity` | Amazon | `Battery Capacity` (2 tok) | `Battery Capacity` | single candidate |
| `Product Weight` | Target | `Weight` (key ⊂ field) | `Weight` → `1.95 Pounds` | matches, then **fails** `<= 1.0`. A real match producing a real failure, not a silent miss |
| `Product Weight` | Amazon | empty — `Battery Weight` is neither sub- nor superset | `None` | correct: `Battery Weight` is a different quantity |
| `Item Dimensions` | Amazon | exact | `Item Dimensions` | step 1 beats `Item Dimensions L x W x Thickness` |
| `Battery` | Amazon | `Battery Capacity`, `Battery Weight`, `Battery Cell Type` (all key ⊃ field, 2-3 tok) | `Battery Capacity` | 2 tokens, first in insertion order among the 2-token candidates |
| `Battery` | Target | exact `Battery` | `Battery` → `1 Non-Universal Lithium Ion` | step 1 |

## 2.5 Failure modes, accepted and documented

Put these in the comment block above `find_spec_value`, next to the existing "limits, by design"
block on `spec_passes`.

- **Both keys present.** `Capacity` *and* `Battery Capacity` in one dict: step 1 picks `Capacity`
  exactly. If the field were `Battery Capacity`, step 1 picks that. Only a field matching neither
  exactly falls to step 2, where fewest-tokens applies. Deterministic in every case.
- **Plurals and stems are not handled.** `Port` will not match `Ports`. Adding a stemmer is a
  fuzzy-matching library by another name; the criteria prompt already tells the model to use the
  name "as a retailer prints it".
- **Vague single-token fields are ambiguous by nature.** `Battery` on Amazon has four plausible
  answers and the tie-break picks one. The debug log is how that gets diagnosed. Not solved.
- **Semantically different specs with shared tokens still match.** `Output Wattage` vs
  `Wattage` matches (correct); `Output Current` vs `Current` matches (correct); nothing in the
  fixture set produces a wrong match, but a future one could. Fail-closed only guards missing
  specs, not wrong ones.
- **Still no unit conversion.** `20000 milliampere hours` and `24000 (mAh)` both reduce to their
  first number. `mAh` vs `Wh` would compare nonsense. Unchanged from Phase 2, still out of scope.

## 2.6 Tests (`tests/test_ranking.py`)

Add a section using literal dicts built from the §2.2 key lists — do **not** import fixtures
(Phase 5 §11 deliberately decoupled `test_ranking.py` from scraper fixtures).

- `find_spec_value(BESTBUY_SPECS, "Battery Capacity")` → the `Capacity` value.
- `find_spec_value(AMAZON_SPECS, "Number of USB Ports")` → the `Number of Ports` value.
- `find_spec_value(AMAZON_SPECS, "Item Dimensions")` → the exact key's value, not the
  `L x W x Thickness` one.
- `find_spec_value(TARGET_SPECS, "Capacity")` → `Battery Capacity`'s value, not the soft bullet.
- `find_spec_value(AMAZON_SPECS, "Product Weight")` → `None`.
- `find_spec_value({"Dimensions (Overall)": "x"}, "dimensions overall")` → `"x"` (punctuation).
- `passes_must_haves(BESTBUY_SPECS, [{"field": "Battery Capacity", "op": ">=", "value": 20000}])`
  → `True`. This is the end-to-end regression for the reported bug.
- Every existing `test_ranking.py` assertion must still pass unchanged.

---

# ITEM 3 — Target store/fulfillment data (timeboxed experiment)

## 3.1 The problem and the constraint

`plp_search_v2` returns no fulfillment block for anonymous callers, so `parse_search` hardcodes
`store_id: None, distance_miles: None`. Best Buy lost `find_nearby_stores` with the denied API
key and Amazon never had one. So **no retailer supplies a distance**, every candidate scores
`NEUTRAL_SCORE`, and the spec's `0.10 * distance_score` term is dead weight on every product
equally.

`product_summary_with_fulfillment_v1` returns HTTP 206 with nulls even with target.com's exact
params. Likely cause: no visitor cookie and no store preference on the session.

**Hard constraint: `target.py`'s existing httpx search path is not touched.** It works, and it is
the only non-browser scraper in the app. Everything below is a separate function on a separate
code path that returns "no data" on any failure and leaves `parse_search`'s output identical.

## 3.2 The ladder — cheapest first, stop at the first thing that works

**Step 1 — params only, no cookies, no browser. Budget: 45 min.**
Replay `product_summary_with_fulfillment_v1` in a scratch script with the full param set
target.com's own page sends, which the Phase 5 attempt may have had only partially:
`key`, `tcins` (comma-joined), `store_id`, `pricing_store_id`, `scheduled_delivery_store_id`,
`zip`, `state`, `latitude`, `longitude`, `channel=WEB`, `page=/p/A-<tcin>`, `visitor_id`,
`is_bot=false`, `required_store_id`. Real values for lat/lon/zip, and a **real** `store_id` taken
from the already-working `nearby_stores_v1` response rather than `DEFAULT_STORE_ID`.
Success criterion: one non-null `store_options` entry with an availability status.

**Step 2 — add cookies to the existing httpx client. Budget: 45 min.**
Still no browser. Send the cookies target.com sets for an anonymous visitor who has picked a
store, on the same `HEADERS` client:
`visitorId`, `GuestLocation=<zip>|<lat>|<lon>|<state>`, and the store cookie
(`fiatsCookie` / `sapphire`, shaped `DSI_<store_id>|DSN_<name>|C_<zip>`).
Values invented by hand first; if that fails, values copied once from a real browser devtools
session. Success criterion as step 1.

**Step 3 — borrow real cookies from the ITEM 1 profile. Budget: 90 min. Requires ITEM 1 done.**
Extend `scripts/warm_profile.py` (or a sibling) to also open `target.com`, let the operator set a
store through the UI once, then dump `await context.cookies("https://www.target.com")` to
`~/.price-checker/target_cookies.json`. `target.py` loads that file if present and passes the
cookies to its httpx client. The browser stays entirely out of the request path — it is a
one-time credential capture, not a fetch mechanism.
Success criterion as step 1. Cookies expire; the honest fallback when they do is the same
fallback as failure (§3.3), plus a log line saying to re-run the capture.

**Step 4 — drive target.com in Playwright and read rendered availability. NOT IN SCOPE.**
Recommended against, and not to be attempted this phase: it would put our only reliable scraper
behind PerimeterX on a browser path that ITEM 1 has not yet proven, to recover a signal worth
0.10 of the score. If steps 1-3 all fail, that is the answer.

## 3.3 Timebox and give-up criterion

**Total: 3 hours across steps 1-3.** Stop at the first success. Stop unconditionally at 3 hours.

Give-up criterion, concrete: *no request in steps 1-3 returned a fulfillment block containing at
least one non-null `store_options` entry for at least one tcin.*

Fallback on give-up, and it is a legitimate phase outcome:
- Revert every experimental edit. `target.py` returns to its committed state.
- `distance_score` stays `NEUTRAL_SCORE` for every candidate, app-wide.
- Record in the plan and the commit message: no MVP retailer supplies per-product distance; the
  spec's 0.10 distance weight is inert and applies equally to all candidates, so it changes no
  ordering. Note that `find_nearby_stores` still works and still gives Target a real
  `pricing_store_id`, which is what it is used for today.
- Do **not** fake a distance from the store list, do not reweight the score to redistribute the
  0.10, do not delete the term. Reweighting is a spec change and belongs to the user.

## 3.4 If it works — how the data flows back

Shape it so `search()`'s six-key contract and `parse_search` are unchanged.

- New pure parser `parse_fulfillment(payload) -> dict[str, dict]`, keyed by tcin, valued
  `{"store_id": str, "distance_miles": float | None}`. Pure, fixture-testable, no network.
- New `add_fulfillment(rows, store_id) -> list[dict]` in `target.py`: pulls the tcins back out of
  the row urls with the existing `tcin_from_url`, makes **one** batched request for all of them,
  and fills `store_id`/`distance_miles` on the matching rows. On any exception or empty parse it
  logs a warning and returns `rows` unchanged. Wrapped in `try/except httpx.HTTPError` like every
  other live call in the file. No retries.
- Called from `TargetScraper.search` only, after `parse_search`, only on the live path, only when
  `store_ids` was supplied. Fixture mode never calls it.
- **Distance source.** If `store_options` carries a distance, use it. If it only carries
  availability, fall back to the distance of that `store_id` from the `nearby_stores_v1` response
  already fetched this run — `parse_stores` returns exactly that. Mechanically this needs the map
  to survive from `find_nearby_stores` to `search`; the cheapest honest way is a module-level
  `{store_id: distance}` dict in `target.py` written by `find_nearby_stores`. That is scraper
  state across two calls of the same run, which is a small deviation worth calling out — flag it
  in the commit message. Do not thread a new argument through `ScraperBase.search`; that changes
  the interface for three scrapers to serve one.
- Fixture `tests/fixtures/target_fulfillment.json` (real captured response) plus a
  `test_scrapers.py` case: every value has a `str` `store_id` and a `float`-or-`None`
  `distance_miles`. Offline, like everything else.
- `compute_distance_score` and `pipeline.py` need **zero** changes — they already read
  `product["distance_miles"]`.

---

# ITEM 4 — Carry-over: the detail-lookup cutoff

## 4.1 The contradiction

`pipeline.py` today:

```
MAX_PRODUCTS_PER_RETAILER = 3
DETAIL_LOOKUPS_PER_RETAILER = 2
```

Candidates past position 2 take the no-detail branch, and that branch drops the product outright
whenever `must_haves` or `min_review_count` is set (`skip ...: no detail lookup, filters
unverifiable`). `CANNED_CRITERIA` sets both. So in practice the third product is always dropped
and `MAX_PRODUCTS_PER_RETAILER = 3` buys nothing over 2 — the constant overstates what the
pipeline does, and the binding decision's stated intent ("ranking runs on thinner spec data for
lower-ranked candidates") never happens.

## 4.2 Options

**A. Raise `DETAIL_LOOKUPS_PER_RETAILER` to 3. RECOMMENDED.**
**B. Lower `MAX_PRODUCTS_PER_RETAILER` to 2** so the constant stops lying.
**C. Change the drop rule** — keep past-cutoff candidates and rank them on empty specs.

Reject **C**: `spec.md` makes `must_haves` a hard filter (`if not passes_must_haves(...): continue`).
Ranking an unverified product means a product that fails the user's hard requirement can be shown
to them. That is a spec violation, not a tuning choice. The binding decision's "thinner data"
intent is only reachable for criteria with *no* must_haves and `min_review_count = 0`, and the
existing branch already handles exactly that case correctly.

Reject **B**: it saves nothing in the common case (the third product is already skipped before
any page load, so B removes a candidate the user could have gotten for free when the criteria are
loose) and it shrinks the pool feeding `narration.TOP_N = 5` across three retailers.

## 4.3 Recommendation: A, with the latency stated

Set `DETAIL_LOOKUPS_PER_RETAILER = 3`. Keep both constants and the branch — the mechanism is
correct and stays useful if the cap is ever raised; only the number was wrong.

Latency cost, honestly: one extra product page load per Playwright retailer. `get_specs` and
`get_reviews` share one load through the 60s cache, so that is +1 load for Best Buy and +1 for
Amazon = **+2 page loads, roughly +10 seconds** on the ~2 minute live run (Phase 5 §4's
arithmetic: ~5s per load). Target's extra pdp call is ~200ms of JSON.

Update the comment block above the constants with the new arithmetic:
per Playwright retailer 1 search + 3 product loads = 4, two retailers = 8 loads, ~40s of the run.

This revises Phase 5's binding decision #2 in one number only. The hard cap of 3 products per
retailer stands.

---

## 5. Known, recorded, NOT fixed this phase

**`tests/fixtures/bestbuy_search.html` has 2 hydrated tiles out of 18.** The grid is virtualized
and the capture was taken with only the top of the page rendered, so `parse_search` is exercised
against 2 rows. The plan's stated fixture requirements — an out-of-stock tile and a no-price tile
— are therefore **unexercised for Best Buy**. `parse_search`'s `in_stock: None` branch and its
`price: None` branch have no offline coverage.

Not fixed here: recapture is a live operation and it is currently blocked, which is precisely
what ITEM 1 is about. **If ITEM 1 succeeds, recapture becomes possible and is worth doing** —
scroll the search page before `page.content()` so more tiles hydrate, then rerun the full offline
suite against the new fixture. Treat it as the first follow-up after ITEM 1 lands, not as part
of ITEM 1's acceptance.

---

## 6. Verification checklist

### Part A — offline. This is the gate. All must pass.

Blank `.env` (no `LIVE_SCRAPE`, no keys), network disconnected, run from the repo root.

1. `python -m pytest tests -q` — green. Count is **90 today**; expect 90 + the new
   `test_ranking.py` matching cases (§2.6) + `parse_fulfillment` if ITEM 3 landed. Record the new
   number in the commit message. **No test count may drop.**
2. Run it again with the network physically disconnected: identical result. No test may touch
   Playwright, the profile directory, or the network.
3. `grep -rn "async_playwright\|launch_persistent_context" backend/` → matches only in
   `backend/scrapers/browser.py`.
4. `grep -rn "stealth\|proxy\|undetected\|user_agent=\[" backend/ scripts/` → zero matches
   beyond the single `USER_AGENT` constant. §1.9 holds.
5. `git status` after a full offline run → clean. No profile directory, no cookie file, no
   `browser-profile/` anywhere under the repo. The default path is outside the tree; confirm it
   by printing `browser.PROFILE_DIR` and checking it is not under the repo root.
6. `python scripts/check_bestbuy.py` / `check_target.py` / `check_amazon.py` → `MODE: FIXTURE`,
   the shared-product-page warning, and output byte-identical to Phase 5's. Fixture mode must
   never launch a browser: confirm no Chromium process appears.
7. `python scripts/check_pipeline.py` → `MODE: FIXTURE`. With `DETAIL_LOOKUPS_PER_RETAILER = 3`,
   Best Buy now contributes a candidate it previously skipped **and** the ITEM 2 fix makes its
   `Battery Capacity >= 20000` must_have pass against the `Capacity` key. Record the new
   per-retailer candidate counts and compare to Phase 5's. A Best Buy count going from 0 to
   non-zero is the visible proof both items landed.
8. `grep -n "DETAIL_LOOKUPS_PER_RETAILER" backend/services/pipeline.py` → set to 3, comment
   arithmetic updated, the branch still present.
9. Grep the diff for emojis → zero.

### Part B — live. Information only. May legitimately fail.

`LIVE_SCRAPE=1`. One run each, never in a loop, never concurrently.

10. The full §1.7 sequence, steps 1-6, with each result recorded. Step 1 (cold baseline still
    blocked) and step 2 (warm profile unblocks) are the two that matter.
11. Concurrency check: start `uvicorn`, fire two `/api/chat/message` requests at once, confirm
    they serialize on `_LAUNCH_LOCK` and both complete — no `SingletonLock` error, no second
    Chromium.
12. Cross-process check: with uvicorn live, run `python scripts/check_bestbuy.py`. Expect the
    readable `browser profile is in use` log line, not a traceback.
13. ITEM 3 steps 1-3 within the 3-hour timebox. Record which step, if any, produced a non-null
    `store_options`, and the exact params/cookies that did it.
14. `LIVE_SCRAPE=1 python scripts/check_pipeline.py` once, timed. Compare wall time to Phase 5's
    recorded figure and record it.
15. If ITEM 1 succeeded: recapture `bestbuy_search.html` with scrolling (§5), rerun all of Part A.

**Part B failing does not fail the phase.** A cold-and-warm-both-blocked result for Best Buy, and
a give-up on ITEM 3, are both legitimate deliverables — provided ITEM 2 and ITEM 4 are green
offline and the degradation still holds: a blocked Best Buy must never empty the run.

---

## 7. Decisions not covered by spec.md

1. **Persistent browser profile.** Direct, user-approved deviation from `spec.md`'s "One browser
   instance per call, closed immediately (no persistent browser process)". Scoped to state on
   disk; the one-instance-per-call process rule is kept (§1.2, §1.3).
2. **`BROWSER_PROFILE_DIR` env var.** New config the spec does not list. Defaults to a path
   outside the repo; blank still works, so "blank `.env` means fixtures" is unaffected (§1.4).
3. **Module-level `asyncio.Lock` serializing all Playwright fetches.** Not in the spec. Required
   because Chromium exclusively locks a `user_data_dir` and Phase 7 runs the scheduler in the
   same process as chat (§1.5).
4. **`scripts/warm_profile.py`, a manual headed seeding step.** New operational requirement: the
   app needs a human-run command before live Best Buy scraping works at all. Must be in the
   Deploy notes if it proves out (§1.6).
5. **Cross-process profile collision is reported, not solved.** No lock file, no second profile.
6. **Token-subset spec matching with a fewest-tokens tie-break.** The spec says nothing about
   cross-retailer spec names; `ranking.py`'s current comment says "cross-retailer spec names are
   not normalized". That comment is now wrong and must be rewritten to describe §2.3 and its
   failure modes (§2.5).
7. **Target fulfillment as a timeboxed experiment with a documented give-up.** Not a spec
   feature. The spec's `fulfillment.store_options[].location_id/distance` field map assumes data
   redsky no longer returns anonymously.
8. **The 0.10 distance weight is inert if ITEM 3 gives up.** The spec's `compute_final_score`
   weights are kept verbatim rather than redistributed. Reweighting is a user decision, not a
   coder one (§3.3).
9. **Cookies captured from a browser and replayed by httpx** (ITEM 3 step 3) — a mechanism the
   spec does not contemplate. Only reached after two cheaper steps fail, and the browser stays
   out of the request path.
10. **Module-level `{store_id: distance}` map in `target.py`** if ITEM 3 needs the fallback
    distance source. Scraper state spanning two calls of one run; flagged rather than hidden
    (§3.4).
11. **`DETAIL_LOOKUPS_PER_RETAILER = 3`.** Revises Phase 5 binding decision #2 by one number,
    costing roughly +10s live. The two-tier mechanism and the hard cap of 3 both stand (§4.3).
12. **Option C (ranking past-cutoff candidates on empty specs) is rejected as spec-violating**,
    even though Phase 5's binding decision text implies it. `must_haves` is a hard filter in
    `spec.md`; an unverified candidate cannot be shown (§4.2).
13. **Best Buy search fixture coverage gap recorded, not fixed** (§5).
