import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.scrapers.target import LIVE_SCRAPE, TargetScraper  # noqa: E402

QUERY = "portable charger"
LAT = 37.7749
LON = -122.4194
RADIUS_MI = 25
FIXTURE_NOTE = ("FIXTURE MODE: get_specs and get_reviews return the same saved product page "
                "for every url.")


def show(label, data):
    print(f"\n--- {label} ---")
    print(json.dumps(data, indent=2))


async def main():
    print("MODE:", "LIVE" if LIVE_SCRAPE else "FIXTURE")
    if not LIVE_SCRAPE:
        print(FIXTURE_NOTE)
    scraper = TargetScraper()

    results = await scraper.search(QUERY, None)
    show("search", results)
    show("find_nearby_stores", await scraper.find_nearby_stores(LAT, LON, RADIUS_MI))
    if not results:
        return

    url = results[0]["url"]
    show("get_specs", await scraper.get_specs(url))
    show("get_reviews", await scraper.get_reviews(url))


asyncio.run(main())
