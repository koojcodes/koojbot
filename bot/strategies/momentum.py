"""Multi-timeframe momentum strategy with near-extreme detection and BTC priority."""
from __future__ import annotations

from bot.core.signals import Signal, SignalAction
from bot.data.market_data import MarketSnapshot
from bot.data.news import NewsAnalysis
from bot.strategies.base import Strategy
from bot.utils.logger import log

# Markets whose titles contain these keywords get halved volume requirements
_CRYPTO_KEYWORDS = ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol")


class MomentumStrategy(Strategy):
    """
    Multi-timeframe momentum strategy for prediction markets.

    Uses a fast (short) and slow (long) lookback to confirm breakout direction.
    Both timeframes must agree. Filters out near-certain markets and low volume.
    """

    name = "momentum"

    def __init__(
        self,
        short_lookback: int = 5,          # fast signal
        long_lookback: int = 20,           # trend confirmation
        entry_threshold: float = 0.03,     # 3% move triggers entry (standard)
        crypto_threshold: float = 0.025,   # slightly lower for BTC/crypto
        take_profit: float = 0.05,         # 5% take profit
        stop_loss: float = 0.03,           # 3% stop loss
        min_volume: float = 5000,          # minimum market volume
    ):
        self.short_lookback = short_lookback
        self.long_lookback = long_lookback
        self.entry_threshold = entry_threshold
        self.crypto_threshold = crypto_threshold
        self.take_profit = take_profit
        self.stop_loss_pct = stop_loss
        self.min_volume = min_volume

    async def evaluate(
        self,
        snapshot: MarketSnapshot,
        news: NewsAnalysis | None = None,
    ) -> Signal | None:
        market = snapshot.market
        history = snapshot.price_history

        # Detect if this is a crypto market
        title_lower = market.title.lower()
        is_crypto = any(kw in title_lower for kw in _CRYPTO_KEYWORDS)

        # Volume filter — crypto gets halved minimum
        effective_min_vol = self.min_volume / 2 if is_crypto else self.min_volume
        if market.volume < effective_min_vol:
            return None

        # Need enough price history for both timeframes
        min_required = self.long_lookback + 1
        if len(history) < min_required:
            return None

        current_price = history[-1][1]

        # Near-extreme detection: skip if price is near certainty (above 85%) or too near-zero below 5%
        if current_price > 0.85 or current_price < 0.05:
            return None

        # Underdog zone (5–15%): require larger move
        if current_price < 0.15:
            threshold = self.entry_threshold * 1.5
        else:
            threshold = self.crypto_threshold if is_crypto else self.entry_threshold

        # Short-term momentum
        past_short = history[-(self.short_lookback + 1)][1]
        if past_short == 0:
            return None
        short_momentum = (current_price - past_short) / past_short

        # Long-term momentum
        past_long = history[-(self.long_lookback + 1)][1]
        if past_long == 0:
            return None
        long_momentum = (current_price - past_long) / past_long

        # Both timeframes must exceed threshold and agree in direction
        if abs(short_momentum) < threshold:
            return None
        if short_momentum > 0 and long_momentum <= 0:
            return None
        if short_momentum < 0 and long_momentum >= 0:
            return None

        # Trend consistency check on short window (60% of moves must agree)
        recent_moves = []
        for i in range(-self.short_lookback, 0):
            if i - 1 >= -len(history):
                move = history[i][1] - history[i - 1][1]
                recent_moves.append(move)

        if not recent_moves:
            return None

        pos_moves = sum(1 for m in recent_moves if m > 0)
        neg_moves = sum(1 for m in recent_moves if m < 0)
        total = len(recent_moves)

        # Combined momentum score (average of both timeframes)
        avg_momentum = (abs(short_momentum) + abs(long_momentum)) / 2

        if short_momentum > 0:
            # Bullish — BUY Yes
            trend_strength = pos_moves / total
            if trend_strength < 0.6:
                return None

            action = SignalAction.BUY
            outcome = "Yes"
            target = min(0.95, current_price + self.take_profit)
            stop = max(0.01, current_price - self.stop_loss_pct)
            confidence = min(0.8, trend_strength * avg_momentum * 10)
        else:
            # Bearish — BUY No
            trend_strength = neg_moves / total
            if trend_strength < 0.6:
                return None

            action = SignalAction.BUY
            outcome = "No"
            no_price = market.prices.get("No", 1.0 - current_price)
            target = min(0.95, no_price + self.take_profit)
            stop = max(0.01, no_price - self.stop_loss_pct)
            confidence = min(0.8, trend_strength * avg_momentum * 10)

        crypto_tag = " [BTC/CRYPTO]" if is_crypto else ""
        signal = Signal(
            market_id=market.id,
            market_title=market.title,
            exchange=market.exchange,
            action=action,
            outcome=outcome,
            confidence=confidence,
            target_price=target,
            stop_loss=stop,
            reasoning=(
                f"Short momentum {short_momentum:+.1%} + long {long_momentum:+.1%} "
                f"over {self.short_lookback}/{self.long_lookback} periods, "
                f"trend strength {trend_strength:.0%}{crypto_tag}"
            ),
            strategy=self.name,
        )

        log.info(
            f"[bold yellow]MOMENTUM[/] {action.value.upper()} {outcome} on "
            f"[bold]{market.title}[/] short={short_momentum:+.1%} "
            f"long={long_momentum:+.1%} conf={confidence:.0%}"
        )

        return signal
