"""Conviction scorer — combines news + momentum signals into a single high-confidence trade."""
from __future__ import annotations

from datetime import datetime

from bot.core.signals import Signal, SignalAction
from bot.utils.logger import log


class ConvictionScorer:
    """
    Combines signals from multiple strategies into a single high-conviction trade.

    Scoring matrix:
    - Both agree  → boost confidence (×1.25), strategy="conviction"
    - Only news   → reduce confidence (×0.8)
    - Only moment → reduce confidence (×0.7)
    - Conflicting → return None (HOLD / skip)
    """

    def __init__(self, news_weight: float = 0.6, momentum_weight: float = 0.4):
        self.news_weight = news_weight
        self.momentum_weight = momentum_weight

    def score(
        self,
        news_signal: Signal | None,
        momentum_signal: Signal | None,
    ) -> Signal | None:
        """Combine two strategy signals. Returns conviction signal or None."""

        if news_signal is None and momentum_signal is None:
            return None

        # Only news fired
        if news_signal is not None and momentum_signal is None:
            if not news_signal.is_actionable:
                return None
            conf = min(news_signal.confidence * 0.8, 0.95)
            log.info(f"[cyan]CONVICTION[/] News-only → conf={conf:.0%} on {news_signal.market_title}")
            return Signal(
                market_id=news_signal.market_id,
                market_title=news_signal.market_title,
                exchange=news_signal.exchange,
                action=news_signal.action,
                outcome=news_signal.outcome,
                confidence=conf,
                target_price=news_signal.target_price,
                stop_loss=news_signal.stop_loss,
                reasoning=f"[NEWS ONLY] {news_signal.reasoning}",
                strategy="conviction",
                timestamp=datetime.utcnow(),
            )

        # Only momentum fired
        if momentum_signal is not None and news_signal is None:
            if not momentum_signal.is_actionable:
                return None
            conf = min(momentum_signal.confidence * 0.7, 0.95)
            log.info(f"[yellow]CONVICTION[/] Momentum-only → conf={conf:.0%} on {momentum_signal.market_title}")
            return Signal(
                market_id=momentum_signal.market_id,
                market_title=momentum_signal.market_title,
                exchange=momentum_signal.exchange,
                action=momentum_signal.action,
                outcome=momentum_signal.outcome,
                confidence=conf,
                target_price=momentum_signal.target_price,
                stop_loss=momentum_signal.stop_loss,
                reasoning=f"[MOMENTUM ONLY] {momentum_signal.reasoning}",
                strategy="conviction",
                timestamp=datetime.utcnow(),
            )

        # Both signals present — check for conflict
        if not news_signal.is_actionable and not momentum_signal.is_actionable:
            return None

        news_key = f"{news_signal.action.value}:{news_signal.outcome}"
        mom_key = f"{momentum_signal.action.value}:{momentum_signal.outcome}"

        if news_key != mom_key:
            log.info(
                f"[dim]CONVICTION[/] Conflict on {news_signal.market_title}: "
                f"news={news_key} vs momentum={mom_key} → HOLD"
            )
            return None

        # Agreement — boost combined confidence
        combined = (
            news_signal.confidence * self.news_weight
            + momentum_signal.confidence * self.momentum_weight
        )
        boosted = min(combined * 1.25, 0.95)

        # Blend targets
        targets = [s for s in [news_signal.target_price, momentum_signal.target_price] if s is not None]
        target = sum(targets) / len(targets) if targets else None

        # Use the tighter (higher) stop loss
        stops = [s for s in [news_signal.stop_loss, momentum_signal.stop_loss] if s is not None]
        stop = max(stops) if stops else None

        reasoning = (
            f"[HIGH CONVICTION] 📰 News ({news_signal.confidence:.0%}) + "
            f"📈 Momentum ({momentum_signal.confidence:.0%}) agree → "
            f"BUY {news_signal.outcome}. "
            f"News: {news_signal.reasoning[:120]} | "
            f"Momentum: {momentum_signal.reasoning}"
        )

        log.info(
            f"[bold green]CONVICTION[/] Both agree: BUY {news_signal.outcome} on "
            f"[bold]{news_signal.market_title}[/] → boosted conf={boosted:.0%}"
        )

        return Signal(
            market_id=news_signal.market_id,
            market_title=news_signal.market_title,
            exchange=news_signal.exchange,
            action=news_signal.action,
            outcome=news_signal.outcome,
            confidence=boosted,
            target_price=target,
            stop_loss=stop,
            reasoning=reasoning,
            strategy="conviction",
            timestamp=datetime.utcnow(),
        )
