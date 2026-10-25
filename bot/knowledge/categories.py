"""
Knowledge category definitions — two independent tracks.

  "traditional"  — chart patterns, indicators, entries, exits, risk management.
                   Applies to traditional market trading (stocks, futures, spot crypto).
                   Connected to no live exchange yet but accumulates knowledge for future use.

  "kalshi"       — Kalshi BTC 15-minute prediction market domain knowledge.
                   Binary YES/NO contracts on BTC price direction over a 15-minute window.
                   This is the bot's ACTIVE trading track.

IMPORTANT DISTINCTION:
  Traditional trading = buying/selling actual assets based on price action, chart patterns,
                        technical indicators, support/resistance, etc.

  Kalshi trading      = betting on whether BTC will be above/below a strike price in 15 minutes.
                        The "edge" comes from crowd mispricing, not from chart patterns.
                        FVG, liquidity grabs, stop hunts — these DO NOT directly apply here.
                        What matters: does the market price (YES/NO) reflect the true probability?
"""
from __future__ import annotations

# ── Traditional day trading categories ────────────────────────────────────────

TRADITIONAL_CATEGORIES: dict[str, dict] = {
    "entry_setup": {
        "description": (
            "Specific conditions, indicators, and patterns that signal a good entry point. "
            "Covers trigger conditions, confirmation signals, and optimal timing."
        ),
        "keywords": [
            "entry", "enter", "buy signal", "long signal", "short signal",
            "trigger", "setup", "when to buy", "breakout", "bounce",
            "rsi oversold", "support bounce", "confirmation", "fvg", "fair value gap",
            "liquidity grab", "liquidity sweep", "snap back",
        ],
    },
    "exit_management": {
        "description": (
            "Rules for taking profit, cutting losses, and managing open positions. "
            "Covers stop loss placement, take profit targets, trailing stops, and time-based exits."
        ),
        "keywords": [
            "exit", "stop loss", "take profit", "target", "close position",
            "trailing stop", "cut losses", "when to sell", "tp", "sl",
            "risk reward", "r:r", "lock in profit",
        ],
    },
    "market_structure": {
        "description": (
            "How to read price action, identify support and resistance levels, "
            "recognize chart patterns, and understand trend context."
        ),
        "keywords": [
            "support", "resistance", "trend", "structure", "higher high", "lower low",
            "chart pattern", "head and shoulders", "double top", "consolidation",
            "range", "breakout", "price action", "candle", "wick", "4 hour", "4h",
            "previous high", "previous low", "range high", "range low",
        ],
    },
    "indicator_usage": {
        "description": (
            "How to correctly interpret and apply technical indicators. "
            "Covers RSI, MACD, Bollinger Bands, volume indicators, and moving averages."
        ),
        "keywords": [
            "rsi", "macd", "bollinger", "ema", "sma", "moving average",
            "indicator", "oscillator", "volume", "vwap", "atr", "stochastic",
            "divergence", "overbought", "oversold", "signal line",
        ],
    },
    "risk_management": {
        "description": (
            "Position sizing rules, maximum loss limits, portfolio-level risk controls, "
            "and capital preservation strategies for traditional trading."
        ),
        "keywords": [
            "position size", "risk management", "portfolio", "capital", "max loss",
            "daily loss", "drawdown", "leverage", "sizing", "allocation",
            "never risk more", "percent of account", "kelly criterion",
            "backtest", "forward test", "risk to reward",
        ],
    },
    "market_timing": {
        "description": (
            "When to trade and when to sit out. Covers trading session effects, "
            "news-driven volatility windows, low-liquidity periods, and pre/post-market behavior."
        ),
        "keywords": [
            "timing", "session", "market open", "market close", "news event",
            "fomc", "cpi", "earnings", "avoid", "sit out", "wait",
            "pre-market", "after hours", "liquidity", "volume window",
        ],
    },
    "btc_specific": {
        "description": (
            "BTC-specific behaviors in traditional/spot markets: funding rates, liquidation "
            "levels, on-chain signals, halving cycle effects, and crypto market microstructure."
        ),
        "keywords": [
            "bitcoin", "btc", "crypto", "funding rate", "liquidation", "long liquidation",
            "short squeeze", "halving", "on-chain", "whale", "exchange outflow",
            "open interest", "perpetual", "spot", "dominance",
        ],
    },
}

# ── Kalshi prediction market categories ───────────────────────────────────────

