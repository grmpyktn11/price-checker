# Phase 5 Plan — Retailers: Target (Tier A), Amazon (Tier B), Best Buy (Tier B rewrite)

Scope: three scrapers, their fixtures, their check scripts, wiring them into `SCRAPERS`, and two
approved carry-over fixes.

**Not this phase:** review sources (`reviews_reddit/forums/youtube.py` — Phase 6), LLM calls
#2/#3/#4 (Phase 6), the selector-break LLM fallback (deferred, section 8), scheduler/email
(Phase 7), any frontend file (Phase 8). No `routers/items.py`, `listings.py`, `alerts.py`.

There is no browser UI test this phase because there is no new UI. The coder **will** use
Playwright the library for scraping — that is a different thing from the Playwright MCP browser
tools used in Phase 4.

---

## 0. The spec is now wrong about Best Buy

`spec.md` says Best Buy is Tier A via `https://api.bestbuy.com/v1` with `BESTBUY_API_KEY`.
**The API access application was denied. That key will never exist.** Every line of `spec.md`
describing Best Buy as an official API (the "MVP retailers" line in Overview, the `bestbuy.py`
Tier A subsection, the Review Sources table row, `BESTBUY_API_KEY` in Environment Variables) is
obsolete. Best Buy becomes a Tier B Playwright scrape with the same shape as Amazon.

Consequences that fall out of that and are decided below, not left open:
- `BESTBUY_API_KEY` is deleted from `bestbuy.py`, `.env.example`, `conftest.py`, and both check
  scripts. A one-line comment stays in `bestbuy.py` recording that the API was applied for and
  denied, so nobody re-adds it.
- Best Buy loses `find_nearby_stores()` entirely (section 5.5). Target becomes the only source of
  store distance in the whole app.
- The three Best Buy JSON fixtures are deleted and replaced by two HTML fixtures.
- Best Buy is now the **riskiest** component in the build, not the safest: it runs Akamai bot
  detection, which is more aggressive than Amazon's. Section 8 says exactly what happens when it
  blocks, and the verification checklist (section 12) treats a live Best Buy block as an
  **expected outcome, not a failed build**.

---

## 1. File list

| File | Action | Purpose |
|---|---|---|
| `backend/scrapers/base.py` | edit | add `load_fixture_text()` next to `load_fixture()` |
| `backend/scrapers/browser.py` | new | Playwright launch/fetch/delay/block-detect helpers |
| `backend/scrapers/bestbuy.py` | **rewrite** | Tier B Playwright, API code removed |
| `backend/scrapers/target.py` | new | Tier A redsky JSON |
| `backend/scrapers/amazon.py` | new | Tier B Playwright |
| `backend/services/pipeline.py` | edit | 3-scraper `SCRAPERS`, per-retailer cap, carry-over fix A |
| `backend/services/criteria.py` | edit | carry-over fix B: rule validation -> followup |
| `tests/conftest.py` | edit | patch `LIVE_SCRAPE` on all three scrapers |
| `tests/test_ranking.py` | edit | inline `SPECS`, drop the deleted-fixture import |
| `tests/test_scrapers.py` | new | parse functions against fixtures, offline |
| `tests/test_criteria.py` | edit | add the valueless-rule cases |
| `tests/fixtures/bestbuy_search.html` | new | saved search page |
| `tests/fixtures/bestbuy_product.html` | new | saved product page (specs + reviews) |
| `tests/fixtures/target_search.json` | new | saved `plp_search_v2` response |
| `tests/fixtures/target_pdp.json` | new | saved `pdp_client_v1` response |
| `tests/fixtures/target_stores.json` | new | saved `nearby_stores_v1` response |
| `tests/fixtures/amazon_search.html` | new | saved search page |
| `tests/fixtures/amazon_product.html` | new | saved product page |
| `tests/fixtures/bestbuy_response.json` | **delete** | dead, API removed |
| `tests/fixtures/bestbuy_details.json` | **delete** | dead, API removed |
| `tests/fixtures/bestbuy_stores.json` | **delete** | dead, `find_nearby_stores` removed |
| `scripts/check_bestbuy.py` | rewrite | no key, no stores section |
| `scripts/check_target.py` | new | standalone print-and-eyeball |
| `scripts/check_amazon.py` | new | standalone print-and-eyeball |
| `scripts/check_pipeline.py` | edit | mode banner reads `LIVE_SCRAPE`, not the dead key |
| `scripts/save_fixtures.py` | new | dev tool: live-fetch and write every fixture |
| `requirements.txt` | edit | `playwright`, `beautifulsoup4`, `lxml` |
| `.env.example` | edit | drop `BESTBUY_API_KEY`, add `LIVE_SCRAPE`, `PLAYWRIGHT_HEADLESS` |

Do **not** create: `spec_extraction.py`, `sentiment.py`, `reviews_*.py`, `scheduler.py`,
`geocode.py`, `email.py`, any router, any frontend file.

### Manual step, call it out in the commit message

```
pip install -r requirements.txt
playwright install chromium        # downloads ~150MB, one time, not done by pip
```

