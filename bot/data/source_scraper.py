"""
Source scraper — platform-aware batch content ingestion.

Identifies the source (subreddit, YouTube channel, Instagram profile, etc.) from
an ingested post and provides a unified interface for fetching more content from
that source.

Platform support status:
  Reddit    — ✅ Full: top posts by upvotes via JSON API
  YouTube   — 🟡 Partial: channel identified, video list via oEmbed + page scrape
  Instagram — 🔴 Blocked: platform login wall, no public API
  TikTok    — 🔴 Blocked: bot detection, no public API
  X/Twitter — 🔴 Blocked: requires paid API access ($100+/month)
  LinkedIn  — 🟡 Partial: Firecrawl can help but profile scraping is limited
  Facebook  — 🔴 Blocked: login wall

When a platform is blocked, the bot still identifies the source and informs
the user — it just cannot execute the batch scrape automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from bot.utils.logger import log

# Reuse the Reddit user agent from content_fetcher
try:
    from bot.telegram_bot.content_fetcher import _REDDIT_USER_AGENT
except ImportError:
    _REDDIT_USER_AGENT = "MaxTheTrader/1.0 (prediction market trading bot)"


# ── Quality scoring config (all tweakable) ────────────────────────────────────

QUALITY_COMMENT_WEIGHT = 3        # multiplier for num_comments
QUALITY_BODY_DIVISOR   = 100      # body_length / this value
QUALITY_MEDIA_BONUS    = 10       # flat bonus for posts with embedded media (YouTube, etc.)
QUALITY_FLOOR          = 5.0      # minimum quality_score to include a post
AUTO_INGEST_MAX_POSTS  = 10       # cap on posts per autonomous ingest

# Hard-filter: skip meta/community posts unless engagement overrides
_SKIP_TITLE_RE = re.compile(
    r'(?:(?:\[?meta\]?|welcome|introduce yourself|daily discussion|weekly|megathread|share yours))'
    r'|(?:^[^\w]*(?:what|who|how|does anyone|is it worth|should i|has anyone|any)\b)',
    re.IGNORECASE,
)
_SKIP_TITLE_OVERRIDE = 20.0      # skip-title posts above this quality still pass


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class SourceInfo:
    """Describes the source a post came from."""
    platform:       str    # "Reddit", "YouTube", "Instagram", "X/Twitter", etc.
    source_id:      str    # subreddit name, channel handle, username, etc.
    source_name:    str    # Human-readable: "r/algotrading", "@TradingChannel"
    source_url:     str    # URL to the source page
    track:          str    # "kalshi" or "traditional"
    ranking_label:  str    # "upvotes", "views", "likes + comments", etc.
    scrape_supported: bool          # Can we actually batch-scrape this?
    unsupported_reason: str = ""    # Why scraping is not available (if blocked)


@dataclass
class SourcePost:
    """A single piece of content from a batch source scrape."""
    url:         str
    title:       str
    score:       int      # upvotes / view count / likes — platform-specific
    score_label: str      # "upvotes", "views", "likes", etc.
    platform:    str
    extra:         dict  = field(default_factory=dict)  # platform-specific metadata
    # ── Engagement quality fields ──
    num_comments:  int   = 0
    body_length:   int   = 0
    has_media:     bool  = False
    quality_score: float = 0.0


# ── Quality scoring ───────────────────────────────────────────────────────────

def compute_quality(post: SourcePost) -> float:
    """Compute engagement quality score for a single post."""
    return (
        (post.num_comments * QUALITY_COMMENT_WEIGHT)
        + (post.body_length / QUALITY_BODY_DIVISOR)
        + (QUALITY_MEDIA_BONUS if post.has_media else 0)
    )


def score_and_filter_posts(
    posts: list[SourcePost],
    *,
    quality_floor: float = QUALITY_FLOOR,
    max_posts: int = AUTO_INGEST_MAX_POSTS,
    source_name: str = "",
) -> list[SourcePost]:
    """Score posts, apply hard filters + quality floor, return top N by quality.

    Logs every decision so the dashboard shows exactly what was included/skipped.
    """
    tag = f"[QUALITY] {source_name}" if source_name else "[QUALITY]"
    scored: list[SourcePost] = []

    for p in posts:
        p.quality_score = compute_quality(p)
        short = p.title[:50]

        # Hard filter: skip posts with tiny body AND no discussion
        if p.body_length < 50 and p.num_comments == 0:
            log.info(f"{tag} SKIP (empty) \"{short}\" q={p.quality_score:.1f}")
            continue

        # Hard filter: skip meta/question posts unless high engagement overrides
        if _SKIP_TITLE_RE.search(p.title) and p.quality_score < _SKIP_TITLE_OVERRIDE:
            log.info(f"{tag} SKIP (title) \"{short}\" q={p.quality_score:.1f}")
            continue

        # Quality floor
        if p.quality_score < quality_floor:
            log.info(
                f"{tag} SKIP (q={p.quality_score:.1f} < floor {quality_floor}) "
                f"\"{short}\" 💬{p.num_comments} body={p.body_length}"
            )
            continue

        log.info(
            f"{tag} PASS \"{short}\" "
            f"q={p.quality_score:.1f} 💬{p.num_comments} body={p.body_length} media={p.has_media}"
        )
        scored.append(p)

    # Sort by quality descending, cap at max_posts
    scored.sort(key=lambda p: p.quality_score, reverse=True)
    result = scored[:max_posts]

    log.info(
        f"{tag} → {len(result)} posts from {len(posts)} candidates "
        f"(floor={quality_floor}, cap={max_posts})"
    )
    return result


# ── Source detection ───────────────────────────────────────────────────────────

def detect_source(
    platform: str,
    url: str,
    subreddit: str = "",
    channel_name: str = "",
    channel_url: str = "",
) -> SourceInfo | None:
    """
    Identify the source page/channel/profile a post came from.

    Returns a SourceInfo if a meaningful source can be identified, None otherwise.

    Args:
        platform:     Platform string from FetchedContent.source_platform
        url:          Original post URL
        subreddit:    Pre-extracted subreddit (for Reddit posts)
        channel_name: Pre-extracted channel name (for YouTube videos)
        channel_url:  Pre-extracted channel URL (for YouTube videos)
    """
    if platform == "Reddit":
        return _detect_reddit_source(url, subreddit)
    elif platform == "YouTube":
        return _detect_youtube_source(url, channel_name, channel_url)
    elif platform in ("Instagram", "TikTok", "Facebook"):
        return _detect_blocked_source(platform, url)
    elif platform == "X/Twitter":
        return _detect_blocked_source(platform, url)
    elif platform == "LinkedIn":
        return _detect_linkedin_source(url)
    return None


def _detect_reddit_source(url: str, subreddit: str) -> SourceInfo | None:
    from bot.data.relevant_subreddits import is_relevant_subreddit

    if not subreddit:
        # Try to parse from URL
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0].lower() == "r":
            subreddit = parts[1]

    if not subreddit:
        return None

    relevant, track = is_relevant_subreddit(subreddit)
    if not relevant:
        return None

    return SourceInfo(
        platform="Reddit",
        source_id=subreddit,
        source_name=f"r/{subreddit}",
        source_url=f"https://www.reddit.com/r/{subreddit}/",
        track=track,
        ranking_label="upvotes",
        scrape_supported=True,
    )


def _detect_youtube_source(
    url: str,
    channel_name: str,
    channel_url: str,
) -> SourceInfo | None:
    """
    Identify a YouTube channel from a video.

    We have channel_name from the transcript extraction metadata.
    Batch scraping requires fetching the channel's video list — currently
    limited by the youtubesearchpython library's httpx compatibility issue.
    The architecture is in place; full execution resumes once that is resolved
    or replaced with a YouTube Data API key.
    """
    if not channel_name and not channel_url:
        return None

    # Determine track from channel name keywords
    track = _guess_track_from_text(channel_name)
    handle = channel_url.lstrip("https://www.youtube.com/").lstrip("@") if channel_url else channel_name
    display = f"@{handle}" if not handle.startswith("@") else handle

    return SourceInfo(
        platform="YouTube",
        source_id=handle,
        source_name=display,
        source_url=channel_url or f"https://www.youtube.com/@{handle}",
        track=track,
        ranking_label="views",
        scrape_supported=False,
        unsupported_reason=(
            "YouTube batch scraping requires a YouTube Data API key. "
            "Send individual video URLs to ingest them one by one."
        ),
    )


def _detect_blocked_source(platform: str, url: str) -> SourceInfo | None:
    """Identify source for platforms we cannot batch-scrape."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    # Extract username/handle from URL path
    username = ""
    if platform in ("Instagram", "TikTok"):
        # instagram.com/{username}/p/{postid}/  or  tiktok.com/@{username}/video/{id}
        if path_parts:
            raw = path_parts[0].lstrip("@")
            if raw and raw not in ("p", "reel", "tv", "video"):
                username = raw
    elif platform == "X/Twitter":
        # twitter.com/{handle}/status/{id}
        if path_parts and path_parts[0] not in ("i", "intent", "share"):
            username = path_parts[0]

    if not username:
        return None

    reasons = {
        "Instagram":  "Instagram blocks automated scraping. Send posts manually.",
        "TikTok":     "TikTok blocks automated scraping. Send posts manually.",
        "X/Twitter":  "X/Twitter batch access requires a paid API subscription.",
        "Facebook":   "Facebook requires login for all content access.",
    }
    ranking = {
        "Instagram":  "likes + comments",
        "TikTok":     "likes + views",
        "X/Twitter":  "likes + retweets",
        "Facebook":   "reactions",
    }

    track = _guess_track_from_text(username)

    return SourceInfo(
        platform=platform,
        source_id=username,
        source_name=f"@{username}",
        source_url=url,
        track=track,
        ranking_label=ranking.get(platform, "engagement"),
        scrape_supported=False,
        unsupported_reason=reasons.get(platform, "Platform not supported for batch scraping."),
    )


