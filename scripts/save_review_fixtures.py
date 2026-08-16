import asyncio
import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# so the script runs from the repo root or from inside scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from backend.scrapers.base import FIXTURES_DIR  # noqa: E402
from backend.services import google_cse, reviews_forums, reviews_reddit, reviews_youtube  # noqa: E402

# the committed cse_*.json are hand-built in the documented CSE response shape, not live
# captures: the Custom Search API is not enabled on the project's key yet (403). run this
# once it is, to replace them with real ones
QUERY = "portable charger"
CATEGORY = "electronics"
# one full capture costs 2 CSE queries and 102 YouTube units. run it once, not in a loop
COST_NOTE = "cost: 2 CSE queries, 102 YouTube units"


def write(filename: str, payload: dict) -> None:
    (FIXTURES_DIR / filename).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"{filename}: {len(json.dumps(payload))} chars")


async def save_cse() -> None:
    write("cse_reddit.json", await google_cse.search(reviews_reddit.build_reddit_query(QUERY, CATEGORY)))
    write("cse_forums.json", await google_cse.search(reviews_forums.build_forum_query(QUERY, CATEGORY)))


async def save_youtube() -> None:
    async with httpx.AsyncClient(timeout=reviews_youtube.TIMEOUT_SECONDS) as client:
        search = await reviews_youtube.get_json(client, reviews_youtube.SEARCH_URL, {
            "part": "snippet", "type": "video", "order": "relevance",
            "maxResults": reviews_youtube.MAX_VIDEOS, "relevanceLanguage": "en",
            "q": f"{QUERY} review",
        })
        write("youtube_search.json", search)
        videos = reviews_youtube.parse_videos(search, {})
        if not videos:
            print("youtube_videos.json skipped: search returned nothing")
            return
        write("youtube_videos.json", await reviews_youtube.get_json(
            client, reviews_youtube.VIDEOS_URL,
            {"part": "statistics,snippet", "id": ",".join(v["video_id"] for v in videos)}))
        write("youtube_comments.json", await reviews_youtube.get_json(
            client, reviews_youtube.COMMENTS_URL,
            {"part": "snippet", "order": "relevance", "textFormat": "plainText",
             "maxResults": reviews_youtube.COMMENTS_PER_VIDEO,
             "videoId": videos[0]["video_id"]}))


async def main():
    if not google_cse.GOOGLE_CSE_API_KEY or not reviews_youtube.YOUTUBE_API_KEY:
        print("set GOOGLE_CSE_API_KEY, GOOGLE_CSE_ID and YOUTUBE_API_KEY in .env: this tool "
              "only writes fixtures from live responses")
        return
    print(COST_NOTE)
    await save_cse()
    await save_youtube()


asyncio.run(main())
