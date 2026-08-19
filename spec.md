> **This is the original plan, not documentation of the finished app.**
> It is kept because the app disagrees with it in most of the interesting places: the "LLM in
> exactly 5 places, everything else deterministic" rule lost, deterministic spec matching was
> replaced by model judgment, three retailers became four, and the offline fixture mode was
> deleted entirely. See [README.md](README.md) for what was actually built.

# Deal Tracker — MVP Spec

## Overview
Personal deal-tracking app. Two phases:
1. **Chat phase** — conversational needfinding (Claude API) extracts structured product criteria, searches real retailers, ranks results, user buys now or adds to watchlist.
2. **Watchlist phase** — background jobs re-scan tracked items on a schedule, detect deals/new alternatives, send email alerts.

MVP retailers: **Best Buy** (official API), **Target** (internal API), **Amazon** (Playwright scrape). Walmart and niche retailers are v2, not in scope now.

Single user, single VPS, no auth needed for v1.

---

## Tech Stack
- Frontend: React (Vite) + MUI + MUI X Charts
- Backend: FastAPI (Python), single process
- DB: SQLite
- Scheduler: APScheduler, in-process (no Redis/Celery)
- Scraping: Playwright (Amazon only, Tier B fallback for others)
- LLM: Claude API (Anthropic SDK) — used ONLY in the 5 places listed below, nowhere else
- Email: Resend API
- Reverse proxy/HTTPS: Caddy
- Geocoding/autocomplete: Google Places Autocomplete API

---

## Where the LLM is used (exactly these 5 places, nothing else)
1. **Chat intake** — turn free-text user input into structured criteria JSON; ask follow-up questions if criteria is incomplete/ambiguous (e.g. missing budget, vague spec, car trim not specified).
2. **Spec extraction fallback** — only when a product page has no structured spec table; extract specs (mAh, thickness, etc.) from raw description text into JSON.
3. **Nice-to-have scoring** — score subjective criteria (e.g. "cute", "sleek") 0.0–1.0 per product from title/description text.
4. **Cross-source sentiment check** — read Reddit/forum/YouTube-comment text, classify sentiment (positive/negative/mixed) to cross-check against star ratings.
5. **Result narration** — turn ranked structured results into a natural-language chat reply. Also used as last-resort extraction if Tier B CSS selectors return empty (selector broke) — dump raw page text, extract JSON, log the break.

Every other step (queries, scraping, normalization, filtering, ranking math, scheduling, email, deal detection) is deterministic code — no LLM.

---

## Data Model (SQLite)

```sql
profile (
  id INTEGER PRIMARY KEY,
  lat REAL, lon REAL, display_address TEXT
)

items (
  id INTEGER PRIMARY KEY,
  name TEXT,
  category TEXT,
  criteria_json TEXT,       -- full structured criteria object
  budget_max REAL,
  target_price REAL,
  fulfillment_preference TEXT,  -- pickup | shipping | either
  radius_miles INTEGER,
  min_review_count INTEGER,
  status TEXT,               -- watching | archived
  created_at TIMESTAMP
)

listings (
  id INTEGER PRIMARY KEY,
  item_id INTEGER REFERENCES items(id),
  retailer TEXT,              -- bestbuy | target | amazon
  store_id TEXT,               -- null = online
  store_name TEXT,
  distance_miles REAL,
  url TEXT,
  price REAL,
  in_stock BOOLEAN,
  shipping_days_est INTEGER,
  scraped_at TIMESTAMP,
  UNIQUE(item_id, retailer, store_id, url)
)

price_history (
  id INTEGER PRIMARY KEY,
  listing_id INTEGER REFERENCES listings(id),
  price REAL,
  recorded_at TIMESTAMP
)

reviews (
  id INTEGER PRIMARY KEY,
  item_id INTEGER REFERENCES items(id),
  source TEXT,                -- amazon | bestbuy | target | reddit | forum | youtube
  rating REAL,
  review_count INTEGER,
  verified_ratio REAL,
  rating_distribution_json TEXT,
  authenticity_flag TEXT,     -- ok | mixed_signal | suspicious_velocity | skewed_distribution
  url TEXT,
  summary_text TEXT,
  fetched_at TIMESTAMP
)

alerts (
  id INTEGER PRIMARY KEY,
  item_id INTEGER REFERENCES items(id),
  listing_id INTEGER REFERENCES listings(id),
  reason TEXT,                -- price_drop | target_hit | new_alternative
  sent_at TIMESTAMP           -- null until included in a digest/immediate email
)
```