def _detect_linkedin_source(url: str) -> SourceInfo | None:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    # linkedin.com/in/{person}  or  linkedin.com/company/{name}
    if len(parts) >= 2 and parts[0] in ("in", "company"):
        source_id = parts[1]
        track = _guess_track_from_text(source_id)
        return SourceInfo(
            platform="LinkedIn",
            source_id=source_id,
            source_name=f"LinkedIn: {source_id}",
            source_url=url,
            track=track,
            ranking_label="reactions",
            scrape_supported=False,
            unsupported_reason=(
                "LinkedIn post lists require Firecrawl credits and are rate-limited. "
                "Send individual post URLs to ingest them."
            ),
        )
    return None


def _guess_track_from_text(text: str) -> str:
    """Quick heuristic to guess kalshi vs traditional from any text."""
    lower = text.lower()
    kalshi_hints = {"kalshi", "prediction", "polymarket", "15min", "btcbet", "updown"}
    if any(h in lower for h in kalshi_hints):
        return "kalshi"
    return "traditional"


# ── Reddit scraping (full support) ────────────────────────────────────────────

async def fetch_source_top_posts(
    source: SourceInfo,
    limit: int = 25,
    min_score: int = 5,
) -> list[SourcePost]:
    """
    Fetch top posts from a source. Only Reddit is fully supported right now.
    Returns empty list for unsupported platforms.
    """
    if not source.scrape_supported:
        return []

    if source.platform == "Reddit":
        return await _fetch_reddit_top(source.source_id, limit, min_score)

    return []


