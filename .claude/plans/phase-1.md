# Phase 1 Plan — Scaffolding, DB, Profile API, Best Buy Scraper, Fixture Layer

Scope is Phase 1 only. Files for later phases are not created, not stubbed, not imported.
Repo is currently empty except `spec.md` and `.claude/`.

---

## 1. File list

All paths relative to repo root `C:\Users\ih841\OneDrive\Documents\GitHub\price-checker`.

| File | Purpose |
|---|---|
| `.gitignore` | Ignore `.env`, `app.db`, `__pycache__/`, `.venv/`, `node_modules/`, `dist/` |
| `.env.example` | All env var names from spec, all values blank except `DATABASE_URL=sqlite:///./app.db` |
| `requirements.txt` | fastapi, uvicorn[standard], sqlalchemy, httpx, python-dotenv (nothing for later phases) |
| `backend/__init__.py` | Empty, makes `backend.main:app` importable |
| `backend/main.py` | FastAPI app init, load `.env`, create tables, mount profile router |
| `backend/db.py` | SQLAlchemy engine + `SessionLocal` + `Base` + `get_db()` dependency |
| `backend/models.py` | SQLAlchemy models for profile, items, listings, price_history, reviews, alerts |
| `backend/routers/__init__.py` | Empty |
| `backend/routers/profile.py` | `GET /api/profile`, `PATCH /api/profile/location` |
| `backend/scrapers/__init__.py` | Empty |
| `backend/scrapers/base.py` | `ScraperBase` interface + `load_fixture()` helper |
| `backend/scrapers/bestbuy.py` | Tier A Best Buy scraper: search, get_specs, get_reviews, find_nearby_stores |
| `tests/fixtures/bestbuy_response.json` | Saved `/v1/products(search=...)` response (search fixture) |
| `tests/fixtures/bestbuy_details.json` | Saved `/v1/products(sku=...)?show=details` response (specs fixture) |
| `tests/fixtures/bestbuy_stores.json` | Saved `/v1/stores(area(...))` response (nearby-stores fixture) |
| `scripts/check_bestbuy.py` | Standalone script: calls all four scraper methods with hardcoded inputs, prints results |

Empty directories created but left unfilled this phase: `backend/services/`. Do **not** create
`scheduler.py`, `routers/items.py|chat.py|listings.py|alerts.py`, `scrapers/target.py|amazon.py`,
or any `services/*.py` — later phases own those.

---

## 2. Mock layer mechanism (the no-API-key constraint)

**Rule: the presence of the API key is the switch. There is no flag, no env var, no config, no class.**

One helper in `backend/scrapers/base.py`:

```
load_fixture(filename) -> dict
  # reads tests/fixtures/<filename>, json.load, returns dict
  # path resolved from this file's location, not cwd, so scripts and uvicorn both work
```

In `bestbuy.py`, the key is read once at module import:

```
BESTBUY_API_KEY = os.getenv("BESTBUY_API_KEY", "")
```

Every method that would hit the network begins with a single two-line guard, e.g.:

```
# no key configured: parse the saved fixture instead of calling the API
if not BESTBUY_API_KEY:
    return parse_search(load_fixture("bestbuy_response.json"))
...live httpx call...
return parse_search(response.json())
```

Design requirements this enforces, and the coder must honor all four:

1. **Parsing is a separate pure function per endpoint** — `parse_search(payload)`,
   `parse_details(payload)`, `parse_reviews(payload)`, `parse_stores(payload)`.
   Both branches (fixture and live) call the *same* parse function. This is the whole point:
   the fixture exercises the real field mapping.
2. **The guard is the only difference between the two paths.** No separate mock class,
   no `FakeScraper`, no dependency injection, no `if TESTING`.
3. Fixture files are **real response shapes**, top-level keys included (`products`, `stores`,
   `total`, `from`, `to`, etc.), not pre-parsed convenience blobs.
4. Fixture mode ignores its arguments (query, sku, lat/lon) — it always returns the same saved
   payload. That is acceptable and expected; do not add filtering logic to simulate search.

Adding `BESTBUY_API_KEY=...` to `.env` switches to live with zero code change.

Fixture-mode calls must still be `async def` with the same signatures, so nothing downstream cares.

---

## 3. `backend/db.py`

- `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")`
- `create_engine(DATABASE_URL, connect_args={"check_same_thread": False})` — FastAPI serves
  requests from a threadpool; SQLite blocks cross-thread connections without this.
- `SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)`
- `Base = declarative_base()`
- `get_db()` generator dependency: yield session, close in `finally`.

