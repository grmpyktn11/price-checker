# Phase 3 Plan — Chat API, LLM call #1 (criteria), LLM call #5 (narration)

Scope: the spec's "Chat Endpoint Flow" section only. No frontend (Phase 4), no new scrapers
(Phase 5), no LLM calls #2/#3/#4 (Phase 6), no scheduler (Phase 7).

Phases 1 and 2 are committed: `backend/{db,models,main}.py`, `routers/profile.py`,
`scrapers/{base,bestbuy}.py`, `services/{ranking,pipeline,deals,nice_to_have}.py`, three Best Buy
fixtures, `scripts/check_{bestbuy,pipeline}.py`, `tests/test_{ranking,deals}.py`.

---

## 1. File list

| File | Purpose |
|---|---|
| `backend/services/criteria.py` | LLM call #1. Followup question or complete criteria dict. |
| `backend/services/narration.py` | LLM call #5. Ranked results -> natural-language reply. |
| `backend/routers/chat.py` | `POST /api/chat/message`, `POST /api/chat/decision`, conversation store. |
| `backend/main.py` | One line: mount the chat router. |
| `requirements.txt` | Add `anthropic` only. |
| `tests/test_criteria.py` | Canned extractor branches + criteria-contract test against `run_pipeline`. |
| `tests/test_narration.py` | Canned narrator template, empty-results case. |
| `tests/test_chat.py` | Both endpoints, every branch, via `TestClient` + in-memory DB. |

Do **not** create: `spec_extraction.py`, `sentiment.py`, `reviews_*.py`, `geocode.py`, `email.py`,
`scheduler.py`, `target.py`, `amazon.py`, `routers/{items,listings,alerts}.py`, any frontend file,
any shared `llm.py` (see section 4.4).

---

## 2. Conversation storage decision

**In-memory dict in `backend/routers/chat.py`. No new table.**

One-line justification: a conversation is scratch state that only matters while the chat window is
open — its one durable output is the `items` row written by the "watch" decision, which the spec's
schema already covers — so adding `conversations`/`messages` tables would fork the schema away from
the spec for data nothing reads twice.