---

## Backend Structure

```
/backend
  main.py                  # FastAPI app init, mounts routers, starts scheduler
  db.py                    # SQLAlchemy engine/session, SQLite file
  models.py                # SQLAlchemy models matching schema above
  scheduler.py             # APScheduler jobs: scrape_job, review_check_job, digest_job
  /routers
    items.py               # CRUD for items/watchlist
    chat.py                 # chat endpoint, criteria extraction, search+rank+narrate
    listings.py              # GET listings/price-history for an item
    alerts.py                # GET alerts
    profile.py                # GET/PATCH location
  /scrapers
    base.py                  # ScraperBase interface: search(), get_specs(), get_reviews()
    bestbuy.py                # Tier A, official API
    target.py                  # Tier A, internal redsky API
    amazon.py                   # Tier B, Playwright
  /services
    criteria.py                # LLM call #1 (chat intake)
    spec_extraction.py          # LLM call #2 (fallback spec parse)
    nice_to_have.py               # LLM call #3 (subjective scoring)
    sentiment.py                  # LLM call #4 (cross-source sentiment)
    narration.py                   # LLM call #5 (chat narration)
    ranking.py                      # scoring/ranking math (pure code)
    deals.py                         # deal-detection threshold logic (pure code)
    reviews_reddit.py                 # PRAW integration
    reviews_forums.py                  # Google CSE integration
    reviews_youtube.py                  # YouTube Data API integration
    geocode.py                           # Google Places lookup
    email.py                              # Resend integration, digest template
requirements.txt
```

---

## Scraper Interface

```python
class ScraperBase:
    async def search(self, query: str, store_ids: list[str] | None) -> list[dict]:
        """Returns list of {name, url, price, in_stock, store_id, distance_miles}"""

    async def get_specs(self, product_url: str) -> dict:
        """Returns spec dict, e.g. {mAh: 24000, thickness_mm: 22}. Empty dict if unavailable —
        triggers spec_extraction.py LLM fallback."""

    async def get_reviews(self, product_url: str) -> dict:
        """Returns {rating, review_count, verified_ratio (if available)}"""

    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        """Returns [{store_id, name, distance_miles}] within radius. Not implemented for Amazon."""
```

### bestbuy.py (Tier A)
- `search()`: `GET https://api.bestbuy.com/v1/products(search={query})&apiKey={key}&format=json`
- Field map: `salePrice`→price, `name`→name, `url`→url, `onlineAvailability`→in_stock, `customerReviewCount`→review_count, `customerReviewAverage`→rating
- `find_nearby_stores()`: Best Buy Stores API, lat/lon/radius param
- `get_specs()`: `/products({sku})?show=details` response, parse `details` array

### target.py (Tier A)
- `search()`: `GET https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2?keyword={query}`
- Field map from JSON response: price, title, tcin (product id), `fulfillment.shipping_options`, `fulfillment.store_options[].location_id/distance`
- `find_nearby_stores()`: included in per-product fulfillment response, or separate nearby-stores endpoint if needed
- No official docs — endpoint found via browser devtools Network tab; wrap all calls in try/except, log failures, treat as Tier B fallback candidate if it breaks

