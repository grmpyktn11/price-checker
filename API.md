# Shopper API

Backend contract for building a frontend. FastAPI on `http://localhost:8000`, all routes prefixed `/api`.
Interactive docs at `/docs` while the server runs.

Personal single-user app: **no auth, no user ids, no tenancy**. One profile row, one watchlist.

## The two flows

**Chat** — you describe what you want, the backend extracts criteria, searches four retailers,
ranks the results, and narrates them. You then buy now or watch.

**Watchlist** — watched items are re-scanned on a schedule. Price changes are recorded, deals raise
alerts, alerts go out by email.

---

## Chat

### `POST /api/chat/message`

```json
{ "conversation_id": "any-uuid-you-generate", "message": "portable charger under $150" }
```

`conversation_id` is generated client-side and held in memory on the server (max 50 conversations,
oldest evicted, **cleared on restart**). There is no conversation list endpoint and no history to
fetch — the client owns the visible transcript.

Two response shapes, discriminated on `type`. Keys of the other branch are **absent**, not null.

Follow-up — the backend needs more information:

```json
{ "type": "followup", "question": "What is your budget?" }
```

Results:

```json
{
  "type": "results",
  "narration": "The best option that meets your 20,000 mAh requirement is...",
  "retailers_answered": true,
  "debug": { "trace_id": "d14ce1be", "...": "see Debug trace below" },
  "products": [
    {
      "product_id": 0,
      "name": "Anker 737 Power Bank 24,000 mAh",
      "url": "https://www.bestbuy.com/...",
      "price": 129.99,
      "in_stock": true,
      "retailer": "bestbuy",
      "store_id": null,
      "distance_miles": null,
      "rating": 4.7,
      "review_count": 1843,
      "rating_source": "amazon_inherited",
      "final_score": 0.71,
      "spec_match": 1.0,
      "review_score": 0.94,
      "price_score": 1.0,
      "distance_score": 0.98,
      "nice_to_have_score": 0.5,
      "specs_inherited_from": "amazon",
      "video_url": "https://www.youtube.com/watch?v=...",
      "variants": [
        { "name": "Womier Q61 PRO - White", "url": "https://...", "price": 60.09,
          "retailer": "bestbuy" }
      ],
      "sources": [
        { "source": "bestbuy", "url": null, "rating": 4.4, "review_count": 965,
          "mention_count": null, "summary": null },
        { "source": "reddit", "url": "https://reddit.com/...", "rating": null,
          "review_count": null, "mention_count": 8, "summary": "held up for two years..." }
      ],
      "sentiment": "positive"
    }
  ]
}
```

Up to 5 products. `product_id` is an index into this conversation's last results — it is **not** a
database id and is only valid for the next `/chat/decision` call on the same conversation.

`retailers_answered` is the one you must not ignore. `products: []` means two completely different
things:

- `retailers_answered: true` — the retailers answered and nothing matched. "No products" is a real
  answer about the market.
- `retailers_answered: false` — **no retailer answered at all**: blocked, unparseable or failed.
  Nothing was learned. Do not tell the user nothing matched; the backend's own `narration` says the
  search did not run, and `debug.retailers` says which failed and how.

`debug` is the full trace of the run (below). It can be tens of kilobytes.

Fields worth understanding:

- `rating_source` — `"amazon"` is first-party; anything ending `_inherited` means the rating came
  from the same product at another retailer. Worth surfacing, since it is not this listing's own rating.
- `specs_inherited_from` — retailer the specs came from, or null. Same idea.
- `video_url` — a YouTube review for this product, or null. Only the products the YouTube stage
  actually reached have one, and that stage only runs when the ranking is too close to call, so
  **null is the normal case**. Hide the control rather than showing a dead one. Treat it like any
  other scraped url: it is model-supplied, so scheme-check it before putting it in an `href`.
- `variants` — other listings of the **same product** folded into this one: another colour, or
  the same model at another retailer. Only the best-scoring listing of a product is returned, so
  a shopper is never offered the pink and the white version as two separate recommendations.
  Usually empty. Show them as alternatives on the winner's card, not as their own results.
- `sources` — one row per source that said something about this product: the retailer's star
  rating, and the Reddit/YouTube discussion for the few products research reached. `summary` is
  the actual evidence the ranking used, so surfacing it is how a shopper checks the reasoning
  instead of trusting the score. A `source` ending `_inherited` was attributed from another
  listing. Trimmed to 600 characters; `url` links to the full thing.
- `sentiment` — the model's read of that discussion, or null when the product was not researched.
- the five sub-scores are the ranking breakdown and always sum to `final_score` at
  `0.35 / 0.25 / 0.20 / 0.10 / 0.10`. Useful to show; not required.