No Alembic, no migrations. `Base.metadata.create_all(bind=engine)` is called once in `main.py`.

---

## 4. `backend/models.py` — exact field definitions

Classic `Column(...)` style (shorter than `Mapped[]` annotations). Import
`Column, Integer, Float, String, Boolean, DateTime, ForeignKey, UniqueConstraint` and
`from datetime import datetime, timezone`. All timestamp defaults use
`lambda: datetime.now(timezone.utc)`. No `relationship()` — nothing in Phase 1 needs
ORM traversal, and adding it now is speculative.

```
class Profile(Base):
    __tablename__ = "profile"
    id               = Column(Integer, primary_key=True)
    lat              = Column(Float)
    lon              = Column(Float)
    display_address  = Column(String)

class Item(Base):
    __tablename__ = "items"
    id                     = Column(Integer, primary_key=True)
    name                   = Column(String)
    category               = Column(String)
    criteria_json          = Column(String)      # full structured criteria object, serialized
    budget_max             = Column(Float)
    target_price           = Column(Float)
    fulfillment_preference = Column(String)      # pickup | shipping | either
    radius_miles           = Column(Integer)
    min_review_count       = Column(Integer)
    status                 = Column(String)      # watching | archived
    created_at             = Column(DateTime, default=utcnow)

class Listing(Base):
    __tablename__ = "listings"
    id                 = Column(Integer, primary_key=True)
    item_id            = Column(Integer, ForeignKey("items.id"))
    retailer           = Column(String)          # bestbuy | target | amazon
    store_id           = Column(String)          # null = online
    store_name         = Column(String)
    distance_miles     = Column(Float)
    url                = Column(String)
    price              = Column(Float)
    in_stock           = Column(Boolean)
    shipping_days_est  = Column(Integer)
    scraped_at         = Column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("item_id", "retailer", "store_id", "url"),)

class PriceHistory(Base):
    __tablename__ = "price_history"
    id          = Column(Integer, primary_key=True)
    listing_id  = Column(Integer, ForeignKey("listings.id"))
    price       = Column(Float)
    recorded_at = Column(DateTime, default=utcnow)

class Review(Base):
    __tablename__ = "reviews"
    id                       = Column(Integer, primary_key=True)
    item_id                  = Column(Integer, ForeignKey("items.id"))
    source                   = Column(String)   # amazon | bestbuy | target | reddit | forum | youtube
    rating                   = Column(Float)
    review_count             = Column(Integer)
    verified_ratio           = Column(Float)
    rating_distribution_json = Column(String)
    authenticity_flag        = Column(String)   # ok | mixed_signal | suspicious_velocity | skewed_distribution
    url                      = Column(String)
    summary_text             = Column(String)
    fetched_at               = Column(DateTime, default=utcnow)

class Alert(Base):
    __tablename__ = "alerts"
    id         = Column(Integer, primary_key=True)
    item_id    = Column(Integer, ForeignKey("items.id"))
    listing_id = Column(Integer, ForeignKey("listings.id"))
    reason     = Column(String)                 # price_drop | target_hit | new_alternative
    sent_at    = Column(DateTime)               # null until included in a digest/immediate email
```

Comments above go inline on the same line as the column, exactly as the spec's SQL block has them.
No extra columns, no `nullable=False`, no indexes beyond the one UNIQUE constraint — the spec
schema is the contract.

---

## 5. `backend/main.py`

- `load_dotenv()` first, before any module that reads env vars is imported (so
  `bestbuy.BESTBUY_API_KEY` picks up the real value). Inline comment saying exactly that.
- `app = FastAPI(title="Deal Tracker")`
- `Base.metadata.create_all(bind=engine)` at import time — single user, single process, no migrations.
- `app.include_router(profile.router)`
- No CORS middleware yet (no frontend in Phase 1; Vite proxies `/api` in a later phase).
- No scheduler, no startup events.

---

## 6. `backend/routers/profile.py` — API contract

`router = APIRouter(prefix="/api", tags=["profile"])`

The profile table holds exactly one row, id 1. A small `get_or_create_profile(db)` helper
returns it, inserting a blank row on first call. Both endpoints use it.

**GET /api/profile**

- Request: no body, no params.
- 200 response:
```json
{"id": 1, "lat": null, "lon": null, "display_address": null}
```
- Never 404. First call creates the blank row.

**PATCH /api/profile/location**

- Request body (pydantic `LocationUpdate`, all three required):
```json
{"lat": 37.7749, "lon": -122.4194, "display_address": "San Francisco, CA"}
```
- `lat` float, `Field(ge=-90, le=90)`. `lon` float, `Field(ge=-180, le=180)`.
  `display_address` str.
