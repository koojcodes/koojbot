"""
Relevant subreddit registry — maps subreddit names to knowledge tracks.

Used by the subreddit scraper to decide whether to propose batch ingestion
after a user sends a Reddit post URL to the bot.

Two tracks:
  "kalshi"      — prediction market strategy, Kalshi-specific, binary bets
  "traditional" — day trading, technical analysis, spot/futures markets
"""
from __future__ import annotations

# ── Known relevant subreddits ─────────────────────────────────────────────────
# Keys are lowercase — lookups are normalised before checking.

KNOWN_RELEVANT_SUBREDDITS: dict[str, str] = {
    # ── Kalshi / prediction markets ──
    "kalshibtcupdown15":   "kalshi",   # KalshiBTCUpDown15
    "kalshibtcupordown15": "kalshi",   # KalshiBTCUporDown15 (actual sub name)
    "kalshibtcupordown":   "kalshi",
    "kalshibtcupdown":     "kalshi",
    "kalshimarkets":       "kalshi",
    "kalshi":              "kalshi",
    "predictionmarkets":   "kalshi",
    "polymarket":          "kalshi",
    "btcpredictions":      "kalshi",
    "predictionmarket":    "kalshi",
    "eventmarkets":        "kalshi",

    # ── Traditional trading ──
    "algotrading":         "traditional",
    "daytrading":          "traditional",
    "daytrade":            "traditional",
    "bitcointrading":      "traditional",
    "technicalanalysis":   "traditional",
    "cryptocurrency":      "traditional",
    "bitcoin":             "traditional",
    "btc":                 "traditional",
    "cryptomarkets":       "traditional",
    "cryptotrading":       "traditional",
    "btcmarkets":          "traditional",
    "trading":             "traditional",
    "stockmarket":         "traditional",
    "investing":           "traditional",
    "wallstreetbets":      "traditional",
    "options":             "traditional",
}

# ── Keyword-based fallback ────────────────────────────────────────────────────
# If a subreddit name contains any of these keywords it is considered relevant.
# The track is determined by which keyword family matches first.

_KALSHI_KEYWORDS: frozenset[str] = frozenset({
    "kalshi", "prediction", "polymarket", "15min", "15m",
    "updown", "btcbet", "binarybet",
})

_TRADITIONAL_KEYWORDS: frozenset[str] = frozenset({
    "trading", "trade", "daytrading", "algotrading",
    "btc", "bitcoin", "crypto", "technicalanalysis",
    "stockmarket", "investing", "futures", "options",
    "forex", "chart",
})


def is_relevant_subreddit(name: str) -> tuple[bool, str]:
    """
    Determine whether a subreddit is worth batch-scraping and which KB track
    its content should be filed under.

    Returns (is_relevant, track) where track is "kalshi" or "traditional".
    Returns (False, "") if the subreddit is not considered relevant.

    Order of precedence:
      1. Exact match in KNOWN_RELEVANT_SUBREDDITS
      2. Kalshi keyword substring in subreddit name
      3. Traditional keyword substring in subreddit name
      4. Not relevant
    """
    lower = name.lower()

    # 1. Exact known match
    if lower in KNOWN_RELEVANT_SUBREDDITS:
        return True, KNOWN_RELEVANT_SUBREDDITS[lower]

    # 2. Kalshi keyword match (higher specificity — checked first)
    for kw in _KALSHI_KEYWORDS:
        if kw in lower:
            return True, "kalshi"

    # 3. Traditional keyword match
    for kw in _TRADITIONAL_KEYWORDS:
        if kw in lower:
            return True, "traditional"

    return False, ""