### amazon.py (Tier B — Playwright)
- `search()`: Playwright loads `https://www.amazon.com/s?k={query}`, waits for `.s-result-item`, extracts tiles via CSS selectors: `.a-price-whole` (price), `h2 span` (name), `a.a-link-normal` (url)
- `get_specs()`/`get_reviews()`: separate page load on product URL; selectors: `#acrCustomerReviewCount`, `.a-icon-alt` (rating text, parse "4.5 out of 5 stars"), spec table `#productDetails_techSpec_section_1`
- One browser instance per call, closed immediately after (no persistent browser process)
- Use realistic user-agent string, add 1-3s randomized delay between actions
- If selectors return empty result set → call `narration.py`'s fallback extraction (LLM call #5) with raw page text, log selector break

---

## Core Pipeline (used by both chat search and background rescans)

```python
async def run_pipeline(item_criteria: dict, lat: float, lon: float, radius_mi: int) -> list[RankedProduct]:
    query = build_query(item_criteria)  # deterministic string formatting, code

    results = []
    for scraper in [bestbuy, target, amazon]:
        stores = await scraper.find_nearby_stores(lat, lon, radius_mi) if hasattr(scraper, "find_nearby_stores") else None
        raw = await scraper.search(query, stores)
        for product in raw:
            specs = await scraper.get_specs(product["url"])
            if not specs:
                specs = await spec_extraction.extract(product_page_text)  # LLM call #2, fallback only
            if not passes_must_haves(specs, item_criteria["must_haves"]):  # code, hard filter
                continue
            reviews = await gather_reviews(item_criteria, product)  # amazon/bestbuy/target + reddit/forums/youtube
            if max(r["review_count"] for r in reviews) < item_criteria["min_review_count"]:
                continue  # hard filter, code
            nice_score = await nice_to_have.score(product, item_criteria["nice_to_haves"])  # LLM call #3
            review_score = compute_review_score(reviews)  # code, includes fake-review heuristics
            results.append(RankedProduct(product, specs, reviews, nice_score, review_score))

    ranked = sorted(results, key=lambda r: compute_final_score(r), reverse=True)  # code
    return ranked
```

`compute_final_score`:
```python
score = 0.35*spec_match + 0.25*review_score + 0.20*price_score + 0.10*distance_score + 0.10*nice_to_have_score
```

