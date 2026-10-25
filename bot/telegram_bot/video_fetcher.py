"""
Video transcript fetcher — two-step workflow for extracting spoken content from
short-form video posts (TikTok, Instagram Reels, YouTube Shorts).

Workflow:
  1. Given a TikTok/Instagram URL, search YouTube for the same video by the same creator.
  2. If found on YouTube, extract the auto-generated transcript (free, no API key needed).
  3. If not found, return a structured failure so the bot can notify the user.

For YouTube URLs, skip step 1 and go straight to transcript extraction.

Libraries used (no API keys required):
  - youtube-transcript-api  → transcript extraction by video ID
  - youtube-search-python   → YouTube search without an API key
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.utils.logger import log


# Platforms where we attempt cross-platform YouTube search
_SHORT_FORM_PLATFORMS = {"TikTok", "Instagram", "Facebook"}

# Max characters of transcript to pass to the pipeline (keeps token count reasonable)
_MAX_TRANSCRIPT_CHARS = 4000


@dataclass
class VideoResult:
    """Result of a video content fetch attempt."""
    success: bool
    transcript: str = ""           # full extracted transcript text
    video_id: str = ""             # YouTube video ID
    video_title: str = ""          # YouTube video title
    channel_name: str = ""         # YouTube channel / creator name
    youtube_url: str = ""          # canonical YouTube URL
    source_platform: str = ""      # original platform ("TikTok", "Instagram", etc.)
    found_on_youtube: bool = False
    error: str = ""

    @property
    def summary_header(self) -> str:
        """One-line description for Telegram progress messages."""
        if self.found_on_youtube and self.transcript:
            return (
                f"📺 Found on YouTube: *{self.video_title[:60]}* "
                f"by {self.channel_name} — transcript extracted "
                f"({len(self.transcript)} chars)"
            )
        elif self.found_on_youtube and not self.transcript:
            return f"📺 Found on YouTube but no transcript available: {self.video_title[:60]}"
        else:
            return f"⚠️ Video not found on YouTube — using caption only"


async def fetch_video_transcript(
    url: str,
    source_platform: str,
    caption_text: str = "",
) -> VideoResult:
    """
    Main entry point. Given a social media URL and platform:

    - YouTube URLs: extract transcript directly.
    - TikTok / Instagram: search YouTube for the same creator + video,
      then extract transcript. Notify if not found.

    caption_text: the og:description already extracted (used to build search query)
    """
    if _is_youtube_url(url):
        video_id = _extract_youtube_id(url)
        if not video_id:
            return VideoResult(
                success=False,
                source_platform="YouTube",
                error="Could not extract YouTube video ID from URL",
            )
        return await _get_transcript_for_id(video_id, source_platform="YouTube")

    if source_platform in _SHORT_FORM_PLATFORMS:
        return await _find_and_transcribe(
            url=url,
            source_platform=source_platform,
            caption_text=caption_text,
        )

    # Platform not supported for video search (Reddit, LinkedIn, Web, etc.)
    return VideoResult(
        success=False,
        source_platform=source_platform,
        error=f"Video transcript not supported for {source_platform}",
    )


# ── YouTube direct ──────────────────────────────────────────────────────────────

def _is_youtube_url(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _extract_youtube_id(url: str) -> str | None:
    """Extract the video ID from a YouTube URL."""
    patterns = [
        r"youtube\.com/watch\?v=([A-Za-z0-9_\-]{11})",
        r"youtu\.be/([A-Za-z0-9_\-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_\-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_\-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def _get_transcript_for_id(
    video_id: str,
    source_platform: str = "YouTube",
    video_title: str = "",
    channel_name: str = "",
) -> VideoResult:
    """Fetch and return the transcript for a known YouTube video ID."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # v1.x API: instance method .fetch() returning FetchedTranscript
        # (v0.x used class method .get_transcript() — removed in 1.0)
        api = YouTubeTranscriptApi()
        fetched_transcript = api.fetch(
            video_id,
            languages=["en", "en-US", "en-GB"],
        )
        # Segments are FetchedTranscriptSnippet objects with .text attribute
        raw = " ".join(seg.text.strip() for seg in fetched_transcript)
        # Collapse whitespace
        raw = re.sub(r"\s+", " ", raw).strip()
        # Truncate to keep token count manageable
        transcript = raw[:_MAX_TRANSCRIPT_CHARS]
        if len(raw) > _MAX_TRANSCRIPT_CHARS:
            transcript += f"... [truncated — {len(raw):,} chars total]"

        log.info(
            f"[VIDEO] Transcript extracted: {len(raw):,} chars "
            f"(video_id={video_id})"
        )
        return VideoResult(
            success=True,
            transcript=transcript,
            video_id=video_id,
            video_title=video_title,
            channel_name=channel_name,
            youtube_url=f"https://www.youtube.com/watch?v={video_id}",
            source_platform=source_platform,
            found_on_youtube=True,
        )

    except Exception as e:
        err = str(e)
        log.warning(f"[VIDEO] Transcript unavailable for {video_id}: {err}")
        return VideoResult(
            success=False,
            video_id=video_id,
            video_title=video_title,
            channel_name=channel_name,
            youtube_url=f"https://www.youtube.com/watch?v={video_id}",
            source_platform=source_platform,
            found_on_youtube=True,   # found the video but no transcript
            error=f"Transcript unavailable: {err}",
        )


