import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.scrapers.bestbuy import BestBuyScraper  # noqa: E402

QUERY = "portable charger"
INSTALL_HINT = "run: playwright install chromium"


def show(label, data):
    print(f"\n--- {label} ---")
    print(json.dumps(data, indent=2))


async def main():
    # find_nearby_stores is not implemented: the Stores API needed the denied key
    scraper = BestBuyScraper()

    results = await scraper.search(QUERY, None)
    show("search", results)
    if not results:
        return

    url = results[0]["url"]
    show("get_specs", await scraper.get_specs(url))
    show("get_reviews", await scraper.get_reviews(url))


try:
    asyncio.run(main())
except Exception as error:
    # the chromium download is a separate manual step from pip install
    print(INSTALL_HINT if "Executable doesn't exist" in str(error) else error)