Consequences, accepted:
- The backend is one process (Phase 7's APScheduler runs in it, and touches none of this dict), so
  a plain module-level dict is coherent for every request.
- A server restart (including `uvicorn --reload` on save) drops all conversations. The user retypes
  one message. Phase 4's browser client reloading is fine — the client keeps `conversation_id` and
  the server-side history survives, since only a *server* restart clears it.
- An expired/unknown `conversation_id` on `/api/chat/decision` is a 404 with a clear message, not a
  crash (section 5.2).

```python
@dataclass
class Conversation:
    history: list[dict]          # [{"role": "user"|"assistant", "content": str}], oldest first
    criteria: dict | None = None       # set once extraction completes, written to items on watch
    results: list[RankedProduct] = field(default_factory=list)   # last ranked set, indexed by product_id

CONVERSATIONS: dict[str, Conversation] = {}
MAX_CONVERSATIONS = 50   # single user; drop the oldest so a long-running process cannot grow forever
```

`get_conversation(conversation_id)` returns the existing entry or creates one, evicting the
oldest key when over `MAX_CONVERSATIONS` (dicts keep insertion order — no LRU library).

`product_id` is the **index into `conversation.results`**, and is echoed in each product object of
the `/message` response. Nothing is persisted before "watch", so there is no DB id to use.

---

## 3. `backend/services/criteria.py`

### 3.1 Constants (top of file)

```python
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1000
DEFAULT_RADIUS_MILES = 25
DEFAULT_MIN_REVIEW_COUNT = 0    # only filter on reviews when the user actually asked for it
```

### 3.2 Signatures

```python
async def extract(history: list[dict], message: str) -> dict
def normalize(raw: dict) -> dict                 # fill the three keys run_pipeline requires
def parse_json_reply(text: str) -> dict | None   # fences/prose tolerant, None on garbage
def build_messages(history: list[dict], message: str) -> list[dict]
```

### 3.3 Return contract

`extract` returns exactly one of:

```python
{"type": "followup", "question": "..."}
{"type": "criteria", "criteria": {...}}     # the section 3.5 dict
```

The router branches on `result["type"]` and nothing else.

### 3.4 Canned mode (no `ANTHROPIC_API_KEY`) — the key is the switch

Same pattern as `bestbuy.py`: one guard at the top of `extract`, no flag, no mock class, no
`if TESTING`. Adding the key to `.env` switches to live with zero code change.

```python
CANNED_QUESTION = "What is your budget, and do you need it shipped or available for pickup?"

# no key configured: ask once, then return the saved criteria. counts turns, reads nothing
if not ANTHROPIC_API_KEY:
    return {"type": "followup", "question": CANNED_QUESTION} if not history else {
        "type": "criteria", "criteria": normalize(CANNED_CRITERIA)
    }
```

Rules the coder must honour:
- The branch is decided by `len(history)` alone. **No keyword matching, no regex on the message,
  no fake NLP.** The message text is ignored, exactly as `bestbuy.py`'s fixture mode ignores its
  `query`.
- `history` is the prior turns of that conversation, not including the new message. So the first
  `/api/chat/message` of a conversation returns the followup and every later one returns criteria.
  Both branches are exercised in normal use, which is the point.
- `CANNED_CRITERIA` is a module-level constant equal to the dict in section 3.5. It is a fixture
  in Python form, not a computation.

### 3.5 The criteria dict — the contract with Phase 2

Byte-for-byte the shape `pipeline.py`'s comment block documents and `ranking.py` consumes.
`CANNED_CRITERIA`:

```python
{
    "name": "portable charger",
    "category": "electronics",
    "keywords": ["usb-c", "140w"],
    "must_haves": [
        {"field": "Battery Capacity", "op": ">=", "value": 20000},
    ],
    "preferred_specs": [
        {"field": "Number of USB Ports", "op": ">=", "value": 3},
        {"field": "Product Weight", "op": "<=", "value": 1.0},
    ],
    "nice_to_haves": ["compact", "looks sleek"],
    "budget_max": 150.0,
    "target_price": 99.0,
    "fulfillment_preference": "either",
    "radius_miles": 25,
    "min_review_count": 100,
}
```

Rule shape for both `must_haves` and `preferred_specs`, unchanged from `ranking.spec_passes`:
`{"field": <retailer spec name string>, "op": ">=" | "<=" | "==" | "contains" | "exists", "value": <number|string>}`.
`exists` rules may omit `value`.

`normalize(raw)` only guarantees what `run_pipeline` indexes directly, and passes everything else
through untouched:

```python
raw.setdefault("radius_miles", DEFAULT_RADIUS_MILES)
raw.setdefault("min_review_count", DEFAULT_MIN_REVIEW_COUNT)
# name is required; a criteria object without it is treated as malformed (section 3.7)
```

No key whitelist, no pydantic model for criteria — `pipeline.py` reads everything else with
`.get(key, default)` already, and a schema here would be a second copy of the contract to keep in
sync.

### 3.6 Live mode (key present)

```python
SYSTEM_PROMPT = """You extract shopping criteria from a conversation.

Reply with a single JSON object and nothing else. Use one of exactly two shapes.

If anything essential is missing or ambiguous - the product, the budget, a vague spec, a
model/trim that changes the price - ask one question:
{"type": "followup", "question": "..."}

Otherwise return the criteria:
{"type": "criteria", "criteria": {
  "name": "portable charger",
  "category": "electronics",
  "keywords": ["usb-c"],
  "must_haves": [{"field": "Battery Capacity", "op": ">=", "value": 20000}],
  "preferred_specs": [{"field": "Number of USB Ports", "op": ">=", "value": 3}],
  "nice_to_haves": ["compact"],
  "budget_max": 150.0,
  "target_price": 99.0,
  "fulfillment_preference": "either",
  "radius_miles": 25,
  "min_review_count": 100
}}

Rules:
- op is one of >=, <=, ==, contains, exists.
- field is the spec name as a retailer prints it on a product page, e.g. "Battery Capacity".
- value for >=, <=, == is a number in the unit the retailer prints; no unit conversion happens later.
- must_haves are hard filters, preferred_specs are soft preferences, nice_to_haves are subjective phrases.
- budget_max and target_price may be null. Ask at most one question per reply."""
```

Call:

```python
client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)   # built inside extract, only when a key exists
response = await client.messages.create(
    model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
    messages=build_messages(history, message),
)
text = response.content[0].text
```

`build_messages` = `[*history, {"role": "user", "content": message}]`. The stored history already
uses Anthropic's `{"role", "content"}` shape, so no translation layer exists.

`parse_json_reply(text)`:
1. Strip a leading ```` ```json ````/```` ``` ```` fence and trailing fence if present.
2. Slice from the first `{` to the last `}` (models sometimes wrap JSON in a sentence).
3. `json.loads`; return `None` on `ValueError`.

### 3.7 Malformed live response

`extract` post-checks the parsed object and treats all of these as malformed:
`parse_json_reply` returned `None`; `type` is not `"followup"`/`"criteria"`; type is followup with
no non-empty `question`; type is criteria with no non-empty `criteria["name"]`.

On malformed: `logger.warning("criteria extraction returned malformed json: %s", text[:500])` and
return

```python
{"type": "followup", "question": "Sorry, I did not catch that. Can you rephrase what you are looking for?"}
```

Rationale, one comment: a bad model reply is a conversation problem, not an outage — keeping the
chat alive beats a 500. **Transport/API errors are not caught here** — they propagate to the
router, which turns them into a 502 (section 5.3). The two failure modes are genuinely different
and are handled in different places.

---

## 4. `backend/services/narration.py`

### 4.1 Constants

```python
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 500
TOP_N = 5    # products narrated and returned to the client
```

### 4.2 Signatures

```python
async def narrate(criteria: dict, ranked: list[RankedProduct]) -> str
def canned_narration(criteria: dict, ranked: list[RankedProduct]) -> str
def summarize(ranked: list[RankedProduct]) -> list[dict]    # compact json the model reads
```

`narrate` receives the already-truncated top-N list; the router does the slicing so the narration
and the `products` array can never disagree.

### 4.3 Canned mode

Deterministic template over the **real** ranked results, so the endpoint output stays meaningful
with no key. Empty list:

```
No products matched your criteria for portable charger.
```

Non-empty (`\n` separated, one line per product after the header):

```
Found 4 options for portable charger. Best match: Insignia 5,000 mAh Portable Charger at $24.99 from bestbuy.
1. Insignia 5,000 mAh Portable Charger - $24.99 - bestbuy - score 0.71
2. Belkin BoostCharge 10K - $39.99 - bestbuy - score 0.66
3. mophie powerstation - $99.99 - bestbuy - score 0.58
4. Anker 737 Power Bank - $129.99 - bestbuy - score 0.51
```

Rules: score is `final_score` rounded to 2 decimals; price prints as `$0.00` formatting, or `price
unavailable` when `price is None`; name/retailer come straight from the RankedProduct. No adjectives,
no reasoning — this is a template, not a fake writer.

### 4.4 Live mode

```python
SYSTEM_PROMPT = """You are summarizing shopping search results for the person who asked.
Write 2-4 plain sentences: what the best option is and why, then anything worth flagging
(over budget, few reviews, out of stock). Reference products by name. No markdown, no lists,
no emojis. Do not invent details that are not in the JSON."""
```

User message: `json.dumps({"criteria": criteria, "results": summarize(ranked)})`.
`summarize` emits one small dict per product — `name, retailer, price, in_stock, final_score,
spec_match, review_score, price_score, rating, review_count` — not the whole `RankedProduct`
(`specs` is a large raw blob and would dominate the prompt).

Return `response.content[0].text.strip()`.

**On any exception (API error, empty content, non-text block): log and return
`canned_narration(...)`.** Narration is cosmetic; a search that found products must never 500
because the writeup failed. This is the opposite policy from `criteria.py` on purpose, and gets a
one-line comment saying so.

No shared `llm.py` this phase: the client construction is three lines duplicated across two files,
and the spec's file tree has no such module. Phase 6 adds three more call sites and is the phase
that may factor it out.

---

## 5. `backend/routers/chat.py`

`router = APIRouter(prefix="/api", tags=["chat"])`, paths `/chat/message` and `/chat/decision` —
matching `profile.py`'s existing style (prefix `/api`, full path on the decorator).

### 5.1 Pydantic models

```python
class MessageIn(BaseModel):
    conversation_id: str
    message: str

class ProductOut(BaseModel):
    product_id: int          # index into this conversation's last results, used by /chat/decision
    name: str | None
    url: str | None
    price: float | None
    in_stock: bool | None
    retailer: str
    store_id: str | None
    distance_miles: float | None
    rating: float | None         # from the highest-review-count entry in reviews
    review_count: int | None
    final_score: float
    spec_match: float
    review_score: float
    price_score: float
    distance_score: float
    nice_to_have_score: float

class MessageOut(BaseModel):
    type: str                        # followup | results
    question: str | None = None      # followup only
    narration: str | None = None     # results only
    products: list[ProductOut] | None = None   # results only

class DecisionIn(BaseModel):
    conversation_id: str
    product_id: int
    decision: Literal["buy_now", "watch"]

class DecisionOut(BaseModel):
    decision: str
    url: str | None                  # purchase link
    item_id: int | None = None       # watch only
    message: str
```

Both endpoints use `response_model_exclude_unset=True`, so a followup response carries no
`narration`/`products` keys and a results response carries no `question`. That is what produces the
exact two shapes in section 6 from one model. `exclude_none` was rejected during review: it also
strips `store_id`/`distance_miles` out of the product objects when they are null, contradicting the
section 6 example and handing Phase 4 a missing key instead of a null. `exclude_unset` drops only
keys that were never assigned, so the branch keys disappear and null product fields survive.

Serialization helpers (module level, one job each):

```python
def primary_review(ranked: RankedProduct) -> dict     # entry with the highest review_count, or {}
def to_product_out(product_id: int, ranked: RankedProduct) -> ProductOut
```

`to_product_out` reads `ranked.product` for name/url/price/in_stock/store_id/distance_miles,
`ranked.retailer`, the five sub-scores plus `final_score`, and `rating`/`review_count` from
`primary_review`. `ranked.specs` and the full `reviews` list are **not** serialized — Phase 4's
ProductCard does not display them and they are large.

### 5.2 `POST /api/chat/message`

```python
@router.post("/chat/message", response_model=MessageOut, response_model_exclude_none=True)
async def post_message(body: MessageIn, db: Session = Depends(get_db)) -> MessageOut
```

Order of operations:
1. `conversation = get_conversation(body.conversation_id)`.
2. `result = await criteria.extract(conversation.history, body.message)`.
3. Append `{"role": "user", "content": body.message}` to history (after extraction, so the new
   message is not duplicated in the prompt).
4. Followup: append `{"role": "assistant", "content": question}`, return
   `MessageOut(type="followup", question=...)`.
5. Criteria: `profile = get_or_create_profile(db)` (imported from `routers/profile.py` — it is
   already the single-row helper; do not write a second one). If `profile.lat is None or
   profile.lon is None` -> `HTTPException(400, "Set your location first: PATCH /api/profile/location")`.
6. `ranked = await run_pipeline(criteria_dict, profile.lat, profile.lon, criteria_dict["radius_miles"])`.
7. `conversation.criteria = criteria_dict`; `conversation.results = ranked[:TOP_N]`.
8. `narration = await narrate(criteria_dict, conversation.results)`.
9. Append `{"role": "assistant", "content": narration}` to history.
10. Return `MessageOut(type="results", narration=..., products=[to_product_out(i, r) for i, r in enumerate(conversation.results)])`.

Zero results is **not** an error: `type: "results"`, the "no products matched" narration, and
`products: []`.

The endpoint is `async def` because `run_pipeline` is a coroutine. `get_db` stays a sync
dependency, as in `profile.py`.

### 5.3 `POST /api/chat/decision`

```python
@router.post("/chat/decision", response_model=DecisionOut, response_model_exclude_none=True)
def post_decision(body: DecisionIn, db: Session = Depends(get_db)) -> DecisionOut
```

Lookups, in order, each a distinct 404 detail:
- `conversation_id` not in `CONVERSATIONS` -> `404 "conversation not found or expired"`.
- `conversation.results` empty -> `404 "no results in this conversation yet"`.
- `product_id` out of range -> `404 "unknown product_id"`.

**buy_now** — no DB write at all:

```python
return DecisionOut(decision="buy_now", url=chosen.product["url"],
                   message=f"Buy {chosen.product['name']} at {chosen.retailer}.")
```

**watch** — exactly three rows, one each, `db.commit()` once at the end:

1. One `Item`:
   `name` = `criteria["name"]`, `category` = `criteria.get("category")`,
   `criteria_json` = `json.dumps(criteria)`, `budget_max`/`target_price`/`fulfillment_preference`/
   `radius_miles`/`min_review_count` from criteria, `status="watching"`. `created_at` defaults.
2. One `Listing`, **for the chosen product only** — not the rest of the result set: `item_id`,
   `retailer`, `store_id`, `store_name=None` (Best Buy search returns online rows with no store),
   `distance_miles`, `url`, `price`, `in_stock`, `shipping_days_est=None`. `scraped_at` defaults.
   Watching one listing is cheaper to rescan, and finding better alternatives is the job of
   Phase 7's separate `new_alternative` scan, not of the watch action.
3. One `PriceHistory` row for that listing when `price is not None` — `listing_id`, `price`.
   Needs `db.flush()` after the listing so its id exists.

Edge case: the chosen product has `url is None` -> `HTTPException(400, "product has no url")`
before any write. The listings unique key is meaningless without a url, and a rescan cannot
re-find it.

Response:

```python
DecisionOut(decision="watch", url=chosen.product["url"], item_id=item.id,
            message=f"Watching {chosen.product['name']}.")
```

A second watch in the same conversation creates a second item — no dedupe this phase. Dedupe
belongs to Phase 8, when `routers/items.py` exists and owns item CRUD.

### 5.4 Error handling summary

| Condition | Result |
|---|---|
| Missing/invalid body fields, bad `decision` literal | FastAPI 422 |
| Profile has no lat/lon | 400, message names the endpoint to call |
| Watch on a product with no url | 400 `"product has no url"` |
| Anthropic transport/API error in `criteria.extract` | 502 `"criteria extraction failed"` |
| Malformed Claude JSON in `criteria.extract` | 200, followup asking to rephrase (section 3.7) |
| Anthropic error in `narrate` | 200, canned narration (section 4.4) |
| One retailer throws inside `run_pipeline` | already caught in Phase 2, logged, run continues |
| Unknown conversation / no results / bad product_id | 404, distinct details |

The 502 is a single `try/except anthropic.APIError` around the `criteria.extract` call in the
router, re-raised as `HTTPException(502, ...)` with the original logged. Nothing else is wrapped.

### 5.5 `backend/main.py`

Add `chat` to the existing routers import and one `app.include_router(chat.router)` line. Nothing
else changes.

---

## 6. API contract — example request/response per branch

**Followup (first message, canned mode)**

```
POST /api/chat/message
{"conversation_id": "c1", "message": "i need a portable charger"}
```
```json
{"type": "followup", "question": "What is your budget, and do you need it shipped or available for pickup?"}
```

**Results (second message, canned mode, Best Buy fixtures)**

```
POST /api/chat/message
{"conversation_id": "c1", "message": "under $150, shipped is fine"}
```
```json
{
  "type": "results",
  "narration": "Found 4 options for portable charger. Best match: Insignia ...",
  "products": [
    {
      "product_id": 0,
      "name": "Insignia - 5,000 mAh Portable Charger - Black",
      "url": "https://www.bestbuy.com/site/.../6447382.p?skuId=6447382",
      "price": 24.99,
      "in_stock": true,
      "retailer": "bestbuy",
      "store_id": null,
      "distance_miles": null,
      "rating": 4.7,
      "review_count": 1843,
      "final_score": 0.714,
      "spec_match": 0.5,
      "review_score": 0.94,
      "price_score": 1.0,
      "distance_score": 0.5,
      "nice_to_have_score": 0.5
    }
  ]
}
```

**No matches**

```json
{"type": "results", "narration": "No products matched your criteria for portable charger.", "products": []}
```

**buy_now**

```
POST /api/chat/decision
{"conversation_id": "c1", "product_id": 0, "decision": "buy_now"}
```
```json
{"decision": "buy_now",
 "url": "https://www.bestbuy.com/site/.../6447382.p?skuId=6447382",
 "message": "Buy Insignia - 5,000 mAh Portable Charger - Black at bestbuy."}
```

**watch** (one item, one listing, one price_history row — the picked product only)

```
POST /api/chat/decision
{"conversation_id": "c1", "product_id": 0, "decision": "watch"}
```
```json
{"decision": "watch",
 "url": "https://www.bestbuy.com/site/.../6447382.p?skuId=6447382",
 "item_id": 1,
 "message": "Watching Insignia - 5,000 mAh Portable Charger - Black."}
```

**Errors**

```json
{"detail": "conversation not found or expired"}
{"detail": "Set your location first: PATCH /api/profile/location"}
```

---

## 7. Tests

All run with no API keys set. No network.

`tests/test_criteria.py`
- `extract([], "anything")` -> `{"type": "followup", ...}` with a non-empty question.
- `extract([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], "anything")`
  -> `type == "criteria"`.
- The returned criteria has `name`, `radius_miles`, `min_review_count`, and every `must_haves` /
  `preferred_specs` entry has `field` and `op`.
- **Contract test**: `run_pipeline(returned_criteria, 37.7749, -122.4194, criteria["radius_miles"])`
  in fixture mode returns a non-empty list of `RankedProduct`. This is the assertion that catches a
  drift between `criteria.py` and `pipeline.py`.
- `normalize({"name": "x"})` fills `radius_miles=25`, `min_review_count=0`.
- `parse_json_reply` on a fenced block, on JSON wrapped in prose, on garbage (-> `None`).

`tests/test_narration.py`
- Canned narration on a hand-built two-product list: exact expected string.
- Empty list -> the "No products matched" string containing the criteria name.
- A product with `price=None` renders `price unavailable` and does not raise.

`tests/test_chat.py` — `TestClient(app)` with `app.dependency_overrides[get_db]` pointed at an
in-memory SQLite session (same setup as `tests/test_deals.py`), and a profile row seeded with
lat/lon.
- First POST -> `type == "followup"`, no `products` key in the JSON.
- Second POST -> `type == "results"`, `len(products) >= 1`, `products[0]["product_id"] == 0`,
  every score field present and a float.
- Profile without lat/lon -> second POST returns 400.
- `decision buy_now` -> 200, url matches `products[0].url`, `select count(*) from items` is 0.
- `decision watch` -> 200, `item_id` set; **items == 1, listings == 1, price_history == 1**,
  regardless of how many products the search returned. The single listing's `url` equals the
  chosen product's url. `items.criteria_json` round-trips through `json.loads` with
  `name == "portable charger"`.
- `decision watch` on `product_id: 1` of the same conversation -> a second item, and still exactly
  one listing per item (no dedupe this phase).
- Unknown conversation_id -> 404. Out-of-range product_id -> 404. Decision before any results -> 404.
- `decision: "maybe"` -> 422.
- Clear `CONVERSATIONS` between tests (a fixture with `CONVERSATIONS.clear()`).

---

## 8. Coder verification checklist

Run from the repo root with a blank `.env` (no `ANTHROPIC_API_KEY`, no `BESTBUY_API_KEY`).
No Playwright, no browser — no frontend exists yet.

1. `pip install -r requirements.txt` — succeeds, `anthropic` installed.
2. `python -m pytest tests -q` — all tests pass, including the Phase 2 files. No network.
3. `uvicorn backend.main:app --reload --port 8000` starts clean with no key set.
4. **Set the location before any chat step.** Every search below returns 400 until this runs —
   that is intended behaviour, not a bug:
   `curl.exe -X PATCH http://localhost:8000/api/profile/location -H "Content-Type: application/json" -d "{\"lat\":37.7749,\"lon\":-122.4194,\"display_address\":\"San Francisco, CA\"}"`
   -> 200.
5. `http://localhost:8000/docs` lists exactly four endpoints: the two profile ones plus
   `/api/chat/message` and `/api/chat/decision`.
6. (Requires step 4.) `curl.exe -X POST http://localhost:8000/api/chat/message -H "Content-Type: application/json" -d "{\"conversation_id\":\"c1\",\"message\":\"i need a portable charger\"}"`
   -> `{"type":"followup","question":"..."}` and **no** `products`/`narration` keys.
7. (Requires step 4.) Same command again with any message -> `type":"results"`, a narration string,
   and 4 products with `product_id` 0-3, in `final_score` descending order.
8. Fresh `conversation_id` `c2` -> followup again (per-conversation turn counting, not global).
9. `curl.exe -X POST http://localhost:8000/api/chat/decision -H "Content-Type: application/json" -d "{\"conversation_id\":\"c1\",\"product_id\":0,\"decision\":\"buy_now\"}"`
   -> 200 with a bestbuy.com url. Then
   `python -c "import sqlite3;print(sqlite3.connect('app.db').execute('select count(*) from items').fetchone())"`
   -> `(0,)`. **buy_now writes nothing.**
10. Same with `"decision":"watch"` -> 200 with `item_id`. Then items == 1, **listings == 1**,
    **price_history == 1** — one row each, even though the search returned 4 products:
    `python -c "import sqlite3;c=sqlite3.connect('app.db');print([c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('items','listings','price_history')])"`
    -> `[1, 1, 1]`.
11. The stored listing is the picked product:
    `python -c "import sqlite3;print(sqlite3.connect('app.db').execute('select url, price from listings').fetchall())"`
    -> one row whose url matches `products[0].url` from step 7.
12. `python -c "import sqlite3,json;print(json.loads(sqlite3.connect('app.db').execute('select criteria_json from items').fetchone()[0])['must_haves'])"`
    -> the `{field, op, value}` rule list, proving the stored criteria is the Phase 2 shape.
13. Watch a second product from the same conversation (`"product_id":1`) -> 200, and counts become
    `[2, 2, 2]`. Two items, one listing each. No dedupe this phase (Phase 8 owns that).
14. Decision with `conversation_id":"nope"` -> 404 `"conversation not found or expired"`.
    `product_id: 99` on `c1` -> 404. Decision on `c2` (followup only) -> 404.
15. `"decision":"buy"` -> 422.
16. Restart uvicorn, repeat step 9 -> 404 (in-memory store cleared, as documented in section 2).
    This is expected behaviour, not a bug.
17. Delete the profile lat/lon
    (`python -c "import sqlite3;c=sqlite3.connect('app.db');c.execute('update profile set lat=null,lon=null');c.commit()"`),
    then drive a conversation to the criteria turn -> 400 naming
    `PATCH /api/profile/location`. Restore the location by re-running step 4 afterwards.
18. `grep -rn "ANTHROPIC_API_KEY" backend/` -> exactly two matches, the module-level reads in
    `criteria.py` and `narration.py`. No third read, no config module.
19. `grep -rn "if not ANTHROPIC_API_KEY" backend/` -> exactly two matches, one per file, each the
    first statement of the public function. No mock class, no `TESTING` flag.
20. Put a junk `ANTHROPIC_API_KEY=xxx` in `.env`, restart, send a message -> the request reaches
    the live path and returns **502** for `/chat/message` (auth error from the SDK), not a 500 or a
    silent canned reply. Blank the key again and confirm canned mode returns.
21. `grep -rn "keyword\|lower()" backend/services/criteria.py` -> no matches inside the canned
    branch. The canned extractor must not inspect the message text.
22. `grep -n "conversation.results" backend/routers/chat.py` -> the watch path must not iterate it.
    A `for` loop over the result set inside the decision endpoint is a bug.
23. `git status` — new files exactly match section 1. No `scheduler.py`, no `target.py`,
    no `amazon.py`, no `spec_extraction.py`, no `sentiment.py`, no frontend directory.
24. Grep the diff for emojis -> zero.
