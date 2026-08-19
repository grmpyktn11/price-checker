# Inside a Shopper search

One sentence in, five ranked products out, about 80 seconds later. Every step, every payload,
every row written.

## The pieces

Python backend (FastAPI, port 8000) does the work. React frontend (Vite, port 5173) is the UI.
The frontend never calls a retailer or Claude directly, only the backend. Vite proxies `/api` to
port 8000 in dev, so both are the same origin and no CORS is needed.

Storage is one SQLite file, `app.db`, resolved from the repo root rather than the working
directory. Nine tables, each shown below where it is written. No migrations: `create_all()` runs
at import, so new tables appear on restart but new columns do not. `add_missing_columns()` handles
that case with an additive `ALTER TABLE`.

## 1. The request

`POST /api/chat/message`

```json
{
  "conversation_id": "8f2a-...",
  "message": "i want a wireless mouse under 40 dollars"
}
```

`conversation_id` is generated in the browser. History is stored as JSON, not a row per turn,
because the app only ever reads or writes a whole conversation.

```
conversations
  id            VARCHAR  PK    client-generated
  history_json  VARCHAR        [{"role": "user", "content": "..."}] oldest first
  criteria_json VARCHAR        set once extraction succeeds
  results_json  VARCHAR        what /chat/decision needs, indexed by product_id
  created_at    DATETIME
  updated_at    DATETIME
```

Timestamps are naive UTC. SQLite stores no offset and reads back naive.

## 2. Sentence to criteria

Model call 1. Two possible reply shapes.

Needs more information:

```json
{"type": "followup", "question": "What is your budget?"}
```

The turn ends there. The question is appended to history and returned. No search runs.

Otherwise:

```json
{"type": "criteria", "criteria": {
  "name": "wireless mouse",
  "category": "electronics",
  "keywords": ["wireless"],
  "must_haves": [{"field": "Battery Capacity", "op": ">=", "value": 20000}],
  "preferred_specs": [],
  "nice_to_haves": ["compact"],
  "budget_max": 40.0,
  "target_price": null,
  "fulfillment_preference": "either",
  "radius_miles": 25,
  "min_review_count": 5
}}
```

`op` is one of `>= <= == contains exists`. `field` is the spec name as a retailer prints it, with
no unit conversion downstream.

`normalize()` then replaces explicit nulls, not just missing keys. The model emits `null`, and a
`None` reaching a comparison raised a `TypeError` that got swallowed and looked like a retailer
outage.

## 3. Location gate

```
profile
  id               INTEGER PK    always 1, single user
  lat              FLOAT
  lon              FLOAT
  display_address  VARCHAR
  email            VARCHAR       alerts go here, USER_EMAIL is the fallback
```

No lat/lon returns 400, and the turn just appended is not saved. Leaving a user message with no
assistant reply would put two user turns in a row in the next prompt.

## 4. Nearest stores

One Google Places `searchText` call per retailer that has stores, cached on rounded coordinates.

```json
{"target": 1.07, "bestbuy": 0.57, "microcenter": 12.4}
```

Score is `max(0, 1 - miles / radius_miles)`. Amazon, a retailer with nothing nearby, and a failed
lookup all get a neutral `0.5`.

This is per retailer, not per product. Shopper knows a Best Buy is 0.6 miles away. It cannot know
whether that store stocks the item, because no retailer publishes that.

## 5. Four retailers, concurrently

`asyncio.gather` over all four. The stage costs the slowest, not the sum. Sequentially this was
63s of a 115s search.

| retailer | transport | measured | state |
| --- | --- | --- | --- |
| Target | httpx to redsky JSON | 3.7s | No browser. 403s for hours, then relents. |
| Micro Center | Playwright, server-rendered | 23.0s | No bot wall. Publishes Mfr Part#. |
| Best Buy | Playwright, JS-hydrated | 24.4s | Search fine. Product pages Akamai-blocked. |
| Amazon | Playwright, JS-hydrated | 26.0s | Works, throttles after a burst. |

Every scraper returns the same shape:

```json
{
  "name": "Logitech M317 Mouse - Blue Aurora",
  "url": "https://www.target.com/p/-/A-79370051",
  "price": 11.99,
  "in_stock": true,
  "store_id": null,
  "distance_miles": null,
  "rating": 4.38,
  "review_count": 392
}
```

The rating comes off the search tile. It used to be read from the product page, which is exactly
the page Best Buy blocks and Amazon throttles first, so most cards said "no rating found". All
four retailers print it on the search results.

Top 3 per retailer survive. Each gets its product page opened for specs, returned as a flat dict:

```json
{"Type": "Mechanical", "Mfr Part#": "WOMIER SK80 BLA"}
```

If the page loads but has no spec table, the raw page text goes to Claude with the wanted field
names. Capped at 3 per search, as a shared budget object rather than an int, or each concurrent
retailer would get the full allowance.

