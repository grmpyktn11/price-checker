# Deal Tracker

Personal deal-tracking app. Chat describes what you want, three retailers get searched, results are
ranked and narrated. Watched items are re-scanned on a schedule and price drops raise email alerts.

See [API.md](API.md) for the endpoint contract and [spec.md](spec.md) for the original design.

## Running it

Requires an `ANTHROPIC_API_KEY` — the app refuses to start without one, because product filtering is
a model call with no offline substitute.

```bash
cp .env.example .env          # then fill in the keys
pip install -r requirements.txt
playwright install chromium
.venv/Scripts/uvicorn backend.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev     # http://localhost:5173
```

Vite proxies `/api` to port 8000. Restart both after editing `.env` — the backend reads keys at
import and Vite bakes `VITE_` vars in at startup.

## How a search works

1. Search all three retailers, filter and score the results — one model call judges every listing
   at once (does it qualify, how well does it fit, which listings are the same product).
2. Rank them.
3. Research the **top 5 individually**: one Reddit search per product, on that product's own name,
   paced because Reddit rate-limits.
4. One model call reads that per-product discussion and also says which products it cannot
   separate. Only if the top is genuinely too close does YouTube get searched, for the top 2 only —
   a decisive search costs zero YouTube quota (each search is 100 of ~10000 daily units).
5. Re-rank on what the research found, then narrate.

`distance_score` is per retailer, not per product: Google Places finds the nearest Target and Best
Buy to your saved location once per location, and the score falls linearly from the door to the
edge of `radius_miles`. Amazon and any failed lookup score a neutral 0.5.

## Tests

```bash
pytest              # 207 tests, free, no network, ~5s
pytest -m live      # 28 tests that hit the real retailers and the real Claude API
```

The default run is pure logic against the saved captures in `tests/fixtures/` — one real scrape per
retailer, kept permanently as frozen test input. Nothing mocks the model and nothing replays a
scrape at runtime: a test either exercises pure logic on a literal payload, or it makes the real
calls and is marked `live`.

## Retailers

The app is live-only — every search hits the real sites, there is no fixture mode.

Current state (2026-08-17): Target's redsky endpoints 403 intermittently, Best Buy product pages
are Akamai-blocked (search prices still come through, and the evidence comes from Amazon or from
the product's own discussion instead), Amazon works but rate-limits under load, and Reddit 429s a
fair share of searches — the pipeline treats every one of these as a missing source, never a
failed run.
