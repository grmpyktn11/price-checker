# Phase 2 Plan — Core Pipeline, Ranking Math, Deal Detection

Scope: the pure-code core of the pipeline (spec's "Core Pipeline" + "Deal Detection Logic").
No LLM calls, no new scrapers, no scheduler, no routers, no frontend.

Phase 1 is committed: `backend/{db,models,main}.py`, `backend/routers/profile.py`,
`backend/scrapers/{base,bestbuy}.py`, three Best Buy fixtures, `scripts/check_bestbuy.py`.

---

## 1. File list

| File | Purpose |
|---|---|
| `backend/services/__init__.py` | Empty. Directory exists but has no package marker yet. |
| `backend/services/ranking.py` | `RankedProduct` + all scoring math. Pure functions, no I/O. |
| `backend/services/deals.py` | `evaluate_deal()` + its two DB readers. |
| `backend/services/pipeline.py` | `run_pipeline()`, `gather_reviews()`, hard filters, scraper list. |
| `backend/services/nice_to_have.py` | LLM call #3 slot. Phase 2 body returns a constant. |
| `scripts/check_pipeline.py` | Standalone: hardcoded criteria -> `run_pipeline()` -> printed ranking. |
| `tests/test_ranking.py` | pytest, the worked examples in section 6 as assertions. |
| `tests/test_deals.py` | pytest, in-memory SQLite, the four `evaluate_deal` branches. |
| `requirements.txt` | Add `pytest` only. |

Do **not** create: `criteria.py`, `spec_extraction.py`, `sentiment.py`, `narration.py`,
`reviews_*.py`, `geocode.py`, `email.py`, `scheduler.py`, `target.py`, `amazon.py`, any router.

`run_pipeline` lives in `services/pipeline.py`: the spec's tree does not name it, and it is
orchestration shared by `routers/chat.py` (Phase 3) and `scheduler.py` (Phase 7), so it belongs
next to the services it calls rather than inside either caller.

Phase 2 does no DB writes. `run_pipeline` is read-only and returns a list; `listings` /
`price_history` inserts are the scheduler's job (Phase 7). `deals.py` reads the DB only.

---

## 2. The criteria dict

This is the object `criteria.py` (Phase 3, LLM call #1) must emit and what `items.criteria_json`
stores, serialized. Document this exact example as a comment block at the top of
`backend/services/pipeline.py`, since that is the only file that consumes the whole object.

```python
{
    "name": "portable charger",          # main search noun, required
    "category": "electronics",           # used later by the subreddit/forum maps
    "keywords": ["usb-c", "140w"],       # extra search terms, may be empty
    "must_haves": [                      # hard filter, all must pass
        {"field": "Battery Capacity", "op": ">=", "value": 20000},
        {"field": "Pass-Through Charging", "op": "contains", "value": "yes"},
    ],
    "preferred_specs": [                 # soft, drives spec_match, may be empty
        {"field": "Number of USB Ports", "op": ">=", "value": 3},
        {"field": "Product Weight", "op": "<=", "value": 1.0},
    ],
    "nice_to_haves": ["compact", "looks sleek"],   # subjective, LLM call #3 scores these
    "budget_max": 150.0,                 # ranking penalty on price_score, NOT a filter, may be None
    "target_price": 99.0,                # deals.py only, may be None
    "fulfillment_preference": "either",  # pickup | shipping | either, unused in Phase 2
    "radius_miles": 25,
    "min_review_count": 100,
}
```

Access rules for the coder:
- `name`, `radius_miles`, `min_review_count` are required; read with `criteria["..."]`.
- Everything else read with `.get(key, default)` so a thin criteria dict still runs.
- Defaults: `keywords` `[]`, `must_haves` `[]`, `preferred_specs` `[]`, `nice_to_haves` `[]`,
  `budget_max` `None`, `category` `None`.

There are exactly two hard filters in Phase 2: `must_haves` and `min_review_count`. Both come
straight from the spec's pseudocode. `budget_max` is not one of them — see section 3.8.

---

## 3. `backend/services/ranking.py`

Constants at top of file:

```python
FULL_CONFIDENCE_REVIEW_COUNT = 1000   # review count where rating is trusted at face value
NEUTRAL_SCORE = 0.5                   # used wherever a signal is missing entirely
```

### 3.1 `RankedProduct`

`@dataclass` (not a dict: the sub-scores are read by name in five places and a typo in a dict key
fails silently).

```python
@dataclass
class RankedProduct:
    product: dict            # scraper search dict: name, url, price, in_stock, store_id, distance_miles
    retailer: str            # bestbuy | target | amazon
    specs: dict              # raw retailer strings, e.g. {"Battery Capacity": "24,000 milliamp hours"}
    reviews: list[dict]      # one dict per source: {source, rating, review_count, verified_ratio}
    spec_match: float
    review_score: float
    nice_to_have_score: float
    distance_score: float
    price_score: float = 0.0   # set after the full candidate set is known
    final_score: float = 0.0   # set just before sorting
```

The spec's pseudocode constructs this positionally; the pipeline uses keyword args instead
(nine fields, positional is unreadable).

### 3.2 Signatures

```python
def build_query(criteria: dict) -> str
def passes_must_haves(specs: dict, must_haves: list[dict]) -> bool
def compute_spec_match(specs: dict, preferred_specs: list[dict]) -> float
def compute_review_score(reviews: list[dict]) -> float
def compute_distance_score(distance_miles: float | None, radius_miles: int) -> float
def assign_price_scores(candidates: list[RankedProduct], budget_max: float | None) -> None   # mutates price_score
def compute_final_score(ranked_product: RankedProduct) -> float
```

Private helpers (module-level, single-purpose):

```python
def find_spec_value(specs: dict, field: str) -> str | None
def first_number(raw: str) -> float | None
def spec_passes(specs: dict, rule: dict) -> bool
def over_budget_penalty(price: float, budget_max: float | None) -> float
```

### 3.3 `build_query`

Space-join `name` with each entry of `keywords`, lowercase, collapse runs of whitespace.
`{"name": "portable charger", "keywords": ["usb-c", "140w"]}` -> `"portable charger usb-c 140w"`.
Nothing else goes into the query; must_haves are filters, not search terms.

### 3.4 must_have / preferred_spec matching

One rule shape, used by both lists:

```python
{"field": <retailer spec name>, "op": ">=" | "<=" | "==" | "contains" | "exists", "value": <num|str>}
```

`spec_passes(specs, rule)` is the single matcher:

1. `find_spec_value(specs, rule["field"])` — exact key match ignoring case first, then the first
   key that *contains* the field string ignoring case. Returns `None` if neither hits.
2. Value not found -> `False`. **Fail closed.** A must-have whose field is absent is not
   satisfied. Same for preferred_specs (counts as unmet).
3. `exists` -> `True` (the value was found and is non-empty).
4. `contains` -> `str(rule["value"]).lower() in raw.lower()`.
5. `>=`, `<=`, `==` -> `first_number(raw)` compared to `float(rule["value"])`. `None` -> `False`.

`first_number(raw)` regex: `[-+]?\d[\d,]*(?:\.\d+)?`, strip commas, `float()`.
`"24,000 milliamp hours"` -> `24000.0`, `"2.2 inches"` -> `2.2`, `"3"` -> `3.0`,
`"Smart digital display"` -> `None`.

**Stated limits, put in a comment at the top of the matcher, not designed around:**
- No unit conversion. The criteria value must already be in whatever unit the retailer prints.
  `>= 20000` against `"24,000 milliamp hours"` works; `>= 20` (amp hours) silently fails.
- Only the first number in the string is read. `"5.1 x 2.1 inches"` reads `5.1`.
- Field names are retailer-specific strings. Best Buy says `"Battery Capacity"`; Target and
  Amazon will say something else. Cross-retailer spec normalization is not solved here and is
  not attempted in Phase 2.

`passes_must_haves(specs, must_haves)` -> `all(spec_passes(specs, r) for r in must_haves)`.
Empty list -> `True`.

### 3.5 `compute_spec_match`

`satisfied / total` over `preferred_specs`. Empty list -> `1.0` (nothing to prefer, no penalty).

This is why `preferred_specs` exists: must_haves are a hard filter, so every surviving product
passes all of them and a spec_match built from must_haves would be `1.0` for everybody, wasting
the heaviest weight in the formula.

2 preferred, 1 met -> `0.5`.

### 3.6 `compute_review_score`

Input is the list from `gather_reviews`. Only Best Buy exists in Phase 2, so the list has one
entry with `rating`, `review_count`, `verified_ratio=None`.

1. Drop entries with no `rating`. If nothing is left -> return `NEUTRAL_SCORE`
   (cannot judge; do not return 0.0, that punishes a product for a missing feed).
2. Pick the remaining entry with the highest `review_count` as primary — most signal.
3. `rating_component = rating / 5.0`.
4. `confidence = min(1.0, log10(1 + count) / log10(1 + FULL_CONFIDENCE_REVIEW_COUNT))`,
   `count` treated as 0 when `None`.
5. Shrink toward neutral: `score = confidence * rating_component + (1 - confidence) * NEUTRAL_SCORE`.
   A 5.0 from 3 reviews must not beat a 4.7 from 1843.
6. Verified-purchase weighting (spec bullet 1): only if `verified_ratio is not None` and below
   `0.7`, multiply by `verified_ratio / 0.7`. One comment noting no current source populates it —
   Best Buy has no such field; Amazon (Phase 6) is the first that will.
7. Clamp to `[0.0, 1.0]`.

**Not implemented in Phase 2, and no placeholder code for them.** One comment block says why:
- velocity anomaly (spec bullet 2) needs listing age; nothing in the schema records when a
  product was first listed, only `scraped_at`.
- rating-distribution skew (spec bullet 3) needs Amazon's star breakdown; no Phase 2 source
  returns one, and `reviews.rating_distribution_json` is never populated yet.
- cross-source sentiment (spec bullet 4) is LLM call #4, a later phase, and needs Reddit/forum/
  YouTube text that no Phase 2 source produces.

`authenticity_flag` is likewise not computed in Phase 2 — the column stays unwritten until a
source produces a signal that could set it.

### 3.7 `compute_distance_score`

```
distance is None      -> NEUTRAL_SCORE
radius_miles falsy    -> NEUTRAL_SCORE
otherwise             -> max(0.0, 1.0 - distance_miles / radius_miles)
```

0 mi -> 1.0, at the radius -> 0.0, beyond -> 0.0. Best Buy `search()` returns
`distance_miles: None` for every row (online inventory, per Phase 1), so in Phase 2 every
candidate gets `NEUTRAL_SCORE` and this term contributes an identical 0.05 to every product,
changing no ordering. That is the intent: an online-only listing is neither near nor far, and it
must not be scored as if it were at maximum distance.

### 3.8 `assign_price_scores` and the budget penalty

**`budget_max` is not a filter.** Over-budget products stay in the results and rank lower via
`price_score`. The user wants to see the near-miss that costs slightly more but is clearly
better, and prices move over time in a deal tracker, so today's over-budget listing is next
month's deal.

Two steps, in one function, applied to the whole candidate set after it is gathered:

**Step 1 — normalize across the candidate set** (not against the budget: `budget_max` is a
ceiling, not a target, and a budget-relative score would rate a $130 item against a $150 budget
as 0.13 even when it is the best thing found).

```
cheapest = 1.0, most expensive = 0.0, linear in between
raw_score = (max_price - price) / (max_price - min_price)
all prices equal (or one candidate) -> 1.0 for everyone
```

Over-budget products are included in the min/max span like any other candidate — excluding them
would be a filter through the back door.

Accepted consequence of set-relative normalization: a product's `price_score`, and therefore its
`final_score`, shifts between rescans as the candidate set changes, even when its own price did
not move. Scores are comparable within one pipeline run, not across runs. Deal detection does
not use these scores — `deals.py` works on absolute prices from `price_history` — so nothing
alert-related is affected. Add one comment saying this.

**Step 2 — multiply by the over-budget penalty.** No new weight term: the budget is a statement
about price, so it belongs inside the existing 0.20 price term.

```python
# ratio penalty: 10 percent over budget costs 9 percent of the price score, 2x over costs half
def over_budget_penalty(price, budget_max):
    if not budget_max or price <= budget_max:
        return 1.0
    return budget_max / price
```

`price_score = raw_score * over_budget_penalty(price, budget_max)`.

Properties this buys, all of which the alternatives lose:
- Continuous. No cliff at the budget line, so $151 against a $150 budget is barely penalized.
- Bounded in `(0, 1]`, never negative, never zero — an over-budget product can still win on
  the other four terms.
- Degrades with how far over budget it is, so a wildly over-budget product sinks on its own.
- `budget_max` `None` -> `1.0`, the term disappears.

Candidates with `price is None` keep `price_score = 0.0` and are skipped by both steps.

The five spec weights are untouched and still sum to 1.0.

### 3.9 `compute_final_score`

Spec weights, verbatim, as named constants is overkill — inline them in the one expression:

```python
0.35 * spec_match + 0.25 * review_score + 0.20 * price_score
    + 0.10 * distance_score + 0.10 * nice_to_have_score
```

---

## 4. `backend/services/pipeline.py`

```python
SCRAPERS = [("bestbuy", BestBuyScraper())]   # Phase 5/6 append target and amazon
```

```python
async def run_pipeline(item_criteria: dict, lat: float, lon: float, radius_mi: int) -> list[RankedProduct]
async def gather_reviews(retailer: str, scraper, product: dict) -> list[dict]
async def nearby_store_ids(scraper, lat: float, lon: float, radius_mi: int) -> list[str] | None
```

`run_pipeline` body, in order:

1. `query = build_query(item_criteria)`.
2. For each `(retailer, scraper)` in `SCRAPERS`:
   a. `store_ids = await nearby_store_ids(...)` — calls `find_nearby_stores`, returns the
      `store_id` list, catches `NotImplementedError` -> `None`. (The spec's `hasattr` check does
      not work: `ScraperBase` defines the method for every scraper, so Amazon will raise rather
      than be absent.)
   b. `raw = await scraper.search(query, store_ids)`.
   c. For each product:
      - `specs = await scraper.get_specs(product["url"])`.
      - `if not specs:` log and skip. This is the LLM call #2 slot — see section 5.
      - `if not passes_must_haves(specs, must_haves):` skip.
      - `reviews = await gather_reviews(retailer, scraper, product)`.
      - **review-count filter**: `max((r["review_count"] or 0) for r in reviews)` — with the
        empty-list case treated as 0 — `< min_review_count` -> skip.
      - `nice_score = await nice_to_have.score(product, nice_to_haves)`.
      - Build the `RankedProduct` with `compute_spec_match`, `compute_review_score`,
        `compute_distance_score(product["distance_miles"], radius_mi)`.
3. `assign_price_scores(candidates, item_criteria.get("budget_max"))`.
4. Set `final_score = compute_final_score(c)` on each (so callers and the script can print it),
   then `sorted(..., key=lambda r: r.final_score, reverse=True)`.

**No budget filter step exists in this loop.** `budget_max` is read once, in step 3, and passed
to `assign_price_scores`; it appears nowhere else in the pipeline. A product priced above
`budget_max` is scored, ranked and returned like any other. This section is the only place the
plan ever touched budget, so there is no other step to remove — the only remaining consumers of
`budget_max` in the whole codebase are `assign_price_scores` and, separately, `target_price` in
`deals.py`, which is a different field.

Each skip gets one `logger.info` line with the product name and the reason — this is the only way
to see why the pipeline returned three of four products when running the script.

Wrap each scraper's whole block in `try/except Exception` + log, so one broken retailer does not
kill the run. Nothing finer-grained.

`gather_reviews(retailer, scraper, product)`:
- `data = await scraper.get_reviews(product["url"])`; return `[]` if empty.
- Return `[{"source": retailer, **data}]`.
- One comment marking where Reddit/forum/YouTube entries get appended in their phase. No stub
  functions for them — appending to a list needs no scaffolding now.

---

## 5. How the LLM steps are stubbed

Two touchpoints in the pseudocode. They are handled differently on purpose.

**LLM call #3, nice-to-have scoring — real module, constant body.**
`backend/services/nice_to_have.py` is a file the spec's tree already names, so creating it is not
speculative. Entire contents:

```python
NICE_TO_HAVE_STUB_SCORE = 0.5   # neutral: same for every product, so it cannot change ordering

# LLM call #3 lands in a later phase; until then every product scores neutral
async def score(product: dict, nice_to_haves: list[str]) -> float:
    return NICE_TO_HAVE_STUB_SCORE
```

`async def` even though it does no I/O, and the real signature from day one — the later phase
replaces the return statement and touches nothing in `pipeline.py`. No client object, no prompt
constant, no config, no `if ANTHROPIC_API_KEY` branch. Those belong to the phase that writes the
call.

**LLM call #2, spec extraction — no module, no stub.**
The spec's fallback is `spec_extraction.extract(product_page_text)`, but `product_page_text` is
undefined in the pseudocode and no Phase 2 scraper returns page text — Best Buy's `get_specs`
either returns the parsed `details` array or nothing. A stub taking an argument that cannot be
produced is exactly the speculative code the standards forbid. So Phase 2 skips the product and
marks the spot:

```python
if not specs:
    # LLM call #2 (spec_extraction) goes here once a scraper returns raw page text
    logger.info("skip %s: no specs", product["name"])
    continue
```

The later phase adds the module, has the scraper return page text, and replaces the two lines.
No restructuring: the branch, its position, and its control flow are already correct.

---

## 6. Worked numeric examples

These are the pytest assertions in `tests/test_ranking.py`. Compare with `pytest.approx(..., abs=1e-3)`.

**`first_number`**: `"24,000 milliamp hours"` -> `24000.0`; `"2.2 inches"` -> `2.2`;
`"140 watts"` -> `140.0`; `"Smart digital display"` -> `None`.

**`passes_must_haves`** against the `bestbuy_details.json` spec dict:
- `[{"field": "Battery Capacity", "op": ">=", "value": 20000}]` -> `True` (24000 >= 20000).
- `[{"field": "Product Weight", "op": "<=", "value": 1.0}]` -> `False` (1.4 > 1.0).
- `[{"field": "Pass-Through Charging", "op": "contains", "value": "yes"}]` -> `True` ("Yes").
- `[{"field": "Waterproof", "op": "exists"}]` -> `False` (absent, fail closed).
- `[]` -> `True`.

**`compute_spec_match`**: the two `preferred_specs` from section 2 against the same specs ->
ports 3 >= 3 met, weight 1.4 <= 1.0 unmet -> `0.5`.

**`compute_review_score`** (`log10(1001) = 3.0004`):

| product | rating | count | rating_component | confidence | score |
|---|---|---|---|---|---|
| Anker | 4.7 | 1843 | 0.940 | `log10(1844)/3.0004 = 1.088` -> clamped 1.0 | **0.940** |
| Belkin | 4.4 | 612 | 0.880 | `2.7875/3.0004 = 0.929` | `0.929*0.880 + 0.071*0.5` = **0.853** |
| Mophie | 4.1 | 238 | 0.820 | `2.3784/3.0004 = 0.793` | `0.793*0.820 + 0.207*0.5` = **0.754** |
| Insignia | None | 0 | — | — | **0.500** (no rating -> neutral) |

**`compute_distance_score`**: `(None, 25) -> 0.5`; `(0, 25) -> 1.0`; `(5, 25) -> 0.8`;
`(25, 25) -> 0.0`; `(40, 25) -> 0.0`; `(5, 0) -> 0.5`.

**`over_budget_penalty`** with `budget_max = 150.0`:

| price | penalty | note |
|---|---|---|
| 129.99 | 1.000 | under budget, untouched |
| 150.00 | 1.000 | exactly at budget |
| 165.00 | 0.909 | 10 percent over |
| 189.99 | 0.790 | 27 percent over |
| 300.00 | 0.500 | 2x budget |
| any, `budget_max=None` | 1.000 | no budget set |

**`assign_price_scores`** on the four fixture prices with `budget_max=None`
(129.99, 39.99, 99.99, 24.99; min 24.99, max 129.99, span 105.00):
Anker `0.0`, Belkin `90.00/105 = 0.857`, Mophie `30.00/105 = 0.286`, Insignia `1.0`.
Single candidate -> `1.0`. Two candidates at the same price -> both `1.0`.

**`assign_price_scores`** with `budget_max = 150.0` and an over-budget candidate added
(prices 189.99, 99.99, 39.99; min 39.99, max 189.99, span 150.00):
- 189.99: raw `0.000`, penalty `0.790` -> `0.000`
- 99.99: raw `90.00/150 = 0.600`, penalty `1.0` -> `0.600`
- 39.99: raw `1.000`, penalty `1.0` -> `1.000`

**`compute_final_score`, over-budget product still winning.** Same three candidates,
`budget_max = 150.0`, distance and nice both neutral `0.5` (so each row gets a flat `+0.10`):

| | price | spec_match | review | price_score | final |
|---|---|---|---|---|---|
| Anker 757 (over budget) | 189.99 | 1.0 | 0.940 | 0.000 | `0.350+0.235+0.000+0.10` = **0.685** |
| Mophie | 99.99 | 0.5 | 0.754 | 0.600 | `0.175+0.189+0.120+0.10` = **0.584** |
| Belkin | 39.99 | 0.0 | 0.853 | 1.000 | `0.000+0.213+0.200+0.10` = **0.513** |

The $189.99 product is 27 percent over budget and scores `0.0` on price, yet still ranks first
because it satisfies every preferred spec and has the strongest reviews. Under a hard budget
filter it would not have appeared at all. Belkin, the cheapest and fully in budget, ranks last
because price is only 20 percent of the score. This is the behaviour the override asks for.

**`compute_final_score`, budget not set** (the original two-product example, for regression):

| | spec_match | review | price | distance | nice | final |
|---|---|---|---|---|---|---|
| Anker | 0.5 | 0.940 | 0.000 | 0.5 | 0.5 | `0.175+0.235+0.000+0.05+0.05` = **0.510** |
| Belkin | 0.0 | 0.853 | 0.857 | 0.5 | 0.5 | `0.000+0.213+0.171+0.05+0.05` = **0.485** |

---

## 7. `backend/services/deals.py`

```python
def get_price_history(db: Session, listing_id: int, days: int = 90) -> list[PriceHistory]
def get_item_for_listing(db: Session, listing_id: int) -> Item | None
def evaluate_deal(db: Session, listing_id: int) -> str | None
```

The spec's signature has no `db`. It gets one: an explicit session parameter is how every other
DB caller in this codebase will work (`get_db()` in routers, an owned session in the scheduler),
and it is what makes `evaluate_deal` testable against in-memory SQLite.

`evaluate_deal`, following the spec's pseudocode exactly:

```
history = get_price_history(db, listing_id, days=90)   # ordered by recorded_at ascending
if not history: return None                            # nothing recorded yet
current = history[-1].price
recent = [p.price for p in history if p.recorded_at >= cutoff_30d]
all_time_min = min(p.price for p in history)
item = get_item_for_listing(db, listing_id)

if item and item.target_price and current <= item.target_price: return "target_hit"
if current <= all_time_min: return "price_drop"
if recent and current <= 0.9 * mean(recent): return "price_drop"
return None
```

Details the coder must get right:

- **Naive UTC cutoffs.** Phase 1 writes `datetime.now(timezone.utc)` (aware), but SQLite returns
  naive datetimes on read, and comparing aware to naive raises `TypeError`. Build cutoffs as
  `datetime.now(timezone.utc).replace(tzinfo=None)` minus the timedelta, with a one-line comment
  saying why. This applies to both the 90-day query filter and the 30-day window.
- `all_time_min` is over the 90-day window only, matching the pseudocode. `current` is itself in
  that window, so `current <= all_time_min` fires whenever the price is at a 90-day low —
  including a flat price that has never moved. That is the spec's behaviour; keep it and add one
  comment noting it means "at or below the 90-day low", not "strictly a new low".
- `get_item_for_listing` joins `Listing` -> `Item` on `listing.item_id`. Returns `None` if the
  listing or item is missing; `evaluate_deal` then just skips the target check.
- Empty 30-day window (only rows older than 30 days) -> skip the rolling check, do not divide by
  zero.
- Order of checks is fixed: `target_hit` wins over `price_drop`.
- `budget_max` plays no part here. Deal detection is against `item.target_price` and the
  listing's own price history, both absolute prices — the ranking scores never enter.

No alert rows are written here — that is the scheduler's job. `evaluate_deal` returns a string
or `None` and nothing else.

---

## 8. `scripts/check_pipeline.py`

Same shape as `scripts/check_bestbuy.py`: `sys.path.insert` of the repo root, `load_dotenv()`
before importing backend modules, `asyncio.run(main())`, no argparse.

- Prints `MODE: FIXTURE` or `LIVE` from `BESTBUY_API_KEY`, so the operator knows which path ran.
- Hardcoded `CRITERIA` = the exact dict from section 2. Hardcoded `LAT = 37.7749`,
  `LON = -122.4194`, `RADIUS_MI = 25`.
- Calls `run_pipeline(CRITERIA, LAT, LON, RADIUS_MI)`.
- Prints one block per ranked product, in order: rank, name, price, retailer, `final_score`, then
  the five sub-scores each on its own line rounded to 3 decimals. Sub-scores must be visible —
  a bare ordering is not debuggable.
- Marks over-budget rows in the printed line (e.g. a trailing `(over budget)` when
  `price > budget_max`), so it is obvious they were kept rather than filtered.
- Prints `no products passed the filters` when the list is empty.
- Logging at `INFO` so the per-product skip lines from `pipeline.py` appear inline
  (`logging.basicConfig(level=logging.INFO)`).

**Expected fixture-mode output, so the coder does not think it is broken:** Best Buy's fixture
mode ignores its arguments, so `get_specs` and `get_reviews` return the *same* Anker payload for
all four products. Every candidate therefore gets identical specs, identical reviews, identical
spec_match and review_score, and the ranking is decided purely by `price_score` — Insignia first,
Belkin, Mophie, Anker last. All four fixture prices are under the `150.0` budget, so no penalty
applies and all four appear. With `min_review_count = 100` all four survive the review filter
(they all inherit Anker's 1843). The scoring itself is verified by the unit tests, not by this
script. The script proves the pipeline runs end to end, applies its two filters, and produces an
ordering.

---

## 9. Tests

`tests/test_ranking.py` — every number in section 6, plus:
- `build_query` with and without `keywords`.
- `passes_must_haves([])` -> `True`, `compute_spec_match(specs, [])` -> `1.0`.
- `compute_review_score([])` -> `0.5`.
- `assign_price_scores` with one candidate, with all-equal prices, with a `None` price.
- `assign_price_scores` with `budget_max=None` vs the same set with a budget: under-budget
  candidates keep an identical `price_score` either way.
- `over_budget_penalty` table, including `budget_max=None` and price exactly at budget.
- The over-budget ranking example: assert the 189.99 product is `ranked[0]`.

`tests/test_deals.py` — build an in-memory engine (`create_engine("sqlite://")`),
`Base.metadata.create_all`, insert one `Item` + one `Listing` + `PriceHistory` rows with explicit
naive `recorded_at` values. One test per branch:
1. current below `item.target_price` -> `"target_hit"` (and it wins even when a price_drop
   condition also holds).
2. current at the 90-day low, no target -> `"price_drop"`.
3. current <= 90% of the 30-day mean but above the 90-day low -> `"price_drop"`.
4. flat-ish price above all thresholds, `target_price=None`, and current strictly above the
   90-day min -> `None`.
5. no history rows -> `None`.
6. rows only older than 30 days -> no crash.

Pipeline needs no test file this phase; `check_pipeline.py` covers it.

---

## 10. Coder verification checklist

Run from the repo root with a blank `.env`.

1. `pip install -r requirements.txt` — succeeds, `pytest` now installed.
2. `python -m pytest tests -q` — all tests pass. No network access during the run.
3. `python scripts/check_pipeline.py` — prints `MODE: FIXTURE`, then 4 ranked products in
   price order (Insignia, Belkin, Mophie, Anker), each with all five sub-scores printed and
   `final_score` descending down the list. `distance_score` is `0.5` on every row.
4. Run it again from inside `scripts/` — identical output (path handling is cwd-independent).
5. **budget is not a filter**: set `"budget_max": 30.0` in the script's `CRITERIA`, rerun ->
   still 4 products, none dropped, no skip line logged for price. Three of them are now flagged
   over budget and their `price_score` falls (Anker 129.99 keeps raw `0.000` times penalty
   `0.231`; Belkin raw `0.857` times `0.750` = `0.643`; Mophie raw `0.286` times `0.300` =
   `0.086`; Insignia 24.99 is under budget, penalty `1.0`, stays `1.000`). Ordering may change;
   the product count must not. Revert.
6. Set `"budget_max": None`, rerun -> price_scores match the section 6 no-budget table
   (`0.0 / 0.857 / 0.286 / 1.0`). Revert.
7. Set `"min_review_count": 5000`, rerun -> `no products passed the filters`. Revert.
8. Add `{"field": "Product Weight", "op": "<=", "value": 1.0}` to `must_haves`, rerun ->
   zero products (fixture specs are shared, so all four fail). Revert.
9. Set `"preferred_specs": []`, rerun -> every `spec_match` is `1.000`. Revert.
10. `grep -rn "budget_max" backend/` -> matches only in `ranking.py` (`assign_price_scores`,
    `over_budget_penalty`) and the one read in `pipeline.py` step 3, plus the criteria comment
    block. Any `continue` or `if ... budget` inside the product loop is a bug.
11. `python -c "import asyncio;from backend.services.nice_to_have import score;print(asyncio.run(score({}, ['cute'])))"`
    -> `0.5`.
12. `grep -rn "anthropic\|ANTHROPIC" backend/ scripts/` -> no matches. No LLM client exists yet.
13. `python -c "from backend.services import pipeline"` with `ANTHROPIC_API_KEY` unset -> imports
    clean.
14. `uvicorn backend.main:app --port 8000` still starts and `/api/profile` still answers —
    Phase 2 touched nothing Phase 1 owns.
15. `git status` — no new files outside section 1's list. No `target.py`, `amazon.py`,
    `scheduler.py`, `criteria.py`, `spec_extraction.py`, `sentiment.py`, `narration.py`.
16. Grep the diff for emojis -> zero. Grep for `TODO` -> only the two marker comments in
    `pipeline.py` / `nice_to_have.py` described in section 5, if any.