# ── Cross-platform search ───────────────────────────────────────────────────────

async def _extract_metadata_ytdlp(url: str) -> dict:
    """Use yt-dlp to extract video metadata (title, uploader, description).

    Works for Instagram reels, TikTok, and other platforms where OG scraping
    fails due to auth walls.  Returns a dict with keys: title, uploader,
    description (all strings, possibly empty).
    """
    import asyncio

    def _run():
        try:
            import yt_dlp
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": False,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    "title": (info.get("title") or "").strip(),
                    "uploader": (info.get("uploader") or info.get("channel") or "").strip(),
                    "description": (info.get("description") or "").strip(),
                }
        except Exception as e:
            log.warning(f"[VIDEO] yt-dlp metadata extraction failed: {e}")
            return {"title": "", "uploader": "", "description": ""}

    return await asyncio.get_event_loop().run_in_executor(None, _run)


async def _find_and_transcribe(
    url: str,
    source_platform: str,
    caption_text: str,
) -> VideoResult:
    """
    Search YouTube for a TikTok/Instagram video and extract its transcript.

    Search strategy:
      1. Extract creator username from URL
      2. Extract keywords from caption text
      3. If both are empty, try yt-dlp to extract video metadata
      4. Search YouTube for "{creator} {keywords}"
      5. Take the top result and attempt transcript extraction
    """
    creator = _extract_creator(url, source_platform)
    keywords = _extract_keywords(caption_text)

    # Fallback: use yt-dlp to extract metadata when OG scraping gave us nothing
    if not keywords and not creator:
        log.info(f"[VIDEO] No creator/keywords from URL+caption — trying yt-dlp metadata")
        meta = await _extract_metadata_ytdlp(url)
        if meta["uploader"]:
            creator = meta["uploader"]
        if meta["title"]:
            keywords = _extract_keywords(meta["title"])
        if not keywords and meta["description"]:
            keywords = _extract_keywords(meta["description"])

    if not keywords and not creator:
        return VideoResult(
            success=False,
            source_platform=source_platform,
            error="Not enough information to search YouTube — caption was empty",
        )

    query = _build_search_query(creator, keywords, source_platform)
    log.info(f"[VIDEO] Searching YouTube for: '{query}'")

    try:
        from youtubesearchpython import VideosSearch
        search = VideosSearch(query, limit=3)
        results = search.result()
        videos = results.get("result", [])
    except Exception as e:
        return VideoResult(
            success=False,
            source_platform=source_platform,
            error=f"YouTube search failed: {e}",
        )

    if not videos:
        log.info(f"[VIDEO] No YouTube results for: '{query}'")
        return VideoResult(
            success=False,
            source_platform=source_platform,
            error=f"No YouTube videos found for query: '{query}'",
        )

    # Take the best match (first result)
    best = videos[0]
    video_id = best.get("id", "")
    video_title = best.get("title", "")
    channel = best.get("channel", {}).get("name", "")

    log.info(
        f"[VIDEO] Best YouTube match: '{video_title}' by {channel} "
        f"(id={video_id})"
    )

    if not video_id:
        return VideoResult(
            success=False,
            source_platform=source_platform,
            error="YouTube search returned a result but no video ID",
        )

    return await _get_transcript_for_id(
        video_id=video_id,
        source_platform=source_platform,
        video_title=video_title,
        channel_name=channel,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _extract_creator(url: str, platform: str) -> str:
    """Extract the creator username from a social media URL."""
    patterns = {
        "TikTok": r"tiktok\.com/@([^/?&]+)",
        "Instagram": r"instagram\.com/(?:(?:reel|p)/[^/]+/|([^/?&]+)/)",
        "Facebook": r"facebook\.com/([^/?&]+)",
    }
    pattern = patterns.get(platform, "")
    if pattern:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return ""


def _extract_keywords(caption: str, max_words: int = 8) -> str:
    """
    Extract the most meaningful words from a caption for use in a YouTube search.
    Strips hashtags, emoji, and common filler words.
    """
    if not caption:
        return ""

    # Remove hashtags and URLs
    text = re.sub(r"#\w+", "", caption)
    text = re.sub(r"https?://\S+", "", text)
    # Remove emoji (basic unicode range)
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    # Remove punctuation except spaces
    text = re.sub(r"[^\w\s]", " ", text)

    filler = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "is", "are", "was", "be", "my", "your", "this",
        "that", "it", "i", "you", "we", "they", "what", "how", "just",
        "so", "all", "do", "not", "with", "from", "by", "up", "out",
        "as", "get", "go", "now", "new", "can", "here", "will", "if",
    }
    words = [w for w in text.lower().split() if len(w) > 2 and w not in filler]
    return " ".join(words[:max_words])


def _build_search_query(creator: str, keywords: str, platform: str) -> str:
    """Build the YouTube search query from creator + keywords."""
    parts = []
    if creator:
        parts.append(creator)
    if keywords:
        parts.append(keywords)
    # Add platform hint to surface cross-posted content
    if platform == "TikTok":
        parts.append("trading")   # most TikTok traders also post on YT under trading content
    return " ".join(parts)
