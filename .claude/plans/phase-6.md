# Phase 6 Plan — Review sources and LLM calls #2, #3, #4

Scope: three review-source modules, three LLM services, `gather_reviews` wiring, cross-retailer
review **and spec** attribution, the honest finish of `compute_review_score`, and the first writer
for the `reviews` table.

**Not this phase:** scheduler/email (Phase 7), any frontend file (Phase 8), any router beyond the
one persistence call in `chat.py`, no new retailer, no rate-limiter framework.

All three carry-over questions are now answered by the user and recorded in section 12.

---

## 0. Source reality this plan is written against

`spec.md`'s Review Sources table is obsolete in three rows. Recorded here so nobody rebuilds it:

| Spec says | Reality | Consequence |
|---|---|---|
| Reddit via PRAW | API access **denied**. `REDDIT_CLIENT_ID/SECRET` stay blank forever | Reddit is Google CSE with `site:reddit.com`. No `praw` dependency, ever |
| Best Buy Tier A API field | API denied (Phase 5); product pages Akamai-blocked (Phase 5b) | Best Buy supplies **search-tile data only**: name, url, price, in_stock. No specs, no rating |
| Target Tier A aggregate block | PerimeterX 403 to this host, treated as **permanent** by user decision | Target supplies nothing |

**First-party retailer review data is Amazon-only, and live first-party spec data is Amazon-only
too.** Sections 9.2 and 9.3 add cross-retailer attribution for reviews (exact model number) and
for specs (title rails), which is what keeps Best Buy in the run at all.

Quota reality, the tightest constraint in the build:

- **Google CSE: 100 queries/day**, shared by Reddit and Forums. Both are CSE.
- **YouTube: ~10,000 units/day.** `search.list` costs **100**; `videos.list` and
  `commentThreads.list` cost **1 each**. So YouTube's cost is the search, not the comments.
- `ANTHROPIC_API_KEY` is real; LLM calls are metered in dollars, not quota (section 8.4).

---

## 1. File list

| File | Action | Purpose |
|---|---|---|
| `backend/services/google_cse.py` | new | one CSE HTTP call + daily budget counter, shared by the two CSE sources |
| `backend/services/reviews_reddit.py` | new | CSE `site:reddit.com`, `CATEGORY_SUBREDDIT_MAP` |
| `backend/services/reviews_forums.py` | new | CSE site-restricted, `FORUM_SITES` |
| `backend/services/reviews_youtube.py` | new | YouTube Data API v3 |
| `backend/services/reviews_store.py` | new | the only reader/writer of the `reviews` table |
| `backend/services/spec_extraction.py` | new | LLM call #2 |
| `backend/services/nice_to_have.py` | **rewrite** | LLM call #3, replaces the constant stub |
| `backend/services/sentiment.py` | new | LLM call #4 |
| `backend/services/attribution.py` | new | title-based spec identity: `title_tokens`, `numbers_conflict`, `same_product`, `attribute_specs` |
| `backend/services/ranking.py` | edit | finish `compute_review_score`, add `apply_authenticity_flags`, `model_key`, `attribute_reviews`; `RankedProduct.specs_inherited_from` |
| `backend/services/pipeline.py` | edit | external gather, `gather_reviews`, both attribution passes, spec-extraction fallback, optional `db`/`item_id` |
| `backend/scrapers/base.py` | edit | add `get_page_text()` to `ScraperBase`, default `""` |
| `backend/scrapers/amazon.py` | edit | `parse_reviews` returns `rating_distribution`; implement `get_page_text` |
| `backend/scrapers/bestbuy.py` | edit | `get_page_text` from the same cached html |
| `backend/scrapers/target.py` | edit | `get_page_text` from the pdp description JSON |
| `backend/models.py` | edit | comment only: the `reviews.source` enum gains `*_inherited`, and why nothing writes `suspicious_velocity` |
| `backend/routers/chat.py` | edit | `watch_product` persists reviews; `ProductOut` gains `specs_inherited_from` |
| `tests/conftest.py` | edit | patch the six new key constants |
| `tests/test_reviews.py` | new | parse functions + quota counter + store, offline |
| `tests/test_sentiment.py` | new | `contradicts`, flag precedence, canned classify |
| `tests/test_attribution.py` | new | model-number review identity **and** title-based spec identity, offline |
| `tests/test_ranking.py` | edit | skew, mixed_signal, inherited, Amazon-only cases |
| `tests/test_scrapers.py` | edit | `rating_distribution` and model-number-key assertions |
| `tests/fixtures/cse_reddit.json` | new | captured CSE response, reddit query |
| `tests/fixtures/cse_forums.json` | new | captured CSE response, forum query |
| `tests/fixtures/youtube_search.json` | new | captured `search.list` |
| `tests/fixtures/youtube_videos.json` | new | captured `videos.list` |
| `tests/fixtures/youtube_comments.json` | new | captured `commentThreads.list` |
| `scripts/check_reviews.py` | new | print all three sources for one query |
| `scripts/save_review_fixtures.py` | new | dev tool, one live capture of the five fixtures |
| `.env.example` | edit | delete the three `REDDIT_*` vars, add the budget vars |
| `requirements.txt` | unchanged | `httpx` and `anthropic` already present. **No `praw`** |

Do **not** create: `scheduler.py`, `email.py`, `geocode.py`, `deals.py` changes, any frontend
file, a generic rate-limiter, a retry helper, a cache framework, a fuzzy-matching library wrapper.

---

## 2. Fixture/offline mechanism — same pattern, six more switches

The rule from Phase 1 and Phase 5 holds unchanged: **the key is the switch, both branches call
the same pure parse function, and the constant is patchable.**

Each new module reads its own key into a module-level constant at import:

```python
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
```

Guard, first statement of every public async function, two lines, identical shape:

```python
# no key configured: parse the saved fixture instead of spending quota
if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
    return parse_cse(load_fixture("cse_reddit.json"), SOURCE)
...live fetch...
return parse_cse(payload, SOURCE)
```

`reviews_reddit.py` and `reviews_forums.py` each declare their **own** `GOOGLE_CSE_API_KEY`
constant even though `google_cse.py` also has one. Three reads of one env var is deliberate:
`from x import Y` copies the binding and would defeat `monkeypatch`, and reading
`google_cse.GOOGLE_CSE_API_KEY` through the module object at call time is less readable than a
local constant. Each file stays understandable on its own, per the coding standards.

`conftest.py` gains six lines in the existing `canned_mode` fixture:

```python
monkeypatch.setattr(google_cse, "GOOGLE_CSE_API_KEY", "")
monkeypatch.setattr(reviews_reddit, "GOOGLE_CSE_API_KEY", "")
monkeypatch.setattr(reviews_forums, "GOOGLE_CSE_API_KEY", "")
monkeypatch.setattr(reviews_youtube, "YOUTUBE_API_KEY", "")
monkeypatch.setattr(nice_to_have, "ANTHROPIC_API_KEY", "")
monkeypatch.setattr(sentiment, "ANTHROPIC_API_KEY", "")
monkeypatch.setattr(spec_extraction, "ANTHROPIC_API_KEY", "")
```

`google_cse` is patched too as belt-and-braces: even if a guard is ever missed, the shared HTTP
function refuses to run without a key.

**The suite stays offline and deterministic. That is the gate, not a nice-to-have.** No test may
open a socket, read the daily counter from a previous test, or depend on wall-clock date except
through an injected value (section 3.3).

### The LLM modules follow `criteria.py`'s precedent, not the scraper precedent

`criteria.py` canned-mode returns `CANNED_CRITERIA` rather than parsing a fixture. The three new
LLM modules do the same: a canned constant for the pipeline path, and the **pure reply parser
unit-tested directly** against a literal JSON string in the test file. Reason: an LLM reply is not
a captured API response with a stable shape worth committing as a fixture; the contract is the
prompt, and the parser is what needs the coverage.

---

## 3. `backend/services/google_cse.py` — the shared CSE call and the quota strategy

Justified the same way `browser.py` was: two modules need byte-identical request, error handling,
and budget accounting, and two copies drifting is the failure mode. **Three functions, no class,
no registry.**

```python
ENDPOINT = "https://www.googleapis.com/customsearch/v1"
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
RESULTS_PER_QUERY = 10          # CSE max for one call; a second page would cost a second query
DAILY_BUDGET = 80               # free tier is 100/day; 20 held back for check scripts and retries
TIMEOUT_SECONDS = 10
```

```python
def budget_left(today: date | None = None) -> int
async def search(query: str) -> dict          # {} on budget exhausted, 429, or any HTTP error
def parse_items(payload: dict) -> list[dict]  # pure: [{title, link, snippet, display_link}]
```

### 3.1 Why the whole strategy is "query once per run, not once per product"

The spec's pseudocode calls `gather_reviews(item_criteria, product)` inside the product loop.
Taken literally with 3 retailers x 3 products, that is **18 CSE queries per pipeline run** — the
entire free tier in five chat messages. So:

**Reddit, Forums, and YouTube are fetched once per run, keyed on the item query, and the same
result block is attached to every candidate.** Retailer reviews stay per-product. That is the
single decision that makes the source affordable:

| | per run |
|---|---|
| CSE queries | **2** (1 reddit + 1 forums) |
| YouTube units | **~104** (1 search + 1 videos + 3 commentThreads) |

