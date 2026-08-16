import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.services import google_cse, reviews_forums, reviews_reddit, reviews_youtube, sentiment  # noqa: E402

QUERY = "portable charger usb-c 140w"
CATEGORY = "electronics"
FIXTURE_NOTE = ("FIXTURE MODE: every source returns the same saved capture, taken for "
                "'portable charger', regardless of the query above.")

logging.basicConfig(level=logging.INFO)


def mode(has_key: bool) -> str:
    return "LIVE" if has_key else "FIXTURE"


def show(review: dict | None) -> None:
    if review is None:
        print("   no results")
        return
    for field in ("source", "rating", "review_count", "verified_ratio", "mention_count", "url"):
        print(f"   {field}: {review.get(field)}")
    print(f"   summary_text: {review['summary_text'][:300]}")


async def main():
    cse_live = bool(google_cse.GOOGLE_CSE_API_KEY and google_cse.GOOGLE_CSE_ID)
    print("reddit  MODE:", mode(bool(reviews_reddit.GOOGLE_CSE_API_KEY and cse_live)))
    print("forums  MODE:", mode(bool(reviews_forums.GOOGLE_CSE_API_KEY and cse_live)))
    print("youtube MODE:", mode(bool(reviews_youtube.YOUTUBE_API_KEY)))
    if not cse_live:
        print(FIXTURE_NOTE)

    reviews = []
    for label, review in (
        ("reddit", await reviews_reddit.gather(QUERY, CATEGORY)),
        ("forums", await reviews_forums.gather(QUERY, CATEGORY)),
        ("youtube", await reviews_youtube.gather(QUERY)),
    ):
        print(f"\n{label}:")
        show(review)
        if review:
            reviews.append(review)

    print("\nsentiment:", json.dumps(await sentiment.classify(reviews)))
    print("cse budget left:", google_cse.budget_left())


asyncio.run(main())