Every search records an outcome: `OK`, `OK_BUT_EMPTY` (answered, genuinely nothing), `BLOCKED`
(captcha or 403), `SELECTORS_RETURNED_NOTHING` (real page, parser found nothing, our bug), or
`ERROR`. They are never collapsed into "failed" because they need different fixes, and only the
first two mean the retailer actually answered.

## 6. Qualification, fit and identity

Up to 12 products. One model call judges all of them.

Sent:

```json
{
  "requirements": {
    "product": "wireless mouse",
    "keywords": ["wireless", "rgb"],
    "required_specs": [], "preferred_specs": [], "nice_to_haves": ["compact"]
  },
  "products": [
    {"index": 0, "retailer": "bestbuy", "title": "CORSAIR M75 ...",
     "price": 52.99, "url": "https://...", "specs": {}}
  ]
}
```

Returned, index-aligned:

```json
{"products": [
  {"index": 0, "qualifies": true, "spec_fit": 1.0, "nice_fit": 0.5,
   "group": "g1", "reason": ""}
]}
```

A malformed or partial reply leaves unjudged products at a neutral default rather than dropping
them. Only an explicit `false` disqualifies.

What qualifies means:

* A stated quantity is strict. 2,000mAh fails a 20,000mAh request, and so does 10,000.
* A stated feature is strict when the product plainly lacks it. A $9 own-brand mouse has no RGB
  whether or not the title says so.
* Unclear is not absent. A gaming mouse whose title omits RGB is kept, and the doubt goes into
  `spec_fit`.
* Vague wording like "compact" never disqualifies.

`group` does two jobs. Inheritance: a candidate with no first-party rating takes the best-supported
rating in its group, tagged `amazon_inherited` and shown as borrowed, with a 0.9 penalty because
the identity came from a model reading two titles. Deduplication: only the best-scoring listing in
a group is returned, the rest ride along as `variants`. Ungrouped candidates are never collapsed,
because no group means the model said nothing about identity.

## 7. Scoring

| weight | component |
| --- | --- |
| 35% | spec_match |
| 25% | review_score |
| 20% | price_score |
| 10% | distance_score |
| 10% | nice_to_have_score |

`price_score` is relative to this search. Cheapest 1.0, dearest 0.0, unless the whole set is
within 5% of the cheapest, in which case everything scores 1.0. Without that rule, $60.04 and
$60.09 scored 1.0 and 0.0. Over budget multiplies by `budget_max / price`, a penalty rather than
a filter.

`review_score` is `rating / 5` scaled by confidence, where confidence is
`log10(1 + count) / log10(1001)`. 1,000 reviews is full trust. No rating anywhere gives a neutral
0.5, because a missing feed is not a bad product.

## 8. Per-product research

The top 5 each get their own Reddit search on their own name, not one shared search for the
category.

```
GET https://www.reddit.com/r/{subreddits}/search.rss
    ?q={product name}&restrict_sr=1&sort=relevance&t=year
```

Up to 4 subreddits joined into one multireddit path, so a category costs one request. 10 posts per
query, each body clipped to 600 characters. No key needed, it is the public RSS feed.

Sequential, 2s apart, with one retry after 4s. Reddit 429s most requests from this host. This is
the largest remaining block of the wait.

A result appends one row:

```json
{"source": "reddit", "rating": null, "review_count": null,
 "mention_count": 8,
 "url": "https://reddit.com/...",
 "summary_text": "[r/MouseReview] ...",
 "authenticity_flag": "ok"}
```

`rating` and `review_count` are deliberately null. A count of threads is not a count of reviews,
and the review floor must not see it as one.

## 9. Reading the discussion, and the tie-break

Sent, one entry per researched product:

```json
[{"name": "CORSAIR M75 ...", "rating": 4.4, "discussion": "[reddit] ... [youtube] ..."}]
```

Returned:

```json
{"products": [
   {"index": 0, "sentiment": "positive", "confidence": 0.8, "summary": "one sentence"}
 ],
 "too_close": [0, 1]}
```

`too_close` empty means done, and zero YouTube quota spent. Non-empty means the top 2 get a
YouTube search (100 of about 10,000 daily units each) and the same call runs again with the extra
evidence. That is why the Video button appears on some cards and not others.

The sentiment also cross-checks the stars. Negative talk against a rating of 4.3 or higher, or
positive talk against 3.5 or lower, flags the row `mixed_signal` and cuts review_score by 15%. A
distribution over 80% five-star with under 10% in the 2 to 4 band flags `skewed_distribution` and
cuts 25%.

## 10. Re-rank, collapse, filter

Scores recompute, the list re-sorts, groups collapse to one listing each, then the review floor
applies.

`evidence_count` returns `None` when no source reported anything, and the floor skips those.
Reading a blocked page's silence as "0 reviews" deleted the only RGB mice in an RGB search.