`compute_review_score` (fake-review heuristics, all code except one sub-check):
- weight verified_purchase_ratio (penalize low %)
- flag review_count vs listing_age velocity anomaly
- flag rating_distribution skew (from Amazon's star-breakdown %, when available)
- cross-source sentiment mismatch → `sentiment.py`, LLM call #4, reads Reddit/forum/YouTube-comment text, flags `mixed_signal` if it contradicts star rating

---

## Review Sources

| Source | File | Method |
|---|---|---|
| Amazon | scrapers/amazon.py | Tier B scrape, aggregate block |
| Best Buy | scrapers/bestbuy.py | Tier A API field |
| Target | scrapers/target.py | Tier A scrape of aggregate block (API may not include it — verify, fallback to page scrape) |
| Reddit | services/reviews_reddit.py | PRAW, category→subreddit map, search item name, time_filter=year |
| Forums | services/reviews_forums.py | Google CSE, `site:` restricted per category (curated dict maintained in code) |
| YouTube | services/reviews_youtube.py | YouTube Data API v3, search "{item_type} review", pull views/comments/likes |

`CATEGORY_SUBREDDIT_MAP` and `FORUM_SITES` are plain Python dicts in `services/reviews_reddit.py` / `services/reviews_forums.py`, extended manually per category as items are added.

---

## Chat Endpoint Flow

```
POST /api/chat/message
  body: {conversation_id, message}
  → LLM call #1 (criteria.py): parse message + conversation history
    → if criteria incomplete: return {type: "followup", question: "..."}
    → if criteria complete: 
        → run_pipeline(criteria, profile.lat, profile.lon, criteria.radius_miles)
        → LLM call #5 (narration.py): narrate top N ranked results
        → return {type: "results", narration: "...", products: [...]}

POST /api/chat/decision
  body: {conversation_id, product_id, decision: "buy_now" | "watch"}
  → buy_now: return purchase link, no DB write
  → watch: insert into items + first listings/price_history rows, return watchlist confirmation
```

---

## Scheduler Jobs (scheduler.py)

```python
scheduler.add_job(scrape_job, "interval", hours=6)       # all items with status=watching
scheduler.add_job(review_check_job, "cron", hour=3)       # daily, new-alternative scan
scheduler.add_job(digest_job, "cron", hour=8)              # daily digest email
```

**scrape_job**: for each watched item, `run_pipeline()` reused, results upserted into `listings` (unique key: item_id+retailer+store_id+url). Price changed → new `price_history` row. Then `deals.py` threshold check → insert `alerts` row if match.

**review_check_job**: broader `run_pipeline()` call without existing-URL filtering, diff against known `listings.url` for that item → new unseen product passing filters → `alerts` row, `reason="new_alternative"`.

**digest_job**: query `alerts WHERE sent_at IS NULL`, if any → render HTML via `email.py`, send via Resend, mark `sent_at`. Target-price-hit alerts should instead be sent immediately at detection time in `scrape_job`, not batched.

---

## Deal Detection Logic (services/deals.py)

```python
def evaluate_deal(listing_id: int) -> str | None:
    history = get_price_history(listing_id, days=90)
    current = history[-1].price
    rolling_avg_30d = mean(p.price for p in history if p.recorded_at >= now() - timedelta(days=30))
    all_time_min = min(p.price for p in history)
    item = get_item_for_listing(listing_id)

    if item.target_price and current <= item.target_price:
        return "target_hit"
    if current <= all_time_min:
        return "price_drop"
    if current <= 0.9 * rolling_avg_30d:
        return "price_drop"
    return None
```

---

## Frontend Structure

```
/frontend/src
  /pages
    Chat.tsx          # chat UI, message list, product result cards, buy-now/watch buttons
    Watchlist.tsx        # item list, best-option summary, deal badges, add/remove
    ItemDetail.tsx          # price history chart (MUI X Charts), listings table (MUI DataGrid), reviews panel
    Alerts.tsx                # alert history table
    Settings.tsx                # location input (Places Autocomplete + geolocation button)
  /components
    ProductCard.tsx
    DealBadge.tsx
    ListingsTable.tsx
    PriceHistoryChart.tsx
  api.ts                # fetch wrappers for all backend endpoints
```

### Settings.tsx location input
- MUI TextField wired to Google Places Autocomplete JS API
- "Use current location" button: `navigator.geolocation.getCurrentPosition()` → lat/lon → reverse geocode (Google Geocoding API) → display + save
- `PATCH /api/profile/location {lat, lon, display_address}`

---

## API Endpoints (full list)

```
POST   /api/chat/message
POST   /api/chat/decision
GET    /api/items
POST   /api/items                  # manual add, skips chat
PATCH  /api/items/{id}
DELETE /api/items/{id}
POST   /api/items/{id}/rescan      # manual trigger, runs scrape_job for one item
GET    /api/items/{id}/listings
GET    /api/items/{id}/price-history
GET    /api/items/{id}/reviews
GET    /api/alerts
GET    /api/profile
PATCH  /api/profile/location
```

---

## Environment Variables

```
ANTHROPIC_API_KEY=
BESTBUY_API_KEY=
GOOGLE_PLACES_API_KEY=
GOOGLE_CSE_API_KEY=
GOOGLE_CSE_ID=
YOUTUBE_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=
RESEND_API_KEY=
USER_EMAIL=                # where digest/alert emails go
DATABASE_URL=sqlite:///./app.db
```

---

## Coding Standards

- Prioritize less code over clever code. If a simpler version does the job, use it.
- Readability over everything. No fancy patterns, no unnecessary abstraction layers, no premature optimization.
- Every file/function should be understandable on its own without jumping around the codebase.
- Comments: prefer inline `//` (or `#` in Python) comments that explain what a line/block does as you read top to bottom, not big docstring essays. Comment the "why" when it's not obvious, not the "what" when it's already obvious from the code.
- No emojis anywhere in code, comments, commit messages, or UI copy.
- Minimal language in comments — short, plain, direct. No filler.
- Keep functions short and single-purpose. If a function is doing 3 things, split it into 3 functions.
- No speculative/generic code for features not in this spec. Build only what's listed. Adding retailers, auth, multi-user support, etc. is out of scope for MVP.
- Config (field maps, category-subreddit maps, forum site lists) lives in plain dicts/constants at the top of the relevant file, not scattered.
- Prefer explicit over implicit — named variables over magic numbers, clear function names over short cryptic ones.

Example of the comment style wanted:
```python
# strip $ and commas, convert to float
def parse_price(raw: str) -> float:
    return float(raw.replace("$", "").replace(",", ""))

# hard filter: drop anything under the user's min review threshold
def passes_review_filter(review_count: int, min_required: int) -> bool:
    return review_count >= min_required
```

---

## Frontend Philosophy for MVP

The initial frontend is intentionally rough — this is a testing harness, not a final product. A real design pass happens later, separately, once the backend/pipeline is proven out.

- Use MUI's default components as-is, no custom theming, no custom styling beyond basic layout.
- No polish: default colors, default spacing, plain text labels, no animations, no loading skeletons — a spinner or "Loading..." text is enough.
- Structure/functionality over appearance: every page just needs to expose the data and let you trigger actions (send chat message, add/remove watchlist item, trigger rescan, view listings/price history/reviews/alerts). If it works and is legible, it's done.
- Layout can be a single column, default MUI Container, no custom grid design.
- Do not build a design system, do not build reusable styled components beyond what's functionally necessary (e.g. a basic ProductCard to display fields, not a "designed" card).
- Skip: dark mode, custom fonts, icons beyond MUI defaults, empty-state illustrations, transitions.
- Goal: fastest path to a clickable/usable interface so the backend pipeline can be tested end-to-end. Visual redesign is a separate future pass, not part of this build.

## Responsive Design (Mobile + Desktop)

This is a website, not separate apps — one responsive build that works on both. Still applies even though the frontend is rough for MVP — it just needs to not break on a phone screen, not look good.

- MUI's built-in breakpoint system (`xs`, `sm`, `md`, `lg`) handles this by default — no custom responsive work needed beyond using MUI components normally (they're responsive out of the box).
- Single column layout works fine on both mobile and desktop for MVP — no need to build separate mobile/desktop layouts.
- ItemDetail price chart and listings table: horizontally scrollable table on mobile instead of squeezing columns (MUI Table default overflow behavior is enough, no custom work).
- Test in browser dev tools responsive mode (Chrome devtools device toolbar) during local testing — no need for a real phone until final check.
- No native app, no PWA wrapper for MVP — just a responsive website reachable from any browser.

