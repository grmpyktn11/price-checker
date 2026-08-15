import json
from pathlib import Path

# resolved from this file, not cwd, so scripts and uvicorn both find it
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


# scrapers call this when their API key is missing, so the app runs with an empty .env
def load_fixture(filename: str) -> dict:
    with open(FIXTURES_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


class ScraperBase:
    async def search(self, query: str, store_ids: list[str] | None) -> list[dict]:
        """Returns list of {name, url, price, in_stock, store_id, distance_miles}"""
        raise NotImplementedError

    async def get_specs(self, product_url: str) -> dict:
        """Returns spec dict, e.g. {mAh: 24000, thickness_mm: 22}. Empty dict if unavailable —
        triggers spec_extraction.py LLM fallback."""
        raise NotImplementedError

    async def get_reviews(self, product_url: str) -> dict:
        """Returns {rating, review_count, verified_ratio (if available)}"""
        raise NotImplementedError

    async def find_nearby_stores(self, lat: float, lon: float, radius_mi: int) -> list[dict]:
        """Returns [{store_id, name, distance_miles}] within radius. Not implemented for Amazon."""
        raise NotImplementedError