- `distance_score` is per-**retailer**, not per-product: it scores how far the nearest Target or
  Best Buy is from the profile location (Google Places), falling linearly from 1.0 at the door to
  0.0 at `radius_miles`. Amazon, a retailer with no store nearby, and a failed Places lookup all
  score a neutral `0.5`. No retailer publishes per-product store stock.

The top 5 ranked products are then researched individually — one Reddit search each — and one
model call reads that discussion per product. If it reports the top of the ranking too close to
call, YouTube is searched for the top 2 and the call is repeated; a decisive search spends zero
YouTube quota. `narration` reflects the post-research order.

A live search takes **30-60 seconds, and over 100 when a retailer is slow**. Show a pending state
and disable the input; do not let a second message start while one is in flight. Poll
`/api/chat/progress/{conversation_id}` (below) to show what it is actually doing rather than a
bare spinner.

**Errors:** `400` location not set (see profile), `404` unknown conversation, `502` model call failed.

### `POST /api/chat/decision`

```json
{ "conversation_id": "same-id", "product_id": 0, "decision": "buy_now" }
```

`decision` is `"buy_now"` or `"watch"` — any other value is a `422`.

```json
{ "decision": "buy_now", "url": "https://...", "message": "Buy ... at bestbuy." }
{ "decision": "watch", "url": "https://...", "item_id": 3, "message": "Watching ..." }
```

`buy_now` writes nothing. `watch` creates one item, one listing and one price-history row for the
chosen product only. `item_id` is absent on `buy_now`.

**Errors:** `404` unknown conversation / no results yet / bad `product_id`, `400` product has no url.

`404 "conversation not found or expired"` happens routinely — the backend restarted. Offer a reset
that clears the transcript and generates a new `conversation_id`.

---

## Projects

Import a Claude planning conversation, pull the shopping list out of it, and search for the
items you tick.

**There is no Claude API for reading a user's conversations** — no conversations endpoint, no
consumer OAuth. The transcript arrives one of two ways, both user-driven: pasted text, or a
claude.ai **share link**, which is a public snapshot the user creates and can revoke.

### `POST /api/projects/import`

```json
{ "text": "the whole conversation" }
{ "share_url": "https://claude.ai/share/..." }
```

Send one or the other. **Paste is the path that works.** A share URL is fetched with a rendering
browser and **the host is checked against claude.ai before anything is fetched** — the URL is
user-supplied and reaches `page.goto()` — but claude.ai is behind Cloudflare, which serves an
interstitial instead of the page. That is detected and reported as a block. One model call extracts the list; it is told to keep only things a shop sells, so
"time", "patience", software and services never become rows, and neither does anything the
conversation decided against or the user already owns.

`201` with the project. Items the model marked `essential` start `selected: true`.

**Errors:** `400` neither field sent, or not a claude.ai host. `422` the conversation had nothing
buyable in it. `502` the share link would not load — a bot check, a still-private chat, or a page
too short to be a transcript, each with its own message. A `502` never means "nothing to buy":
that distinction matters, because telling someone their conversation was empty when it was never
read sends them to fix the wrong thing.

### `POST /api/projects/{id}/search`

```json
{ "item_ids": [3, 4] }
```

`202` with `{"searching": [...], "skipped": [...]}`. Returns immediately — the run takes minutes.
**Max 5 items per run**, sequentially, and project searches skip the Reddit/YouTube research
stage. Both limits exist for the same reason: N concurrent or fully-researched pipelines multiply
the request rate against retailers that already rate-limit us. Ranking still uses retailer star
ratings, which all four print on their search page.

**Errors:** `400` no location set or nothing ticked, `404` unknown project or items, `409`
already running.

### `GET /api/projects/{id}/progress`

```json
{ "running": true, "status": "running", "current_index": 1,
  "items": [{"id": 3, "name": "8-port gigabit switch", "state": "done", "products_found": 3},
            {"id": 4, "name": "Cat6 patch cables", "state": "searching", "products_found": 0}],
  "current_search": { "stage": "collect_candidates", "...": "the live trace of this item" } }
```

`{"running": false}` is the stop-polling signal. `state` is `pending | searching | done | failed`.

This is **not** `trace._live`: that entry is popped when a run finishes, so a multi-item project
would report `running: false` in the gap between items and the client would stop polling. This
store lives for the whole project run, and embeds the per-item trace as `current_search`.

### `GET /api/projects/{id}` / `GET /api/projects` / `DELETE /api/projects/{id}`

