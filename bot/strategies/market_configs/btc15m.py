"""
BTC 15-Minute Price Direction config (KXBTC15M on Kalshi).

Market mechanics:
  - New contract opens every 15 minutes, 24/7
  - Strike = average of CF Benchmarks BRTI in the 60s before window opens
  - Settlement = average of 60 BRTI prices in the final minute (NOT spot price)
  - Binary: Yes pays $1 if BTC ≥ strike at close; No pays $1 if BTC < strike

  Current typical spread: ~4¢ (Yes ask - Yes bid)
  Quadratic fees: ~1.6¢/contract at 65¢ → round-trip ~7¢ total
  → Minimum gross edge needed: ~8-10% probability to be profitable

Signal logic:
  1. Fetch live BTC price from Binance (proxy for CF Benchmarks RTI)
  2. Calculate momentum: 1m, 5m, 10m returns
  3. Calculate price vs strike delta
  4. Check current market Yes price vs our estimated fair value
  5. Only trade if: momentum agrees, vs-strike agrees, net edge > threshold,
     AND we're in the entry window (2-12 minutes into the 15-min window)

Key insight: CF Benchmarks BRTI ≈ Binance BTCUSDT ± 0.05%.
We use Binance as a real-time proxy; small tracking error is acceptable.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.agents.day_trader_researcher import DayTradeOpportunity
from bot.config import settings
from bot.data.btc_price import BTCPriceFeed, BTCSnapshot, get_btc_feed
from bot.strategies.market_configs.base import (
    MarketConfig, EntryWindow, FeeModel, PositionSizingRules,
)
from bot.utils.logger import log


# ── Tunable parameters ─────────────────────────────────────────────────────────

# Entry window: enter 2 min after open (spreads tighten), exit no closer than 3 min to close
_ENTRY_WINDOW = EntryWindow(
    earliest_minutes_after_open=2.0,
    latest_minutes_before_close=3.0,
)

# Quadratic fee structure (Kalshi standard for crypto markets)
_FEE_MODEL = FeeModel(fee_type="quadratic", rate=0.07)

# Typical max spread at mid-price (used for net edge calculation)
_TYPICAL_SPREAD = 0.04   # 4¢

# Signal thresholds (env-overridable via settings — see BTC15M_* in .env)
_MIN_VS_STRIKE_PCT     = settings.BTC15M_MIN_VS_STRIKE_PCT
_MIN_MOMENTUM_STRENGTH = 0.0005   # 5m return must be ≥0.05% to count as momentum
_STRONG_SIGNAL_PCT     = 0.002    # ≥0.2% vs strike = strong signal
_MAX_RSI_OVERBOUGHT    = 75       # don't chase longs when RSI > 75
_MIN_RSI_OVERSOLD      = 25       # don't chase shorts when RSI < 25

# Market price sanity: don't trade Yes if it's already >82% or No if it's >82%
# (market has already priced in the move)
_MAX_TRADE_PRICE       = 0.82

# Position sizing
_SIZING = PositionSizingRules(
    max_contracts=100,
    max_pct_of_balance=0.04,   # max 4% of balance per BTC15M trade
    scale_by_confidence=True,
    min_contracts=2,
)


@dataclass
class BTC15MSignal:
    """Internal signal structure before converting to DayTradeOpportunity."""
    direction: str           # "HIGHER" or "LOWER"
    confidence: float        # 0-1
    estimated_fair_value: float   # our estimate of the true Yes probability
    current_yes_price: float      # current market Yes ask price
    net_edge: float          # estimated_fair_value - current_yes_price (or vice versa for No)
    reasoning: str
    vs_strike_pct: float     # BTC % above/below strike
    momentum_5m: float       # 5m BTC return
    rsi: float
    urgency: str             # "high" | "medium" | "low"


class BTC15MConfig(MarketConfig):
    """
    Market config for the Kalshi KXBTC15M (Bitcoin 15-minute price up/down) series.

    This config contains all the market-specific intelligence:
      - Real-time BTC price from Binance
      - Momentum + vs-strike signal calculation
      - Entry timing aware of the 15-minute window
      - Fee-adjusted edge calculation
      - RSI filters to avoid chasing extremes
    """

    series_ticker = "KXBTC15M"
    display_name = "BTC 15-Minute Price Direction"
    exchange = "kalshi"
    description = (
        "Binary market: will BTC be ≥ the opening strike price in 15 minutes?\n"
        "Resolves via CF Benchmarks BRTI average. New contract every 15 min, 24/7."
    )

    def __init__(self):
        self.entry_window = _ENTRY_WINDOW
        self.fees = _FEE_MODEL
        self.sizing = _SIZING
        self.min_net_edge = settings.BTC15M_MIN_NET_EDGE
        self.min_confidence = settings.BTC15M_MIN_CONFIDENCE
        self.prefer_hold_to_resolution = True
        self._btc_feed: BTCPriceFeed = get_btc_feed()

    async def get_current_market_id(self, exchange_client: Any) -> str | None:
        """
        Find the currently-active KXBTC15M contract from Kalshi.
        Returns the ticker of the open/active market, or None.
        """
        try:
            data = await exchange_client._request(
                "GET", "/markets",
                params={"series_ticker": "KXBTC15M", "status": "open", "limit": 3},
            )
            markets = data.get("markets", [])
            if not markets:
                # Try "active" status as well
                data = await exchange_client._request(
                    "GET", "/markets",
                    params={"series_ticker": "KXBTC15M", "limit": 3},
                )
                markets = data.get("markets", [])

            if markets:
                return markets[0].get("ticker")
        except Exception as e:
            log.warning(f"[BTC15M] Could not fetch current market: {e}")
        return None

    async def generate_signal(self, context: dict) -> DayTradeOpportunity | None:
        """
        Core signal generation for BTC 15-minute markets.

        context keys (all optional, filled from live data if missing):
          - market_id: str
          - yes_ask: float  (current Yes ask price, $0-$1)
          - yes_bid: float
          - no_ask: float
          - floor_strike: float  (BTC strike price at window open)
          - open_time: datetime
          - close_time: datetime
          - volume: float

        Phase 6: Also queries the knowledge graph for BTC context that
        can modify confidence scores (graph_context module).
        """
        market_id = context.get("market_id", "")
        yes_ask = float(context.get("yes_ask", 0.0))
        yes_bid = float(context.get("yes_bid", 0.0))
        floor_strike = float(context.get("floor_strike", 0.0))
        open_time = context.get("open_time")
        close_time = context.get("close_time")
        volume = float(context.get("volume", 0.0))
        market_title = context.get("market_title", "BTC price up in next 15 mins?")

        # ── Validate required context ──────────────────────────────────────────
        if not floor_strike or not close_time:
            log.warning("[BTC15M] Missing floor_strike or close_time — cannot generate signal")
            return None

        # ── Entry window check ─────────────────────────────────────────────────
        now = datetime.now(timezone.utc)
        if isinstance(close_time, str):
            close_time = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        if isinstance(open_time, str):
            open_time = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
        if close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=timezone.utc)
        if open_time and open_time.tzinfo is None:
            open_time = open_time.replace(tzinfo=timezone.utc)

        minutes_remaining = (close_time - now).total_seconds() / 60
        minutes_elapsed = (now - open_time).total_seconds() / 60 if open_time else 7.5

        if not self.entry_window.is_open(minutes_elapsed, minutes_remaining):
            log.info(
                f"[BTC15M] Outside entry window — {minutes_elapsed:.1f}m elapsed, "
                f"{minutes_remaining:.1f}m remaining (window: "
                f"{self.entry_window.earliest_minutes_after_open}m–{self.entry_window.latest_minutes_before_close}m before close)"
            )
            return None

        if minutes_remaining < 0:
            log.info("[BTC15M] Window closed — skipping")
            return None

        # ── Fetch live BTC price ───────────────────────────────────────────────
        try:
            btc = await self._btc_feed.get_snapshot()
        except Exception as e:
            log.error(f"[BTC15M] Could not fetch BTC price: {e}")
            return None

        if btc.price <= 0:
            log.warning("[BTC15M] Invalid BTC price (0) — skipping")
            return None

        # ── Phase 6: Fetch graph context (non-blocking, cached) ────────────────
        graph_signal = None
        try:
            from bot.swarm.graph_context import get_btc_graph_signal
            graph_signal = await get_btc_graph_signal()
        except Exception:
            pass  # Non-fatal — graph context is supplementary

        # ── Signal calculation ─────────────────────────────────────────────────
        signal = self._calculate_signal(
            btc=btc,
            floor_strike=floor_strike,
            yes_ask=yes_ask,
            yes_bid=yes_bid,
            minutes_remaining=minutes_remaining,
            graph_signal=graph_signal,
        )

        if signal is None:
            log.info(
                f"[BTC15M] No signal — BTC ${btc.price:,.2f} vs strike ${floor_strike:,.2f} "
                f"({btc.vs_strike(floor_strike)['pct_delta']:+.3%}) | "
                f"momentum={btc.momentum_direction} ({btc.momentum_strength})"
            )
            return None

        # ── Build opportunity ──────────────────────────────────────────────────
        window_minutes = max(1, int(minutes_remaining))
        urgency = signal.urgency

        log.info(
            f"[BTC15M] ✅ Signal: {signal.direction} | "
            f"confidence={signal.confidence:.0%} | net_edge={signal.net_edge:.1%} | "
            f"BTC vs strike: {signal.vs_strike_pct:+.3%} | 5m momentum: {signal.momentum_5m:+.3%} | "
            f"{minutes_remaining:.1f}min remaining"
        )

        return DayTradeOpportunity(
            market_id=market_id,
            market_title=market_title,
            exchange=self.exchange,
            opportunity_type="momentum" if abs(signal.vs_strike_pct) < 0.001 else "news_lag",
            direction=signal.direction,
            current_price=yes_ask if signal.direction == "HIGHER" else (1.0 - yes_ask),
            estimated_edge=signal.net_edge,
            confidence=signal.confidence,
            urgency=urgency,
            window_minutes=window_minutes,
            reasoning=signal.reasoning,
            volume=volume,
        )

    # ── Signal math ─────────────────────────────────────────────────────────────

    def _calculate_signal(
        self,
        btc: BTCSnapshot,
        floor_strike: float,
        yes_ask: float,
        yes_bid: float,
        minutes_remaining: float,
        graph_signal=None,
    ) -> BTC15MSignal | None:
        """
        Core signal logic. Returns a BTC15MSignal if conditions are met, else None.

        Decision tree:
          1. Check BTC is meaningfully away from strike (vs_strike filter)
          2. Check 5m momentum agrees with direction (momentum agreement filter)
          3. Check RSI isn't at extreme (avoid chasing overbought/oversold)
          4. Check current market price leaves enough edge after fees
          5. Check market price hasn't already fully priced in the move
          6. (Phase 6) Graph context check — knowledge graph contradicts direction = block
        """
        vs = btc.vs_strike(floor_strike)
        vs_pct = vs["pct_delta"]         # positive = BTC above strike
        momentum_5m = btc.return_5m
        rsi = btc.rsi_14
        spread = yes_ask - yes_bid if (yes_ask > 0 and yes_bid > 0) else _TYPICAL_SPREAD

        log.info(
            f"[BTC15M] Evaluating: BTC=${btc.price:,.2f} strike=${floor_strike:,.2f} "
            f"vs_strike={vs_pct:+.3%} | mom_5m={momentum_5m:+.3%} | "
            f"RSI={rsi:.1f} | yes_ask={yes_ask:.2f} | spread={spread:.2f}"
        )

        # ── Filter 1: BTC must be meaningfully away from strike ──────────────
        if abs(vs_pct) < _MIN_VS_STRIKE_PCT:
            log.info(f"[BTC15M] Filter1 FAIL: vs_strike {vs_pct:+.4%} < min {_MIN_VS_STRIKE_PCT:.4%}")
            return None

        # Determine directional bias from vs-strike
        btc_direction = "HIGHER" if vs_pct > 0 else "LOWER"

        # ── Filter 2: 5m momentum must agree with direction ──────────────────
        # (allows weak agreement — we just need them not to conflict)
        momentum_direction = "HIGHER" if momentum_5m >= 0 else "LOWER"
        if momentum_direction != btc_direction and abs(momentum_5m) >= _MIN_MOMENTUM_STRENGTH:
            log.info(
                f"[BTC15M] Filter2 FAIL: momentum {momentum_direction} conflicts with "
                f"vs-strike {btc_direction} (5m={momentum_5m:+.3%})"
            )
            return None

        # ── Filter 3: RSI extremes ────────────────────────────────────────────
        if btc_direction == "HIGHER" and rsi > _MAX_RSI_OVERBOUGHT:
            log.info(f"[BTC15M] Filter3 FAIL: RSI {rsi:.1f} overbought for HIGHER trade")
            return None
        if btc_direction == "LOWER" and rsi < _MIN_RSI_OVERSOLD:
            log.info(f"[BTC15M] Filter3 FAIL: RSI {rsi:.1f} oversold for LOWER trade")
            return None

        # ── Filter 4: Market price vs our fair value ──────────────────────────
        # Estimate fair value: if BTC is X% above strike, our fair value for Yes is:
        #   base 0.50 + sigmoid(vs_pct, momentum)
        fair_value_yes = self._estimate_fair_value(vs_pct, momentum_5m, btc.return_1m)

        if btc_direction == "HIGHER":
            # Buying Yes at yes_ask
            if yes_ask <= 0:
                return None
            net_edge = fair_value_yes - yes_ask - self._round_trip_cost(yes_ask, spread)
            current_price = yes_ask
        else:
            # Buying No at no_ask = 1 - yes_bid
            no_ask = 1.0 - yes_bid if yes_bid > 0 else (1.0 - yes_ask + spread)
            fair_value_no = 1.0 - fair_value_yes
            if no_ask <= 0:
                return None
            net_edge = fair_value_no - no_ask - self._round_trip_cost(no_ask, spread)
            current_price = no_ask

        if net_edge < self.min_net_edge:
            side = "Yes" if btc_direction == "HIGHER" else "No"
            log.info(
                f"[BTC15M] Filter4 FAIL: net_edge {net_edge:.1%} < min {self.min_net_edge:.1%} "
                f"(dir={btc_direction} side={side} fair_yes={fair_value_yes:.2f} "
                f"entry={current_price:.2f} yes_ask={yes_ask:.2f} yes_bid={yes_bid:.2f})"
            )
            return None

        # ── Filter 5: Market price ceiling ───────────────────────────────────
        if current_price > _MAX_TRADE_PRICE:
            log.info(
                f"[BTC15M] Filter5 FAIL: market already at {current_price:.0%} — move priced in"
            )
            return None

        # ── Filter 6 (Phase 6): Graph context contradiction check ────────────
        graph_conf_mod = 0.0
        if graph_signal and graph_signal.relevant_facts >= 2:
            # If graph strongly disagrees with our direction, block the trade
            graph_direction = "HIGHER" if graph_signal.direction_bias > 0 else "LOWER"
            if (
                graph_direction != btc_direction
                and abs(graph_signal.direction_bias) >= 0.06
            ):
                log.info(
                    f"[BTC15M] Filter6 FAIL: graph context contradicts {btc_direction} "
                    f"(bias={graph_signal.direction_bias:+.3f}, "
                    f"facts={graph_signal.relevant_facts})"
                )
                return None
            # Otherwise, apply confidence modifier from graph
            graph_conf_mod = graph_signal.confidence_modifier
            if graph_direction == btc_direction:
                graph_conf_mod = abs(graph_conf_mod)  # Boost for alignment
            else:
                graph_conf_mod = -abs(graph_conf_mod) * 0.5  # Mild penalty for weak disagreement

        # ── Confidence scoring ────────────────────────────────────────────────
        confidence = self._score_confidence(
            vs_pct=vs_pct,
            momentum_5m=momentum_5m,
            net_edge=net_edge,
            rsi=rsi,
            minutes_remaining=minutes_remaining,
            volume_ratio=btc.volume_ratio,
        )
        confidence += graph_conf_mod
        confidence = max(0.0, min(1.0, confidence))

        if confidence < self.min_confidence:
            log.info(f"[BTC15M] Confidence {confidence:.0%} < min {self.min_confidence:.0%}")
            return None

        # ── Urgency ───────────────────────────────────────────────────────────
        if minutes_remaining <= 4 or abs(vs_pct) >= _STRONG_SIGNAL_PCT:
            urgency = "high"
        elif minutes_remaining <= 8:
            urgency = "medium"
        else:
            urgency = "low"

        # ── Build reasoning string ────────────────────────────────────────────
        graph_note = ""
        if graph_signal and graph_signal.relevant_facts >= 2:
            graph_note = (
                f" | Graph: {graph_signal.relevant_facts} facts, "
                f"bias={graph_signal.direction_bias:+.3f}"
            )
        reasoning = (
            f"BTC ${btc.price:,.2f} is {vs_pct:+.3%} vs strike ${floor_strike:,.2f} | "
            f"5m momentum: {momentum_5m:+.3%} | "
            f"RSI: {rsi:.0f} | "
            f"Market Yes: {yes_ask:.2f} | Our fair value: {fair_value_yes:.2f} | "
            f"Net edge: {net_edge:+.1%} | {minutes_remaining:.1f}min remaining{graph_note}"
        )

        return BTC15MSignal(
            direction=btc_direction,
            confidence=confidence,
            estimated_fair_value=fair_value_yes,
            current_yes_price=yes_ask,
            net_edge=net_edge,
            reasoning=reasoning,
            vs_strike_pct=vs_pct,
            momentum_5m=momentum_5m,
            rsi=rsi,
            urgency=urgency,
        )

    def _estimate_fair_value(
        self,
        vs_strike_pct: float,
        momentum_5m: float,
        momentum_1m: float,
    ) -> float:
        """
        Estimate the 'true' probability that BTC will be ≥ strike at resolution.

        Uses a simple logistic model:
          - Base: 0.50
          - vs_strike contribution: scaled by magnitude (larger gap → higher certainty)
          - Momentum contribution: smaller weight (trend may or may not continue)

        This is intentionally conservative. The real edge comes from being right
        about the direction, not from predicting exact probabilities.
        """
        # vs_strike: 0.1% gap → +5%, 0.2% → +10%, 0.5% → +22%
        vs_contribution = min(0.30, abs(vs_strike_pct) * 45) * (1 if vs_strike_pct > 0 else -1)

        # momentum: 0.1% in 5m → +3%, 0.3% → +8%
        mom_contribution = min(0.15, abs(momentum_5m) * 25) * (1 if momentum_5m > 0 else -1)

        # 1m momentum gets smaller weight
        mom1_contribution = min(0.05, abs(momentum_1m) * 15) * (1 if momentum_1m > 0 else -1)

        fair_value = 0.50 + vs_contribution + mom_contribution * 0.5 + mom1_contribution * 0.3
        return max(0.10, min(0.90, fair_value))

    def _round_trip_cost(self, price: float, spread: float) -> float:
        """Total cost to enter and hold to resolution (fee in + fee out + entry spread)."""
        fee_in = self.fees.estimate_fee(price)
        fee_out = self.fees.estimate_fee(price)   # approx (price moves but this is close enough)
        return fee_in + fee_out + spread

    def _score_confidence(
        self,
        vs_pct: float,
        momentum_5m: float,
        net_edge: float,
        rsi: float,
        minutes_remaining: float,
        volume_ratio: float,
    ) -> float:
        """
        Score 0-1 confidence for the trade based on multiple factors.

        Higher scores for:
          - Larger vs-strike gap
          - Strong 5m momentum
          - Large net edge after fees
          - RSI in neutral zone (40-60)
          - Adequate time remaining (5-10 min)
          - Volume spike (informed activity)
        """
        score = 0.50   # baseline

        # vs-strike magnitude
        abs_vs = abs(vs_pct)
        if abs_vs >= 0.003:
            score += 0.15
        elif abs_vs >= 0.002:
            score += 0.10
        elif abs_vs >= 0.001:
            score += 0.05

        # Momentum strength
        abs_mom = abs(momentum_5m)
        if abs_mom >= 0.003:
            score += 0.10
        elif abs_mom >= 0.001:
            score += 0.05

        # Net edge (after fees)
        if net_edge >= 0.15:
            score += 0.10
        elif net_edge >= 0.10:
            score += 0.05

        # RSI neutral zone bonus (avoid extremes)
        if 35 <= rsi <= 65:
            score += 0.05

        # Time remaining sweet spot: 5-10 min is ideal
        if 4 <= minutes_remaining <= 10:
            score += 0.05
        elif minutes_remaining < 4:
            score -= 0.05   # cutting it close

        # Volume spike
        if volume_ratio >= 1.5:
            score += 0.05

        return max(0.0, min(1.0, score))


# ── Module-level singleton ─────────────────────────────────────────────────────

_btc15m_config: BTC15MConfig | None = None


def get_btc15m_config() -> BTC15MConfig:
    """Return the module-level singleton BTC15MConfig."""
    global _btc15m_config
    if _btc15m_config is None:
        _btc15m_config = BTC15MConfig()
    return _btc15m_config