async def _fetch_reddit_top(
    subreddit: str,
    limit: int,
    min_score: int,
) -> list[SourcePost]:
    """Fetch top posts from a subreddit using the Reddit JSON API."""
    api_url = (
        f"https://www.reddit.com/r/{subreddit}/top.json"
        f"?limit={min(limit, 100)}&t=all"
    )
    log.info(f"[dim]Source scraper: r/{subreddit} top posts[/]")

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": _REDDIT_USER_AGENT},
        ) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()

        posts: list[SourcePost] = []
        for child in data.get("data", {}).get("children", []):
            if child.get("kind") != "t3":
                continue
            post = child["data"]
            score = post.get("score", 0)
            if score < min_score:
                continue

            permalink    = post.get("permalink", "")
            selftext     = post.get("selftext", "")
            num_comments = post.get("num_comments", 0)
            body_length  = len(selftext)
            is_self      = post.get("is_self", False)
            link_url     = post.get("url", "")

            # Detect embedded media (YouTube links in body, external link posts)
            has_media = (
                "youtube.com" in selftext or "youtu.be" in selftext
                or (not is_self and "reddit.com" not in link_url
                    and ("youtube" in link_url or "youtu.be" in link_url))
            )

            posts.append(SourcePost(
                url=f"https://www.reddit.com{permalink.rstrip('/')}",
                title=post.get("title", "").strip(),
                score=score,
                score_label="upvotes",
                platform="Reddit",
                num_comments=num_comments,
                body_length=body_length,
                has_media=has_media,
                extra={
                    "author":       post.get("author", ""),
                    "post_id":      post.get("id", ""),
                    "is_self":      is_self,
                    "upvote_ratio": post.get("upvote_ratio", 0),
                },
            ))

        posts.sort(key=lambda p: p.score, reverse=True)
        log.info(f"[dim]Source scraper: r/{subreddit} → {len(posts)} posts[/]")
        return posts

    except Exception as e:
        log.warning(f"[dim]Source scraper: r/{subreddit} failed — {e}[/]")
        return []
