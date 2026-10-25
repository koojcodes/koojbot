"""
Tag parser for Telegram messages — resolves #tags to intent overrides, series
tickers, and search hints.

The bot now auto-classifies all content (URLs and text) using a Claude call,
so intent tags are OPTIONAL. You only need them if you want to override the
automatic routing decision.

Intent tags (optional overrides — mutually exclusive, first one wins):
    #learn    → force knowledge ingestion mode regardless of classifier result
    #news     → force signal/research pipeline regardless of classifier result

Market tags (still useful — can combine with anything):
    #btc15m   → KXBTC15M  (pins BTC 15-minute contract into the research context)
    #kxbtc15m → KXBTC15M
    #btc      → broad BTC search hint for the Researcher agent
    #eth      → broad ETH search hint
    #crypto   → broad crypto search hint
    #fed      → Fed/rates search hint
    #macro    → macro search hint

Examples (tags now optional):
    "https://reddit.com/r/algotrading/comments/..."
    → auto-classified → likely 'knowledge' → filed into KB

    "#btc15m https://x.com/breaking-btc-news"
    → auto-classified → likely 'signal' → signal pipeline, BTC15M pinned

    "#learn https://youtube.com/watch?v=..."
    → explicit override → always goes to knowledge ingestion

    "#news BTC rejected at 67k resistance"
    → explicit override → always goes to signal pipeline
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Intent tags ────────────────────────────────────────────────────────────────

INTENT_TAGS: dict[str, str] = {
    "learn":   "learn",    # knowledge ingestion — auto-detect track (traditional vs kalshi)
    "kalshi":  "kalshi",   # force Kalshi prediction market track for KB ingestion
    "news":    "news",     # news/signal analysis pipeline
}

# ── Alias → series ticker mapping ─────────────────────────────────────────────
# None = no specific series, just use the tag as a search hint for the Researcher

MARKET_ALIASES: dict[str, str | None] = {
    # BTC 15-minute
    "btc15m":   "KXBTC15M",
    "kxbtc15m": "KXBTC15M",
    "btc15":    "KXBTC15M",

    # Broad searches (no specific series — guides Researcher topic)
    "btc":      None,
    "bitcoin":  None,
    "eth":      None,
    "ethereum": None,
    "crypto":   None,
    "polymarket": None,
    "stocks":   None,
    "etf":      None,
    "equities": None,
    "fed":      None,
    "rates":    None,
    "cpi":      None,
    "macro":    None,
}

# Human-readable display names for confirmed series tickers
SERIES_DISPLAY_NAMES: dict[str, str] = {
    "KXBTC15M": "BTC 15-Minute Price Direction",
    "KXETH15M": "ETH 15-Minute Price Direction",
}

# Series tickers that only make sense with a URL (social media post / news link).
# Plain text is too slow and imprecise for these ultra-short windows.
URL_ONLY_SERIES: set[str] = {
    "KXBTC15M",
    "KXETH15M",
}

_TAG_RE = re.compile(r"#(\w+)", re.IGNORECASE)


@dataclass
class ParsedTags:
    """Result of parsing tags from a Telegram message."""
    intent: str | None              # "learn" | "news" | None
    series_tickers: list[str]       # confirmed series tickers (e.g. ["KXBTC15M"])
    search_hints: list[str]         # broad search topics (e.g. ["btc", "crypto"])
    raw_tags: list[str]             # all recognized tag strings
    clean_text: str                 # message with all known tags stripped out

    @property
    def has_market_tags(self) -> bool:
        return bool(self.series_tickers or self.search_hints)

    @property
    def has_tags(self) -> bool:
        """True if any recognized tag was found (intent OR market)."""
        return bool(self.intent or self.series_tickers or self.search_hints)

    def confirmation_line(self) -> str:
        """Build a human-readable confirmation string for Telegram."""
        parts = []
        if self.intent == "learn":
            parts.append("📚 *Mode: Learn* — filing to knowledge base \\(auto\\-detect track\\)")
        elif self.intent == "kalshi":
            parts.append("🎯 *Mode: Kalshi* — filing to prediction market knowledge base")
        elif self.intent == "news":
            parts.append("📰 *Mode: News* — running research\\+signal pipeline")
        for ticker in self.series_tickers:
            name = SERIES_DISPLAY_NAMES.get(ticker, ticker)
            parts.append(f"📌 *{name}* `[{ticker}]`")
        for hint in self.search_hints:
            parts.append(f"🔍 Broad search: *{hint}*")
        return "\n".join(parts) if parts else ""


def parse_tags(text: str) -> ParsedTags:
    """
    Extract #tags from a message. Recognizes intent tags (#learn, #news) and
    market tags (#btc15m, #btc, etc.). Returns cleaned text with all known tags removed.

    Examples:
        "#learn https://youtube.com/watch?v=abc123"
        → intent="learn", clean_text="https://youtube.com/watch?v=abc123"

        "#news #btc15m https://x.com/post/123"
        → intent="news", series_tickers=["KXBTC15M"], clean_text="https://x.com/post/123"

        "#btc15m BTC is about to pop"
        → intent=None, series_tickers=["KXBTC15M"], clean_text="BTC is about to pop"
    """
    found_tags = _TAG_RE.findall(text)
    clean_text = _TAG_RE.sub("", text).strip()
    # Collapse multiple spaces left behind by tag removal
    clean_text = re.sub(r" {2,}", " ", clean_text).strip()

    intent: str | None = None
    series_tickers: list[str] = []
    search_hints: list[str] = []
    raw_tags: list[str] = []

    for tag in found_tags:
        tag_lower = tag.lower()

        # Check intent tags first
        if tag_lower in INTENT_TAGS:
            if intent is None:   # first intent tag wins
                intent = INTENT_TAGS[tag_lower]
            raw_tags.append(tag_lower)
            continue

        # Check market aliases
        if tag_lower in MARKET_ALIASES:
            raw_tags.append(tag_lower)
            resolved = MARKET_ALIASES[tag_lower]
            if resolved:
                if resolved not in series_tickers:
                    series_tickers.append(resolved)
            else:
                if tag_lower not in search_hints:
                    search_hints.append(tag_lower)
            continue

        # Unknown tag — ignore (e.g. hashtags in the content itself)

    return ParsedTags(
        intent=intent,
        series_tickers=series_tickers,
        search_hints=search_hints,
        raw_tags=raw_tags,
        clean_text=clean_text,
    )
