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

## Tests

```bash
pytest              # 193 tests, free, no network, ~50s
pytest -m live      # 25 tests that call Claude for real: costs money, takes minutes
```

The default run excludes anything that hits the Anthropic API. Nothing mocks the model — a test
either exercises pure logic on a literal payload, or it makes the real call and is marked `live`.

## Live scraping

`LIVE_SCRAPE=1` in `.env` searches the real retailers. Blank, and the scrapers replay saved captures
from `tests/fixtures/` and **ignore your query entirely** — a search for a keyboard will return
whatever the fixtures hold. The UI shows which mode it is in on every page.

Current retailer state: Target works, Best Buy returns prices only (its product pages are
Akamai-blocked), Amazon works but rate-limits under load.
