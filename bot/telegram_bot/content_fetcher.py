"""Content fetcher — extracts text from social media and news URLs.

Platform-specific strategies (in priority order):

  Reddit        — Reddit JSON API (.json suffix) for full post body + top
                  comments. Always used for reddit.com — far richer than scraping.

  X/Twitter,
  LinkedIn      — Firecrawl first (JS-rendered pages; OG gives ≤280 chars).
                  Falls back to OG scraping if Firecrawl key is absent.

  TikTok /
  Instagram /
  Facebook      — OG:description caption, then YouTube transcript cross-search.
                  Firecrawl not used — video content needs the transcript path.

  YouTube       — Transcript extraction via youtube-transcript-api.
                  Firecrawl not used — audio content needs the transcript.

  News articles,
  general Web   — OG scraping first. If result is thin (< 200 chars),
                  Firecrawl fallback renders the full article body.

Firecrawl credits are only spent where they add measurable value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from bot.config import settings
from bot.utils.logger import log

# ── Constants ──────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Reddit requires a descriptive User-Agent or returns 429/403
_REDDIT_USER_AGENT = "MaxTheTrader/1.0 (prediction market trading bot)"

_PLATFORM_DOMAINS = {
    "tiktok.com":   "TikTok",
    "x.com":        "X/Twitter",
    "twitter.com":  "X/Twitter",
    "instagram.com":"Instagram",
    "youtube.com":  "YouTube",
    "youtu.be":     "YouTube",
    "reddit.com":   "Reddit",
    "redd.it":      "Reddit",
    "linkedin.com": "LinkedIn",
    "facebook.com": "Facebook",
}

# Platforms where we always try Firecrawl first (JS-rendered, OG is poor).
# X/Twitter is intentionally excluded — Firecrawl explicitly blocks it.
# OG scraping still returns tweet text up to ~280 chars which is usable.
_FIRECRAWL_FIRST_PLATFORMS = {"LinkedIn"}

# Platforms where we cross-search YouTube for a spoken transcript
_VIDEO_PLATFORMS = {"TikTok", "Instagram", "Facebook"}

# If standard OG scraping returns fewer than this many chars, try Firecrawl
_FIRECRAWL_FALLBACK_THRESHOLD = 200

# Reddit comment extraction limits
_REDDIT_MAX_COMMENT_CHARS = 800
_REDDIT_TOP_N_COMMENTS    = 5

# Regex to extract URLs from post body text (handles both plain and markdown links)
_URL_RE = re.compile(r'https?://[^\s\)\]>\"\']+')

def _extract_embedded_urls(selftext: str, link_url: str = "") -> list[str]:
    """Extract unique URLs from a Reddit post body and link field.

    Returns a deduplicated list of external URLs found in the selftext and/or
    the post's link_url. Filters out Reddit internal URLs (self-links,
    comment links, subreddit links).
    """
    found: list[str] = []
    # URLs in the body text
    for m in _URL_RE.finditer(selftext):
        candidate = m.group(0).rstrip(".,;:!?")  # strip trailing punctuation
        found.append(candidate)
    # Link posts have a separate URL field
    if link_url and "reddit.com" not in link_url:
        found.append(link_url)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in found:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class FetchedContent:
    url:             str
    text:            str   # primary extracted text (OG / Firecrawl / Reddit body)
    source_platform: str
    success:         bool
    error:           str  = ""
    fetch_method:    str  = ""   # "og", "firecrawl", "reddit_json", "transcript"
    subreddit:       str  = ""   # populated for Reddit posts: e.g. "algotrading"
    canonical_url:   str  = ""   # resolved URL for dedup (e.g. /comments/{id}/ instead of /s/XXXX)
    embedded_urls:  list  = None # URLs found in post body (YouTube, articles, etc.)
    # Video transcript fields — populated for TikTok/Instagram/Facebook
    transcript:      str  = ""
    found_on_youtube:bool = False
    video_title:     str  = ""
    channel_name:    str  = ""
    youtube_url:     str  = ""
    transcript_error:str  = ""

    def __post_init__(self):
        if self.embedded_urls is None:
            self.embedded_urls = []


# ── Platform detection ─────────────────────────────────────────────────────────

def detect_platform(url: str) -> str:
    url_lower = url.lower()
    for domain, platform in _PLATFORM_DOMAINS.items():
        if domain in url_lower:
            return platform
    return "Web"


def detect_subreddit(url: str) -> str:
    """
    Extract the subreddit name from a Reddit post URL.

    Works on canonical URLs of the form:
        https://www.reddit.com/r/SUBREDDIT/comments/...
    Returns the subreddit name (no 'r/' prefix), or "" if not parseable.
    """
    parsed = urlparse(url)
    parts  = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "r":
        return parts[1]
    return ""


# ── Firecrawl ──────────────────────────────────────────────────────────────────

def _firecrawl_available() -> bool:
    return bool(settings.FIRECRAWL_API_KEY)


def _clean_firecrawl_markdown(markdown: str) -> str:
    """Strip Firecrawl boilerplate nav/footer noise — keep body content."""
    lines = markdown.splitlines()
    body_lines = [
        ln for ln in lines
        if not (len(ln.strip()) < 60 and ln.count("|") > 3)
    ]
    return "\n".join(body_lines).strip()


def _markdown_from_firecrawl_result(result) -> str:
    """Normalize Firecrawl SDK scrape payloads across SDK versions."""
    if result is None:
        return ""
    if isinstance(result, dict):
        return (
            result.get("markdown")
            or (result.get("data") or {}).get("markdown")
            or ""
        )
    if hasattr(result, "markdown"):
        return result.markdown or ""
    return ""


async def _fetch_with_firecrawl(url: str) -> tuple[str, str]:
    """
    Fetch a URL using Firecrawl.

    Prefers AsyncFirecrawl when available (newer SDKs). Falls back to the
    sync FirecrawlApp.scrape_url path used by older firecrawl-py installs.

    Returns (markdown_text, error_message).
    markdown_text is empty string on failure.
    """
    if not _firecrawl_available():
        return "", "Firecrawl API key not configured"

    try:
        import asyncio

        log.info(f"[dim]Firecrawl scraping: {url[:80]}[/]")
        result = None

        try:
            from firecrawl import AsyncFirecrawl
            client = AsyncFirecrawl(api_key=settings.FIRECRAWL_API_KEY)
            result = await client.scrape(url, formats=["markdown"])
        except ImportError:
            from firecrawl import FirecrawlApp

            def _scrape_sync():
                app = FirecrawlApp(api_key=settings.FIRECRAWL_API_KEY)
                # Older SDKs use scrape_url + params; newer ones accept formats=
                try:
                    return app.scrape_url(url, params={"formats": ["markdown"]})
                except TypeError:
                    return app.scrape_url(url, {"formats": ["markdown"]})

            result = await asyncio.to_thread(_scrape_sync)

        markdown = _markdown_from_firecrawl_result(result)
        if not markdown:
            return "", "Firecrawl returned empty markdown"

        cleaned = _clean_firecrawl_markdown(markdown)
        log.info(f"[dim]Firecrawl returned {len(cleaned):,} chars of markdown[/]")
        return cleaned, ""

    except Exception as e:
        log.warning(f"[dim]Firecrawl failed for {url[:60]}: {e}[/]")
        return "", str(e)


# ── Reddit JSON API ────────────────────────────────────────────────────────────

def _reddit_json_url(url: str) -> str | None:
    """
    Convert a Reddit post URL to its JSON API equivalent.
    Returns None if the URL is a subreddit listing (not a specific post).

    Handles:
      https://reddit.com/r/algotrading/comments/abc123/my_post/
        → https://www.reddit.com/r/algotrading/comments/abc123/my_post/.json
      https://redd.it/abc123
        → https://www.reddit.com/comments/abc123/.json
    """
    parsed = urlparse(url)
    host   = "www.reddit.com"   # normalise old./www./bare reddit.com
    path   = parsed.path.rstrip("/")

    # redd.it short links: redd.it/POSTID → /comments/POSTID
    netloc_lower = parsed.netloc.lower()
    if "redd.it" in netloc_lower and "/comments/" not in path:
        post_id = path.lstrip("/")
        if post_id:
            path = f"/comments/{post_id}"

    if "/comments/" not in path:
        return None

    return urlunparse(("https", host, path + "/.json", "", "", ""))


async def _resolve_reddit_share_link(url: str) -> str:
    """
    Follow a Reddit share/short link to its canonical post URL.

    Reddit mobile share links (/s/XXXXX) and redd.it links redirect
    to the full /r/sub/comments/postid/ URL. We do a lightweight HEAD
    request to resolve the Location header without downloading the page.
    """
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": _REDDIT_USER_AGENT},
        ) as client:
            resp = await client.head(url)
            canonical = str(resp.url)
            log.info(f"[dim]Reddit share link resolved: {canonical[:80]}[/]")
            return canonical
    except Exception as e:
        log.info(f"[dim]Reddit redirect resolution failed ({e}), using original URL[/]")
        return url


_REDDIT_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.IGNORECASE)


def _extract_reddit_post_id(url: str) -> str:
    """Extract the base36 Reddit submission id from a comments URL."""
    match = _REDDIT_POST_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _assemble_reddit_fetched(
    *,
    url: str,
    working_url: str,
    post: dict,
    comments: list[dict],
    fetch_method: str,
) -> FetchedContent:
    """Build FetchedContent from a Reddit post dict + comment dicts."""
    title = (post.get("title") or "").strip()
    selftext = (post.get("selftext") or "").strip()
    author = post.get("author") or ""
    subreddit = post.get("subreddit_name_prefixed") or (
        f"r/{post.get('subreddit')}" if post.get("subreddit") else ""
    )
    score = post.get("score", 0) or 0
    link_url = post.get("url") or ""
    is_link = (
        post.get("is_self", True) is False
        and link_url
        and "reddit.com" not in link_url
    )

    parts = [
        f"[REDDIT POST — {subreddit} | u/{author} | ↑{score:,}]",
        f"Title: {title}",
    ]

    if selftext and selftext not in ("[deleted]", "[removed]"):
        parts.append(f"\n{selftext}")
    elif is_link:
        parts.append(f"\nLink: {link_url}")

    real_comments = sorted(
        [
            c for c in comments
            if (c.get("body") or "").strip()
            and c.get("body") not in ("[deleted]", "[removed]")
        ],
        key=lambda c: c.get("score", 0) or 0,
        reverse=True,
    )

    comment_chars = 0
    for i, c in enumerate(real_comments[:_REDDIT_TOP_N_COMMENTS]):
        body = c["body"].strip()
        author_c = c.get("author", "")
        score_c = c.get("score", 0) or 0
        snippet = body[:400] + ("…" if len(body) > 400 else "")
        entry = f"\n[Comment #{i+1} | u/{author_c} | ↑{score_c}]\n{snippet}"
        if comment_chars + len(entry) > _REDDIT_MAX_COMMENT_CHARS:
            break
        parts.append(entry)
        comment_chars += len(entry)

    embedded = _extract_embedded_urls(selftext, link_url if is_link else "")
    if embedded:
        log.info(f"[dim]Reddit post has {len(embedded)} embedded URL(s): {embedded[0][:60]}[/]")

    log.info(
        f"[dim]Reddit fetched via {fetch_method}: \"{title[:55]}\" "
        f"({len(selftext)} body chars, {len(real_comments)} comments)[/]"
    )

    return FetchedContent(
        url=url, text="\n".join(parts), source_platform="Reddit",
        success=True, fetch_method=fetch_method,
        subreddit=post.get("subreddit", "") or detect_subreddit(working_url),
        canonical_url=working_url,
        embedded_urls=embedded,
    )


async def _fetch_reddit_via_pullpush(url: str, working_url: str) -> FetchedContent:
    """
    Fallback Reddit fetch via Pullpush archive API.

    Used when reddit.com JSON returns 403/blocked from the current network.
    """
    post_id = _extract_reddit_post_id(working_url) or _extract_reddit_post_id(url)
    if not post_id:
        return FetchedContent(
            url=url, text="", source_platform="Reddit", success=False,
            error="Cannot extract Reddit post id for Pullpush fallback",
        )

    log.info(f"[dim]Reddit Pullpush fallback → id={post_id}[/]")
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": _REDDIT_USER_AGENT},
    ) as client:
        post_resp = await client.get(
            "https://api.pullpush.io/reddit/search/submission/",
            params={"ids": post_id},
        )
        post_resp.raise_for_status()
        posts = post_resp.json().get("data") or []
        if not posts:
            raise ValueError(f"Pullpush returned no submission for id={post_id}")

        comment_resp = await client.get(
            "https://api.pullpush.io/reddit/search/comment/",
            params={"link_id": f"t3_{post_id}", "size": 25, "sort": "score"},
        )
        comments = []
        if comment_resp.status_code == 200:
            comments = comment_resp.json().get("data") or []

    return _assemble_reddit_fetched(
        url=url,
        working_url=working_url,
        post=posts[0],
        comments=comments,
        fetch_method="reddit_pullpush",
    )


async def _fetch_reddit_post(url: str) -> FetchedContent:
    """
    Fetch a Reddit post via the Reddit JSON API.

    Assembles: post header + full selftext body + top N comments by score.
    Handles mobile share links (/s/XXXXX) by resolving their redirect first.
    On Reddit JSON 403/network blocks, falls back to Pullpush archive API.
    Returns a FetchedContent with success=False if the URL isn't a specific post
    or if all fetch paths fail — caller falls through to Firecrawl.
    """
    # Resolve share links that don't have /comments/ in the path
    working_url = url
    json_url = _reddit_json_url(url)
    if not json_url:
        log.info(f"[dim]Reddit URL has no /comments/ — resolving redirect[/]")
        working_url = await _resolve_reddit_share_link(url)
        json_url = _reddit_json_url(working_url)

    if not json_url:
        return FetchedContent(
            url=url, text="", source_platform="Reddit", success=False,
            error="URL does not resolve to a specific Reddit post",
        )

    log.info(f"[dim]Reddit JSON API → {json_url}[/]")
    json_error = ""

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": _REDDIT_USER_AGENT},
        ) as client:
            resp = await client.get(json_url)
            resp.raise_for_status()
            data = resp.json()

        post_listing = data[0]["data"]["children"]
        if not post_listing:
            raise ValueError("Empty post listing")

        comments_data = data[1]["data"]["children"] if len(data) > 1 else []
        comments = [
            c["data"] for c in comments_data
            if c.get("kind") == "t1" and isinstance(c.get("data"), dict)
        ]
        return _assemble_reddit_fetched(
            url=url,
            working_url=working_url,
            post=post_listing[0]["data"],
            comments=comments,
            fetch_method="reddit_json",
        )

    except Exception as e:
        json_error = str(e)
        log.warning(f"[dim]Reddit JSON fetch failed: {e}[/]")

    try:
        return await _fetch_reddit_via_pullpush(url, working_url)
    except Exception as e:
        log.warning(f"[dim]Reddit Pullpush fallback failed: {e}[/]")
        return FetchedContent(
            url=url, text="", source_platform="Reddit",
            success=False, error=json_error or str(e),
        )


# ── Standard OG scraping ───────────────────────────────────────────────────────

async def _fetch_og_content(url: str) -> tuple[str, bool, str]:
    """
    Attempt to extract content via OG/Twitter meta tags + paragraph fallback.

    Returns (text, success, error_message).
    """
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")

        # og:description
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            t = og_desc["content"].strip()
            if len(t) > 20:
                log.info(f"[dim]OG scrape: og:description ({len(t)} chars)[/]")
                return t, True, ""

        og_title = soup.find("meta", property="og:title")
        title_text = og_title["content"].strip() if og_title and og_title.get("content") else ""

        # twitter:description
        tw_desc = soup.find("meta", attrs={"name": "twitter:description"})
        if tw_desc and tw_desc.get("content"):
            t = tw_desc["content"].strip()
            if len(t) > 20:
                combined = f"{title_text}: {t}" if title_text else t
                log.info(f"[dim]OG scrape: twitter:description ({len(combined)} chars)[/]")
                return combined, True, ""

        # Page title + first paragraph fallback
        page_title  = soup.find("title")
        title       = page_title.get_text(strip=True) if page_title else title_text
        paragraphs  = soup.find_all("p")
        para_texts  = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50]

        if title or para_texts:
            parts = [title] if title else []
            if para_texts:
                parts.append(para_texts[0][:300])
            combined = " | ".join(parts)
            log.info(f"[dim]OG scrape: title+para fallback ({len(combined)} chars)[/]")
            return combined, True, ""

        return "", False, "No extractable meta content found"

    except httpx.TimeoutException:
        return "", False, "Timeout fetching URL"
    except Exception as e:
        return "", False, str(e)


# ── Main entry point ───────────────────────────────────────────────────────────

async def fetch_social_content(url: str) -> FetchedContent:
    """
    Extract text content from any social media or news URL.

    Routing:
      Reddit              → Reddit JSON API (always)
      X/Twitter, LinkedIn → Firecrawl first, OG fallback
      TikTok/Instagram/FB → OG caption + YouTube transcript cross-search
      YouTube             → YouTube transcript (handled upstream in bot.py)
      News / Web          → OG scraping, Firecrawl fallback if result is thin
    """
    platform     = detect_platform(url)
    fetch_method = "og"   # updated at each branch that takes a different path
    log.info(f"[dim]Content fetcher: {platform} — {url[:80]}[/]")

    # ── Reddit: JSON API first, Firecrawl fallback ──────────────────────────
    if platform == "Reddit":
        result = await _fetch_reddit_post(url)
        if result.success:
            return result

        # Reddit blocks generic OG scraping — skip straight to Firecrawl
        log.info(
            f"[dim]Reddit JSON failed ({result.error[:80]}), "
            f"trying Firecrawl fallback[/]"
        )
        if _firecrawl_available():
            markdown, err = await _fetch_with_firecrawl(url)
            if markdown:
                return FetchedContent(
                    url=url, text=markdown, source_platform="Reddit",
                    success=True, fetch_method="firecrawl",
                )
            log.info(f"[dim]Reddit Firecrawl also failed ({err})[/]")

        # Both paths failed — return the original JSON error (more informative)
        return result

    # ── X/Twitter + LinkedIn: Firecrawl first ───────────────────────────────
    if platform in _FIRECRAWL_FIRST_PLATFORMS and _firecrawl_available():
        markdown, err = await _fetch_with_firecrawl(url)
        if markdown:
            return FetchedContent(
                url=url, text=markdown, source_platform=platform,
                success=True, fetch_method="firecrawl",
            )
        log.info(f"[dim]Firecrawl failed ({err}), falling back to OG scrape[/]")

    # ── Standard OG scraping ─────────────────────────────────────────────────
    caption_text, caption_success, caption_error = await _fetch_og_content(url)

    # ── Video platforms: YouTube transcript cross-search ─────────────────────
    transcript       = ""
    found_on_youtube = False
    video_title      = ""
    channel_name     = ""
    youtube_url      = ""
    transcript_error = ""

    # ── yt-dlp fallback for thin captions (Instagram/TikTok auth walls) ───
    if platform in _VIDEO_PLATFORMS and len(caption_text) < _FIRECRAWL_FALLBACK_THRESHOLD:
        log.info(f"[dim]Caption thin ({len(caption_text)} chars) — trying yt-dlp metadata[/]")
        try:
            from bot.telegram_bot.video_fetcher import _extract_metadata_ytdlp
            meta = await _extract_metadata_ytdlp(url)
            ytdlp_text = meta.get("description") or meta.get("title") or ""
            if len(ytdlp_text) > len(caption_text):
                log.info(f"[dim]yt-dlp gave better caption: {len(ytdlp_text)} chars[/]")
                caption_text = ytdlp_text
                caption_success = True
        except Exception as e:
            log.warning(f"[dim]yt-dlp metadata fallback failed: {e}[/]")

    if platform in _VIDEO_PLATFORMS:
        log.info(f"[dim]Video platform ({platform}) — attempting YouTube transcript[/]")
        try:
            from bot.telegram_bot.video_fetcher import fetch_video_transcript
            vr = await fetch_video_transcript(url, platform, caption_text=caption_text)
            found_on_youtube = vr.found_on_youtube
            video_title      = vr.video_title
            channel_name     = vr.channel_name
            youtube_url      = vr.youtube_url
            if vr.success and vr.transcript:
                transcript   = vr.transcript
                fetch_method = "transcript"
                log.info(f"[dim]Transcript extracted: {len(transcript)} chars[/]")
            elif vr.found_on_youtube:
                transcript_error = vr.error or "No transcript available"
            else:
                transcript_error = vr.error or "Not found on YouTube"
        except Exception as e:
            transcript_error = f"Transcript search failed: {e}"
            log.warning(f"[dim]Video transcript error: {e}[/]")

    # ── Firecrawl fallback for thin OG results (non-video platforms) ─────────
    if (
        platform not in _VIDEO_PLATFORMS
        and platform not in _FIRECRAWL_FIRST_PLATFORMS   # already tried above
        and platform != "YouTube"                          # transcript handles YouTube
        and _firecrawl_available()
        and len(caption_text) < _FIRECRAWL_FALLBACK_THRESHOLD
        and not transcript
    ):
        log.info(
            f"[dim]OG result thin ({len(caption_text)} chars) — "
            f"trying Firecrawl fallback[/]"
        )
        markdown, err = await _fetch_with_firecrawl(url)
        if markdown:
            caption_text    = markdown
            caption_success = True
            caption_error   = ""
            fetch_method    = "firecrawl"
            log.info(f"[dim]Firecrawl fallback succeeded ({len(markdown)} chars)[/]")

    # ── Build result ──────────────────────────────────────────────────────────
    if not caption_success and not transcript:
        return FetchedContent(
            url=url, text="", source_platform=platform, success=False,
            error=caption_error or "No extractable content found",
            found_on_youtube=found_on_youtube,
            video_title=video_title, channel_name=channel_name,
            youtube_url=youtube_url, transcript=transcript,
            transcript_error=transcript_error,
        )

    return FetchedContent(
        url=url, text=caption_text, source_platform=platform,
        success=True, fetch_method=fetch_method,
        found_on_youtube=found_on_youtube,
        video_title=video_title, channel_name=channel_name,
        youtube_url=youtube_url, transcript=transcript,
        transcript_error=transcript_error,
    )