At 2 CSE/run the daily budget supports 40 runs. Honest cost: the external signal is
**item-level, not product-level** (section 8.3 states what that does to LLM call #4).

### 3.2 One query per source, not one per site

`FORUM_SITES["computers"]` has five sites. Five queries would be five budget units. Instead the
site list is folded into a single `q` with an OR group:

```
portable charger review (site:forums.tomshardware.com OR site:linustechtips.com OR ...)
```

Same for Reddit's subreddit paths. Cap the OR group at `MAX_SITES_PER_QUERY = 5` so `q` stays
short and the results are not diluted across a dozen low-signal sites.

### 3.3 The daily counter

```python
_SPENT = {"date": None, "count": 0}   # in-process; a restart forgives the day's spend
```

`search()` increments before the request. When `_SPENT["count"] >= DAILY_BUDGET`, log
`cse daily budget exhausted (%d), skipping` and return `{}` **without a request**.

Stated honestly, not solved: the counter is in-process, so a restart resets it and the app could
overspend against Google's real 100. That is why `DAILY_BUDGET` is 80 and why the 429 path below
exists. Persisting the counter needs a table the spec does not have; not worth it for a
single-user app.

`budget_left(today)` takes an optional date **so tests can drive the rollover without touching the
clock**. Production passes nothing.

### 3.4 Graceful degradation when exhausted

Three failure modes, one behaviour:

| Cause | Detection | Result |
|---|---|---|
| Local budget hit | `_SPENT` counter | `{}`, warning, no request |
| Google quota hit | HTTP 429, or 403 with `dailyLimitExceeded` in the body | `{}`, warning, `_SPENT` forced to `DAILY_BUDGET` so the rest of the day skips locally |
| Network/other | `httpx.HTTPError`, non-200 | `{}`, warning |

`{}` -> `parse_items` returns `[]` -> the source returns **no review dict at all** (not a dict of
Nones). The pipeline then runs with fewer external sources, which it already tolerates. **No
retries, no backoff, no queue.** Matches Phase 5's blocked-page policy exactly.

### 3.5 Staleness cache via `reviews.fetched_at` — where it actually applies

The cache only works when `item_id` exists, i.e. on rescans and on a re-search of a watched item.
A first chat search has no item row yet and cannot cache. That is fine: chat searches are
human-paced and rare; **rescans are what would burn the quota** (Phase 7 runs `scrape_job` every
6 hours = 4 runs/day/item = 8 CSE/day/item uncached, so 10 watched items would exceed the tier).

```python
REVIEW_STALENESS_DAYS = 7   # external sentiment moves slowly; retailer ratings are refetched free
```

In `reviews_store.py`:

```python
EXTERNAL_SOURCES = ("reddit", "forum", "youtube")

def load_fresh_external(db, item_id: int, max_age_days: int = REVIEW_STALENESS_DAYS) -> list[dict]
def save_reviews(db, item_id: int, reviews: list[dict]) -> None
def row_to_dict(row: Review) -> dict
```

`load_fresh_external` returns the review dicts for rows whose `fetched_at >= utcnow() -
max_age_days` and whose source is external. If it returns a non-empty list, **the pipeline skips
all three external fetches entirely** — zero CSE queries, zero YouTube units for that run. At 7
days that is 2 CSE queries per watched item per week.

`save_reviews` deletes existing rows for `(item_id, source)` then inserts, so there is exactly one
row per source per item. No history table for reviews; the spec does not ask for one.

---

## 4. `backend/services/reviews_reddit.py`

```python
SOURCE = "reddit"
GOOGLE_CSE_API_KEY / GOOGLE_CSE_ID          # the switch
MAX_SUBREDDITS = 4
# category -> subreddits, extended by hand as categories are added (spec.md, Review Sources)
CATEGORY_SUBREDDIT_MAP = {
    "electronics": ["r/electronics", "r/gadgets", "r/UsbCHardware", "r/batteries"],
    "computers":   ["r/buildapc", "r/laptops", "r/hardware", "r/monitors"],
    "audio":       ["r/headphones", "r/audiophile", "r/BudgetAudiophile"],
    "tv":          ["r/hometheater", "r/4kTV"],
    "phones":      ["r/Android", "r/iphone", "r/smartphones"],
    "photography": ["r/photography", "r/cameras"],
    "appliances":  ["r/appliances", "r/BuyItForLife"],
}
DEFAULT_SUBREDDITS = ["r/BuyItForLife", "r/ProductReviews"]   # unknown category
```

```python
def build_reddit_query(query: str, category: str | None) -> str
async def gather(query: str, category: str | None) -> dict | None
```

`build_reddit_query` -> `"{query} review (site:reddit.com/r/a OR site:reddit.com/r/b ...)"`,
capped at `MAX_SUBREDDITS`. **No `time_filter=year`** — the spec's PRAW parameter has no CSE
equivalent; Google recency is not controllable per query. Stated as a lost capability.

`gather` returns `None` when there are no results (nothing to persist, nothing to score), else the
standard external review dict (section 6.1).

---

## 5. `backend/services/reviews_forums.py`

```python
SOURCE = "forum"
MAX_SITES_PER_QUERY = 5
# seeded to match the sites the CSE itself is configured with. reddit.com is reviews_reddit's job
FORUM_SITES = {
    "electronics": ["forums.tomshardware.com", "linustechtips.com", "techpowerup.com/forums",
                    "slickdeals.net"],
    "computers":   ["forums.tomshardware.com", "linustechtips.com", "anandtech.com",
                    "techpowerup.com/forums", "overclock.net"],
    "audio":       ["head-fi.org", "avforums.com", "rtings.com"],
    "tv":          ["rtings.com", "avforums.com"],
    "phones":      ["forums.macrumors.com", "rtings.com"],
    "photography": ["dpreview.com/forums"],
    "appliances":  ["rtings.com", "slickdeals.net"],
}
DEFAULT_FORUM_SITES = ["rtings.com", "forums.tomshardware.com", "slickdeals.net"]
```

All eleven non-reddit seeded sites appear at least once, so no configured site is dead.

```python
def build_forum_query(query: str, category: str | None) -> str
async def gather(query: str, category: str | None) -> dict | None
```

Same shape as Reddit. The two modules differ only in their dict and their `SOURCE`; that
duplication is intentional and cheaper than a shared "site-restricted source" abstraction.

---

## 6. `backend/services/reviews_youtube.py`

```python
SOURCE = "youtube"
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
SEARCH_URL   = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL   = "https://www.googleapis.com/youtube/v3/videos"
COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
MAX_VIDEOS = 5              # search.list results kept
COMMENT_VIDEOS = 3          # how many of those get a comment fetch
COMMENTS_PER_VIDEO = 10
DAILY_BUDGET_UNITS = 9000   # free tier is ~10000; headroom for check scripts
SEARCH_COST = 100           # search.list. videos.list and commentThreads.list cost 1 each
```

Calls, in order, all in one `httpx.AsyncClient`:

1. `search.list` `part=snippet&type=video&order=relevance&maxResults=5&relevanceLanguage=en&q={query} review` — **100 units**
2. `videos.list` `part=statistics,snippet&id=<5 ids joined>` — **1 unit**
3. `commentThreads.list` `part=snippet&order=relevance&textFormat=plainText&maxResults=10&videoId=<id>` for the top 3 — **3 units**

**Total ~104 units, so ~95 runs/day.** YouTube is not the binding constraint; CSE is.

### Are the comments worth their quota cost? Yes, and this is the concrete finding.

`commentThreads.list` costs **1 unit**, not 100. Three videos of comments cost 3 units against a
104-unit call that was already spent on the search. Comments are ~3% of the cost and are the only
**actual prose from real buyers** any of the three external sources produces — CSE gives Google's
truncated snippet, YouTube gives full comment bodies. They are the highest-value text in LLM call
#4's input for near-zero marginal quota. Fetch them.

Field map:

| Output | Source path |
|---|---|
| `video_id` | `search: items[].id.videoId` |
| `title` | `search: items[].snippet.title` (html-unescaped) |
| `channel` | `search: items[].snippet.channelTitle` |
| `url` | `https://www.youtube.com/watch?v={video_id}` |
| `view_count` | `videos: items[].statistics.viewCount` -> int, absent -> `None` |
| `like_count` | `videos: items[].statistics.likeCount` -> int (hidden on some videos -> `None`) |
| `comment_count` | `videos: items[].statistics.commentCount` -> int |
| `comments[]` | `commentThreads: items[].snippet.topLevelComment.snippet.textDisplay` |

```python
def parse_videos(search_payload: dict, videos_payload: dict) -> list[dict]
def parse_comments(payload: dict) -> list[str]
def build_summary(videos: list[dict]) -> str
async def gather(query: str) -> dict | None
```

**No rating is derived from likes.** A like ratio is not a 0-5 star rating and pretending
otherwise would corrupt `compute_review_score`. `rating` stays `None`.

### 6.1 The external review dict — one shape for all three sources

```python
{
    "source": "reddit" | "forum" | "youtube",
    "rating": None,             # no external source publishes a star rating
    "review_count": None,       # NOT the number of threads. see below
    "verified_ratio": None,
    "rating_distribution": None,
    "url": "<top result url>",             # persisted to reviews.url
    "summary_text": "<joined titles/snippets/comments, truncated>",  # -> reviews.summary_text
    "mention_count": 8,         # threads or videos matched; display only, never a filter
    "authenticity_flag": "ok",  # set by apply_authenticity_flags
}
```

**These three dicts are item-level and are attached to EVERY candidate in the run**, not only to
candidates from the retailer that happened to be searched. A Best Buy candidate and a Target
candidate receive exactly the same Reddit, forum, and YouTube dicts an Amazon candidate does.
What a non-Amazon candidate lacks is a **retailer star rating**, and that is what section 9.2's
attribution pass addresses. `gather_reviews` (section 9.1) makes the attachment explicit and is
tested for it (section 10.2).

**`review_count` is `None`, deliberately.** `pipeline.py`'s hard filter is
`max(r["review_count"] or 0 for r in reviews) < min_review_count`. If Reddit reported 8 matched
threads as a review count, a product with zero real reviews could pass a `min_review_count=5`
filter on the strength of eight forum posts. `None` -> `0` -> the filter is unaffected. The count
lives in `mention_count`, which nothing filters on. **See section 12.4 item 3 — this is the one
point where this plan does not do what an amendment literally asked, and it needs a yes/no.**

`summary_text` is capped at `MAX_SUMMARY_CHARS = 2000` per source, so three sources feed at most
6000 characters into LLM call #4.

---

## 7. `compute_review_score` — what is actually computable

`spec.md` lists four fake-review heuristics. Here is the honest status of each against data that
exists:

| Spec bullet | Status | Why |
|---|---|---|
| weight verified_purchase_ratio | **UNIMPLEMENTABLE** | No source publishes it. Amazon's aggregate block does not expose it; deriving it means paging the review list, which is out of scope and is a second Playwright load per product. Best Buy and Target publish nothing at all now |
| review_count vs listing_age velocity | **UNIMPLEMENTABLE** | Nothing supplies a listing age. `listings.scraped_at` is when *we* first saw it, not when the listing was created. Amazon sometimes prints "Date First Available" in the detail bullets, but it is absent from the committed `amazon_product.html`, so there is no offline coverage and no basis for a threshold. Noted for a future phase; not built on a maybe |
| rating_distribution skew | **IMPLEMENTABLE, and built this phase** | Amazon's `#histogramTable` **is present in the committed fixture** with clean `aria-label="71 percent of reviews have 5 stars"` labels. Costs one extra selector on an already-loaded page and is fully testable offline |
| cross-source sentiment mismatch | **IMPLEMENTABLE** | LLM call #4, section 8.3 |

So `authenticity_flag` can in practice only ever take **three of its four values**:
`ok`, `mixed_signal`, `skewed_distribution`. **`suspicious_velocity` is unreachable** and no code
writes it. Leave the value in the `models.py` column comment with one line saying why nothing
emits it, so it is not rediscovered as a bug.

### 7.1 Amazon-only first-party data, and what attribution recovers

With Best Buy product pages blocked and Target 403ing, the `reviews` list for a candidate is:

- Amazon candidate: `[amazon_row, reddit, forum, youtube]`
- Best Buy / Target candidate: `[reddit, forum, youtube]`, **plus an inherited Amazon row when the
  model numbers match exactly** (section 9.2)

Consequences, all real:

1. `compute_review_score` returns `NEUTRAL_SCORE` for any candidate with no first-party **and no
   inherited** rating, so the spec's 0.25 review weight discriminates only among candidates that
   have one.
2. The `min_review_count` hard filter is evaluated **after** attribution (section 9.1), so a
   candidate that matches an Amazon model number is judged on Amazon's review count rather than
   on zero. Section 9.2.6 lists exactly which candidates can still be dropped.
3. **Review attribution needs a first-party model number, which needs a reachable product page.**
   Live Best Buy product pages are blocked, so live Best Buy candidates have no model number.
   Section 9.3 is what keeps them alive — it inherits *specs* on title evidence — but by design
   (9.3.8) that does **not** unlock review inheritance. A live Best Buy candidate typically ends
   the run with inherited specs and no rating.

### 7.2 Amazon distribution parsing (`amazon.py`)

`parse_reviews` gains one key. Read `#histogramTable a[aria-label]`, regex
`(\d+) percent of reviews have (\d) stars`, build `{"5": 0.71, "4": 0.09, ...}` as fractions.
Missing table -> `None`. `bestbuy.parse_reviews` and `target.parse_reviews` return
`"rating_distribution": None` so the contract is uniform.

The existing `test_reviews_are_numbers` test asserts specific keys, not set equality, so it is
unaffected.

### 7.3 `apply_authenticity_flags` — new in `ranking.py`, pure

```python
FIVE_STAR_DOMINANCE = 0.80    # share of 5-star reviews above which the curve is suspicious
HOLLOW_MIDDLE_MAX = 0.10      # combined 2-4 star share below which the curve is bimodal
SKEWED_DISTRIBUTION_PENALTY = 0.75
MIXED_SIGNAL_PENALTY = 0.85

def distribution_is_skewed(distribution: dict | None) -> bool
def apply_authenticity_flags(reviews: list[dict], external_sentiment: str | None) -> None
```

`distribution_is_skewed` is `True` only when **both** conditions hold: `5-star >= 0.80` **and**
`2+3+4 star <= 0.10`. A high 5-star share on its own is normal for good products; the hollow
middle is the actual fake-review shape. The committed fixture (71/9/5/4/11) correctly does
**not** flag — that is the regression test.

`apply_authenticity_flags` mutates each dict's `authenticity_flag` in place:

- external rows -> always `"ok"` (they carry no rating to be suspicious about)
- retailer rows, **first-party or inherited alike** -> `"skewed_distribution"` if the distribution
  is skewed, else `"mixed_signal"` if `sentiment.contradicts(external_sentiment, row["rating"])`,
  else `"ok"`

**Precedence: `skewed_distribution` wins over `mixed_signal`**, because it is measured data about
this exact product and the sentiment signal is item-level and weaker (section 8.3). The column
holds one value; state the precedence in a comment.

### 7.4 The finished function

```python
def compute_review_score(reviews: list[dict]) -> float
```

Signature unchanged, still pure, still no I/O — flags and any inherited row are already on the
dicts when it is called.

1. `rated` = rows with a non-`None` rating. Empty -> `NEUTRAL_SCORE` (missing feed, not a bad
   product). Unchanged.
2. `primary` = highest `review_count`. Confidence shrink toward neutral. Unchanged.
3. `verified_ratio` branch: **kept, with its comment rewritten** to say no MVP source populates it,
   so it is dead but harmless and correct if a source ever does.
4. **New:** `if primary.get("authenticity_flag") == "skewed_distribution": score *= 0.75`
5. **New:** `elif primary.get("authenticity_flag") == "mixed_signal": score *= 0.85`
6. Clamp to `0..1`. Unchanged.

The two penalty constants are guesses and must be commented as such. They are deliberately mild:
both signals are weak, and a heavy penalty on a weak signal is worse than no signal.

### 7.5 Inherited ratings are NOT discounted — the justification

`compute_review_score` treats an inherited Amazon row exactly like a first-party one: no extra
multiplier, no confidence haircut, flags computed identically.

Reasoning, and the counter-argument, both stated:

- **Star ratings on Amazon and Best Buy are product-level, not listing-level.** They aggregate
  reviews of the product, not of the seller or of that retailer's particular page. An exact model
  number match means it is the same physical product, so the rating describes the thing the user
  would receive. Discounting it would encode "we are less sure", when the identity rule (exact
  model equality, no fuzzy fallback) is precisely what removes that uncertainty.
- A discount would also be **self-defeating**: the whole point of the user's design is that a
  Best Buy listing should not be silently dropped for lacking reviews that demonstrably exist for
  the same product. Restoring the data and then penalising it re-introduces the bias in softer
  form.
- **Residual risk, accepted and documented:** a retailer-exclusive bundle or kit sold under the
  parent model number would inherit the base product's rating. Exact model equality is the guard
  the user chose; if a wrong-bundle case ever shows up, the fix is a stricter identity key, not a
  fudge factor. The `*_inherited` source value (9.2.4) is what makes such a case findable.

Note the contrast with **inherited specs**, which **are** discounted (9.3.6) — different identity
evidence, different answer, both justified rather than assumed.

What inheritance does **not** do: it never changes `price`, `in_stock`, `url`, `store_id`, or
`distance_miles`. Only the review dict and the spec dict move.

---

## 8. The LLM calls

### 8.1 LLM call #2 — `spec_extraction.py`

Fires **only** when `scraper.get_specs()` returned `{}`, exactly as the spec's pseudocode says.
The blocker until now was that the pipeline has no page text. Fixed with one new interface method:

```python
# ScraperBase, new fifth method, default returns ""
async def get_page_text(self, product_url: str) -> str:
    """Raw visible product-page text for the LLM spec fallback. "" when unavailable."""
```

- Amazon / Best Buy: `BeautifulSoup(html).get_text(" ", strip=True)` from
  `fetch_product_html(url)` — **zero extra page loads**, because the 60-second single-entry cache
  from Phase 5 still holds the page `get_specs` just fetched.
- Target: the pdp JSON's `product_description` / `soft_bullets` text. One cheap extra JSON call.
- Returns `""` when blocked. `""` -> no LLM call, so a captcha page is never sent to Claude. That
  guard was Phase 5's stated prerequisite for building this at all.

**Ordering note:** LLM call #2 runs inside the retailer loop, before spec inheritance. It is the
*first-party* recovery path — a page we did reach but could not parse. Spec inheritance (9.3) is
the *last* resort, for a page we never reached at all. First-party recovery must always be tried
first, and specs it produces count as first-party, not inherited.

```python
ANTHROPIC_API_KEY / MODEL = "claude-sonnet-4-5" / MAX_TOKENS = 1000
MAX_PAGE_CHARS = 12000        # roughly 3k tokens; product pages run far longer than this
CANNED_SPECS = {}             # no key: no fallback, the product is skipped exactly as before

def parse_specs_reply(text: str) -> dict          # reuses criteria.parse_json_reply
async def extract(page_text: str, wanted_fields: list[str]) -> dict
```

`wanted_fields` is the union of `must_haves` and `preferred_specs` field names **plus
`"Model Number"`**, so a page recovered by the LLM fallback can still participate in attribution.

Prompt: "Extract product specifications from this page text. Return a single JSON object mapping
spec name to value **as a string, verbatim including units**. Use these names when the page
contains the corresponding spec: `<wanted_fields>`. Omit anything not stated on the page. Do not
infer, convert units, or guess. No other text."

Contract: `{"Battery Capacity": "24000 mAh", "Model Number": "C2046S"}` — flat `str -> str`, the
same shape `parse_specs` returns, so `find_spec_value` works on it unchanged. Non-object reply,
non-string values, or a parse failure -> `{}` and a warning. Values are **never** coerced to
numbers here; `first_number` already does that downstream.

Cap: `SPEC_EXTRACTION_PER_RUN = 3` in `pipeline.py`. Beyond it, log and skip. Without a cap a
retailer whose selectors broke would send one 12k-char prompt per product on every rescan forever.

### 8.2 LLM call #3 — `nice_to_have.py` rewrite

```python
NO_PREFERENCES_SCORE = 1.0    # nothing asked for cannot be missed
CANNED_SCORE = 0.5            # no key: neutral, identical for every product, cannot reorder
MAX_TOKENS = 200

def parse_score_reply(text: str, nice_to_haves: list[str]) -> float
async def score(product: dict, nice_to_haves: list[str]) -> float
```

Signature unchanged from the stub, so the call site moves but its shape does not.

- `not nice_to_haves` -> return `NO_PREFERENCES_SCORE` **before** any key check. Matches
  `compute_spec_match`, which returns `1.0` when nothing is preferred. This is a behaviour change:
  the stub returned 0.5. It shifts absolute `final_score` values in the offline suite but cannot
  change ordering, since it is constant across candidates. Existing assertions on exact scores
  must be updated, not worked around.
- No key -> `CANNED_SCORE`.
- Live -> one call per **surviving** candidate (9.1: the call now happens after both filters, so
  candidates dropped by `must_haves` or `min_review_count` cost nothing).
- Input is the product **title only** plus price and retailer; specs are excluded because "cute"
  and "sleek" are not spec judgements and the blob would dominate the prompt.

Prompt: "Score how well this product matches each subjective preference, 0.0 to 1.0, judging only
from the title. 0.5 when the title says nothing either way. Reply with a single JSON object:
`{"scores": {"<preference>": 0.0}}` and nothing else."

Contract: one key per requested preference. `parse_score_reply` averages the values for the
requested preferences, ignores extra keys, clamps each to `0..1`, and returns `CANNED_SCORE` if
the reply is unparseable or has no requested key. Averaging in code, not in the prompt: the model
scores, deterministic code aggregates.

### 8.3 LLM call #4 — `sentiment.py`

**Split into one LLM call per run plus a pure per-product comparison.** The spec implies a
per-product call; that would be up to 9 calls per run on text that is identical for all of them.

```python
MAX_TOKENS = 300
MAX_INPUT_CHARS = 6000        # 3 sources x MAX_SUMMARY_CHARS
CANNED_SENTIMENT = {"sentiment": "unknown", "confidence": 0.0, "summary": ""}
POSITIVE_RATING_FLOOR = 4.3   # a rating this high alongside negative talk is the contradiction
NEGATIVE_RATING_CEILING = 3.5

def build_input(external_reviews: list[dict]) -> str
def parse_sentiment_reply(text: str) -> dict
async def classify(external_reviews: list[dict]) -> dict
def contradicts(sentiment: str | None, rating: float | None) -> bool
```

**Exactly what text feeds it**, since this was an open question:

| Source | Text | Origin |
|---|---|---|
| Reddit | up to 10 x (result title + Google snippet), ~200 chars each | CSE `items[].title` + `items[].snippet` |
| Forums | same | CSE |
| YouTube | up to 5 video titles + up to 30 top-level comments (10 each from 3 videos) | `search.list` + `commentThreads.list` |

CSE snippets are Google's truncated extract, **not** full threads — fetching thread bodies would
mean scraping reddit.com/old.reddit.com per result, which is out of scope and a new block risk.
The YouTube comments are therefore the only full-sentence buyer prose in the input, which is why
section 6 spends 3 quota units on them. Truncate the whole concatenation at `MAX_INPUT_CHARS`,
labelled by source so the model knows what it is reading.

Prompt: "You are reading community discussion collected for a shopping query. Classify overall
sentiment about this kind of product. Reply with one JSON object: `{"sentiment": "positive" |
"negative" | "mixed" | "unknown", "confidence": 0.0-1.0, "summary": "one sentence"}`. Use
`unknown` when the text is off-topic or too thin to judge. Do not invent details."

`summary` is stored in `reviews.summary_text` for the sentiment row and shown in Phase 8's reviews
panel. `contradicts` is pure code:

```
sentiment == "negative" and rating >= 4.3   -> True
sentiment == "positive" and rating <= 3.5   -> True
otherwise (including "mixed" and "unknown") -> False
```

**"mixed" does not contradict anything.** Community discussion is usually mixed; treating that as a
red flag would flag nearly every product and make the signal worthless.

**Stated weakness, do not paper over it:** the external text is retrieved with the *item* query
("portable charger usb-c 140w"), not the product's brand and model, because a per-product query
costs a CSE unit per product and the quota does not allow it. So `mixed_signal` means "this
listing's star rating sits far outside what the community says about this class of product", which
is a much weaker claim than the spec's "contradicts this product's reviews". `MIXED_SIGNAL_PENALTY
= 0.85` is sized for that weakness. If the CSE tier is ever raised, per-product queries are the
first upgrade to make.

### 8.4 LLM call count and cost per pipeline run

Caps are `MAX_PRODUCTS_PER_RETAILER = 3` and `DETAIL_LOOKUPS_PER_RETAILER = 3`, three retailers.

| Call | Count per run | Prompt size |
|---|---|---|
| #2 spec extraction | 0-3 (capped, fires only on empty specs from a reachable page) | ~3k tokens in, ~200 out |
| #3 nice-to-have | 0-9 (one per **surviving** candidate; 0 when `nice_to_haves` is empty) | ~150 in, ~50 out |
| #4 sentiment | exactly 1 | ~1.7k in, ~100 out |
| #5 narration | 1 (existing) | ~600 in, ~200 out |

Worst case ~14 calls. At Sonnet 4.5 rates ($3/M in, $15/M out) that is roughly **$0.04-0.05 per
run**, dominated by call #2's page text. Typical case (specs parse fine, so #2 never fires) is
~10 calls and roughly **$0.01**. A watched item on the Phase 7 6-hourly schedule is 4 runs/day, so
about **$0.04-0.20 per item per day**. Bounded, not free — worth stating in the commit message and
worth revisiting if the watchlist grows past a handful of items.

Both attribution passes add **zero** calls of any kind — see 9.2.7 and 9.3.9.

---

## 9. `pipeline.py` wiring

```python
SPEC_EXTRACTION_PER_RUN = 3   # capped: a broken selector must not send one page per product

async def gather_external_reviews(item_criteria, db=None, item_id=None) -> list[dict]
async def gather_reviews(retailer, scraper, product, external) -> list[dict]
async def run_pipeline(item_criteria, lat, lon, radius_mi, db=None, item_id=None) -> list[RankedProduct]
```

`db` and `item_id` are **optional and default to `None`**: a first chat search has neither, and
must still work. When both are present the staleness cache and persistence are active; when either
is missing, external sources are fetched fresh and nothing is written. One comment saying so.

### 9.1 The new order of operations

Both attribution passes are joins across the *whole* candidate set, so neither can live inside the
per-product loop. That forces four things out of the loop and after it: **the no-specs drop, the
must_haves filter, the review-count filter, and the review/nice-to-have scoring.** This is the one
structural change in `pipeline.py` this phase, and it is required by the user's two attribution
decisions, not a preference.

The critical ordering constraint the coordinator called out: **spec inheritance must run before the
`skip: no specs` drop**, or the candidate is gone before it can inherit. Same for `must_haves`,
which must run *after* inheritance so an inherited candidate is filtered on the specs it now has.

1. `external = await gather_external_reviews(...)` — **once, before the retailer loop.**
   - if `db` and `item_id`: `cached = reviews_store.load_fresh_external(db, item_id)`; non-empty
     -> use it, spending zero quota.
   - else call the three `gather()` functions, dropping `None` results.
2. `sentiment_result = await sentiment.classify(external)` — once.
3. **Retailer loop** (unchanged caps, unchanged tile ranking), per product:
   - `specs = await scraper.get_specs(url)`; if empty, and under `SPEC_EXTRACTION_PER_RUN`:
     `text = await scraper.get_page_text(url)`; if text, `specs = await spec_extraction.extract(...)`.
     **Specs may still be `{}` here and that is now allowed** — the candidate stays in the list.
   - `reviews = await gather_reviews(retailer, scraper, product, external)`.
   - append a `RankedProduct` with `specs` (possibly `{}`), `reviews`, `distance_score` set;
     `spec_match`, `review_score`, `nice_to_have_score` left at their defaults.
   - **No drops happen in the loop any more** beyond the existing pre-detail-cutoff branch.
4. **After the loop, in this exact order:**
   1. `attribution.attribute_specs(candidates)` — section 9.3. Fills empty spec dicts only.
   2. drop candidates whose specs are **still** empty -> existing `skip <name>: no specs` log.
   3. `passes_must_haves(c.specs, must_haves)` drop -> existing `skip <name>: failed must_haves`
      log. Inherited specs are evaluated here exactly like first-party ones — **this is the hazard
      the user accepted**, and 9.3 is entirely about narrowing it.
   4. `c.spec_match = compute_spec_match(c.specs, preferred_specs)`, then apply the inherited
      discount (9.3.6).
   5. `ranking.attribute_reviews(candidates)` — section 9.2, model-number identity, **first-party
      specs only** (9.3.8).
   6. drop on `min_review_count` -> existing `skip <name>: N reviews` log.
   7. `apply_authenticity_flags` + `compute_review_score` per survivor.
   8. `c.nice_to_have_score = await nice_to_have.score(...)` per survivor.
   9. `assign_price_scores`, `compute_final_score`, sort. Unchanged.
5. `if db and item_id: reviews_store.save_reviews(db, item_id, external)`.

**Cost of moving `get_reviews` before the `must_haves` filter:** on the Playwright retailers it is
free, because `get_reviews` hits the same URL `get_specs` just loaded and Phase 5's 60-second
single-entry cache serves it. Target pays one extra ~200ms JSON call per product that later fails
`must_haves`. Both acceptable; state the arithmetic in the comment above the loop.

`gather_reviews` builds `[{"source": retailer, **data}] + external` — the retailer row first, then
**the same three item-level dicts for every candidate regardless of retailer** — and returns
`external` alone when the retailer supplies nothing. The external dicts are **shared objects
across candidates**; only retailer rows are mutated, so the sharing is safe. Note it in a comment.

### 9.2 Cross-retailer REVIEW attribution — exact model number only

A candidate with no first-party retailer review dict may **inherit** another candidate's
retailer-level review dict from the same run, on one condition: **the two products publish the
exact same model number.** No fuzzy name matching, ever — that is where a different capacity,
colour, or bundle silently acquires the wrong star rating.

#### 9.2.1 Where the model number comes from

Both retailers publish it, and in both cases it is already in the dict `parse_specs` returns.
**No new scraper extraction is needed.** Verified against the committed fixtures:

| Retailer | Fixture | Spec key | Value | Extracted by |
|---|---|---|---|---|
| Best Buy | `bestbuy_product.html` (`#key-specs-list`) | `"Model Number"` | `A1383H11-1` | `bestbuy.parse_specs` — already works |
| Amazon | `amazon_product.html` (`table.a-keyvalue`) | `"Model Number"` (plus a duplicate `"Model Name"`) | `C2046S` | `amazon.parse_specs` — already works |
| Target | `target_pdp.json` bullets | **none** | — | Target publishes no model number, so it can never be an attribution source or target |

Read via the existing `find_spec_value(specs, "Model Number")`, so Phase 5b's token-subset matcher
also picks up `"Model number"` and `"Model Name"`.

**Best Buy does NOT publish a model number in its search tiles** — `bestbuy_search.html` contains
no `Model`/`model` string at all. It lives only on the product page. That fact is what forced the
separate title-based mechanism in 9.3.

#### 9.2.2 Normalization — minimal, and stated exactly

```python
MODEL_KEY_MIN_LENGTH = 3
MODEL_KEY_BLOCKLIST = ("na", "n/a", "none", "unknown", "doesnotapply", "notapplicable")

# uppercase, drop whitespace and hyphens. "a1383h11-1" -> "A1383H111"
def model_key(specs: dict) -> str | None
```

Three transformations and nothing else: uppercase; remove all whitespace; remove all hyphens. Then
reject (return `None`) when the result is shorter than `MODEL_KEY_MIN_LENGTH`, is in the
blocklist, or the spec key was absent.

**What normalization deliberately does not do:** no stripping of trailing letters, no prefix
matching, no removing digits, no stemming, no edit distance. `A1383H11-1` and `A1383H11-2` stay
different; `C2046S` and `C2046` stay different. Only separators are removed, never a character
that carries information.

#### 9.2.3 The join

```python
def attribute_reviews(candidates: list[RankedProduct]) -> None
```

Pure, in `ranking.py`, no I/O, mutates `candidate.reviews` in place.

1. Build `donors: dict[str, dict]` mapping `model_key` -> that candidate's **first-party retailer
   review row**, for every candidate with a usable key, **non-inherited specs** (9.3.8), and a row
   with a non-`None` rating. On key collision keep the row with the **higher `review_count`**, per
   the user's "whichever source has the most reviews".
2. For every candidate with **no** first-party retailer row with a rating: look up its key. On a
   hit, append a **copy** of the donor row with `source` rewritten to `f"{donor_source}_inherited"`
   and an added `"inherited_from_retailer"` key. A copy, not the shared object, so the two rows can
   carry different `authenticity_flag` values.
3. No hit, or no usable key -> **do nothing.** Item-level sources only. Never guess.

A candidate never inherits when it already has a first-party rating.

#### 9.2.4 Marking inherited review rows

**Decision: a distinct `source` value, `"<origin>_inherited"` — e.g. `"amazon_inherited"`.**

- No schema change: `reviews.source` is free text and this is one more documented value.
  `models.py`'s enum comment becomes
  `amazon | bestbuy | target | reddit | forum | youtube | <retailer>_inherited`.
- Self-describing in a raw `sqlite3` dump, which is where somebody will be looking when they wonder
  why a Best Buy item has an Amazon rating. Greppable. `EXTERNAL_SOURCES` tests unaffected.
- Rejected: keeping `source = "amazon"` plus a boolean column — needs a migration for a single-user
  SQLite app with no migration tooling, and `source=amazon` under a Best Buy item is exactly the
  ambiguity the user asked to eliminate.

#### 9.2.5 What gets written to the `reviews` table

Rows are per **item**; `watch_product` persists the chosen candidate's rows (9.4). A Best Buy
listing that inherited Amazon's row persists `source = "amazon_inherited"`.

#### 9.2.6 Which candidates can STILL be dropped by `min_review_count`

1. Any candidate whose retailer publishes no model number — every Target candidate, always.
2. Any candidate whose model number matches nothing else in the run (with 3 results per retailer,
   an exact overlap is not guaranteed).
3. **Every live Best Buy candidate**, because its product page is blocked, so it has no first-party
   model number — and 9.3.8 deliberately refuses to let title-inherited specs supply one.
4. Every candidate when Amazon itself is blocked or returns nothing — no donor exists anywhere.
5. Item-level sources never rescue a candidate (`review_count` is `None` by design, 6.1).

All are logged by the existing `skip <name>: N reviews` line, so they are visible rather than
silent. Case 3 is the one to watch in live runs.

#### 9.2.7 Cost

**Zero additional external calls.** A dict build and a lookup over data already fetched.

### 9.3 Cross-retailer SPEC attribution — title rails

**The user's decision:** a candidate whose own `get_specs` returned empty may inherit another
candidate's specs for the same product, and those inherited specs are then used to evaluate
`must_haves` and `spec_match`. Chosen over exempting no-spec candidates from filters, and over
leaving Best Buy dormant.

**The hazard, stated first because it drives every rule below:** inherited specs feed a **hard
filter**. A wrong-variant match does not merely mislabel a rating — it evaluates `must_haves`
against a different product's numbers and silently admits a product that fails the user's stated
requirement. Every rail below is biased toward **preferring a miss over a wrong match**, and the
expected outcome is a *low* inheritance rate. A rail that fires rarely is working as designed.

Identity must come from the **search tile title only**: Best Buy tiles carry no model number and
its product page is unreachable live.

#### 9.3.1 What the real titles look like

Grounded in the committed fixtures, not invented:

- Best Buy: `"Anker - Power Bank (20K, 87W, Built-In USB-C Cable) - Black"`,
  `"Samsung - Magnetic Wireless Battery Pack - Gray"`. Format is `Brand - Description - Color`.
  Capacity is written `20K`, **not** `20000mAh`.
- Amazon: `"charmast 20000mAh Portable Charger with Built-in Cables..."`,
  `"INIU 45W Fast Charging Portable Charger, Smaller 10000mAh..."`, and — importantly — several
  with **no brand at the front at all**: `"Portable Charger with Wall Plug, Slim USB C Power
  Bank..."`, `"Portable Charger 10000mAh with 4 Built-in Cables..."`.

Two consequences baked into the design: the brand rail must cope with a *generic* first token
(answer: no brand, no inheritance — a miss, which is the safe direction), and the numeric rail
must expand `20K` to `20000` or it will never see that Best Buy states a capacity at all.

#### 9.3.2 `backend/services/attribution.py`

A separate module rather than more code in `ranking.py`: `ranking.py` is scoring math, this is
identity matching, and the file is one dict of stopwords plus four short functions.

```python
GENERIC_BRAND_WORDS = ("portable", "power", "usb", "wireless", "battery", "charger", "external",
                       "fast", "slim", "magnetic", "travel", "compact", "mini", "new", "the")
STOPWORDS = GENERIC_BRAND_WORDS + ("with", "for", "and", "built", "in", "pack", "bank", "cable",
                                   "cables", "plug", "wall", "display", "led", "black", "white",
                                   "gray", "grey", "blue", "red", "silver", "pink", "purple")
SCALE_SUFFIXES = {"k": 1000, "m": 1000000}    # Best Buy writes "20K" where Amazon writes "20000mAh"
UNIT_ALIASES = {"mah": "capacity", "wh": "energy", "w": "power", "watt": "power", "watts": "power",
                "v": "volts", "gb": "storage", "tb": "storage", "in": "length", "inch": "length",
                "inches": "length", "mm": "length", "lb": "weight", "lbs": "weight", "oz": "weight"}
DISTINCTIVE_MIN_LENGTH = 3
DISTINCTIVE_MIN_DIGITS = 3     # a shared number needs 3+ digits to count as model-like evidence
```

```python
def title_tokens(title: str) -> list[str]        # lowercase, punctuation to spaces, split
def brand_token(title: str) -> str | None        # first non-generic token, else None
def title_numbers(title: str) -> tuple[dict, set]  # ({unit_kind: {values}}, {bare values})
def numbers_conflict(a_title: str, b_title: str) -> bool
def distinctive_shared(a_title: str, b_title: str, brand: str) -> set[str]
def same_product(a_title: str, b_title: str) -> bool
def attribute_specs(candidates: list[RankedProduct]) -> None
```

#### 9.3.3 Rail 1 — brand agreement

`brand_token` = the first token that is not in `GENERIC_BRAND_WORDS` and is not a pure number.
Best Buy's `"Anker - Power Bank..."` -> `anker`. Amazon's `"charmast 20000mAh..."` -> `charmast`.
Amazon's `"Portable Charger with Wall Plug..."` -> `portable` is generic, next is `charger`, also
generic -> continue -> if the first three tokens are all generic, return `None`.

`brand_token(a) is None or brand_token(b) is None or a != b` -> **no inheritance.** Brand-less
Amazon titles (half the fixture) simply never donate. That is a miss, and misses are the safe
direction.

#### 9.3.4 Rail 2 — numeric disagreement is a hard reject

The single most valuable rail. `title_numbers` scans each title for numbers and classifies each:

- Strip commas: `24,000` -> `24000`.
- Apply a scale suffix attached to the digits: `20K` -> `20000`, `1.5M` -> `1500000`.
- Find the **unit token**: the alphabetic run attached to the digits (`10000mAh`, `87W`) or the
  immediately-following whitespace-separated token (`24000 mAh`). Map it through `UNIT_ALIASES` to
  a **unit kind** (`capacity`, `power`, ...). An unrecognised unit token means the number is
  treated as **bare** — unknown units are never compared, because comparing them would invent
  conflicts.
- No unit token -> **bare number** (`737`, `20000` from `20K`).

Result: `united = {unit_kind: set_of_values}` and `bare = set_of_values`.

**`numbers_conflict(a, b)` is `True` — reject — when either holds:**

1. **Same unit kind, different values.** For any unit kind present in both, if the value sets are
   not equal -> conflict. `24000 mAh` vs `20000 mAh` -> conflict. `87W` vs `87W` -> fine.
   This is the "Anker 737 24,000 mAh must never inherit from Anker 737 20,000 mAh" case.
2. **Both sides have bare numbers and the bare sets are disjoint.** `{737}` vs `{733}` -> disjoint
   -> conflict. This is the "Anker 733 must never match Anker 737" case.

**How a bare number is compared against a number with a unit: it is not.** `737` (bare) is never
compared against `24000 mAh` (capacity). Comparing them would make every model-number-bearing
title conflict with every capacity-bearing one and the rail would reject everything. The two pools
are kept separate, and each is only ever compared against its own kind. This is the explicit answer
to the coordinator's question.

Deliberate asymmetry: an *empty* pool on one side is **not** a conflict (no evidence), but it also
contributes no support — rail 3 then has to carry the identity claim.

Worked against the real fixture strings:

| A (Best Buy) | B (Amazon) | Result |
|---|---|---|
| `Anker - Power Bank (20K, 87W, Built-In USB-C Cable) - Black` | `Anker Power Bank 20000mAh 87W Built-In USB-C Cable` | bare `{20000}` vs bare `{}`; capacity `{20000}` only on B; power `87` both -> **no conflict**; rail 3 sees shared number 20000 -> inherit |
| same | `Anker 737 Power Bank 24000mAh 140W` | power `87` vs `140` -> **conflict, reject** (and capacity too) |
| `Anker - Power Bank (20K...)` | `Anker 733 Power Bank 20000mAh` | bare `{20000}` vs bare `{733}` disjoint -> **conflict, reject** |

Note the third row: the rail rejects a pair that might genuinely be the same product, because
Best Buy's `20K` becomes a bare number and Amazon's `733` is also bare. **That is the trade being
made** — the bare-number rail cannot tell a model number from an unlabelled capacity, so it errs
toward rejecting. Accepted per rail 4.

#### 9.3.5 Rail 3 — at least one distinctive shared token beyond the brand

`distinctive_shared` returns tokens present in both titles that are **not** the brand, **not** in
`STOPWORDS`, and satisfy one of:

- contains at least one digit **and** at least one letter, length >= `DISTINCTIVE_MIN_LENGTH`
  (`a1383`, `usb-c` normalized to `usbc`, `pd20w`), or
- is a shared **number value** with at least `DISTINCTIVE_MIN_DIGITS` digits (`20000`, `10000`).
  Computed on the normalized numeric values, so Best Buy's `20K` matches Amazon's `20000mAh`.

Empty set -> **no inheritance.** Generic word overlap ("portable", "charger", "black") can never
establish identity, which is why `STOPWORDS` exists and why it is a plain tuple at the top of the
file per the coding standards.

#### 9.3.6 Rail 4 — prefer a miss, and `same_product`

```python
def same_product(a_title, b_title) -> bool:
    # brand agreement, no numeric conflict, and at least one distinctive shared token
```

All three rails must pass. Then in `attribute_specs`:

- Only candidates with a **non-empty** spec dict are donors.
- For each candidate with an **empty** spec dict, collect every donor where `same_product` holds.
- **Zero donors -> inherit nothing. More than one donor -> inherit nothing** and log
  `ambiguous spec donor for %s: %d candidates`. Ambiguity is exactly the state in which a wrong
  match is most likely, so it resolves to a miss rather than to a tie-break. This is rail 4,
  literally implemented.
- Exactly one donor -> `candidate.specs = dict(donor.specs)` (a copy) and
  `candidate.specs_inherited_from = donor.retailer`, plus a `logger.info` naming both titles so
  every inheritance is auditable in the check-script output.

#### 9.3.7 Rail 5 — one-directional, fills only, never overrides

Inheritance only ever writes into a dict that was `{}`. A candidate with first-party specs — even
one thin spec — is never touched, never merged into, never partially overwritten. There is no
reverse flow and no transitive inheritance: a candidate that inherited specs is **not** added to
the donor pool, so inherited specs can never propagate to a third candidate.

#### 9.3.8 Spec inheritance does NOT unlock review inheritance

The important integrity rule, and it must not be quietly dropped later:

Inherited specs carry the donor's `"Model Number"`. If `attribute_reviews` were allowed to read it,
a **title-based** identity claim would be laundered into what the user specified as
**exact-model-number-only** identity, and the stricter rule would be silently downgraded to the
looser one. So `attribute_reviews` (9.2.3) skips any candidate whose `specs_inherited_from` is set,
both as a donor and as a recipient.

Consequence, stated plainly: a live Best Buy candidate typically ends the run with **inherited
specs and no rating**, so it survives `must_haves` but can still be dropped by a non-zero
`min_review_count` (9.2.6 case 3). Spec inheritance alone does not fully revive Best Buy.

If you would rather a spec-inheriting candidate also inherit that same donor's review row — same
identity claim, marked identically, no laundering into the model-number rule — that is a one-line
change in `attribute_reviews`. **Flagged as a question in 12.3, not decided here**, because it
loosens a constraint you set explicitly.

#### 9.3.9 Cost

**Zero additional external calls.** No page load, no API call, no LLM call, no DB query.
`attribute_specs` is a pairwise title comparison over at most 9 candidates — at most ~36 pairs of
short-string tokenization, once per run.

#### 9.3.10 Marking inherited specs

**Mechanism: a new field on `RankedProduct`,** `specs_inherited_from: str | None = None`, mirroring
the `*_inherited` convention used for reviews.

- Surfaced to the API: `ProductOut` gains `specs_inherited_from: str | None`. **This is the "do not
  make it impossible" requirement** — Phase 8 can render "specs from amazon" on the card, or not,
  without any backend change. No UI is built this phase.
- Logged: one `logger.info` per inheritance naming both titles, so `check_pipeline.py` output shows
  provenance without opening the DB.
- Narration: `narration.summarize` gains the field so LLM call #5 can mention it if relevant. It is
  a one-key addition to an existing compact dict.

**Rejected: writing a `"_inherited_from"` key into the specs dict itself.** It would travel with
the data, but `find_spec_value` token-matches over every key in that dict, so a synthetic key
becomes a candidate match for a user's rule. Contaminating the dict that feeds the hard filter to
carry metadata about the hard filter is the wrong trade.

**Honest gap in durable storage:** specs are **not persisted anywhere in the MVP.** There is no
specs column on `listings`, no specs table, and `items.criteria_json` holds the user's criteria,
not the product's specs. So there is currently nothing in the DB to mark. The provenance lives on
`RankedProduct`, in the API response, and in the logs. If durable provenance matters, the natural
home is a `listings.specs_source` column added when Phase 7 starts writing listing rows from the
scheduler — **flagged in 12.3 as a question, not built here**, since adding a column for data that
is not yet stored would be speculative.

#### 9.3.11 Inherited specs ARE discounted in `spec_match` — the justification

```python
SPEC_MATCH_INHERITED_PENALTY = 0.9   # title identity is weaker evidence than a model number
```

Applied to `spec_match` only (9.1 step 4.4), never to `must_haves`.

Why this is the opposite answer from inherited reviews (7.5), and why that is not inconsistent:

- **The evidence is genuinely weaker.** Review inheritance rests on exact model-number equality —
  a manufacturer identifier. Spec inheritance rests on brand + no numeric conflict + one shared
  distinctive token, from a marketing title. Those are different confidence levels and deserve
  different treatment; giving them the same treatment would be the inconsistency.
- **It is a free, well-aimed tiebreak, not theatre.** `must_haves` is binary and cannot express
  partial confidence — a candidate either passes or is dropped, and there is no third option.
  `spec_match` is a continuous 0.35-weight term, and it is exactly the right place to say "between
  two otherwise equal candidates, prefer the one whose specs were read off its own page". That is
  a real ordering effect on a real uncertainty.
- **The counter-argument, stated:** if we trust the match enough to run a hard filter on it, why
  discount the soft score? Because the two are not the same decision. The hard filter has no
  alternative — the choice there is "use inherited specs or drop the candidate", and the user chose
  the former. The soft score has a costless alternative, so residual doubt is expressed there.
- 0.9 is deliberately mild and is commented as a judgement call, like the review penalties.

#### 9.3.12 Residual wrong-match risk — the honest limit, with a concrete example

After all five rails, this is what still slips through:

**Variants that differ in a spec neither title mentions.** Consider:

- Best Buy tile: `"Anker - Power Bank (20K, 87W, Built-In USB-C Cable) - Black"`
- Amazon tile: `"Anker Power Bank 20000mAh 87W Built-In USB-C Cable, Blue"`

Brand agrees (`anker`). No numeric conflict (capacity 20000 both after `20K` expansion, power 87
both). Distinctive shared token `20000`. **Inherits.** But suppose these are two SKUs that differ
in **port count** — three ports on the Amazon unit, two on the Best Buy one — a fact stated in
neither title. If the user's `must_haves` includes `Number of USB Ports >= 3`, the Best Buy
candidate is admitted on Amazon's port count and **fails the user's stated requirement while
appearing to pass it.** No rail can see this: the rails only compare what the titles say, and the
titles are silent.

Colour variants (`Black` vs `Blue`) are the common benign form of the same thing and are why
colour words are in `STOPWORDS`. The dangerous form is a functional difference that marketing
copy omits: port count, bundled accessories, refurbished/"Renewed" grade, regional plug type, or a
generation bump the brand did not put in the title.

**The honest summary: the rails reliably catch variants that differ in a number the titles state,
and cannot catch variants that differ in anything the titles omit.** The `specs_inherited_from`
marker on every affected candidate is the mitigation — it makes such a case diagnosable after the
fact rather than invisible, which is the difference between an accepted limitation and a silent
bug. If a wrong-variant admission is ever observed, the correct fix is a stricter rail (or dropping
spec inheritance for `must_haves` specifically), not a lower penalty multiplier.

### 9.4 Persisting the chosen product's reviews (`chat.py`)

`watch_product` gains three lines after `db.flush()` on the item:

```python
# first writer for the reviews table: the chosen product's retailer row (first-party or
# inherited) plus the shared external rows, so a rescan can reuse them instead of spending quota
reviews_store.save_reviews(db, item.id, chosen.reviews)
```

This is why `reviews_store` exists as its own module rather than living in `pipeline.py`: it has
two callers in two layers, and Phase 7's `scrape_job` will be a third.

---

## 10. Fixtures and tests

### 10.1 Fixtures — five files, one live capture

`scripts/save_review_fixtures.py` (dev tool, never imported, refuses to run without keys) captures
with a hardcoded query `"portable charger"` and category `"electronics"`:

| File | Capture | One-time cost |
|---|---|---|
| `cse_reddit.json` | full CSE response for the reddit query | 1 CSE query |
| `cse_forums.json` | full CSE response for the forum query | 1 CSE query |
| `youtube_search.json` | `search.list` | 100 units |
| `youtube_videos.json` | `videos.list` | 1 unit |
| `youtube_comments.json` | `commentThreads.list` for one video | 1 unit |

**Total capture cost: 2 CSE queries and 102 YouTube units, once.** Real captured responses, not
hand-trimmed blobs. Requirement: `cse_forums.json` must contain results from at least two distinct
`display_link` domains, and `youtube_videos.json` must contain one video with `likeCount` absent.

**No new scraper fixture is captured for either attribution pass.** The two committed product
fixtures are genuinely different products (`A1383H11-1` vs `C2046S`) and the two hydrated Best Buy
tiles (Anker, Samsung) do not correspond to any Amazon tile, so the fixture pipeline exercises the
**no-match** path for both mechanisms — the correct and honest default. Match paths are covered by
unit tests over literal titles and spec dicts (10.6), the same decoupling Phase 5 §11 applied to
`test_ranking.py`. **Do not edit a fixture to force a match.**

### 10.2 `tests/test_reviews.py` — offline

- `parse_cse` on both fixtures: non-empty, every row has `title`/`link`/`snippet`, every `link`
  starts with `https://`.
- `reviews_reddit.gather()` and `reviews_forums.gather()` in canned mode return a dict whose
  `rating`, `review_count`, and `verified_ratio` are **all `None`**, whose `source` is `reddit` /
  `forum`, whose `summary_text` is non-empty and `<= MAX_SUMMARY_CHARS`, and whose `mention_count`
  is an int.
- `build_reddit_query("portable charger", "electronics")` contains `site:reddit.com/r/` and at most
  `MAX_SUBREDDITS` site terms; unknown category falls back to `DEFAULT_SUBREDDITS`.
- `build_forum_query` for an unknown category uses `DEFAULT_FORUM_SITES`; every site in every
  `FORUM_SITES` value is a bare domain with no scheme.
- `parse_videos` merges statistics onto the search rows; the video with no `likeCount` gets
  `like_count is None`.
- **Item-level dicts reach every candidate:** run the fixture pipeline and assert that candidates
  from **all** retailers present in the result set carry `reddit`, `forum`, and `youtube` entries.
- Quota: `budget_left(date(2026,1,1))` is `DAILY_BUDGET`; after `DAILY_BUDGET` simulated spends
  `search()` returns `{}` **without an HTTP call** (assert via a monkeypatched sentinel that would
  raise); passing the next day's date resets it.
- `reviews_store.save_reviews` / `load_fresh_external` round-trip on in-memory SQLite; an 8-day-old
  row is **not** returned; saving twice leaves one row per source; an `amazon_inherited` row is not
  returned by `load_fresh_external`.
- **`min_review_count` is unaffected by external sources:** three external rows with
  `mention_count=50` -> `max(r["review_count"] or 0 for r in reviews)` is `0`.

### 10.3 `tests/test_sentiment.py` — offline

- `contradicts("negative", 4.8)` -> `True`; `contradicts("positive", 3.0)` -> `True`;
  `contradicts("mixed", 4.9)` -> `False`; `contradicts("unknown", 4.9)` -> `False`;
  `contradicts("negative", None)` -> `False`.
- `parse_sentiment_reply` on a fenced reply, on a bare object, and on garbage -> the canned dict.
- `classify([])` in canned mode -> `CANNED_SENTIMENT`, no exception.
- `build_input` truncates at `MAX_INPUT_CHARS` and labels each source.

### 10.4 `tests/test_ranking.py` — additions

- `distribution_is_skewed({"5":0.71,"4":0.09,"3":0.05,"2":0.04,"1":0.11})` -> `False`
  (the real fixture curve must not flag).
- `distribution_is_skewed({"5":0.88,"4":0.02,"3":0.01,"2":0.02,"1":0.07})` -> `True`.
- `distribution_is_skewed(None)` -> `False`.
- `apply_authenticity_flags` precedence: both skewed and contradicted -> `skewed_distribution`.
- `compute_review_score` with `skewed_distribution` is exactly `0.75x` the unflagged list;
  `mixed_signal` is `0.85x`.
- External rows cannot move the number: `[amazon_row, reddit, forum, youtube]` == `[amazon_row]`.
- No retailer row (`[reddit, forum, youtube]`) -> `NEUTRAL_SCORE`.
- **No inherited-review discount:** a list containing an `amazon_inherited` row scores identically
  to the same list with that row's source set to `amazon`. Regression test for 7.5.
- **Inherited-spec discount applies:** two candidates with identical specs and preferred_specs, one
  with `specs_inherited_from = "amazon"`, produce `spec_match` values in the ratio `0.9`.

### 10.5 `tests/test_scrapers.py` — additions

- `amazon.parse_reviews(AMAZON_PRODUCT)["rating_distribution"]` has all five star keys, floats
  summing to `1.0 +/- 0.02`.
- `bestbuy` and `target` `parse_reviews` return `rating_distribution is None`.
- Model number is extractable from both product fixtures with no new parser:
  `find_spec_value(bestbuy.parse_specs(BESTBUY_PRODUCT), "Model Number") == "A1383H11-1"` and
  `find_spec_value(amazon.parse_specs(AMAZON_PRODUCT), "Model Number") == "C2046S"`.
- `target.parse_specs(TARGET_PDP)` has **no** model-number key — asserts the documented gap.
- `bestbuy.parse_search(BESTBUY_SEARCH)` rows carry no model number — asserts 9.2.1's finding, so
  nobody later assumes review attribution works without a detail lookup.

### 10.6 `tests/test_attribution.py` — new, offline, literal titles and dicts

Built from real fixture strings but as literals, so the tests do not depend on fixture contents.

**Part 1 — review identity (model number), the four cases from the earlier amendment:**

1. **Exact match inherits.** Best Buy candidate `{"Model Number": "A1383H11-1"}` with no retailer
   row; Amazon candidate with the same model number and a row rated 4.2 / 226. After
   `attribute_reviews`: `rating == 4.2`, `review_count == 226`,
   `source == "amazon_inherited"`, `inherited_from_retailer == "amazon"`.
2. **No match falls back to item-level only.** `A1383H11-1` vs `C2046S` -> `reviews` unchanged.
3. **Wrong-variant model numbers do NOT match.** Parameterised, all must stay distinct:
   `("A1383H11-1","A1383H11-2")`, `("C2046S","C2046")`, `("C2046S","C2046T")`, `("X100","X1000")`.
   Positive normalization pairs that **must** match: `("a1383h11-1","A1383H11 1")`,
   `("A-1383","A1383")`.
4. **Inherited is distinguishable.** `source.endswith("_inherited")` true for the inherited row and
   false for the donor's; the rows are separate objects; after `save_reviews`, a query for
   `source = "amazon"` does not return the inherited row.

Plus: `model_key({})` -> `None`; `model_key({"Model Number": "N/A"})` -> `None`;
`model_key({"Model Number": "AB"})` -> `None`; a candidate with a first-party rating does not
inherit; two donors with one key resolve to the higher `review_count`.

**Part 2 — spec identity (titles), the seven cases required by this amendment:**

5. **Exact-ish title match inherits specs.**
   `"Anker - Power Bank (20K, 87W, Built-In USB-C Cable) - Black"` (empty specs) inherits from
   `"Anker Power Bank 20000mAh 87W Built-In USB-C Cable"` (non-empty specs). Assert the spec dict
   is equal to the donor's, is a **separate object** (mutating one does not change the other), and
   `specs_inherited_from == "amazon"`.
6. **Differing capacity does NOT inherit — the critical case.**
   `"Anker 737 Power Bank 24,000 mAh"` vs `"Anker 737 Power Bank 20,000 mAh"` -> `same_product`
   is `False`, specs stay `{}`. Also assert `numbers_conflict` is `True` directly, so the failure
   is attributed to rail 2 and not to an accident of another rail.
7. **Differing model digits does NOT inherit.** `"Anker 737 Power Bank"` vs
   `"Anker 733 Power Bank"` -> `False` (disjoint bare-number sets).
8. **Different brand does NOT inherit.** `"Samsung - Magnetic Wireless Battery Pack - Gray"` vs
   `"Anker Power Bank 20000mAh"` -> `False`. Plus the brand-less case: a title starting
   `"Portable Charger with Wall Plug..."` has `brand_token is None` and never donates or receives.
9. **First-party specs are never overridden.** A candidate with `{"Battery Capacity": "20000 mAh"}`
   and a title that matches a donor perfectly keeps its own dict byte-for-byte and has
   `specs_inherited_from is None`.
10. **Inherited-spec candidate is distinguishable from first-party.** `specs_inherited_from` is
    `"amazon"` on the inheritor and `None` on the donor; `ProductOut` serializes the field; the
    `logger.info` provenance line is emitted.
11. **A must_have evaluated against inherited specs behaves identically to first-party.**
    `passes_must_haves(inherited_candidate.specs, [{"field": "Battery Capacity", "op": ">=",
    "value": 20000}])` returns exactly what the same call on the donor's own specs returns, for
    both a passing and a failing rule. Inheritance must not change filter semantics — only which
    dict is being filtered.

Plus the rails in isolation: `numbers_conflict` `True` for `("87W","140W")` and
`("24,000 mAh","20000mAh")`, `False` for `("20K, 87W","20000mAh 87W")` (scale-suffix expansion) and
`False` for `("Anker 737","Anker Power Bank")` (one bare pool empty);
`distinctive_shared` empty for two titles sharing only stopwords; **ambiguity resolves to a miss** —
two equally matching donors -> nothing inherited and the ambiguity logged; **no transitive
inheritance** — an inheritor is not a donor for a third candidate.

### 10.7 `scripts/check_reviews.py`

Same shape as the other check scripts: `sys.path.insert`, `load_dotenv()`, `asyncio.run`, no
argparse. Prints `MODE: LIVE` / `MODE: FIXTURE` per source, the three review dicts, the sentiment
result, then `cse budget left: N`. Fixture mode prints the shared-fixture warning in Phase 5 style.

`scripts/check_pipeline.py` gains one line per candidate printing the review sources attached,
whether any review row is inherited, and `specs_inherited_from`, so both attribution passes are
visible without opening the DB.

---

## 11. Verification checklist

### Part A — offline. This is the gate. All must pass.

Blank `.env`, network physically disconnected, run from the repo root.

1. `python -m pytest tests -q` — green. **Count is 101 today**; expect roughly 101 + 45. Record the
   new number. **No test count may drop.**
2. Rerun with the network disconnected: identical result. No test opens a socket.
3. `grep -rn "GOOGLE_CSE_API_KEY\|YOUTUBE_API_KEY\|ANTHROPIC_API_KEY" backend/` — every match is a
   module-level `os.getenv` read or a two-line guard. No config module, no `if TESTING`, no mock
   class, no dispatch table.
4. `grep -rn "praw\|reddit.com/api\|oauth.reddit" backend/ requirements.txt` -> **zero matches**.
5. `grep -rn "REDDIT_CLIENT" . --exclude-dir=.git` -> zero outside this plan. `.env.example` no
   longer lists the three dead Reddit vars.
6. `python scripts/check_reviews.py` -> `MODE: FIXTURE` for all three sources, three non-empty
   review dicts, `rating`/`review_count`/`verified_ratio` all `None`, sentiment `unknown`.
7. `python scripts/check_pipeline.py` -> `MODE: FIXTURE`, still returns candidates, **every**
   candidate from **every** retailer carrying the three item-level review entries. Record the
   per-retailer candidate counts and compare to Phase 5b's.
8. **Both attribution passes report no-match offline** and say so in the log rather than silently:
   the fixture products are genuinely different (`A1383H11-1` vs `C2046S`; Anker/Samsung tiles vs
   the Amazon tiles). An inherited row or an inherited spec dict appearing in the fixture run means
   an identity rule is matching things it should not — **treat it as a bug, not a bonus.**
9. `grep -rn "fuzzy\|difflib\|SequenceMatcher\|levenshtein\|rapidfuzz" backend/` -> **zero
   matches.** Review identity is exact-model-only and spec identity is rail-based; neither may
   become similarity scoring.
10. `grep -n "specs_inherited_from" backend/` -> set in exactly one place (`attribute_specs`), read
    in `attribute_reviews` (the 9.3.8 guard), in the `spec_match` discount, and in `ProductOut`.
    No other writer.
11. Ordering is provable, not assumed: temporarily log the pipeline phase names and confirm the
    sequence is `attribute_specs -> no-specs drop -> must_haves -> spec_match -> attribute_reviews
    -> min_review_count -> review_score -> nice_to_have`. **`attribute_specs` before the no-specs
    drop is the whole point; a candidate dropped first can never inherit.**
12. Run both check scripts again from inside `scripts/` — identical output.
13. `grep -n "suspicious_velocity" backend/` -> matches only the `models.py` column comment and one
    explanatory comment in `ranking.py`. **Nothing writes it.**
14. `grep -rn "_SPENT" backend/` -> one module-level dict in `google_cse.py` and nothing else.
15. `uvicorn backend.main:app --port 8000` starts clean; `POST /api/chat/message` then
    `POST /api/chat/decision {"decision":"watch"}`; confirm the `reviews` table has rows for that
    `item_id` — `sqlite3 app.db "select source, rating, review_count, authenticity_flag, fetched_at
    from reviews"`. **Acceptance test for "nothing writes to reviews".** Confirm `ProductOut`
    carries `specs_inherited_from` in the `/api/chat/message` response body.
16. `git status` — new files match section 1 exactly. No `scheduler.py`, no `email.py`, no frontend
    change.
17. Grep the diff for emojis -> zero.

### Part B — live. Information only. Quota-aware. Be sparing.

Real keys in `.env`. **Every item below is a one-shot. Do not loop, do not rerun to "check".**

18. `python scripts/save_review_fixtures.py` — **once**. 2 CSE queries, 102 YouTube units. Then
    rerun the whole of Part A against the fresh captures.
19. `python scripts/check_reviews.py` — **once**, `MODE: LIVE`. 2 CSE + ~104 units. Confirm real
    snippets, real comments, a sentiment other than `unknown`. Record `cse budget left`.
20. Budget exhaustion, **without spending anything**: temporarily set `DAILY_BUDGET = 0`, rerun
    `check_reviews.py`, confirm both CSE sources log `budget exhausted` and return no dict, YouTube
    still runs, and the pipeline still produces candidates. Revert.
21. `LIVE_SCRAPE=1 python scripts/check_pipeline.py` — **once**, timed. Record wall time,
    per-retailer candidate counts, and the actual LLM call count against 8.4's estimate.
22. **Spec attribution, live — this is the one that matters for the Best Buy decision.** Record:
    how many Best Buy candidates reached `attribute_specs` with empty specs; how many inherited;
    for each inheritance, **both titles and the donor retailer**, copied into the commit message.
    Then eyeball each pair yourself and say whether it is genuinely the same product. If any pair
    looks wrong, stop and report before tightening anything — a wrong match here is the failure
    mode the whole design is guarding against, and the rails should be tightened with your
    agreement, not silently.
23. **Review attribution, live:** expected to remain inert for Best Buy (9.2.6 case 3). Record it
    either way; do not add retries or a workaround.
24. LLM call #2, if provokable: find a live product whose `get_specs` returns `{}` and confirm
    `spec_extraction` returns real specs and that a **blocked** page returns `""` and therefore
    makes no LLM call. If no such product appears, record that call #2 is untested live.

**Part B failing does not fail the phase.** Part A is the gate. Item 22 is the exception worth
pausing on: a wrong spec inheritance is not a "live result may vary" outcome, it is a correctness
problem to report.

---

## 12. Decisions, including the three the user has now settled

### 12.1 Settled: `min_review_count` and non-Amazon candidates — review attribution

The user rejected all three options offered (leave it / default to 0 / exempt retailers) and
specified: **source a product's review data from whichever source has the most reviews, including
across retailers, with identity on exact model number only.** Designed in 9.2. Summary:

- Item-level sources attach to **every** candidate — already true by construction, now explicit in
  6.1, 9.1, and tested in 10.2.
- Identity is exact normalized model number equality only. Section 11 item 9 greps for fuzzy libs.
- Model numbers come from `parse_specs` (the **product page**) for both Best Buy and Amazon; **no
  new scraper extraction is needed.** Best Buy's search tiles have none, which is what forced 9.3.
- Inherited rows are marked `source = "<origin>_inherited"` (9.2.4). No schema change.
- Inherited ratings are **not** discounted (7.5).
- `min_review_count` moves after the join. Remaining drop cases enumerated in 9.2.6.
- Zero additional external calls; net LLM cost goes slightly **down**.

### 12.2 Settled: the selector-break LLM fallback is deferred again

Phase 5 §8 deferred LLM call #5's second job — dumping a raw page into the model when Tier B
selectors return nothing — to Phase 6. The user has deferred it again, to **Phase 7 or later**.
Reasoning, recorded so it is not relitigated:

- The fallback's output for a **search page** is a list of products, and each product's `url`
  becomes part of the `listings` unique key (`item_id + retailer + store_id + url`).
- A hallucinated or subtly wrong url therefore **corrupts watchlist identity**: it creates a
  listing row that can never be re-found on a rescan, accumulates no `price_history`, produces a
  duplicate instead of matching on the next run, and can point the user at a page that does not
  exist when they click buy.
- `price_history` is append-only and keyed to that listing, so the corruption is **permanent and
  silent** — nothing downstream can tell an LLM-invented url from a scraped one.
- The **product-page** half is safe and IS built this phase: `spec_extraction` (call #2) takes text
  from a url we already navigated to and returns specs used for filtering and ranking only, never
  as an identity key.
- Phase 5's `# LLM call #5's fallback extraction goes here in Phase 6` comments in `amazon.py` and
  `bestbuy.py` must be updated to say **Phase 7 or later**, with this reason in one line, rather
  than left pointing at a phase that has passed.

### 12.3 Settled: spec inheritance, and the questions it raises

The user chose **extending inheritance from reviews to specs** over exempting no-spec candidates
from filters and over leaving Best Buy dormant, accepting that inherited specs feed a hard filter.
Designed in 9.3. What was decided:

- Identity from the **search tile title only** (Best Buy has no other option live), behind five
  rails: brand agreement, numeric-conflict hard reject, a distinctive shared token, prefer-a-miss
  on ambiguity, and fill-only/never-override.
- `20K` -> `20000` scale expansion, because Best Buy writes capacity that way and without it the
  numeric rail would be blind to the single most important quantity.
- **Bare numbers are never compared against united numbers** (9.3.4), with the reasoning stated.
- **Spec inheritance does not unlock review inheritance** (9.3.8), so the exact-model-number rule
  from 12.1 is not laundered into a title match.
- Inherited specs **are** discounted in `spec_match` (0.9) while inherited reviews are not — two
  different identity strengths, two different answers, both justified in 7.5 and 9.3.11.
- Provenance via `RankedProduct.specs_inherited_from`, surfaced in `ProductOut` and the logs.
- Residual risk documented with a concrete slip-through example in 9.3.12.

**Three questions this raises, flagged rather than decided:**

1. **Should a spec-inheriting candidate also inherit that same donor's review row?** It is the same
   identity claim, marked identically, with no laundering into the model-number rule — and without
   it a live Best Buy candidate typically survives `must_haves` only to be dropped by
   `min_review_count` (9.3.8). One-line change. Not done, because it loosens a constraint you set
   explicitly.
2. **Do you want durable spec provenance in the DB?** Specs are not persisted anywhere in the MVP
   (9.3.10), so there is currently nothing to mark. The natural home is a `listings.specs_source`
   column added when Phase 7 starts writing listing rows. Not built, because adding a column for
   data that is not stored would be speculative.
3. **`SPEC_MATCH_INHERITED_PENALTY = 0.9`** and the rail thresholds (`DISTINCTIVE_MIN_DIGITS = 3`,
   the `STOPWORDS` list) are judgement calls. Live item 22 is what will tell us whether the rails
   are too loose or too tight; expect to tune them once with real data.

### 12.4 Other decisions not covered by spec.md

1. **Reddit is Google CSE, not PRAW.** The spec's `time_filter=year` has no CSE equivalent and is
   lost — external results are not date-bounded.
2. **External sources are fetched once per run, keyed on the item query, shared across candidates**
   (3.1). Per-product costs 18 CSE queries per run against a 100/day tier. Consequence: the
   sentiment signal is item-level, and `MIXED_SIGNAL_PENALTY` is sized for that weakness (8.3).
3. **Item-level sources still report `review_count = None`, so they do NOT contribute to
   `min_review_count`.** An earlier amendment asked for the filter to consider "first-party,
   inherited, and item-level". This plan honours the first two and deliberately **not** the third:
   a CSE result count is a count of *Google hits*, not reviews; eight Reddit threads is not eight
   reviews; and 10 results is the per-query maximum, so it could only ever satisfy thresholds under
   10 anyway. **Say the word if you want `mention_count` to count toward the filter — one line.**
   Item-level sources DO feed `review_score` via sentiment, and DO attach to every candidate.
4. **`google_cse.py`, `reviews_store.py`, and `attribution.py`** are not in the spec's file tree.
   Justified as `browser.py` was: shared, single-purpose, a handful of functions each.
5. **`DAILY_BUDGET = 80` in-process counter.** Not persisted; a restart forgives the day's spend.
6. **`REVIEW_STALENESS_DAYS = 7`** and the `fetched_at` cache. Without it, four rescans a day per
   item would exhaust the CSE tier at ten watched items.
7. **`ScraperBase` gains a fifth method, `get_page_text`.** Required to make LLM call #2 reachable.
8. **Amazon `rating_distribution` is parsed and `skewed_distribution` is implemented**, on a
   two-condition heuristic (dominant 5-star **and** hollow middle). Thresholds and penalties are
   judgement calls, commented as such.
9. **`nice_to_have.score` returns 1.0 when `nice_to_haves` is empty**, matching
   `compute_spec_match`. Changes absolute offline scores (not ordering); existing exact-score
   assertions must be updated.
10. **LLM call #4 is one call per run plus a pure per-product comparison.**
11. **`run_pipeline` gains optional `db` and `item_id` parameters**, both defaulting to `None`.
12. **YouTube comments are fetched** (3 units) because `commentThreads.list` costs 1 unit, not 100.
13. **The no-specs drop, `must_haves`, the review-count filter, and both scoring calls all move
    after the retailer loop** (9.1). Forced by both attribution passes being whole-set joins.
    Side effects: fewer LLM calls, and one extra Target JSON call per must_haves-failing product.

## 13. What in spec.md I believe is now unbuildable

- **`verified_ratio` weighting.** No MVP source publishes a verified-purchase ratio. The column,
  the `ScraperBase` docstring, and the penalty branch all stay, but nothing populates them.
- **Review-velocity anomaly, and therefore the `suspicious_velocity` flag value.** Nothing supplies
  a listing age. **No code will ever write `suspicious_velocity` this MVP.**
- **The Review Sources table's Best Buy and Target rows.** Best Buy supplies search-tile fields
  only; Target supplies nothing. Spec inheritance (9.3) is what keeps Best Buy in the ranking at
  all, and review inheritance (9.2) cannot help it while its product page is blocked.
- **PRAW, `time_filter=year`, and the three `REDDIT_*` environment variables.** Dead; delete the
  env vars from `.env.example` so nobody reapplies for the API.
- **Product-level external sentiment.** Affordable only at item level under a 100 query/day tier.
- **`spec.md`'s implicit assumption that every ranked product's specs were read from its own product
  page.** After 9.3 that is no longer true for Best Buy, and `specs_inherited_from` exists so the
  difference is visible rather than assumed away.