The detail response carries `items` plus `results`, keyed by item id, holding `ProductOut`-shaped
dicts — so the page survives a reload. `run_pipeline` persists nothing on its own.

### `POST /api/projects/{id}/items/{item_id}/track`

```json
{ "product_id": 0 }
```

Creates an ordinary watchlist item, so the scheduler rescans and alerts on it like any other.
`/chat/decision` cannot serve this: it looks its product up in a conversation.

---

## Debug trace

A live search fails for several unrelated reasons at once, and `products: []` hides all of them.
Every run records a trace of what actually happened, returned inline on the results response under
`debug` and readable again afterwards from this endpoint.

### `GET /api/debug/status`

State the Debug page needs: scheduler jobs with their next run times, how many alerts are queued
for the digest, whether email is configured and where it goes, and the watched-item count.

### `POST /api/debug/jobs/{scrape|review_check|digest}`

Runs a scheduled job now. `scrape` and `review_check` re-search every watched item, so they take
minutes — they start in the background and return immediately. `digest` is fast and returns how
many alerts it emailed. `404` for an unknown name.

### `PATCH /api/profile/email`

`{"email": "you@example.com"}` — where alerts go. `""` clears it and falls back to the
`USER_EMAIL` env var. `422` if it has no `@`.

### `POST /api/debug/test-email`

Sends one message to the alert address. Proves delivery without waiting for an alert to exist.
Returns `{"sent": false, "detail": "..."}` rather than an error when the keys are missing.

**These are unauthenticated, like the rest of the API.** Fine on localhost; do not expose them
through a tunnel.

### `GET /api/debug/last` → the most recent trace

`404` with `detail: "no search has run since the backend started"` until a search has run. The last
5 traces are held **in memory only** — no table, cleared on restart. Refreshing this does not re-run
anything, so a panel can poll it freely.

The shape, with every field explained:

```json
{
  "trace_id": "d14ce1be",
  "started_at": "2026-08-17T05:01:35+00:00",
  "query": "gaming mouse wireless",
  "criteria": { "name": "gaming mouse", "min_review_count": 5, "...": "the full criteria object" },

  "retailers": [
    {
      "retailer": "bestbuy",
      "search_url": "https://www.bestbuy.com/site/searchpage.jsp?st=gaming+mouse",
      "outcome": "OK",
      "http_status": null,
      "page_chars": 1806433,
      "raw_rows": 24,
      "detail": "the search answered and product rows were parsed out of it",
      "ms": 37875,
      "candidates_kept": 3,
      "error": null
    }
  ],

  "review_lookup": { "searches": [], "tiles_kept": 3, "searches_left": 0 },
  "stores": { "source": "google places text search",
              "distance_miles": { "target": 1.07, "bestbuy": 0.57 },
              "not_found": [], "ms": 937 },
  "product_filter": { "products_in": 6, "candidates_in": 3, "review_tiles_in": 3,
                      "qualified": 3, "rejected": 0, "ms": 4985 },
  "candidates": [
    { "name": "...", "retailer": "bestbuy", "price": 27.99, "spec_fields": 0,
      "specs_inherited_from": null, "rating": 4.6, "rating_source": "amazon_inherited",
      "evidence_count": 1843, "same_product_group": "g1" }
  ],
  "research": [
    { "rank": 1, "name": "...", "retailer": "bestbuy", "reddit_posts": 10,
      "reddit_retried": false, "youtube": true, "youtube_videos": 5 }
  ],
  "youtube": { "triggered": true, "too_close_positions": [1, 2],
               "reason": "the discussion could not separate the top of the ranking" },
  "drops": [
    { "stage": "review_floor", "name": "...", "retailer": "bestbuy",
      "reason": "0 reviews or mentions found, below the 5 the criteria ask for" }
  ],
  "stages_ms": { "collect_candidates": 37875, "product_filter": 4985, "research_top": 26203 },
  "total_ms": 89265,
  "retailers_answered": true,
  "products_returned": 2
}
```

**`retailers`** — one row per retailer, in the order they were searched. `outcome` is the field that
matters, and the three failures are deliberately three values because they need three different
fixes:

| `outcome` | Means |
|---|---|
| `OK` | the search answered and rows were parsed out of it |
| `OK_BUT_EMPTY` | it answered and said it has no matching products — a real answer |
| `BLOCKED` | a bot wall answered instead: captcha, 403, or a challenge page |
| `SELECTORS_RETURNED_NOTHING` | a real page loaded and the parser found no product rows in it |
| `ERROR` | the request raised before anything could be parsed |

