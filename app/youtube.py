"""Optional part 1: most popular let's play on YouTube -> transcript -> LLM conclusion.
Enabled only when ENABLE_YOUTUBE=1 and YOUTUBE_API_KEY is set."""
import httpx

from .config import settings
from . import llm


def enabled() -> bool:
    return bool(settings.enable_youtube and settings.youtube_api_key)


def find_letsplay(title: str):
    r = httpx.get("https://www.googleapis.com/youtube/v3/search", timeout=20, params={
        "key": settings.youtube_api_key, "q": f"{title} let's play", "part": "snippet",
        "type": "video", "order": "viewCount", "maxResults": 1, "videoDuration": "long"})
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return None
    it = items[0]
    return it["id"]["videoId"], it["snippet"]["title"]


def get_transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        t = api.fetch(video_id, languages=["en", "ru"])
        return " ".join(s.text for s in t)
    except Exception:
        return None


def process(game) -> None:
    if not enabled():
        game.letsplay_status = "disabled (set ENABLE_YOUTUBE=1 and YOUTUBE_API_KEY)"
        return
    try:
        found = find_letsplay(game.title)
        if not found:
            game.letsplay_status = "not_found"
            return
        vid, vtitle = found
        game.letsplay_url = f"https://www.youtube.com/watch?v={vid}"
        game.letsplay_title = vtitle
        tr = get_transcript(vid)
        if not tr:
            game.letsplay_status = "transcript_unavailable"
            return
        game.letsplay_conclusion, _ = llm.letsplay_conclusion(game.title, tr, game.id)
        game.letsplay_status = "ok"
    except Exception as e:  # noqa
        game.letsplay_status = f"error: {e}"[:100]