---

## Local Testing (before touching the VPS)

Everything runs locally first, identical stack to production — no separate "test setup."

**Local environment**
- SQLite: local file (`app.db` in project folder), same format used later on the VPS.
- Backend: `uvicorn backend.main:app --reload --port 8000`
- Frontend: `npm run dev` (Vite, port 5173), proxy `/api` to `localhost:8000` in `vite.config.ts`
- Playwright: `playwright install chromium` once locally
- APScheduler: same in-process scheduler runs locally — shorten intervals during testing (e.g. `minutes=1` instead of `hours=6`) so jobs fire fast enough to observe

**API keys**
Use real free-tier keys locally (Best Buy, Reddit, YouTube, Google CSE, Resend, Claude API) — none of these restrict localhost. Same `.env` file gets copied to the VPS unchanged later.

**Testing each piece in isolation, before wiring together**
- Scrapers: small standalone script per scraper, call `search()` with a hardcoded query, print results — confirms field mapping before it's part of the pipeline.
- Amazon/Playwright: run with `headless=False` locally to visually confirm selectors are grabbing the right elements, switch to `headless=True` once confirmed.
- Pipeline: call `run_pipeline()` directly with a hardcoded criteria dict, print ranked output — confirms filtering/ranking without needing the chat UI.
- Chat/LLM endpoints: test via FastAPI's auto docs at `localhost:8000/docs`, hit `/api/chat/message` directly with sample text, inspect JSON response — no frontend needed yet.
- Scheduler jobs: trigger manually (`python -c "from backend.scheduler import scrape_job; scrape_job()"`) instead of waiting on the interval, confirms DB writes are correct.

