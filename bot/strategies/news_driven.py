"""News-driven strategy using LLM analysis to detect mispriced prediction markets."""
from __future__ import annotations

from bot.core.signals import Signal, SignalAction
from bot.data.market_data import MarketSnapshot
from bot.data.news import NewsAnalysis
from bot.strategies.base import Strategy
from bot.utils.logger import log


class NewsDrivenStrategy(Strategy):
    """
    News-driven strategy that uses LLM analysis to find mispriced markets.

    Generates a BUY signal when Claude's estimated probability is significantly
    higher than the market price, and SELL when significantly lower.
    Uses the direction field from NewsAnalysis as a secondary confirmation.
    """

    name = "news_driven"

    def __init__(
        self,
        min_divergence: float = 0.10,  # 10% minimum price divergence to trade
        min_confidence: float = 0.5,   # minimum Claude confidence to act
    ):
        self.min_divergence = min_divergence
        self.min_confidence = min_confidence

    async def evaluate(
        self,
        snapshot: MarketSnapshot,
        news: NewsAnalysis | None = None,
    ) -> Signal | None:
        if news is None:
            return None

        if news.confidence < self.min_confidence:
            return None

        market = snapshot.market
        current_price = market.prices.get("Yes", 0.5)
        estimated = news.estimated_probability
        divergence = estimated - current_price

        if abs(divergence) < self.min_divergence:
            return None

        # Direction consistency check: Claude's direction should match the divergence
        # If direction is HIGHER but estimated < current, something is off — reduce confidence
        direction_ok = True
        if divergence > 0 and news.direction == "LOWER":
            direction_ok = False
        elif divergence < 0 and news.direction == "HIGHER":
            direction_ok = False

        # Reduce confidence if direction conflicts with probability
        conf_multiplier = 0.7 if not direction_ok else 1.0
        confidence = min(news.confidence, abs(divergence)) * conf_multiplier

        if confidence < 0.1:
            return None

        if divergence > 0:
            # Market is underpriced — BUY Yes
            action = SignalAction.BUY
            outcome = "Yes"
            target = min(estimated, current_price + abs(divergence) * 0.5)
            stop_loss = max(0.01, current_price - self.min_divergence)
            direction_emoji = "📈"
        else:
            # Market is overpriced — BUY No
            action = SignalAction.BUY
            outcome = "No"
            no_price = market.prices.get("No", 1.0 - current_price)
            target = min(1.0 - estimated, no_price + abs(divergence) * 0.5)
            stop_loss = max(0.01, no_price - self.min_divergence)
            direction_emoji = "📉"

        direction_tag = f" [{news.direction}]" if news.direction != "UNCHANGED" else ""
        signal = Signal(
            market_id=market.id,
            market_title=market.title,
            exchange=market.exchange,
            action=action,
            outcome=outcome,
            confidence=confidence,
            target_price=target,
            stop_loss=stop_loss,
            reasoning=(
                f"{direction_emoji} Estimated {estimated:.1%} vs market {current_price:.1%} "
                f"(divergence: {divergence:+.1%}){direction_tag}. {news.reasoning}"
            ),
            strategy=self.name,
        )

        log.info(
            f"[bold cyan]NEWS[/] {action.value.upper()} {outcome} on "
            f"[bold]{market.title}[/] @ {current_price:.1%} "
            f"(est: {estimated:.1%}, div: {divergence:+.1%}, conf: {confidence:.0%})"
        )

        return signal