KALSHI_CATEGORIES: dict[str, dict] = {
    "crowd_pricing": {
        "description": (
            "How retail bettors and market makers systematically misprice Kalshi BTC contracts. "
            "Covers anchoring bias near round numbers (50c), recency bias after big moves, "
            "overconfidence near contract expiry, and when the crowd is reliably wrong."
        ),
        "keywords": [
            "crowd", "bettor", "overpriced", "underpriced", "mispriced", "bias",
            "round number", "anchoring", "recency", "retail", "market maker",
            "probability", "implied probability", "fair value", "pricing error",
            "prediction market", "overconfident", "herd",
        ],
    },
    "btc_signal_relevance": {
        "description": (
            "Which BTC price signals (momentum, RSI, volume, news, funding rate) actually "
            "have predictive power for 15-minute binary outcomes on Kalshi. Focuses on "
            "signal reliability, lead time relative to the contract window, and noise filters."
        ),
        "keywords": [
            "momentum", "signal", "predict", "direction", "15 minute", "15m",
            "btc move", "lead time", "correlation", "noise", "reliable",
            "rsi divergence", "volume spike", "news catalyst", "funding rate signal",
            "short term", "micro momentum", "5 minute", "1 minute",
        ],
    },
    "contract_mechanics": {
        "description": (
            "How Kalshi BTC 15-minute contracts work: fee structure, spread, resolution "
            "rules (CF Benchmarks BRTI), strike price setting, contract window timing, "
            "and how taker fees affect the breakeven probability required."
        ),
        "keywords": [
            "fee", "spread", "resolution", "strike", "rollover", "contract window",
            "breakeven", "taker fee", "settlement", "expiry", "brti",
            "cf benchmarks", "binary", "kalshi mechanics", "yes price", "no price",
            "0.03", "maker rebate",
        ],
    },
    "edge_identification": {
        "description": (
            "Frameworks for finding genuine positive expected value in Kalshi BTC markets: "
            "minimum probability delta after fees, conditions that reliably create edge, "
            "when NOT to bet, edge decay as contract nears expiry, and avoiding false signals."
        ),
        "keywords": [
            "edge", "expected value", "ev", "kelly", "threshold",
            "minimum edge", "fee-adjusted", "when to bet", "skip", "pass",
            "confidence", "edge decay", "late entry", "early entry",
            "8 percent", "mispricing", "probability delta", "net edge",
        ],
    },
    "kalshi_risk": {
        "description": (
            "Risk management specific to binary prediction market betting: max exposure "
            "per contract, daily loss limits, bankroll management for binary bets, "
            "avoiding tilt after losing streaks, and correlation between consecutive contracts."
        ),
        "keywords": [
            "bankroll", "max bet", "daily limit", "loss limit", "binary risk",
            "consecutive losses", "tilt", "risk per trade", "kelly fraction",
            "overbetting", "ruin", "exposure", "max exposure", "10 percent",
            "position limit", "prediction market risk",
        ],
    },
}

# ── Name lists ─────────────────────────────────────────────────────────────────

TRADITIONAL_CATEGORY_NAMES = list(TRADITIONAL_CATEGORIES.keys())
KALSHI_CATEGORY_NAMES      = list(KALSHI_CATEGORIES.keys())

# Legacy alias — code that still imports CATEGORY_NAMES gets traditional names
CATEGORY_NAMES = TRADITIONAL_CATEGORY_NAMES

# Combined lookup for category_description() regardless of track
_ALL_CATEGORIES: dict[str, dict] = {**TRADITIONAL_CATEGORIES, **KALSHI_CATEGORIES}

# ── Subreddit keyword families ─────────────────────────────────────────────────
# Used by detect_track() to classify content by subreddit name.
# Mirrors the sets in relevant_subreddits.py but lives here to avoid a
# circular import (categories ← knowledge_learner ← bot.py).

_KALSHI_KEYWORDS: frozenset[str] = frozenset({
    "kalshi", "prediction", "polymarket", "15min", "15m",
    "updown", "upordown", "btcbet", "binarybet", "eventmarket",
})

_TRADITIONAL_KEYWORDS: frozenset[str] = frozenset({
    "trading", "trade", "daytrading", "algotrading",
    "btc", "bitcoin", "crypto", "technicalanalysis",
    "stockmarket", "investing", "futures", "options",
    "forex", "chart",
})

# ── Agent knowledge profiles ───────────────────────────────────────────────────