## 11. Narration

Model call. Top 5 with scores and evidence in, 2 to 4 plain sentences out.

If no retailer answered, the model is skipped and a written-in-code sentence says the search did
not run. "Nothing matched" is a claim about the market, and nothing was learned about the market.

## 12. The response

```json
{
  "type": "results",
  "narration": "The Logitech M317 at $11.99 is ...",
  "retailers_answered": true,
  "products": [{
    "product_id": 0,
    "name": "Logitech M317 Mouse - Blue Aurora",
    "url": "https://...", "price": 11.99, "in_stock": true,
    "retailer": "target", "store_id": null, "distance_miles": null,
    "rating": 4.38, "review_count": 392,
    "rating_source": "target",
    "final_score": 0.90,
    "spec_match": 1.0, "review_score": 0.83, "price_score": 0.94,
    "distance_score": 0.96, "nice_to_have_score": 0.0,
    "specs_inherited_from": null,
    "video_url": null,
    "variants": [],
    "sources": [{"source": "reddit", "url": "...", "rating": null,
                 "review_count": null, "mention_count": 8, "summary": "..."}],
    "sentiment": "positive"
  }],
  "debug": {}
}
```

`retailers_answered: false` means an empty product list is a failure, not an answer.

While all of that runs, the browser polls `GET /api/chat/progress/{conversation_id}` about once a
second:

```json
{"running": true, "stage": "collect_candidates", "elapsed_ms": 15230,
 "retailers": [{"retailer": "bestbuy", "outcome": "SEARCHING", "candidates_kept": null},
               {"retailer": "target", "outcome": "OK", "candidates_kept": 3}]}
```

`{"running": false}` is the stop signal. All four retailers appear immediately as `SEARCHING`,
because with them concurrent nothing reports for 25 seconds. It reads the same trace object the
run is filling in, so the waiting screen cannot disagree with the debug panel.

## 13. Track

`POST /api/chat/decision` with `{"product_id": 0, "decision": "watch"}`. Four tables in one
transaction. This is the only path that persists a product.

```
items      id PK, name, category, criteria_json, budget_max, target_price,
           fulfillment_preference, radius_miles, min_review_count,
           status (watching|archived), created_at

listings   id PK, item_id FK, retailer, store_id, store_name, distance_miles,
           url, price, in_stock, shipping_days_est, scraped_at
           UNIQUE(item_id, retailer, store_id, url)

price_history  id PK, listing_id FK, price, recorded_at

reviews    id PK, item_id FK, source, rating, review_count, verified_ratio,
           rating_distribution_json, authenticity_flag, url, summary_text, fetched_at
```

A product with no URL is refused. The listings unique key is meaningless without one and a rescan
could never re-find it.

## 14. The background jobs

APScheduler, in-process, started with the server.

| job | schedule | does |
| --- | --- | --- |
| scrape | every 6h | Re-run the pipeline per watched item, upsert listings, append price history on a change. |
| review_check | cron 03:00 | Find alternatives cheaper than anything already stored. Max 3 alerts per item per run. |
| digest | cron 08:00 | Email every alert with `sent_at IS NULL`, then stamp them. |

```
alerts
  id PK, item_id FK, listing_id FK,
  reason (price_drop|target_hit|new_alternative),
  sent_at DATETIME    NULL means queued for the digest
```

Only `target_hit` emails immediately. The cheaper-than-stored rule and the cap exist because a
rescan across four retailers finds about 20 URLs it has not seen before, and alerting on each sent
21 notices about one USB hub in a single email.

## Projects

`POST /api/projects/import` with pasted conversation text. One model call extracts a shopping list.
Each ticked item runs the same pipeline sequentially, 5 per run, with `research_top_n=0` so no
Reddit or YouTube quota is spent.

```
projects       id PK, name, source (paste|share_link), source_url, created_at, updated_at

project_items  id PK, project_id FK, name, why, criteria_json, quantity,
               essential, selected, status (pending|searching|done|failed),
               results_json, error, searched_at
```

`results_json` holds ProductOut-shaped dicts. `run_pipeline` persists nothing itself, so without
it the page would be empty after a reload.

## Where the 80 seconds goes

Measured on a real run.

| stage | time | kind |
| --- | --- | --- |
| criteria | 2.0s | model call |
| four retailers | 27.2s | network |
| product_filter | 9.8s | model call |
| reddit x5 | 27.5s | network |
| sentiment | 10.9s | model call |
| youtube | 2.4s | network |
| narration | 0.8s | model call |

Retailers and Reddit are 68% of it, both waiting rather than thinking. Retailers are already
parallel. Reddit is deliberately not.

Between 4 and 8 Claude calls per search: criteria, 0 to 3 spec recoveries, qualification, 1 or 2
sentiment, narration.
