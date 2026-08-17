import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.services import reviews_reddit, reviews_youtube, sentiment  # noqa: E402

QUERY = "portable charger usb-c 140w"
CATEGORY = "electronics"
FIXTURE_NOTE = ("FIXTURE MODE: the source returns the saved capture, taken for 'portable "
                "charger', regardless of the query above.")

logging.basicConfig(level=logging.INFO)
# httpx logs full urls at INFO and our keys ride in query strings
logging.getLogger("httpx").setLevel(logging.WARNING)


def mode(live: bool) -> str:
    return "LIVE" if live else "FIXTURE"


def show(review: dict | None) -> None:
    if review is None:
        print("   no results")
        return
    for field in ("source", "rating", "review_count", "verified_ratio", "mention_count", "url"):
        print(f"   {field}: {review.get(field)}")
    print(f"   summary_text: {review['summary_text'][:300]}")


async def main():
    print("reddit  MODE:", mode(bool(reviews_reddit.LIVE_SCRAPE)))
    print("youtube MODE:", mode(bool(reviews_youtube.YOUTUBE_API_KEY)))
    if not reviews_reddit.LIVE_SCRAPE or not reviews_youtube.YOUTUBE_API_KEY:
        print(FIXTURE_NOTE)

    reviews = []
    for label, review in (
        ("reddit", await reviews_reddit.gather(QUERY, CATEGORY)),
        ("youtube", await reviews_youtube.gather(QUERY)),
    ):
        print(f"\n{label}:")
        show(review)
        if review:
            reviews.append(review)

    print("\nsentiment:", json.dumps(await sentiment.classify(reviews)))


asyncio.run(main())
