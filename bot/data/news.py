"""News collection and LLM-powered market analysis."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import anthropic

from bot.config import settings
from bot.utils.logger import log


@dataclass
class NewsArticle:
    title: str
    description: str
    source: str
    url: str
    published_at: datetime | None = None
    content: str = ""


@dataclass
class NewsAnalysis:
    market_id: str
    market_title: str
    articles: list[NewsArticle]
    estimated_probability: float  # 0-1
    confidence: float  # 0-1
    reasoning: str
    direction: str = "UNCHANGED"  # "HIGHER", "LOWER", "UNCHANGED"
    timestamp: datetime = field(default_factory=datetime.utcnow)


# Noise words to strip when building search queries
_NOISE_WORDS = {
    "will", "does", "is", "are", "was", "were", "do", "has", "have", "had",
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "by", "from", "with", "about", "over", "under", "above", "below",
    "before", "after", "during", "between", "through", "into", "onto",
    "there", "it", "its", "be", "been", "being",
}

# Topic-specific keyword expansions
_TOPIC_EXPANSIONS = {
    "bitcoin": "BTC price",
    "btc": "BTC price",
    "ethereum": "ETH price",
    "crypto": "cryptocurrency",
    "fed": "Federal Reserve interest rate",
    "federal reserve": "Fed rate decision",
    "inflation": "CPI inflation rate",
    "interest rate": "Fed rate hike cut",
    "unemployment": "jobs report nonfarm",
    "gdp": "economic growth GDP",
    "election": "election vote polling",
    "recession": "economic downturn recession",
    "oil": "crude oil WTI price",
    "nasdaq": "tech stocks Nasdaq",
    "s&p": "S&P 500 stocks",
}


def _build_query(market_title: str) -> str:
    """Build a focused news search query from a market title."""
    words = [w for w in re.split(r"\W+", market_title.lower()) if w and w not in _NOISE_WORDS]
    title_lower = market_title.lower()

    expansion = ""
    for keyword, expansion_text in _TOPIC_EXPANSIONS.items():
        if keyword in title_lower:
            expansion = expansion_text
            break

    if expansion:
        query = f"{expansion} {' '.join(words[:4])}"
    else:
        query = " ".join(words[:7])

    return query[:80]


def _freshness_label(published_at: datetime | None) -> str:
    """Return a freshness label for an article based on its age."""
    if published_at is None:
        return "[RECENT]"
    now = datetime.now(timezone.utc)
    try:
        pub = published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_hours = (now - pub).total_seconds() / 3600
        if age_hours < 1:
            return "[FRESH]"
        elif age_hours < 6:
            return "[RECENT]"
        else:
            return "[OLD]"
    except Exception:
        return "[RECENT]"


def _is_llm_billing_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "credit balance",
            "credit_balance_exhausted",
            "insufficient_quota",
            "plans & billing",
        )
    )


class NewsCollector:
    """Collects news and uses Claude to analyze probability impact on markets."""

    CACHE_TTL_MINUTES = 15

    def __init__(self, api_key: str = "", anthropic_key: str = ""):
        self.news_api_key = api_key or settings.NEWS_API_KEY
        self.anthropic_key = anthropic_key or settings.ANTHROPIC_API_KEY
        self._client = httpx.AsyncClient(timeout=30.0)
        self._claude = None
        self._llm_disabled_reason = ""
        if self.anthropic_key:
            self._claude = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
        # Cache: market_id -> (cached_at, NewsAnalysis)
        self._cache: dict[str, tuple[datetime, NewsAnalysis]] = {}

    def _get_cached(self, market_id: str) -> NewsAnalysis | None:
        """Return cached analysis if still fresh (within TTL)."""
        entry = self._cache.get(market_id)
        if entry is None:
            return None
        cached_at, analysis = entry
        age_minutes = (datetime.utcnow() - cached_at).total_seconds() / 60
        if age_minutes < self.CACHE_TTL_MINUTES:
            return analysis
        return None

    def _set_cache(self, market_id: str, analysis: NewsAnalysis) -> None:
        self._cache[market_id] = (datetime.utcnow(), analysis)

    async def fetch_news(self, query: str, limit: int = 10) -> list[NewsArticle]:
        """Fetch news articles related to a query."""
        articles: list[NewsArticle] = []

        # Try NewsAPI first
        if self.news_api_key:
            try:
                resp = await self._client.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": query,
                        "sortBy": "publishedAt",
                        "pageSize": limit,
                        "apiKey": self.news_api_key,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for a in data.get("articles", []):
                        pub = None
                        if a.get("publishedAt"):
                            try:
                                pub = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00"))
                            except Exception:
                                pass
                        articles.append(NewsArticle(
                            title=a.get("title", ""),
                            description=a.get("description", ""),
                            source=a.get("source", {}).get("name", ""),
                            url=a.get("url", ""),
                            published_at=pub,
                            content=a.get("content", ""),
                        ))
            except Exception as e:
                log.warning(f"NewsAPI fetch failed: {e}")

        # Fallback: Google News RSS
        if not articles:
            try:
                resp = await self._client.get(
                    f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
                )
                if resp.status_code == 200:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:limit]:
                        articles.append(NewsArticle(
                            title=item.findtext("title", ""),
                            description="",
                            source=item.findtext("source", "") or "Google News",
                            url=item.findtext("link", ""),
                            content="",
                        ))
            except Exception as e:
                log.warning(f"RSS fallback failed: {e}")

        # Sort by freshness — newest first
        def freshness_sort(a: NewsArticle) -> float:
            if a.published_at is None:
                return 0.0
            try:
                pub = a.published_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                return -((datetime.now(timezone.utc) - pub).total_seconds())
            except Exception:
                return 0.0

        articles.sort(key=freshness_sort)
        return articles

    async def analyze_market_news(
        self,
        market_title: str,
        market_id: str,
        current_price: float,
        articles: list[NewsArticle] | None = None,
    ) -> NewsAnalysis | None:
        """Use Claude to analyze how recent news affects a market's probability."""

        # Cache check
        cached = self._get_cached(market_id)
        if cached is not None:
            log.info(f"[dim]Using cached news analysis for: {market_title[:50]}[/]")
            return cached

        if self._llm_disabled_reason or not self._claude:
            return None

        # Fetch news if not provided
        if articles is None:
            query = _build_query(market_title)
            log.info(f"[dim]Fetching news: '{query}'[/]")
            articles = await self.fetch_news(query, limit=8)

        if not articles:
            return None

        # Format top 5 articles with freshness labels
        formatted = []
        for a in articles[:5]:
            label = _freshness_label(a.published_at)
            preview = a.description or a.content[:200] or "(no preview)"
            formatted.append(f"{label} **{a.title}** ({a.source})\n{preview}")
        news_text = "\n\n".join(formatted)

        prompt = f"""You are an expert prediction market analyst.

Market: "{market_title}"
Current market price (implied probability): {current_price:.1%}

Recent news (FRESH = <1hr ago, RECENT = <6hr, OLD = older):
{news_text}

Analyze: Does the news suggest the market is mispriced?
- FRESH/RECENT articles matter most — they may not be priced in yet
- Compare news to the current {current_price:.1%} implied probability

Reply in EXACTLY this format (no other text):
PROBABILITY: <0.0-1.0>
CONFIDENCE: <0.0-1.0>
DIRECTION: <HIGHER|LOWER|UNCHANGED>
REASONING: <1-2 sentences>"""

        try:
            response = await self._claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            prob = 0.5
            conf = 0.3
            reasoning = ""
            direction = "UNCHANGED"

            for line in text.strip().split("\n"):
                line = line.strip()
                if line.startswith("PROBABILITY:"):
                    try:
                        prob = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("CONFIDENCE:"):
                    try:
                        conf = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("DIRECTION:"):
                    raw_dir = line.split(":", 1)[1].strip().upper()
                    if raw_dir in ("HIGHER", "LOWER", "UNCHANGED"):
                        direction = raw_dir
                elif line.startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()

            # Sanity-check: direction should match probability vs current price
            if direction == "HIGHER" and prob < current_price:
                prob = max(prob, current_price + 0.03)
            elif direction == "LOWER" and prob > current_price:
                prob = min(prob, current_price - 0.03)

            analysis = NewsAnalysis(
                market_id=market_id,
                market_title=market_title,
                articles=articles,
                estimated_probability=max(0.0, min(1.0, prob)),
                confidence=max(0.0, min(1.0, conf)),
                reasoning=reasoning,
                direction=direction,
            )

            self._set_cache(market_id, analysis)
            log.info(
                f"[dim]News → prob={prob:.0%} conf={conf:.0%} dir={direction} | {market_title[:50]}[/]"
            )
            return analysis

        except Exception as e:
            if _is_llm_billing_error(e):
                self._llm_disabled_reason = "llm_credits_exhausted"
                self._claude = None
                log.warning(
                    "News analysis disabled for this process: the LLM account has no credits. "
                    "The BTC day-trader loop continues without it."
                )
                return None
            log.error(f"Claude analysis failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()