- Out-of-range or missing field -> FastAPI's default 422.
- 200 response: the updated profile, same shape as GET.

Response shape via a single pydantic `ProfileOut` model with
`model_config = ConfigDict(from_attributes=True)`. Two endpoints, one in/one out model — that
is the whole file plus the helper.

---

## 7. `backend/scrapers/base.py`

Interface copied verbatim from the spec's "Scraper Interface" section — same four methods, same
signatures, same one-line docstrings. Bodies are `raise NotImplementedError`.

Plus the fixture helper (section 2). It lives here rather than in a new `utils.py` because it is
four lines and every scraper needs it; a separate module would be one more file to jump to.

```
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# scrapers call this when their API key is missing, so the app runs with an empty .env
def load_fixture(filename: str) -> dict: ...
```

Coder must confirm `parents[2]` resolves to the repo root from `backend/scrapers/base.py`.

---

## 8. `backend/scrapers/bestbuy.py` — field mapping

Constants at top of file, per the spec's "config lives in plain dicts/constants at the top" rule:
`BASE_URL = "https://api.bestbuy.com/v1"`, `BESTBUY_API_KEY`, `RETAILER = "bestbuy"`.

Shared params on every live call: `apiKey`, `format=json`. Requests via
`httpx.AsyncClient(timeout=10)` inside `async with`.

### `search(query, store_ids)`
- Live URL: `GET {BASE_URL}/products(search={query})?apiKey={key}&format=json`
- Fixture: `bestbuy_response.json`
- Parse `payload["products"]`, one output dict per product:

| Output field | Source |
|---|---|
| `name` | `name` |
| `url` | `url` |
| `price` | `salePrice` |
| `in_stock` | `onlineAvailability` |
| `store_id` | `None` (Best Buy search is online inventory) |
| `distance_miles` | `None` |

- `store_ids` is accepted and unused in Phase 1. One inline comment saying the Best Buy
  products endpoint returns online inventory only; per-store availability is not wired up.
  Do not invent a per-store query.
- `customerReviewCount` / `customerReviewAverage` are **not** returned by `search()` — the spec's
  search contract is `{name, url, price, in_stock, store_id, distance_miles}`. They are the
  field map for `get_reviews()`.

### `get_specs(product_url)`
- Needs a sku. Helper `sku_from_url(url)`: read the `skuId` query param, fall back to the digits
  before `.p` in the path. Returns `None` if neither matches.
- Live: `GET {BASE_URL}/products({sku})?show=details&apiKey=...&format=json`
- Fixture: `bestbuy_details.json`
- Parse: `payload["products"][0]["details"]` is a list of `{"name": ..., "value": ...}`.
  Return `{detail["name"]: detail["value"] for detail in details}` — raw strings, no unit parsing.
  Normalization into `{mAh: 24000}` shapes belongs to the pipeline/spec-extraction phase, not here.
- Return `{}` if sku is None, `products` is empty, or `details` is missing — the spec says an
  empty dict is the signal for the LLM fallback.

### `get_reviews(product_url)`
- Same sku extraction.
- Live: `GET {BASE_URL}/products({sku})?show=customerReviewAverage,customerReviewCount&apiKey=...&format=json`
- Fixture: `bestbuy_details.json` (same file; it carries both review fields).
- Returns `{"rating": customerReviewAverage, "review_count": customerReviewCount, "verified_ratio": None}`
  — Best Buy exposes no verified-purchase ratio. One inline comment saying so.
- Returns `{}` on missing sku or empty products.

### `find_nearby_stores(lat, lon, radius_mi)`
- Live: `GET {BASE_URL}/stores(area({lat},{lon},{radius_mi}))?apiKey=...&format=json`
- Fixture: `bestbuy_stores.json`
- Parse `payload["stores"]` -> `{"store_id": str(storeId), "name": longName or name, "distance_miles": distance}`.
  `store_id` is cast to str because the listings column is TEXT.

### Failure handling
Wrap live calls in `try/except httpx.HTTPError`, log, return `[]` / `{}`. No retries this phase.

---

## 9. `tests/fixtures/bestbuy_response.json`

Hand-written but realistically shaped: top-level `{"from", "to", "currentPage", "total",
"totalPages", "queryTime", "totalTime", "partial", "canonicalUrl", "products": [...]}`.

