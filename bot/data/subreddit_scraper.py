"""
Subreddit scraper — fetches top posts from a Reddit subreddit for batch KB ingestion.

Uses Reddit's public JSON API (no auth required):
  GET https://www.reddit.com/r/{subreddit}/top.json?limit=N&t=all

Rate limiting: callers are responsible for sleeping between fetch_subreddit_top_posts
calls when processing multiple subreddits. For single subreddit ingestion, the
1.5s sleep between individual post fetches in the batch processor is sufficient.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from bot.telegram_bot.content_fetcher import _REDDIT_USER_AGENT
from bot.utils.logger import log


@dataclass
class SubredditPost:
    """Lightweight summary of a Reddit post from the listing API."""
    url:       str    # canonical post URL: https://www.reddit.com/r/.../comments/.../
    title:     str
    score:     int
    author:    str
    post_id:   str
    is_self:   bool   # True = text post, False = link post


async def fetch_subreddit_top_posts(
    subreddit: str,
    limit: int = 25,
    min_score: int = 5,
    time_filter: str = "all",
) -> list[SubredditPost]:
    """
    Fetch the top posts from a subreddit, sorted by score descending.

    Args:
        subreddit:   Subreddit name without 'r/' prefix (e.g. "KalshiBTCupdown15")
        limit:       Max number of posts to fetch from Reddit (before score filter)
        min_score:   Minimum upvote score — posts below this are excluded
        time_filter: Reddit time period: "all" | "year" | "month" | "week" | "day"

    Returns:
        List of SubredditPost objects, sorted by score descending.
        Returns empty list on any error.
    """
    api_url = (
        f"https://www.reddit.com/r/{subreddit}/top.json"
        f"?limit={min(limit, 100)}&t={time_filter}"
    )

    log.info(f"[dim]Subreddit scraper: fetching top/{time_filter} from r/{subreddit}[/]")

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": _REDDIT_USER_AGENT},
        ) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()

        children = data.get("data", {}).get("children", [])
        posts: list[SubredditPost] = []

        for child in children:
            if child.get("kind") != "t3":
                continue

            post = child["data"]
            score = post.get("score", 0)
            if score < min_score:
                continue

            # Build canonical post URL from permalink
            permalink = post.get("permalink", "")
            url = f"https://www.reddit.com{permalink.rstrip('/')}"

            posts.append(SubredditPost(
                url=url,
                title=post.get("title", "").strip(),
                score=score,
                author=post.get("author", ""),
                post_id=post.get("id", ""),
                is_self=post.get("is_self", False),
            ))

        # Reddit already returns sorted by score, but make it explicit
        posts.sort(key=lambda p: p.score, reverse=True)

        log.info(
            f"[dim]Subreddit scraper: r/{subreddit} → "
            f"{len(posts)} posts (≥{min_score} score)[/]"
        )
        return posts

    except httpx.HTTPStatusError as e:
        log.warning(
            f"[dim]Subreddit scraper: r/{subreddit} HTTP {e.response.status_code}[/]"
        )
        return []
    except Exception as e:
        log.warning(f"[dim]Subreddit scraper: r/{subreddit} failed — {e}[/]")
        return []