**Fixtures**
Save real API/scrape responses to disk (`tests/fixtures/bestbuy_response.json`, `tests/fixtures/amazon_search.html`) — write/test parsing logic against these instead of hitting live sites every run. Faster, avoids rate-limits/bot-detection during dev.

**Full local end-to-end pass**
Run `uvicorn` + `npm run dev` together, open `localhost:5173`, use the real chat UI, add an item to the watchlist, manually trigger `/api/items/{id}/rescan`, confirm `price_history`/`alerts` populate, confirm a test digest email actually sends (Resend works from localhost). Also test the full flow at a mobile viewport width in devtools.

**Then deploy**
Once the full local flow works end-to-end, copy the same codebase + `.env` to the VPS. Nothing structural changes — the VPS just keeps it running 24/7 instead of on your laptop.

---

## Deploy

1. VPS: install Python 3.11+, Node 20+, `playwright install --with-deps chromium`, Caddy.
2. `pip install -r requirements.txt`
3. `cd frontend && npm install && npm run build`
4. systemd unit: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`
5. Caddyfile: serve `frontend/dist` at `/`, `reverse_proxy /api/* 127.0.0.1:8000`
6. Point domain A record at VPS IP, Caddy auto-provisions TLS

---

## Coding Standards

**Philosophy**: less code over more code. Readability over cleverness. No fancy patterns, no unnecessary abstractions. If a simple function does the job, use a simple function — no classes/factories/interfaces unless there's an actual reason (e.g. `ScraperBase` because there are genuinely 3 interchangeable implementations).

**Comments**:
- Inline `//` (or `#` in Python) comments preferred over block comments or docstring essays.
- Comment explains WHY or WHAT the next line does in plain terms, not restating the code.
- No emojis, anywhere, in code or comments.
- Minimal language — short, direct, no filler words.
- Every non-obvious step gets a one-line comment. Obvious code (e.g. `x = x + 1`) gets no comment.

Example of the target style:
```python
# strip $ and commas, convert to float
price = float(raw_price.replace("$", "").replace(",", ""))

# skip if this store already has this exact listing
if listing_exists(item_id, retailer, store_id, url):
    update_timestamp(listing_id)
else:
    insert_listing(...)
```

Not this style (too verbose, over-explained):
```python
# In this function, we are going to take the raw price string that we
# scraped from the website and we need to convert it into a float value
# so that we can do math on it later. First we remove any dollar signs...
```

**Naming**: descriptive, lowercase_with_underscores (Python) / camelCase (TS/React). No abbreviations unless extremely standard (`id`, `url`, `mi` for miles is fine — `qty` or `usr` is not).

**Functions**: keep short, single-purpose. If a function needs a comment block to explain what it does internally, it's probably doing too much — split it.

**No premature optimization or config**: don't add feature flags, plugin systems, or generic "extensibility" for things not in this spec. Add retailers/sources by writing one more file in the existing pattern, not by building a framework for it.

---

## Build Order (suggested for Claude Code)
1. DB models + SQLite setup
2. Best Buy scraper (Tier A, easiest, validates whole pipeline first)
3. Core pipeline + ranking/deal logic (code-only parts)
4. Chat endpoint + LLM call #1 (criteria extraction) + LLM call #5 (narration)
5. Target scraper (Tier A)
6. Amazon scraper (Tier B, Playwright)
7. Review sources: Reddit, Forums, YouTube
8. LLM calls #2/#3/#4 (spec fallback, nice-to-have scoring, sentiment check)
9. Scheduler jobs + email digest
10. Frontend: Chat page first, then Watchlist, ItemDetail, Alerts, Settings