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

# two competing products, the way the pipeline researches them: one reddit search each
PRODUCTS = ["Anker 737 Power Bank", "INIU Portable Charger 20000mAh"]
CATEGORY = "electronics"

logging.basicConfig(level=logging.INFO)
# httpx logs full urls at INFO and our keys ride in query strings
logging.getLogger("httpx").setLevel(logging.WARNING)


def show(review: dict | None) -> None:
    if review is None:
        print("   no results")
        return
    for field in ("source", "rating", "review_count", "verified_ratio", "mention_count", "url"):
        print(f"   {field}: {review.get(field)}")
    print(f"   summary_text: {review['summary_text'][:300]}")


async def main():
    payload = []
    for name in PRODUCTS:
        print(f"\n{name}:")
        found = [await reviews_reddit.gather(name, CATEGORY), await reviews_youtube.gather(name)]
        for review in found:
            show(review)
        payload.append({
            "name": name,
            "rating": None,
            "discussion": "\n\n".join(r["summary_text"] for r in found if r),
        })

    print("\nassessment:", json.dumps(await sentiment.assess(payload), indent=2))


asyncio.run(main())