3-4 products. Each product carries at minimum the mapped fields plus surrounding noise so the
parser is proven to pick out the right keys:
`sku`, `name`, `salePrice`, `regularPrice`, `onSale`, `url`, `addToCartUrl`, `onlineAvailability`,
`inStoreAvailability`, `customerReviewAverage`, `customerReviewCount`, `manufacturer`, `modelNumber`,
`image`, `shortDescription`.

Use plausible real products (portable chargers / laptops), real-looking bestbuy.com URLs ending in
`/<sku>.p?skuId=<sku>` so `sku_from_url` is genuinely exercised. Include at least one product with
`onlineAvailability: false` and one with `customerReviewCount: 0` so edge cases show up in the
standalone script output.

`bestbuy_details.json`: one product, `sku`, `name`, `customerReviewAverage`, `customerReviewCount`,
and a `details` array of 8-12 `{"name", "value"}` entries with real spec names
("Battery Capacity", "Product Height", "Number of USB Ports", ...).

`bestbuy_stores.json`: `{"stores": [...]}`, 3 stores with `storeId`, `name`, `longName`, `city`,
`region`, `distance`, `lat`, `lng`.

---

## 10. `scripts/check_bestbuy.py`

Standalone, run as `python scripts/check_bestbuy.py` from repo root. Not a pytest test — the spec
asks for a print-and-eyeball script.

- `load_dotenv()`, then import the scraper.
- Prints `LIVE` or `FIXTURE` mode up front, based on whether the key is set, so the operator
  knows which path ran.
- Hardcoded inputs: query `"portable charger"`, lat/lon `37.7749 / -122.4194`, radius `25`.
- Calls in order, printing each result block: `search()` -> takes `results[0]["url"]` ->
  `get_specs(url)` -> `get_reviews(url)` -> `find_nearby_stores(...)`.
- `asyncio.run(main())`. `json.dumps(..., indent=2)` for readable output.

---

## 11. `.env.example` / `requirements.txt` / `.gitignore`

`.env.example` — every var from the spec's Environment Variables block, in that order, blank
values, plus `DATABASE_URL=sqlite:///./app.db`. Top comment: leave keys blank to run against
fixtures.

`requirements.txt` — pinned-loose (`>=`) is fine:
`fastapi`, `uvicorn[standard]`, `sqlalchemy`, `httpx`, `python-dotenv`.
Nothing else. No playwright, no anthropic, no apscheduler — later phases add their own lines.

`.gitignore` — `.env`, `app.db`, `__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `node_modules/`,
`dist/`, `.pytest_cache/`.

---

## 12. Coder verification checklist (before handing to review)

No frontend exists yet, so there is no browser test this phase. Verify with the server running.

1. `pip install -r requirements.txt` succeeds in a fresh venv.
2. `cp .env.example .env` (leave every key blank), then
   `uvicorn backend.main:app --reload --port 8000` starts with no errors.
3. `app.db` is created on first start. Inspect it:
   `python -c "import sqlite3;print(sqlite3.connect('app.db').execute(\"select name from sqlite_master where type='table'\").fetchall())"`
   -> all six tables present.
4. UNIQUE constraint exists:
   `python -c "import sqlite3;print(sqlite3.connect('app.db').execute(\"select sql from sqlite_master where name='listings'\").fetchone()[0])"`
   -> output contains `UNIQUE (item_id, retailer, store_id, url)`.
5. `curl http://localhost:8000/api/profile` -> `200`, `{"id":1,"lat":null,"lon":null,"display_address":null}`.
6. `curl -X PATCH http://localhost:8000/api/profile/location -H "Content-Type: application/json" -d "{\"lat\":37.7749,\"lon\":-122.4194,\"display_address\":\"San Francisco, CA\"}"`
   -> `200` with the new values.
7. `curl http://localhost:8000/api/profile` again -> persisted values, still `"id":1`
   (no duplicate profile rows: `select count(*) from profile` is 1).
8. Invalid latitude (`{"lat":999,...}`) -> `422`.
9. `http://localhost:8000/docs` lists exactly the two profile endpoints and nothing else.
10. `python scripts/check_bestbuy.py` with a blank `.env` -> prints `FIXTURE`, then non-empty
    output for all four methods, with every mapped field populated from the fixture (no `None`
    price, no `None` name).
11. Fixture path is cwd-independent: run `check_bestbuy.py` from inside `scripts/` too, still works.
12. Set a junk `BESTBUY_API_KEY=xxx` in `.env`, rerun the script -> prints `LIVE` and returns
    empty results from the caught HTTP error rather than crashing. Then blank it again.
13. `git status` shows no `.env`, no `app.db`, no `__pycache__` as untracked.
14. Grep the diff for emojis and for any file belonging to a later phase — both must be zero.
