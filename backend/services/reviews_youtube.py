import html as html_module
import logging
import os

import httpx

from backend.scrapers.base import load_fixture

SOURCE = "youtube"
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
MAX_VIDEOS = 5          # search.list results kept
COMMENT_VIDEOS = 3      # how many of those get a comment fetch
COMMENTS_PER_VIDEO = 10
TIMEOUT_SECONDS = 10
MAX_SUMMARY_CHARS = 2000
# quota: search.list costs 100 units, videos.list and commentThreads.list cost 1 each, so one
# gather is ~104 of the ~10000 daily units. the comments are 3 units and are the only full
# buyer prose any external source produces, which is why they are fetched at all

logger = logging.getLogger(__name__)


async def get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    try:
        response = await client.get(url, params={**params, "key": YOUTUBE_API_KEY})
        response.raise_for_status()
        return response.json()
    # ValueError covers a 200 whose body is not json, which must degrade like an outage
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("youtube request failed (%s): %s", url, error)
        return {}


# search.list carries the title/channel, videos.list carries the counts; merge on video id
def parse_videos(search_payload: dict, videos_payload: dict) -> list[dict]:
    statistics = {
        item.get("id"): item.get("statistics") or {}
        for item in videos_payload.get("items") or []
    }
    videos = []
    for item in (search_payload.get("items") or [])[:MAX_VIDEOS]:
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        counts = statistics.get(video_id, {})
        videos.append({
            "video_id": video_id,
            "title": html_module.unescape(snippet.get("title", "")),
            "channel": snippet.get("channelTitle", ""),
            "url": WATCH_URL.format(video_id=video_id),
            "view_count": to_int(counts.get("viewCount")),
            # likes are hidden on some videos, so this is legitimately None
            "like_count": to_int(counts.get("likeCount")),
            "comment_count": to_int(counts.get("commentCount")),
        })
    return videos


def to_int(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_comments(payload: dict) -> list[str]:
    comments = []
    for item in payload.get("items") or []:
        snippet = (((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {})
        text = snippet.get("textDisplay")
        if text:
            comments.append(html_module.unescape(text))
    return comments


def build_summary(videos: list[dict]) -> str:
    return " | ".join(f"{video['title']} ({video['channel']})" for video in videos)


# no rating is derived from likes: a like ratio is not a 0-5 star rating and pretending
# otherwise would corrupt compute_review_score
def build_review(videos: list[dict], comments: list[str]) -> dict | None:
    if not videos:
        return None
    text = " | ".join([build_summary(videos), *comments])
    return {
        "source": SOURCE,
        "rating": None,
        "review_count": None,
        "verified_ratio": None,
        "rating_distribution": None,
        "url": videos[0]["url"],
        "summary_text": text[:MAX_SUMMARY_CHARS],
        "mention_count": len(videos),
        "authenticity_flag": "ok",
    }


async def gather(query: str) -> dict | None:
    # no key configured: parse the saved fixtures instead of spending quota
    if not YOUTUBE_API_KEY:
        videos = parse_videos(load_fixture("youtube_search.json"),
                              load_fixture("youtube_videos.json"))
        return build_review(videos, parse_comments(load_fixture("youtube_comments.json")))

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        search_payload = await get_json(client, SEARCH_URL, {
            "part": "snippet", "type": "video", "order": "relevance",
            "maxResults": MAX_VIDEOS, "relevanceLanguage": "en", "q": f"{query} review",
        })
        videos = parse_videos(search_payload, {})
        if not videos:
            return None
        videos_payload = await get_json(client, VIDEOS_URL, {
            "part": "statistics,snippet",
            "id": ",".join(video["video_id"] for video in videos),
        })
        videos = parse_videos(search_payload, videos_payload)
        comments = []
        for video in videos[:COMMENT_VIDEOS]:
            payload = await get_json(client, COMMENTS_URL, {
                "part": "snippet", "order": "relevance", "textFormat": "plainText",
                "maxResults": COMMENTS_PER_VIDEO, "videoId": video["video_id"],
            })
            comments.extend(parse_comments(payload))
    return build_review(videos, comments)