AGENT_KNOWLEDGE_PROFILES: dict[str, dict] = {
    "btc15m_kalshi": {
        "track": "kalshi",
        "label": "BTC 15m Kalshi specialist",
    },
    "polymarket_news": {
        "track": "kalshi",
        "label": "Polymarket news/event specialist",
    },
    "stocks_etf_paper_shell": {
        "track": "traditional",
        "label": "Stocks/ETF specialist (paper shell)",
    },
}


def get_agent_track(agent_id: str) -> str:
    return AGENT_KNOWLEDGE_PROFILES.get(agent_id, {}).get("track", "traditional")


def get_agent_category_names(agent_id: str) -> list[str]:
    return KALSHI_CATEGORY_NAMES if get_agent_track(agent_id) == "kalshi" else TRADITIONAL_CATEGORY_NAMES


def detect_target_agent(text: str, subreddit: str = "", hint: str = "auto") -> str:
    """
    Resolve the target knowledge agent for an ingestion event.

    hint can be:
      - explicit agent_id (e.g. "btc15m_kalshi")
      - legacy track hint ("kalshi", "traditional")
      - "auto"
    """
    if hint in AGENT_KNOWLEDGE_PROFILES:
        return hint
    if hint == "kalshi":
        return "btc15m_kalshi"
    if hint == "traditional":
        return "stocks_etf_paper_shell"

    blob = f"{subreddit} {text}".lower()

    # Strong polymarket-first routing
    if "polymarket" in blob:
        return "polymarket_news"

    # Kalshi/BTC15m specialist routing
    kalshi_signals = (
        "kalshi",
        "kxbtc15m",
        "btc15m",
        "15-minute",
        "15 minute",
        "upordown15",
        "updown15",
    )
    if any(token in blob for token in kalshi_signals):
        return "btc15m_kalshi"

    # Traditional stocks/ETF shell routing
    traditional_signals = (
        "stocks",
        "stock market",
        "etf",
        "equities",
        "spy",
        "qqq",
        "nasdaq",
    )
    if any(token in blob for token in traditional_signals):
        return "stocks_etf_paper_shell"

    # Fallback through track detection
    return "btc15m_kalshi" if detect_track(text, subreddit=subreddit) == "kalshi" else "stocks_etf_paper_shell"


# ── Helpers ────────────────────────────────────────────────────────────────────

def category_description(category: str) -> str:
    """Return the human-readable description for a category (either track)."""
    return _ALL_CATEGORIES.get(category, {}).get("description", category)


def detect_categories(text: str, track: str = "traditional") -> list[str]:
    """
    Heuristically detect which categories a piece of text likely covers.
    track: "traditional" | "kalshi"
    Returns categories ranked by keyword hit count (most relevant first).
    """
    registry = KALSHI_CATEGORIES if track == "kalshi" else TRADITIONAL_CATEGORIES
    text_lower = text.lower()
    scores: dict[str, int] = {cat: 0 for cat in registry}
    for cat, info in registry.items():
        for kw in info["keywords"]:
            if kw in text_lower:
                scores[cat] += 1
    return [
        cat for cat, score in sorted(scores.items(), key=lambda x: -x[1])
        if score > 0
    ]


def detect_track(text: str, subreddit: str = "") -> str:
    """
    Auto-detect whether content primarily belongs to 'kalshi' or 'traditional' track.

    If a subreddit name is provided, it is used as a strong prior:
      - Any Kalshi-related subreddit name → "kalshi" (bypasses content scoring)
      - Any traditional trading subreddit  → "traditional" (bypasses content scoring)

    Without a subreddit hint, keyword scoring is used. Kalshi wins only on a
    clear margin to avoid mis-routing general BTC content that uses both
    trading vocabularies.  Ambiguous content defaults to 'traditional'.
    """
    # Subreddit-based routing takes priority — the community context is the
    # strongest signal.  Use the same keyword families as the registry.
    if subreddit:
        sub_lower = subreddit.lower()
        if any(kw in sub_lower for kw in _KALSHI_KEYWORDS):
            return "kalshi"
        if any(kw in sub_lower for kw in _TRADITIONAL_KEYWORDS):
            return "traditional"

    # Fallback: score keyword hits in the post content
    text_lower = text.lower()
    kalshi_score = sum(
        1 for info in KALSHI_CATEGORIES.values()
        for kw in info["keywords"]
        if kw in text_lower
    )
    trad_score = sum(
        1 for info in TRADITIONAL_CATEGORIES.values()
        for kw in info["keywords"]
        if kw in text_lower
    )
    return "kalshi" if kalshi_score > trad_score else "traditional"
