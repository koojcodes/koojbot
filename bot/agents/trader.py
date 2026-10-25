"""Trader Agent — converts synthesis results into Signal objects and queues them for execution."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from bot.agents.base import Agent, AgentResult
from bot.agents.synthesizer import SynthesisResult, MarketImplication
from bot.core.signals import Signal, SignalAction
from bot.exchanges.base import Exchange
from bot.utils.logger import log


@dataclass
class TraderResult:
    signals: list[Signal]
    skipped: int = 0     # implications that didn't meet threshold
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "signals": [s.to_dict() for s in self.signals],
            "skipped": self.skipped,
            "summary": self.summary,
        }


# Minimum delta to generate a signal (10% divergence from current price)
MIN_PROBABILITY_DELTA = 0.08


class TraderAgent(Agent):
    """
    Trader Agent: Converts SynthesisResult into Signal objects.

    Deterministic — no Claude needed. Just maps probability deltas
    to Signal objects and feeds them into the trading engine's signal queue.
    """

    name = "trader"

    def __init__(self, exchange: Exchange, min_delta: float = MIN_PROBABILITY_DELTA):
        self.exchange = exchange
        self.min_delta = min_delta
        self.agent_id = "pipeline_news_trader"

    def _default_exchange(self) -> str:
        if hasattr(self.exchange, "real"):
            return getattr(self.exchange.real, "name", "kalshi")
        return getattr(self.exchange, "name", "kalshi")

    async def run(self, input_text: str, context: dict) -> AgentResult:
        """
        input_text: unused (synthesis context comes through context dict)
        context: SynthesisResult.to_dict()
        """
        start = time.time()

        trading_thesis = context.get("trading_thesis", "")
        overall_confidence = float(context.get("overall_confidence", 0.5))
        time_horizon = context.get("time_horizon", "days")
        implications = context.get("market_implications", [])

        log.info(
            f"[yellow]TRADER AGENT[/] Processing {len(implications)} implications "
            f"(conf={overall_confidence:.0%})"
        )

        signals: list[Signal] = []
        skipped = 0

        for imp in implications:
            market_id = imp.get("market_id", "")
            market_title = imp.get("market_title", "Unknown market")
            current_price = float(imp.get("current_price", 0.5))
            direction = imp.get("direction", "HIGHER")
            delta = float(imp.get("probability_delta", 0.0))
            imp_confidence = float(imp.get("confidence", 0.5))

            # Filter: must meet minimum delta and confidence
            if delta < self.min_delta:
                log.info(f"[dim]TRADER[/] Skipping {market_title[:40]} — delta {delta:.0%} < min {self.min_delta:.0%}")
                skipped += 1
                continue

            if imp_confidence < 0.4:
                log.info(f"[dim]TRADER[/] Skipping {market_title[:40]} — confidence {imp_confidence:.0%} too low")
                skipped += 1
                continue

            # Blend: use both the implication confidence and overall synthesis confidence
            blended_confidence = (imp_confidence * 0.6 + overall_confidence * 0.4)

            # Determine outcome and target
            if direction == "HIGHER":
                outcome = "Yes"
                estimated = min(0.95, current_price + delta)
                target = current_price + delta * 0.5  # halfway to estimate
                stop = max(0.01, current_price - 0.05)
            else:
                outcome = "No"
                no_price = 1.0 - current_price
                estimated = min(0.95, no_price + delta)
                target = no_price + delta * 0.5
                stop = max(0.01, no_price - 0.05)

            reasoning = (
                f"[AGENT PIPELINE] {trading_thesis} | "
                f"Est. {estimated:.0%} vs current {current_price:.0%} ({direction} {delta:+.0%}) | "
                f"Horizon: {time_horizon}"
            )

            signal_exchange = (
                imp.get("exchange")
                or context.get("default_exchange")
                or self._default_exchange()
            )

            signal = Signal(
                market_id=market_id,
                market_title=market_title,
                exchange=signal_exchange,
                action=SignalAction.BUY,
                outcome=outcome,
                confidence=blended_confidence,
                target_price=target,
                stop_loss=stop,
                reasoning=reasoning,
                strategy="agent_pipeline",
                agent_id=self.agent_id,
                execution_exchange=signal_exchange,
                timestamp=datetime.utcnow(),
            )

            signals.append(signal)
            log.info(
                f"[yellow]TRADER AGENT[/] Signal: BUY {outcome} on '{market_title[:40]}' "
                f"@ {current_price:.0%}→{estimated:.0%} conf={blended_confidence:.0%}"
            )

        elapsed = time.time() - start

        summary_lines = [f"🎯 {len(signals)} signal{'s' if len(signals) != 1 else ''} generated"]
        if skipped:
            summary_lines.append(f"   ({skipped} implications skipped — below threshold)")
        for s in signals:
            arrow = "▲" if s.outcome == "Yes" else "▼"
            summary_lines.append(
                f"   {arrow} BUY {s.outcome}: {s.market_title[:45]}\n"
                f"      Conviction: {s.confidence:.0%} | Target: {s.target_price:.0%}"
            )

        summary = "\n".join(summary_lines)

        log.info(
            f"[yellow]TRADER AGENT[/] Done in {elapsed:.1f}s — "
            f"{len(signals)} signals, {skipped} skipped"
        )

        return AgentResult(
            success=True,
            output=summary,
            data=TraderResult(signals=signals, skipped=skipped, summary=summary).to_dict(),
            agent_name=self.name,
            elapsed_seconds=elapsed,
        )
