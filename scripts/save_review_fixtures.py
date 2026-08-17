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
from backend.services import reviews_reddit, reviews_youtube  # noqa: E402

QUERY = "portable charger"
REDDIT_FIXTURE = "reddit_search.xml"
CATEGORY = "electronics"
# reddit is keyless but rate-limits hard, so this is one request. youtube costs 102 units.
# run it once, not in a loop
COST_NOTE = "cost: 1 reddit request, 102 YouTube units"


def write(filename: str, payload: dict) -> None:
    (FIXTURES_DIR / filename).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"{filename}: {len(json.dumps(payload))} chars")


# reddit's feed is xml, so it is written as text rather than re-serialized json
async def save_reddit() -> None:
    xml_text = await reviews_reddit.search(QUERY, CATEGORY)
    if not xml_text:
        print(f"{REDDIT_FIXTURE} skipped: search returned nothing")
        return
    (FIXTURES_DIR / REDDIT_FIXTURE).write_text(xml_text, encoding="utf-8")
    print(f"{REDDIT_FIXTURE}: {len(xml_text)} chars")


async def save_youtube() -> None:
    if not reviews_youtube.YOUTUBE_API_KEY:
        print("youtube fixtures skipped: no YOUTUBE_API_KEY")
        return
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
    print(COST_NOTE)
    await save_reddit()
    await save_youtube()


asyncio.run(main())