`detail` is a plain-English sentence for the same thing, safe to render as-is. `page_chars` is the
size of what came back (null for the JSON retailer's failures): ~2,000 is a challenge page,
~1,800,000 is a real page that failed to parse — that difference is what separates `BLOCKED` from
`SELECTORS_RETURNED_NOTHING` at a glance. `raw_rows` is what the parser found, `candidates_kept` is
what survived the per-retailer cap into the pipeline, `ms` is that retailer's whole leg. `error` is
non-null only when the scraper raised.

**`review_lookup`** — extra Amazon searches spent finding ratings for retailers that publish none.
`searches` rows have the same shape and `outcome` values as a retailer row.

**`stores`** — the Google Places lookup. `distance_miles` is per retailer, `not_found` lists
retailers that have stores but were not returned (no store nearby, or a failed lookup — both score a
neutral distance).

**`product_filter`** — the single qualification model call. `products_in` counts candidates plus
review tiles. Each rejection appears in `drops` with `stage: "product_filter"` and the model's own
stated reason.

**`candidates`** — what reached ranking. `spec_fields: 0` means no retailer published specs for it;
that is not a drop, specs are inherited within a `same_product_group`. `rating_source` ending
`_inherited` means the rating came from another listing judged to be the same product.

**`research`** — the per-product research, in ranked order. `reddit_retried: true` means the first
search came back empty (usually a 429) and was retried once. `youtube` is present per product;
`youtube.triggered` says whether any quota was spent at all, and `too_close_positions` is the
sentiment call's own verdict on which ranked positions it could not separate. Only the top 2 are
searched, so a position can be listed as too close and still show `youtube: false`.

**`drops`** — every candidate removed, with `stage` (`product_filter` or `review_floor`), the
product, the retailer, and a human-readable `reason`. This is the answer to "why is this list
short".

`review_floor` only drops a product a source actually published a count for. A blocked product
page reports no count at all, and that is unknown rather than zero — it is not a reason to drop
the product, since the alternative deletes the best matches at whichever retailer is walled off
that day. `candidates[].evidence_count` is `null` in exactly that case.

### GET /api/chat/progress/{conversation_id}

What that conversation's in-flight search is doing, for a waiting screen. Poll it about once a
second while a `/chat/message` call is outstanding.

```json
{ "running": true, "stage": "research_reddit", "elapsed_ms": 80969,
  "retailers": [ { "retailer": "bestbuy", "outcome": "OK", "candidates_kept": 3 },
                 { "retailer": "target", "outcome": "BLOCKED", "candidates_kept": 0 } ],
  "products_in": 6, "qualified": 3, "researched": 2 }
```

`{"running": false}` when nothing is in flight — that is the stop-polling signal, not an error,
and it is also what an unknown conversation id gets. This is read off the same trace the run is
already filling in, so it can never disagree with the `debug` block on the final response. The
results themselves only ever arrive on the `/chat/message` response.

**`stages_ms` / `total_ms`** — milliseconds per stage, for attributing a slow search. Stage names
are `collect_candidates`, `amazon_review_tiles`, `product_filter`, `lookup_missing_reviews`,
`research_top`, `research_reddit`, `research_youtube`. Nesting is real: `research_reddit` and
`research_youtube` are inside `research_top`.

**`retailers_answered`** — false when no row in `retailers` has an `OK` or `OK_BUT_EMPTY` outcome.
Same value as the key on the chat response.

---

## Watchlist

### `GET /api/items` → `ItemOut[]` · `GET /api/items/{id}` → `ItemOut`

```json
{
  "id": 1, "name": "portable charger", "category": "electronics",
  "criteria_json": "{\"name\": \"portable charger\", ...}",
  "budget_max": 150.0, "target_price": 99.0,
  "fulfillment_preference": "either", "radius_miles": 25,
  "min_review_count": 5, "status": "watching"
}
```

`criteria_json` is a **JSON string**, not an object — `JSON.parse` it if you need the rules.
`status` is `watching` or `archived`.

### `POST /api/items` → `ItemOut` (201)

Manual add, skipping chat.

```json
{ "name": "mechanical keyboard", "category": "electronics", "criteria": {},
  "budget_max": 200.0, "target_price": null, "fulfillment_preference": "either",
  "radius_miles": 25, "min_review_count": 5 }
```

Only `name` is required. `criteria` accepts the same rule object chat produces; a malformed rule is
rejected with `422` and a plain-English message suitable for display.

### `PATCH /api/items/{id}` → `ItemOut`

Any subset of the POST fields plus `status`. An invalid `status` is a `422`.

### `DELETE /api/items/{id}` → `{"deleted": 1}`

Returns `200` with a body, **not 204**. Removes the item's listings, price history, reviews and alerts.

### `POST /api/items/{id}/rescan`

Runs the full pipeline for one item synchronously. **Slow — same ~30-60s as a chat search.**

```json
{ "item_id": 1, "listings_seen": 8, "alerts": ["target_hit"], "emails_sent": 0 }
```

---

## Item detail

### `GET /api/items/{id}/listings` → `ListingOut[]`, cheapest first

```json
{ "id": 2, "item_id": 1, "retailer": "amazon", "store_id": null, "store_name": null,
  "distance_miles": null, "url": "https://...", "price": 104.5, "in_stock": true,
  "shipping_days_est": 2, "scraped_at": "2026-08-16T21:30:00" }
```

`store_id`/`store_name`/`distance_miles` are null for online listings, which is currently all of them.

### `GET /api/items/{id}/price-history` → `PricePointOut[]`

```json
{ "id": 5, "listing_id": 2, "price": 104.5, "recorded_at": "2026-08-16T21:30:00" }
```

Group by `listing_id` for a multi-series chart. A point is only written when the price **changed**,
so series are sparse and unevenly spaced — plot against time, not index.

### `GET /api/items/{id}/reviews` → `ReviewOut[]`

```json
{ "id": 1, "source": "amazon", "rating": 4.2, "review_count": 226,
  "verified_ratio": null, "rating_distribution_json": "{\"5\": 0.71, \"4\": 0.09}",
  "authenticity_flag": "ok", "url": "https://...",
  "summary_text": "...", "fetched_at": "2026-08-16T21:30:00" }
```

`source` is `amazon` | `target` | `bestbuy` | `reddit` | `youtube`, and any of the retailer values may
carry an `_inherited` suffix meaning the rating was attributed from another listing judged to be
the same product. Surface that — it is not this retailer's own rating.

`authenticity_flag` is `ok` | `mixed_signal` | `skewed_distribution`. `mixed_signal` means outside
discussion contradicts the star rating. `rating_distribution_json` is a JSON string of star → share.

Reddit and YouTube rows have `rating: null` and `review_count: null` — they are discussion, not
ratings. Do not render them as a score. They are the winning product's own research, not
item-level chatter, and a YouTube row is only present when the search was a close call.

**All three 404 on an unknown item id.**

---

## Alerts

### `GET /api/alerts` → `AlertOut[]`, newest first, capped at 200

```json
{ "id": 3, "item_id": 1, "item_name": "portable charger", "listing_id": 2,
  "retailer": "bestbuy", "url": "https://...", "price": 89.99,
  "reason": "price_drop", "sent_at": null }
```

`reason` is `target_hit` | `price_drop` | `new_alternative`. `sent_at` null means it has not been
emailed yet — target-price hits send immediately, everything else batches into a daily digest.

---

## Profile

### `GET /api/profile` → `ProfileOut`

```json
{ "id": 1, "lat": 37.7749, "lon": -122.4194, "display_address": "San Francisco, CA" }
```

Always returns `200` and creates a blank row on first call. `lat`/`lon` null means no location set.

### `PATCH /api/profile/location` → `ProfileOut`

```json
{ "lat": 37.7749, "lon": -122.4194, "display_address": "San Francisco, CA" }
```

All three required. Out-of-range lat/lon is a `422`.

**Chat search 400s until a location is set**, so a location form is not optional — it gates the
main flow. Address autocomplete uses Google Places (New) with a browser-exposed key; a coordinates-only
fallback is acceptable when the key is missing.

---

## Errors

Standard FastAPI. **`detail` is a string on our raised errors but an ARRAY OF OBJECTS on `422`
validation failures** — rendering it directly into JSX crashes React. Join the `msg` fields:

```ts
const detail = body.detail;
const message = Array.isArray(detail)
  ? detail.map((d) => d.msg).join("; ")
  : typeof detail === "string" ? detail : `HTTP ${status}`;
```

| Code | Means |
|---|---|
| 400 | location not set, or product has no url |
| 404 | unknown item, unknown/expired conversation, bad product_id, no trace recorded yet |
| 422 | validation failure — `detail` is an array |
| 502 | model call failed upstream |

---

## Notes for building the UI

- **Never render a scraped url into an `href` unguarded.** They come from scraped pages. Allow only
  `https?://` and render anything else as plain text.
- **Searches and rescans take ~30-60s.** Both need a visible pending state.
- **Conversations do not survive a backend restart.** Handle the 404 with a reset affordance.
- Money is a plain float in USD. Timestamps are naive ISO strings in UTC.
- Pages the current reference frontend implements: Chat, Watchlist, Item detail (chart + listings
  table + reviews), Alerts, Settings (location).
