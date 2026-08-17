# Deal Tracker API

Backend contract for building a frontend. FastAPI on `http://localhost:8000`, all routes prefixed `/api`.
Interactive docs at `/docs` while the server runs.

Personal single-user app: **no auth, no user ids, no tenancy**. One profile row, one watchlist.

## The two flows

**Chat** — you describe what you want, the backend extracts criteria, searches three retailers,
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
      "distance_score": 0.5,
      "nice_to_have_score": 0.5,
      "specs_inherited_from": "amazon"
    }
  ]
}
```

Up to 5 products. `product_id` is an index into this conversation's last results — it is **not** a
database id and is only valid for the next `/chat/decision` call on the same conversation.

Fields worth understanding:

- `rating_source` — `"amazon"` is first-party; anything ending `_inherited` means the rating came
  from the same product at another retailer. Worth surfacing, since it is not this listing's own rating.
- `specs_inherited_from` — retailer the specs came from, or null. Same idea.
- the five sub-scores are the ranking breakdown and always sum to `final_score` at
  `0.35 / 0.25 / 0.20 / 0.10 / 0.10`. Useful to show; not required.
- `distance_score` is currently a constant `0.5` — no retailer supplies per-product distance.

A live search takes **~30 seconds**. Show a pending state and disable the input; do not let a second
message start while one is in flight.

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

Runs the full pipeline for one item synchronously. **Slow — same ~30s as a chat search.**

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
ratings. Do not render them as a score.

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

## Status

### `GET /api/status` → `{"live_scrape": true}`

Whether searches hit real retailers or replay saved captures. Worth showing, because in fixture mode
the query is ignored entirely and results will not match what was asked for.

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
| 404 | unknown item, unknown/expired conversation, bad product_id |
| 422 | validation failure — `detail` is an array |
| 502 | model call failed upstream |

---

## Notes for building the UI

- **Never render a scraped url into an `href` unguarded.** They come from scraped pages. Allow only
  `https?://` and render anything else as plain text.
- **Searches and rescans take ~30s.** Both need a visible pending state.
- **Conversations do not survive a backend restart.** Handle the 404 with a reset affordance.
- Money is a plain float in USD. Timestamps are naive ISO strings in UTC.
- Pages the current reference frontend implements: Chat, Watchlist, Item detail (chart + listings
  table + reviews), Alerts, Settings (location).
