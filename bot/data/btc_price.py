"""
BTC real-time price feed using Binance public API (no auth required).

Provides:
  - Current BTC/USDT spot price
  - Recent 1-minute OHLCV candles (up to 30)
  - Momentum metrics: 1m, 5m, 10m returns and direction
  - Volatility estimate (std dev of 1m returns)

Uses Binance because:
  - No API key needed for public endpoints
  - Real-time data, updated every second
  - OHLCV history up to 1000 candles
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from bot.utils.logger import log


# Price source priority (Coinbase is a CF Benchmarks BRTI constituent — best BRTI proxy)
# Binance is geo-blocked in the US (451 error), so we prefer Coinbase/Kraken
_COINBASE_BASE   = "https://api.exchange.coinbase.com"
_KRAKEN_BASE     = "https://api.kraken.com/0/public"
_CACHE_TTL_SECONDS = 10   # cache price data for 10 seconds


@dataclass
class BTCCandle:
    """One 1-minute OHLCV candle."""
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def pct_change(self) -> float:
        """Candle return: (close - open) / open."""
        return (self.close - self.open) / self.open if self.open else 0.0

    @property
    def direction(self) -> str:
        return "UP" if self.close >= self.open else "DOWN"


@dataclass
class BTCSnapshot:
    """Point-in-time BTC price and momentum data."""
    price: float                          # current price (last trade)
    timestamp: datetime

    # Candle history (most recent last)
    candles: list[BTCCandle] = field(default_factory=list)

    # Momentum metrics (calculated from candles)
    return_1m: float = 0.0               # 1-candle return
    return_5m: float = 0.0               # 5-candle cumulative return
    return_10m: float = 0.0              # 10-candle cumulative return
    volatility_1h: float = 0.0           # std dev of 1m returns over last 60 candles
    rsi_14: float = 50.0                 # RSI(14) from 1m candles
    volume_ratio: float = 1.0            # last 5m volume vs prior 5m volume

    @property
    def momentum_direction(self) -> str:
        """Consensus momentum direction from 1m + 5m returns."""
        up = (self.return_1m > 0) + (self.return_5m > 0) + (self.return_10m > 0)
        if up >= 2:
            return "UP"
        elif up <= 1:
            return "DOWN"
        return "NEUTRAL"

    @property
    def momentum_strength(self) -> str:
        """'strong' | 'moderate' | 'weak' based on 5m return magnitude."""
        abs5 = abs(self.return_5m)
        if abs5 >= 0.003:    # ≥0.3%
            return "strong"
        elif abs5 >= 0.001:  # ≥0.1%
            return "moderate"
        return "weak"

    def vs_strike(self, strike: float) -> dict:
        """
        How far is the current price from a given strike price?
        Returns dict with pct_delta, direction, edge_strength.
        """
        if strike <= 0:
            return {"pct_delta": 0.0, "direction": "NEUTRAL", "edge_strength": "none"}
        delta = (self.price - strike) / strike
        direction = "HIGHER" if delta >= 0 else "LOWER"
        abs_delta = abs(delta)
        if abs_delta >= 0.003:
            strength = "strong"
        elif abs_delta >= 0.001:
            strength = "moderate"
        elif abs_delta >= 0.0003:
            strength = "weak"
        else:
            strength = "none"   # essentially at strike — no edge
        return {
            "pct_delta": delta,
            "direction": direction,
            "edge_strength": strength,
        }

    def summary(self, strike: float = 0.0) -> str:
        lines = [
            f"BTC: ${self.price:,.2f} | {self.timestamp.strftime('%H:%M:%S UTC')}",
            f"  1m: {self.return_1m:+.3%}  5m: {self.return_5m:+.3%}  10m: {self.return_10m:+.3%}",
            f"  Momentum: {self.momentum_direction} ({self.momentum_strength})  RSI14: {self.rsi_14:.1f}",
            f"  Vol ratio (5m vs prior 5m): {self.volume_ratio:.2f}x",
        ]
        if strike > 0:
            vs = self.vs_strike(strike)
            lines.append(
                f"  vs Strike ${strike:,.2f}: {vs['pct_delta']:+.3%} ({vs['direction']} | {vs['edge_strength']})"
            )
        return "\n".join(lines)


class BTCPriceFeed:
    """
    Real-time BTC price and momentum data.

    Sources (tried in order):
      1. Coinbase Exchange public API — BRTI constituent, best proxy for CF Benchmarks
      2. Kraken public API — also a BRTI constituent, solid fallback

    Usage:
        feed = BTCPriceFeed()
        snapshot = await feed.get_snapshot()
        print(snapshot.price, snapshot.return_5m)
    """

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10.0)
        self._cache: BTCSnapshot | None = None
        self._cache_ts: float = 0.0

    async def get_snapshot(self, force_refresh: bool = False) -> BTCSnapshot:
        """Return a current BTC snapshot, using cache if fresh enough."""
        now = time.time()
        if not force_refresh and self._cache and (now - self._cache_ts) < _CACHE_TTL_SECONDS:
            return self._cache

        try:
            # Fetch price and candles in parallel
            price_task = self._fetch_price()
            candles_task = self._fetch_candles(limit=30)
            price, candles = await asyncio.gather(price_task, candles_task)

            snapshot = BTCSnapshot(
                price=price,
                timestamp=datetime.now(timezone.utc),
                candles=candles,
            )

            if candles:
                self._fill_metrics(snapshot)

            self._cache = snapshot
            self._cache_ts = now
            return snapshot

        except Exception as e:
            log.warning(f"[BTC FEED] Error fetching price: {e}")
            if self._cache:
                log.warning("[BTC FEED] Using stale cache")
                return self._cache
            # Return a neutral fallback
            return BTCSnapshot(price=0.0, timestamp=datetime.now(timezone.utc))

    async def get_price(self) -> float:
        """Convenience: just return current BTC price."""
        snap = await self.get_snapshot()
        return snap.price

    # ── Private helpers ──────────────────────────────────────────────────────

    async def _fetch_price(self) -> float:
        """Fetch current BTC-USD spot price. Tries Coinbase first, falls back to Kraken."""
        # Try Coinbase (BRTI constituent — best proxy)
        try:
            resp = await self._client.get(
                f"{_COINBASE_BASE}/products/BTC-USD/ticker",
                timeout=8.0,
            )
            resp.raise_for_status()
            return float(resp.json()["price"])
        except Exception as e:
            log.debug(f"[BTC FEED] Coinbase price failed ({e}), trying Kraken...")

        # Fallback: Kraken
        resp = await self._client.get(
            f"{_KRAKEN_BASE}/Ticker",
            params={"pair": "XBTUSD"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        pair_data = result.get("XXBTZUSD", result.get("XBTUSD", {}))
        return float(pair_data["c"][0])   # "c" = last trade closed price

    async def _fetch_candles(self, limit: int = 30) -> list[BTCCandle]:
        """Fetch recent 1-minute OHLCV candles. Tries Coinbase first, falls back to Kraken."""
        # Try Coinbase
        try:
            resp = await self._client.get(
                f"{_COINBASE_BASE}/products/BTC-USD/candles",
                params={"granularity": 60},   # 60-second candles
                timeout=10.0,
            )
            resp.raise_for_status()
            raw = resp.json()
            # Coinbase returns [timestamp, low, high, open, close, volume] newest first
            candles = []
            for k in reversed(raw[:limit]):
                candles.append(BTCCandle(
                    open_time=datetime.fromtimestamp(k[0], tz=timezone.utc),
                    open=float(k[3]),
                    high=float(k[2]),
                    low=float(k[1]),
                    close=float(k[4]),
                    volume=float(k[5]),
                ))
            return candles
        except Exception as e:
            log.debug(f"[BTC FEED] Coinbase candles failed ({e}), trying Kraken...")

        # Fallback: Kraken OHLC
        resp = await self._client.get(
            f"{_KRAKEN_BASE}/OHLC",
            params={"pair": "XBTUSD", "interval": 1},   # 1-minute candles
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        raw = result.get("XXBTZUSD", result.get("XBTUSD", []))
        candles = []
        for k in raw[-limit:]:
            # Kraken: [time, open, high, low, close, vwap, volume, count]
            candles.append(BTCCandle(
                open_time=datetime.fromtimestamp(int(k[0]), tz=timezone.utc),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[6]),
            ))
        return candles

    def _fill_metrics(self, snap: BTCSnapshot):
        """Calculate momentum metrics from candles in place."""
        candles = snap.candles
        if not candles:
            return

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        # Multi-period returns (most recent candle last)
        if len(closes) >= 2:
            snap.return_1m = (closes[-1] - closes[-2]) / closes[-2]
        if len(closes) >= 6:
            snap.return_5m = (closes[-1] - closes[-6]) / closes[-6]
        if len(closes) >= 11:
            snap.return_10m = (closes[-1] - closes[-11]) / closes[-11]

        # Volatility: std dev of 1m returns
        if len(closes) >= 3:
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            snap.volatility_1h = variance ** 0.5

        # RSI(14)
        if len(closes) >= 15:
            snap.rsi_14 = self._calc_rsi(closes, period=14)

        # Volume ratio: last 5 candles vs prior 5 candles
        if len(volumes) >= 10:
            recent_vol = sum(volumes[-5:])
            prior_vol = sum(volumes[-10:-5])
            snap.volume_ratio = recent_vol / prior_vol if prior_vol > 0 else 1.0

    @staticmethod
    def _calc_rsi(closes: list[float], period: int = 14) -> float:
        """Calculate RSI from a list of closing prices."""
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))


# Module-level singleton so the feed is shared across the app
_feed_instance: BTCPriceFeed | None = None


def get_btc_feed() -> BTCPriceFeed:
    """Return the module-level singleton BTCPriceFeed."""
    global _feed_instance
    if _feed_instance is None:
        _feed_instance = BTCPriceFeed()
    return _feed_instance