`playwright install chromium` is **not** covered by `pip install`. Without it every Playwright
call raises at launch time. On the VPS it is `playwright install --with-deps chromium` (already
in the spec's Deploy section). The check scripts should print a readable hint when the launch
raises the "Executable doesn't exist" error rather than dumping a raw traceback.

---

## 2. Fixture mode with no API key — the design problem, and the answer

The Phase 1 rule was "the presence of the API key is the switch." Two of the three scrapers now
have no key at all, and the third just lost its. The property that must survive is not the key —
it is that **both paths call the same parse function**, so the fixture exercises the real field
mapping.

### The switch

One env var, `LIVE_SCRAPE`, read once at import into a module constant in each of the three
scraper files:

```python
LIVE_SCRAPE = os.getenv("LIVE_SCRAPE", "")
```

Guard, identical two-line shape to Phase 1, first statement of every public method:

```python
# not opted in to live scraping: parse the saved fixture instead of hitting the site
if not LIVE_SCRAPE:
    return parse_search(load_fixture_text("amazon_search.html"))
...live fetch...
return parse_search(html)
```

Blank `.env` -> fixtures, offline, deterministic. `LIVE_SCRAPE=1` -> live, zero code change.

### Why this and not the alternatives

- **Always live.** Rejected: `pytest` would hit Amazon and Akamai on every run, results would be
  non-deterministic, and the suite would fail on a plane. The spec's Fixtures section exists
  precisely to avoid this.
- **Fixture only when the fixture file is missing.** Rejected: invisible and backwards — deleting
  a file silently turns on network access.
- **Detect pytest / a `TESTING` flag.** Rejected explicitly by Phase 1 §2 and Phase 3 §3.4, and it
  does not work anyway: `scripts/check_*.py` needs fixture mode outside pytest.
- **Per-retailer vars (`LIVE_AMAZON`, `LIVE_TARGET`, ...).** Rejected: three vars to debug one
  scraper at a time, when the check scripts already isolate one scraper at a time.

### Rules the coder must honour (unchanged from Phase 1, restated)

1. Parsing is a separate pure function per response: `parse_search(html_or_payload)`,
   `parse_specs(...)`, `parse_reviews(...)`, `parse_stores(...)`. Both branches call the same one.
2. The guard is the only difference between the two paths. No mock class, no injection,
   no `if TESTING`.
3. Fixtures are real captured responses, not hand-trimmed convenience blobs. `save_fixtures.py`
   (section 10) produces them.
4. Fixture mode ignores its arguments. `get_specs("<any url>")` returns the one saved product
   page for every product. Do not add url-matching logic to simulate per-product lookup — that is
   the same ban Phase 1 §2.4 put on simulating search. Section 7 says how the real path avoids the
   artifact and how the check scripts stop it being mistaken for a bug.

### This is a switch, not a feature-flag system

One variable, gating exactly one thing (does this process open a network connection), read in
three places, with no registry, no config module, no dispatch table. If a later phase wants a
second such variable, that is the point to push back.

### Test patching

`tests/conftest.py` already patches key constants to `""`. Same mechanism, three more lines:

```python
monkeypatch.setattr(bestbuy, "LIVE_SCRAPE", "")
monkeypatch.setattr(target, "LIVE_SCRAPE", "")
monkeypatch.setattr(amazon, "LIVE_SCRAPE", "")
```

and the `bestbuy.BESTBUY_API_KEY` line is deleted. The suite stays fully offline even with a
populated `.env`.

---

## 3. `backend/scrapers/base.py` — one addition

HTML fixtures cannot go through `json.load`. Add a sibling, do not generalize the existing one:

```python
# HTML fixtures for the Playwright scrapers; JSON ones use load_fixture
def load_fixture_text(filename: str) -> str:
    with open(FIXTURES_DIR / filename, encoding="utf-8") as f:
        return f.read()
```

`ScraperBase` itself is unchanged — the four method signatures are the contract and Phase 5 does
not touch them.

---

## 4. `backend/scrapers/browser.py` — shared Playwright helpers

Not in the spec's file tree. Justified the same way `load_fixture()` was put in `base.py`: two
scrapers need byte-identical launch code (user agent, headless toggle, delays, timeouts, block
detection), and keeping two copies in sync is the failure mode. Three functions, no classes.

```python
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1366, "height": 768}
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1") != "0"   # set 0 locally to watch the browser
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 3.0
NAV_TIMEOUT_MS = 20000
SELECTOR_TIMEOUT_MS = 10000
MIN_REAL_PAGE_CHARS = 2000   # a challenge/interstitial page is tiny compared to a real one
```

```python
async def fetch_html(url: str, wait_for: str | None = None) -> str
async def fetch_product_html(url: str) -> str
def looks_blocked(html: str, markers: tuple[str, ...]) -> bool
```

`fetch_html`:
1. `async with async_playwright() as p:` -> `p.chromium.launch(headless=HEADLESS)`.
2. `browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT, locale="en-US")`, `new_page()`.
3. `page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)`.
4. If `wait_for`: `page.wait_for_selector(wait_for, timeout=SELECTOR_TIMEOUT_MS)` wrapped in
   `try/except PlaywrightTimeoutError` -> `logger.warning` and **continue**. A missing selector is
   a signal, not a fatal error: the caller still needs the HTML to tell "selector broke" from
   "we got blocked".
5. `await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))` — the spec's
   randomized delay, placed after navigation so lazy content settles and the request pattern is
   not machine-regular.
6. `html = await page.content()`.
7. `await browser.close()` in a `finally`. **One browser instance per call, closed immediately**,
   per the spec. No module-level browser, no `atexit`, no pool.

`looks_blocked(html, markers)`: `True` if `len(html) < MIN_REAL_PAGE_CHARS`, or any marker
(case-insensitive) appears in the HTML. Markers are per-retailer constants (sections 5, 6).

### The browser-lifecycle cost, stated honestly

One launch per call is expensive. Cold Chromium launch is roughly 0.5-1.5s, page load 2-5s, plus
the mandated 1-3s delay, so **3-8 seconds per call**. With `MAX_PRODUCTS_PER_RETAILER = 5`
(section 9), two Playwright retailers, and `search` + `get_specs` + `get_reviews`:

```
per retailer: 1 search + 5 x (1 specs + 1 reviews) = 11 page loads
two retailers                                       = 22 page loads
22 x ~5s                                            ~= 110 seconds
```

Plus Target's cheap JSON calls, plus narration: a live chat message takes **roughly 2 minutes**.
Phase 4 deliberately gave the frontend no timeout, so that works, but the UI will sit on
"Sending..." for that long. This is a real cost of the spec's rule, not a bug.

### The one deviation, scoped

`get_specs(url)` and `get_reviews(url)` load the *same product page* seconds apart. That doubles
both the cost and the block risk, and hammering one URL twice in five seconds is itself a bot
signal. So `fetch_product_html(url)` keeps a **single-entry, 60-second cache**:

```python
_LAST_PRODUCT_PAGE = {"url": None, "html": None, "fetched_at": 0.0}
CACHE_SECONDS = 60
```

Reuse only when the url matches **and** the entry is under 60 seconds old; otherwise fetch and
replace. Size one, no eviction policy, no TTL sweeper, three lines of logic. The 60-second bound
is what stops a 6-hour rescan from reading a stale page. The browser lifecycle itself is
unchanged — still one instance per fetch, closed immediately. `search()` uses `fetch_html`
directly and is never cached.

Not done, and deliberately: running the three retailers concurrently with `asyncio.gather` would
roughly halve wall time but restructures `run_pipeline`'s error handling. Ask if the 2-minute
figure is unacceptable.

---

## 5. `backend/scrapers/bestbuy.py` — rewrite, Tier B Playwright

Delete everything API-related: `BASE_URL`, `BESTBUY_API_KEY`, `base_params`, `get_json`,
`parse_stores`, and the `httpx` import. Keep `sku_from_url` — it still works on scraped hrefs and
is used for logging and by the check script. Rename `parse_details` -> `parse_specs` for symmetry
with the other two files (its only importer is `tests/test_ranking.py`, fixed in section 11).

```python
RETAILER = "bestbuy"
SEARCH_URL = "https://www.bestbuy.com/site/searchpage.jsp?st={query}"
LIVE_SCRAPE = os.getenv("LIVE_SCRAPE", "")
# Best Buy API access was applied for and denied. Do not reintroduce BESTBUY_API_KEY.
BLOCK_MARKERS = ("access denied", "reference #18", "_sec/cp_challenge",
                 "are you a robot", "pardon our interruption")
```

`{query}` filled with `quote_plus(query)`.

### Selectors

Best Buy's markup changes. **These are the starting point, not the contract.** The workflow is:
run `scripts/save_fixtures.py` live once, open the saved HTML, confirm or correct each selector
against it, then the fixture becomes the permanent contract for the parse functions.

Search page, wait target `li.sku-item`, one row per `li.sku-item[data-sku-id]`:

| Output field | Selector / source | Notes |
|---|---|---|
| `name` | `.sku-title a` text, stripped | |
| `url` | that anchor's `href`, `urljoin("https://www.bestbuy.com", href)` | hrefs are relative |
| `price` | `[data-testid="customer-price"] span` first match, else `.priceView-customer-price span` | strip `$` and `,`, `float()` |
| `in_stock` | add-to-cart button text/state: "Sold Out" or `disabled` -> `False`, else `True` | `None` if no button found |
| `store_id` | `None` | search page is national inventory |
| `distance_miles` | `None` | |

Skip any `li.sku-item` with no `data-sku-id` (sponsored/placeholder shells).

Product page (`fetch_product_html(product_url)`):

| Method | Selector | Parse |
|---|---|---|
| `get_specs` | `.specs-table .row` -> `.row-title` / `.row-value`; fall back to `ul.specification-list li` -> `.specification-name` / `.specification-value` | `{title: value}` raw strings, no unit parsing |
| `get_reviews` | rating: `[data-testid="customer-rating"]` or `.ugc-c-review-average`; count: `.c-reviews` / `.ugc-review-count` text like `(1,843 reviews)` | `{"rating": float, "review_count": int, "verified_ratio": None}` |

`verified_ratio` is `None` — Best Buy publishes no verified-purchase ratio. One comment.

Specs sit behind a lazy "Specifications" accordion. In `get_specs`'s live path only, before
reading content: if a `button:has-text("Specifications")` exists, click it, then take the
randomized delay. That is the **only** interaction any scraper performs. If it turns out the
specs render without the click, drop it — one fewer action is one less bot signal.

### `find_nearby_stores` — removed

```python
async def find_nearby_stores(self, lat, lon, radius_mi):
    # the Stores API needed a key that was denied; scraping the store locator is out of scope
    raise NotImplementedError
```

`pipeline.nearby_store_ids` already catches `NotImplementedError` and returns `None`, so nothing
downstream changes. **Flagged consequence:** Best Buy contributes no store or distance data at
all, so every Best Buy candidate keeps `distance_score = NEUTRAL_SCORE`. Target is now the only
retailer that can produce a real distance.

---

## 6. `backend/scrapers/amazon.py` — Tier B Playwright

```python
RETAILER = "amazon"
BASE = "https://www.amazon.com"
SEARCH_URL = "https://www.amazon.com/s?k={query}"
LIVE_SCRAPE = os.getenv("LIVE_SCRAPE", "")
BLOCK_MARKERS = ("enter the characters you see below", "/errors/validatecaptcha",
                 "not a robot", "robot check", "automated access")
```

### `search(query, store_ids)`

`store_ids` accepted and unused (Amazon has no stores). Wait target `.s-result-item`, per the
spec. Extract from `div[data-component-type="s-search-result"]` and skip tiles with no `data-asin`
— `.s-result-item` alone also matches ad shells and layout spacers. One comment saying so.

| Output field | Selector | Notes |
|---|---|---|
| `name` | `h2 span` (spec) | |
| `price` | `.a-price-whole` **and** `.a-price-fraction`, concatenated | spec names only `.a-price-whole`, which drops cents (`24.99` -> `24.0`). Deviation, commented. |
| `url` | `f"{BASE}/dp/{tile['data-asin']}"`, falling back to `urljoin(BASE, a.a-link-normal href)` | spec says read the href; the href is a relative ref-tracking URL that changes every load, which makes it useless as the `listings` unique key. ASIN is stable. Deviation, commented. |
| `in_stock` | `price is not None` | search tiles carry no stock status; a buyable price means buyable. Commented. |
| `store_id` / `distance_miles` | `None` | |

### `get_specs(product_url)` / `get_reviews(product_url)`

Both via `fetch_product_html(product_url)`.

`get_specs` reads all three of Amazon's layouts and merges (first non-empty wins per key):
1. `#productDetails_techSpec_section_1 tr` -> `th` / `td` (the spec's named selector)
2. `#productDetails_detailBullets_sections1 tr` -> `th` / `td`
3. `#detailBullets_feature_div li` -> split the `span` pair on `:`

All three formats embed invisible bidi marks. Strip `‎`, `‏`, and collapse the
`" ‏ : ‎ "` separator before splitting. Return `{}` if all three are empty.

`get_reviews`:
- rating: `#averageCustomerReviews .a-icon-alt` text, `"4.5 out of 5 stars"` -> `4.5`. Fall back to
  the first `.a-icon-alt` on the page. Not the bare first match by default — `.a-icon-alt` appears
  dozens of times (badges, sponsored tiles) and the first one is often not the product's rating.
- `review_count`: `#acrCustomerReviewCount` text, `"1,843 ratings"` -> `1843`.
- `verified_ratio`: `None`. **Correction to an existing comment:** `ranking.py` line ~105 says
  "Amazon is the first that will [populate verified_ratio]". That is wrong — Amazon's aggregate
  block does not expose it; deriving it needs paging the review list, which is out of scope.
  Update that comment to say no source populates it in the MVP.
- Rating distribution (`#histogramTable` star percentages) is **not** returned this phase.
  `ScraperBase.get_reviews` documents three keys, `compute_review_score` has no distribution
  branch, and `reviews.rating_distribution_json` has no writer. Adding it means changing the
  review-score math, which is Phase 6's job. One comment naming `#histogramTable` so Phase 6
  does not have to rediscover it.

### `find_nearby_stores`

`raise NotImplementedError` — the spec says "Not implemented for Amazon".

---

## 7. `backend/scrapers/target.py` — Tier A redsky JSON

`httpx.AsyncClient(timeout=10)`, same as the old Best Buy code. No Playwright.

```python
RETAILER = "target"
BASE_URL = "https://redsky.target.com/redsky_aggregations/v1/web"
# public web key lifted from target.com's own JS, not a credential and not a secret.
# it rotates occasionally: a sudden 401/404 from redsky means check this first.
API_KEY = "<capture from devtools>"
DEFAULT_STORE_ID = "3991"   # redsky requires some store for pricing; MVP does not vary it
LIVE_SCRAPE = os.getenv("LIVE_SCRAPE", "")
```

`API_KEY` stays a module constant, **not** an env var: it is not a secret, and putting it in
`.env` would break the rule that a blank `.env` means fixtures.

### Verify before writing parsers

redsky is undocumented and the shape below is from observation, not a contract. **Step one of the
Target work is to capture a real response** (devtools Network tab on a target.com search, or
`save_fixtures.py`) and confirm each path. Where reality differs, the fixture wins and the field
map here is corrected. The spec says the same thing: "No official docs — endpoint found via
browser devtools Network tab; wrap all calls in try/except, log failures."

### `search(query, store_ids)`

`GET {BASE_URL}/plp_search_v2` with params:
`key`, `channel=WEB`, `keyword={query}`, `page=/s/{query}`, `count=24`, `offset=0`,
`platform=desktop`, `pricing_store_id={store_ids[0] or DEFAULT_STORE_ID}`, `visitor_id` (any
32-hex string constant is fine — one comment saying redsky just wants the param present).

**`store_ids` is used here**, unlike Best Buy: passing a nearby store makes the returned
`fulfillment.store_options` be about a store the user can actually reach.

Parse `data.search.products[]`:

| Output field | Source path | Notes |
|---|---|---|
| `name` | `item.product_description.title` | `html.unescape()` it — titles carry `&#38;` etc. |
| `url` | `item.enrichment.buy_url` | already absolute; fall back to `https://www.target.com/p/-/A-{tcin}` |
| `price` | `price.current_retail` | may be absent on variant parents -> `None` |
| `in_stock` | `fulfillment.shipping_options.availability_status == "IN_STOCK"`, OR any `store_options[]` with `order_pickup.availability_status == "IN_STOCK"` | either channel counts as in stock |
| `store_id` | first `fulfillment.store_options[].location_id` that is in stock, as `str` | `None` when only shippable |
| `distance_miles` | `fulfillment.store_options[].distance` when present, else `None` | often absent; do not invent it |

Skip products with no `tcin`.

### `get_specs(product_url)`

`tcin_from_url(url)`: the digits after `/A-` in the path; `None` if absent.
`GET {BASE_URL}/pdp_client_v1` with `key`, `tcin`, `channel=WEB`, `is_bot=false`,
`store_id=DEFAULT_STORE_ID`, `pricing_store_id=DEFAULT_STORE_ID`, `page=/p/A-{tcin}`.

Specs come from `data.product.item.product_description.bullet_descriptions`, a list of HTML
strings shaped `"<B>Battery Capacity:</B> 20000 mAh"`. Parse each into a key/value pair: strip
tags with a regex, `html.unescape`, split on the first `:`. Bullets with no `:` are skipped (they
are marketing sentences, not specs). Merge in `soft_bullets.bullets` only if it is also
`key: value` shaped; otherwise ignore.

Return `{}` when `tcin` is `None`, the request fails, or no bullet parses — the pipeline's
empty-dict signal.

### `get_reviews(product_url)`

Second `pdp_client_v1` call, same params. Parse
`data.product.ratings_and_reviews.statistics`:
- `rating.average` -> `rating`
- `rating.count` (fall back to `review_count`) -> `review_count`
- `verified_ratio`: `None` — Target does not publish one.

No caching between `get_specs` and `get_reviews`, unlike the Playwright scrapers: these are
~200ms JSON calls against an endpoint with no bot challenge, and the cache would be machinery
buying nothing.

### `find_nearby_stores(lat, lon, radius_mi)`

`GET {BASE_URL}/nearby_stores_v1` with `key`, `place={lat},{lon}`, `within={radius_mi}`,
`limit=20`, `channel=WEB`. Parse `data.nearby_stores.stores[]` ->
`{"store_id": str(store_id), "name": location_name, "distance_miles": distance}`.

This is the **only** working `find_nearby_stores` in the app after Phase 5.

### Failure handling

Every live call in `try/except httpx.HTTPError` -> `logger.warning`, return `[]` / `{}`. Per the
spec: "wrap all calls in try/except, log failures, treat as Tier B fallback candidate if it
breaks." No retries.

### get_specs keyed to the real product URL

Live: `get_specs` derives the tcin from the passed `product_url` and calls the pdp endpoint for
that tcin, so every product gets its own specs. Same for Best Buy and Amazon — each navigates to
the passed `product_url`. The "all products share one spec table" artifact exists **only in
fixture mode**, where every call returns the one saved page, exactly as Phase 1 documented and
banned working around. To stop it being mistaken for a bug, each check script and
`check_pipeline.py` print, in fixture mode:

```
FIXTURE MODE: get_specs and get_reviews return the same saved product page for every url.
```

---

## 8. Blocking, selector breaks, and the deferred LLM fallback

### What a blocked page looks like

- **Amazon**: a ~5KB page titled "Amazon.com" containing "Enter the characters you see below",
  "Sorry, we just need to make sure you're not a robot", and a form posting to
  `/errors/validateCaptcha`. Sometimes a 503 "Robot Check".
- **Best Buy (Akamai)**: a small "Access Denied" page with `Reference #18.<hex>.<epoch>.<hex>`,
  or a redirect into `/_sec/cp_challenge/...`, or an interstitial asking to verify you are human.
  The exact strings drift.

**These markers are guesses until proven.** The moment the coder sees a real block, they save that
HTML and put its actual strings into `BLOCK_MARKERS`. The `len(html) < MIN_REAL_PAGE_CHARS` check
is the marker-independent backstop and catches most of it on its own.

### Behaviour when blocked

Detect right after the fetch, before parsing. Then:

```python
if looks_blocked(html, BLOCK_MARKERS):
    logger.warning("%s blocked on %s", RETAILER, url)
    return []      # or {} for get_specs / get_reviews
```

**Return empty. Do not raise.** Reasons, in order:
1. `run_pipeline`'s per-retailer `try/except` would swallow a raise anyway — same outcome, more
   noise.
2. After carry-over fix A (section 9), an exception escaping a scraper logs a full traceback and
   means "code bug". A block is an expected outcome, not a bug, and must not look like one.
3. Empty degrades correctly: blocked `search` -> that retailer contributes nothing and the other
   two still return results; blocked `get_specs` -> `{}` -> the pipeline's existing
   `skip <name>: no specs` path.

**No retries, no backoff, no proxy rotation.** If Best Buy is blocked now it will be blocked in
30 seconds; the scheduler retries in 6 hours (Phase 7) and that is the retry. Adding retry
machinery is speculative until the block rate is actually known.

### Selector break vs. block — different logs

A page that is not blocked but yields zero rows means the selectors broke:

```python
logger.warning("%s search selectors returned nothing (page %d chars) - selectors may have broken",
               RETAILER, len(html))
```

Two distinguishable log lines: `blocked on <url>` vs `selectors returned nothing`. That
distinction is the whole value of the block check.

### The LLM fallback (call #5's second job) is DEFERRED to Phase 6

The spec says a selector break should dump raw page text to `narration.py` for LLM extraction.
Not this phase, because:
1. `narration.py` today has one job and one single-purpose `SYSTEM_PROMPT`. A second job means a
   second prompt and a second public function — and Phase 6 is the phase that writes
   `spec_extraction.py`, which is the *same* operation (page text -> spec JSON). Building the
   two halves in two different phases in two different files guarantees two competing
   implementations of one thing.
2. It has a hard prerequisite that only exists as of this phase: without block detection, the
   fallback would ship captcha-page text to Claude and pay for hallucinated specs. Phase 5 builds
   that prerequisite.
3. Phase 5 already carries the riskiest work in the build. Nothing in this phase's acceptance
   depends on the fallback.

Phase 5 leaves the marker where Phase 2 left the one for LLM call #2 — a comment at the exact
branch, no stub function, no argument that cannot yet be produced:

```python
# LLM call #5's fallback extraction goes here in Phase 6, with page text from this html
```

---

## 9. `backend/services/pipeline.py` — wiring, cap, and carry-over fix A

### 9.1 SCRAPERS

```python
SCRAPERS = [
    ("bestbuy", BestBuyScraper()),
    ("target", TargetScraper()),
    ("amazon", AmazonScraper()),
]
```

Order is cheap-to-expensive only in that Target's JSON is fast; it has no behavioural meaning
since the loop is sequential and results are sorted at the end.

### 9.2 Per-retailer product cap — new, and necessary

Phase 2's loop had no cap because fixture Best Buy returned 4 products instantly. Live, each
`search()` returns ~24, and each product costs two Playwright page loads. Uncapped, one chat
message is 140+ browser launches and near-certain blocking.

```python
MAX_PRODUCTS_PER_RETAILER = 5   # each product costs 2 page loads on the Tier B scrapers
```

Applied as a slice on the search results:
`for product in (await scraper.search(query, store_ids))[:MAX_PRODUCTS_PER_RETAILER]:`

Justification: retailers return relevance-ordered results, and `narration.TOP_N` is 5, so nothing
past the first handful per retailer can reach the user anyway. Fixture mode is unaffected (the
Best Buy fixture has 4 products). One inline comment with the arithmetic from section 4.

### 9.3 Carry-over fix A — `logger.exception`

Line 100 today:

```python
except Exception as error:
    logger.warning("%s failed: %s", retailer, error)
```

becomes:

```python
except Exception:
    # keep the broad catch so one dead retailer does not kill a multi-retailer run,
    # but log the traceback: a code bug must not read like a retailer outage
    logger.exception("%s failed", retailer)
```

The broad `except Exception` **stays** — with three retailers it is now doing real work. The only
change is the log call. Note the level moves from WARNING to ERROR (that is `logger.exception`'s
level), which is correct for a swallowed exception, and `check_pipeline.py`'s
`basicConfig(level=INFO)` shows both. No behaviour change beyond output.

Do not add a second narrower `except` inside the product loop. One product raising still costs the
rest of that retailer — accepted, same as Phase 2, and now visible in the traceback.

---

## 10. `backend/services/criteria.py` — carry-over fix B, valueless rules

### The bug

Claude can emit `{"field": "Battery Capacity", "op": ">=", "value": null}`. That reaches
`ranking.spec_passes`, which does `float(rule["value"])` (ranking.py:66) and raises `TypeError`
inside `pipeline.py`'s swallowing `try`. Result today: the whole retailer is dropped with a vague
log line. With three retailers that is a third of the results gone silently.

### Where validation lives: `criteria.py`

`criteria.py` is the producer, already owns `normalize()`, and already has a followup return
shape and a precedent for using it (`MALFORMED_QUESTION`, §3.7 of the Phase 3 plan: "a bad model
reply is a conversation problem, not an outage"). A valueless rule is exactly that. Validating in
`pipeline.py` or `ranking.py` would mean silently dropping the rule, which changes what the user
asked for without telling them.

### Implementation

Constants at the top, next to the existing ones:

```python
VALID_OPS = (">=", "<=", "==", "contains", "exists")
RULE_LISTS = ("must_haves", "preferred_specs")
```

```python
from backend.services.ranking import first_number   # no cycle: ranking imports nothing from here

# a rule the matcher cannot evaluate: missing field, unknown op, or a comparison with no value
def bad_rule_question(criteria: dict) -> str | None
```

`bad_rule_question` walks both rule lists in order and returns the question for the **first** bad
rule, or `None`. A rule is bad when:

| Condition | Bad? |
|---|---|
| `field` missing, empty, or not a string | yes |
| `op` not in `VALID_OPS` | yes |
| `op == "exists"` | never — `value` is legitimately absent |
| `op` in `>=`, `<=`, `==` and `value` is `None`, `""`, a bool, or a string `first_number` cannot parse | yes |
| `op == "contains"` and `str(value)` is empty or `value is None` | yes |

Numeric coercion happens here too, in the same pass: a string value like `"20,000"` is replaced
with `first_number(value)` so `ranking.spec_passes`'s `float()` cannot fail on a comma. That is
the one repair; everything else is rejected, not guessed.

### Followup wording strategy

Deterministic template built from the rule. **No sixth LLM call** — the spec fixes the LLM at
exactly five places, and this is a validation failure, not an extraction.

Three templates keyed on `op`, so the question tells the user which side of the comparison is
missing:

```python
# >=  ->  'How much "Battery Capacity" do you need, at minimum? Give a number with its unit.'
# <=  ->  'What is the most "Product Weight" you will accept? Give a number with its unit.'
# ==  ->  'What exact "Number of USB Ports" do you need? Give a number.'
# contains / bad op / bad field
#     ->  'What should "Battery Capacity" be? Describe the requirement in one line.'
# field itself unusable
#     ->  'One of your requirements did not come through clearly. Can you restate what it needs to have?'
```

Rules for the wording: quote the field name verbatim so the user recognises their own words, ask
for exactly one thing, name the expected form (a number, with its unit), never mention JSON,
`op`, `value`, or "the model". Plain sentences, no emojis.

### Call site — one, after normalize, both branches

```python
question = bad_rule_question(criteria_dict)
if question:
    logger.warning("criteria contained an unusable rule: %s", question)
    return {"type": "followup", "question": question}
return {"type": "criteria", "criteria": criteria_dict}
```

Placed so that **both** the canned and the live path go through it. The canned path costs nothing
and proves the validator does not false-positive on the known-good `CANNED_CRITERIA`.

Result: a malformed rule can no longer reach `ranking.py`. `ranking.py` is **not** changed.

Two things to state rather than solve:
- **Loop risk.** The followup is appended to the conversation history, so the model sees its own
  bad rule plus the correction request and normally fixes it. There is no attempt counter this
  phase; if it loops, the user rephrases. Adding a counter is speculative until it happens.
- **Residual producer.** `items.criteria_json` rows written before this fix, and Phase 8's manual
  `POST /api/items`, bypass `criteria.py` entirely. Phase 8's items router must call
  `bad_rule_question` too. Noted here so it is not rediscovered later.

---

## 11. Fixtures, tests, and the dev capture tool

### `scripts/save_fixtures.py`

A developer tool, run manually, never imported by anything. With `LIVE_SCRAPE=1` it fetches and
writes all seven fixtures with a hardcoded query (`"portable charger"`) and lat/lon, printing the
byte count of each. With `LIVE_SCRAPE` blank it refuses and says so. This is how fixtures are
created and refreshed; there is no `--save` flag bolted onto the check scripts.

HTML fixtures are large (Amazon search pages are 1-3MB). That is fine and they are committed:
they are the contract the parse functions are tested against, and trimming them by hand is
exactly the "pre-parsed convenience blob" Phase 1 banned. If a file is unmanageably large the
coder may strip `<script>`/`<style>` bodies **only**, and must say so in the commit message.

Fixture content requirements:
- `bestbuy_search.html`, `amazon_search.html`: at least 4 result tiles, including one out of stock
  and one with no price, so the edge cases show up in the check-script output.
- `bestbuy_product.html`, `amazon_product.html`, `target_pdp.json`: a product whose specs include
  a **"Battery Capacity"**-like key with a value at or over 20000, so `CANNED_CRITERIA`'s
  must_have can pass and `check_pipeline.py` returns candidates from all three retailers.
  If the real captured pages do not line up that way, **do not edit the fixture to force it** —
  record the actual per-retailer candidate counts in the checklist instead. Retailer-specific
  spec names defeating a cross-retailer must_have is a true finding about the design's documented
  limitation, not something to paper over.
- `target_search.json`: at least one product with `store_options` (so `store_id`/`distance_miles`
  are exercised) and one shipping-only.

### `tests/test_scrapers.py` — new, fully offline

One section per scraper, all against fixtures, no network, no browser:
- `parse_search` returns a non-empty list; every row has all six contract keys; `price` is a float
  or `None` and never a string with a `$`; `url` is absolute (`startswith("https://")`).
- Amazon: the url is `https://www.amazon.com/dp/<ASIN>` shaped, with no `/ref=` in it.
- Best Buy: `sku_from_url(row["url"])` returns digits for every row.
- `parse_specs` returns a non-empty dict of `str -> str`, no HTML tags left in any value, no
  `‎`/`‏` left in any key or value.
- `parse_reviews` returns `rating` as a float in `0..5`, `review_count` as an int, and
  `verified_ratio is None`.
- Target `parse_stores` returns `store_id` as `str`.
- `looks_blocked` returns `True` for a short string and for a string containing a marker,
  `False` for the real saved search fixtures. (This test is what keeps the block check honest.)

### `tests/test_ranking.py` — one edit

Line 4 imports `parse_details` and line 18 builds `SPECS` from the deleted
`bestbuy_details.json`. Replace both with a **literal `SPECS` dict** in the test file carrying the
same values the existing assertions depend on (Battery Capacity 24,000; Product Weight 1.4;
Number of USB Ports 3; Pass-Through Charging "Yes"; no "Waterproof" key). Every existing assertion
must still pass unchanged. `test_ranking` is about ranking math; its coupling to a scraper fixture
was incidental and is not worth preserving.

### `tests/test_criteria.py` — additions

- `bad_rule_question` on `{"must_haves": [{"field": "Battery Capacity", "op": ">=", "value": None}]}`
  returns a non-empty string containing `Battery Capacity`.
- Same for a `preferred_specs` entry, for an unknown `op`, for a missing `field`, and for
  `contains` with `value: None`.
- `{"op": "exists"}` with no `value` -> `None` (not bad).
- `value: "20,000"` -> `None` (not bad) **and** the rule's value is now the float `20000.0`.
- `bad_rule_question(CANNED_CRITERIA)` -> `None`. No false positives.
- End to end: monkeypatch `CANNED_CRITERIA` to contain a null-valued rule, call `extract` with a
  non-empty history, assert `type == "followup"` and the question names the field. This is the
  regression test for the actual bug.
- The existing contract test (`run_pipeline` on the returned criteria) must still pass with three
  scrapers wired in.

### `tests/test_chat.py`

Asserts `>= 1` and `> 1` products, not exact counts, so adding two retailers does not break it.
Confirm it still passes; do not tighten it.

### `scripts/check_target.py`, `check_amazon.py`, `check_bestbuy.py`

Same shape as the existing `check_bestbuy.py`: `sys.path.insert`, `load_dotenv()`,
`asyncio.run(main())`, no argparse, `json.dumps(indent=2)`.
- Print `MODE: LIVE` / `MODE: FIXTURE` from that module's `LIVE_SCRAPE`.
- In fixture mode, print the shared-product-page warning from section 7.
- Call `search()` with `"portable charger"`, print rows; take `results[0]["url"]`, call
  `get_specs()` and `get_reviews()`, print both.
- `check_target.py` also calls `find_nearby_stores(37.7749, -122.4194, 25)`.
- `check_bestbuy.py` and `check_amazon.py` do **not** — both raise `NotImplementedError` by design.
- Catch the Playwright "Executable doesn't exist" launch error and print
  `run: playwright install chromium` instead of a traceback.

`scripts/check_pipeline.py`: its mode banner imports the now-deleted `BESTBUY_API_KEY`. Point it
at `bestbuy.LIVE_SCRAPE` instead.

---

## 12. Verification checklist

### Part A — offline, deterministic, must all pass

Blank `.env` (no `LIVE_SCRAPE`, no keys). No network. Run from the repo root.

1. `pip install -r requirements.txt` succeeds; `playwright`, `beautifulsoup4`, `lxml` present.
2. `playwright install chromium` succeeds. (Needed for Part B only, but confirm it now.)
3. `python -m pytest tests -q` — all pass, including the pre-existing Phase 2/3 files.
   Disconnect the network and run it again: identical result.
4. `python scripts/check_bestbuy.py` -> `MODE: FIXTURE`, the shared-page warning, then non-empty
   `search`/`get_specs`/`get_reviews` blocks with every mapped field populated. No `None` name,
   no `$` left in a price.
5. `python scripts/check_target.py` -> `MODE: FIXTURE`, same, plus a non-empty
   `find_nearby_stores` with `store_id` as strings and a numeric `distance_miles`.
6. `python scripts/check_amazon.py` -> `MODE: FIXTURE`, same. Every `url` matches
   `https://www.amazon.com/dp/<ASIN>` with no `/ref=`.
7. Run all three again from inside `scripts/` — identical output (cwd-independent fixture paths).
8. `python scripts/check_pipeline.py` -> `MODE: FIXTURE`, candidates from more than one retailer,
   every row printing all five sub-scores, `final_score` descending. Record the per-retailer
   candidate count in the commit message. If a retailer contributes zero, say **why** from the
   skip log lines (`no specs` vs `failed must_haves` vs `N reviews`) — a zero explained by
   retailer-specific spec names is a correct result, a zero with no log line is a bug.
9. **No live calls leak from the offline path**: `grep -rn "LIVE_SCRAPE" backend/` -> exactly
   three module-level reads (one per scraper) plus the guard lines. No fourth read, no config
   module, no `if TESTING`, no mock class.
10. `grep -rn "BESTBUY_API_KEY\|api.bestbuy.com" backend/ scripts/ tests/ .env.example` -> zero
    matches. The dead API is fully gone.
11. `grep -rn "async_playwright" backend/` -> matches only in `backend/scrapers/browser.py`.
    No scraper launches its own browser.
12. Carry-over A: `grep -n "logger.exception" backend/services/pipeline.py` -> one match; the
    broad `except Exception` is still there. Force it — temporarily raise inside
    `TargetScraper.search` — and confirm the run **continues** and the log carries a full
    traceback naming `target`. Revert.
13. Carry-over B, the actual bug: with a blank `.env`, monkeypatch or temporarily edit
    `CANNED_CRITERIA` to `{"field": "Battery Capacity", "op": ">=", "value": None}`, then
    `POST /api/chat/message` twice. Second response is
    `{"type": "followup", "question": "..."}` naming `Battery Capacity`, **not** a 500 and **not**
    a results response with a missing retailer. Confirm no `TypeError` traceback appears in the
    log. Revert.
14. `grep -rn "value" backend/services/ranking.py` — `ranking.py` is unchanged by Phase 5 except
    the one corrected `verified_ratio` comment (section 6).
15. `uvicorn backend.main:app --port 8000` starts clean, `/api/profile` still answers, `/docs`
    still lists exactly the four Phase 1/3 endpoints.
16. `git status` — new files match section 1 exactly. The three `bestbuy_*.json` fixtures are
    deleted. No `scheduler.py`, `spec_extraction.py`, `sentiment.py`, `reviews_*.py`, no frontend
    change.
17. Grep the diff for emojis -> zero.

### Part B — live smoke, once each, may legitimately fail

`LIVE_SCRAPE=1` in `.env`. Run each **once**, not in a loop — repeated runs are what get an IP
flagged. Do these one at a time, not concurrently.

18. `python scripts/check_target.py` -> `MODE: LIVE`. Expect this one to **work**: redsky is
    unauthenticated JSON with no bot challenge. If it 401s or 404s, the `API_KEY` constant has
    rotated — re-capture it from devtools (section 7) and note it in the commit message.
19. `PLAYWRIGHT_HEADLESS=0 python scripts/check_amazon.py` -> a visible browser window. Watch it
    load the search page. Per the spec's Local Testing section, this is the pass where selectors
    are confirmed by eye. Then rerun headless and confirm identical output.
20. `PLAYWRIGHT_HEADLESS=0 python scripts/check_bestbuy.py` -> same. **This is the one most likely
    to fail.**
21. `python scripts/save_fixtures.py` with `LIVE_SCRAPE=1`, then rerun the whole of Part A. The
    parse functions must pass against freshly captured pages, not just the originals. This is the
    real test of the field maps.
22. `LIVE_SCRAPE=1 python scripts/check_pipeline.py` once, timed. Expect roughly 2 minutes
    (section 4). Record the actual wall time in the commit message.

### If Part B is blocked — what to do, and what not to do

A live block is an **expected outcome for Amazon and especially Best Buy**, not a failed phase.

- Confirm it is a block, not a bug: the log must say `bestbuy blocked on <url>`, not
  `selectors returned nothing` and not a traceback. If it says `selectors returned nothing`, the
  selectors are wrong — fix them against the saved HTML.
- **Save the blocked page** as `tests/fixtures/<retailer>_blocked.html` (temporarily, do not
  commit it), read its actual strings, and put them into that scraper's `BLOCK_MARKERS`. Add a
  `test_scrapers.py` case asserting `looks_blocked` catches it. This is the one concrete
  improvement a block buys you — take it.
- Confirm the degradation is correct: `LIVE_SCRAPE=1 python scripts/check_pipeline.py` with
  Best Buy blocked must still return Target (and Amazon) candidates, and log one warning line per
  blocked call. **A block must never empty the whole run.** That assertion is the actual
  acceptance criterion for the Best Buy rewrite — not that Best Buy returns data.
- **Do not** add retries, backoff, sleep escalation, proxy support, `playwright-stealth`,
  undetected-chromedriver, or a residential proxy service. All of it is out of scope and none of
  it is in the spec. If Best Buy is blocked persistently, stop and report it — the decision to
  drop Best Buy, or to spend money on unblocking, is the user's, not the coder's.
- Ship the phase with fixture-mode green and the live result documented either way. Part A is the
  gate; Part B is information.

---

## 13. Decisions not covered by spec.md

1. **`LIVE_SCRAPE` env var.** The spec's fixture mechanism assumed an API key existed to switch
   on. Two scrapers have no credential and Best Buy just lost its, so an explicit opt-in is the
   only honest remaining switch (section 2). One variable, gating network access only.
2. **`backend/scrapers/browser.py`.** Not in the spec's file tree. Added so two scrapers do not
   keep duplicate copies of user-agent/headless/delay/block-detection logic.
3. **60-second single-entry product-page cache.** A scoped deviation from "one browser instance
   per call" — the instance rule is kept; the number of calls is reduced, because `get_specs` and
   `get_reviews` load the identical URL seconds apart (section 4).
4. **`MAX_PRODUCTS_PER_RETAILER = 5`.** New hard cap in `pipeline.py`, not in the spec's
   pseudocode. Without it a live run is 140+ browser launches (section 9.2).
5. **Best Buy `find_nearby_stores` removed.** The Stores API needed the denied key. Consequence:
   Target is the only source of store distance in the app, and every Best Buy candidate scores
   neutral on distance. Say the word if pickup/distance for Best Buy matters enough to justify
   scraping the store locator behind Akamai.
6. **`BESTBUY_API_KEY` deleted from `.env.example`.** The spec lists it under Environment
   Variables. Keeping dead config invites someone to re-apply for the API.
7. **Amazon url built from `data-asin`, not from the `a.a-link-normal` href** the spec names. The
   href is a per-load tracking URL and is useless as the `listings` unique key.
8. **Amazon price reads `.a-price-fraction` too.** The spec names only `.a-price-whole`, which
   silently drops cents.
9. **Target `API_KEY` as a source constant, not an env var.** It is a public key from target.com's
   own JS, and putting it in `.env` would break "blank `.env` means fixtures".
10. **`DEFAULT_STORE_ID = "3991"` for Target's pdp calls.** redsky requires some store for
    pricing; the MVP never varies it. Pricing shown may differ slightly from the user's local
    store.
11. **Rating distribution and `verified_ratio` are not populated by any scraper.** Amazon's
    `#histogramTable` is noted for Phase 6. `ranking.py`'s existing comment claiming Amazon would
    be the first to populate `verified_ratio` is corrected — no MVP source does.
12. **Selector-break LLM fallback deferred to Phase 6** (section 8), with justification.
13. **`bad_rule_question` templates.** The spec says nothing about malformed-rule recovery. Chose
    deterministic per-op templates over a sixth LLM call, since the spec fixes the LLM at exactly
    five places. No retry counter, so a stubborn model could loop — stated, not solved.
14. **Numeric strings are coerced, everything else is rejected.** `"20,000"` becomes `20000.0`;
    `null` is re-asked. That one repair is the line between fixing a formatting quirk and guessing
    what the user meant.

---

## User decisions (2026-08-16), binding — override anything above that conflicts

1. **Best Buy: keep it, revisit if blocked.** Build the Playwright rewrite as planned and accept the
   loss of `find_nearby_stores` for now. Target is the only source of store distance; Best Buy
   candidates score a neutral 0.5 on distance. If Akamai blocks Best Buy persistently in live
   testing, that is a decision for the user, not the coder — do not reach for stealth plugins,
   proxies, or retry escalation. Report the block and stop.

2. **Cap harder to cut live latency.** `MAX_PRODUCTS_PER_RETAILER = 3` (not 5). Additionally, skip
   the per-product `get_specs`/`get_reviews` page loads for candidates outside the top few by the
   cheap pre-spec signals already available from the search tile; only the top candidates get a
   detail page load. Specify the exact cutoff in implementation, keep it a named constant at the
   top of `pipeline.py`, and state the resulting page-load count in a comment. Accepted tradeoff:
   ranking runs on thinner spec data for lower-ranked candidates.
