"""Day Trader Agent — converts DayTradeOpportunity list into tight, fast Signal objects."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from bot.agents.base import Agent, AgentResult
from bot.agents.day_trader_researcher import DayTradeOpportunity
from bot.config import settings
from bot.core.signals import Signal, SignalAction
from bot.exchanges.base import Exchange
from bot.utils.logger import log


# ── Parameters (tighter than the social-media TraderAgent) ───────────────────

MIN_EDGE          = settings.DAY_TRADER_MIN_EDGE
MIN_CONFIDENCE    = settings.DAY_TRADER_MIN_CONFIDENCE
MIN_VOLUME        = settings.DAY_TRADER_MIN_VOLUME
STOP_LOSS_PCT     = 0.03   # 3% stop loss (vs 5% for regular agent)
TARGET_TAKE_PCT   = 0.40   # take profit at 40% of estimated edge (quick scalp)
MAX_HOLD_HOURS    = 1.5    # 1.5 hour max hold (hint for risk manager)


@dataclass
class DayTraderResult:
    signals: list[Signal]
    skipped: int = 0
    skip_reasons: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "signals": [s.to_dict() for s in self.signals],
            "skipped": self.skipped,
            "skip_reasons": self.skip_reasons,
            "summary": self.summary,
        }


class DayTraderAgent(Agent):
    """
    Day Trader Agent: Converts DayTradeOpportunity objects into Signal objects
    with tight parameters suitable for short-term prediction market scalping.

    Deterministic — no Claude needed. Just maps opportunities to signals,
    applying stricter filters and tighter stops than the social-media TraderAgent.

    Key differences from TraderAgent:
    - Stop loss: 3% (vs 5%)
    - Target: 40% of edge (vs 50%) — take profit faster
    - Min confidence: 55% (vs 40%)
    - Max hold hint: 1.5h (vs 4h)
    - Strategy tag: "day_trader"
    """

    name = "day_trader"
    agent_id = "btc15m_kalshi"

    def __init__(
        self,
        exchange: Exchange,
        min_edge: float = MIN_EDGE,
        min_confidence: float = MIN_CONFIDENCE,
        min_volume: float = MIN_VOLUME,
        stop_loss_pct: float = STOP_LOSS_PCT,
    ):
        self.exchange = exchange
        self.min_edge = min_edge
        self.min_confidence = min_confidence
        self.min_volume = min_volume
        self.stop_loss_pct = stop_loss_pct

    async def run(self, input_text: str = "", context: dict | None = None) -> AgentResult:
        """
        Convert DayTradeOpportunity dicts (from context) into Signal objects.

        context["opportunities"]: list of DayTradeOpportunity.to_dict()
        """
        context = context or {}
        start = time.time()

        raw_opportunities = context.get("opportunities", [])
        log.info(
            f"[bold yellow]DAY TRADER[/] Processing {len(raw_opportunities)} "
            f"day trade opportunities..."
        )

        signals: list[Signal] = []
        skipped = 0
        skip_reasons: list[str] = []

        for opp_dict in raw_opportunities:
            # Reconstitute opportunity
            opp = DayTradeOpportunity(
                market_id=opp_dict.get("market_id", ""),
                market_title=opp_dict.get("market_title", "Unknown"),
                exchange=opp_dict.get("exchange", "kalshi"),
                opportunity_type=opp_dict.get("opportunity_type", "momentum"),
                direction=opp_dict.get("direction", "HIGHER"),
                current_price=float(opp_dict.get("current_price", 0.5)),
                estimated_edge=float(opp_dict.get("estimated_edge", 0.0)),
                confidence=float(opp_dict.get("confidence", 0.0)),
                urgency=opp_dict.get("urgency", "medium"),
                window_minutes=int(opp_dict.get("window_minutes", 60)),
                reasoning=opp_dict.get("reasoning", ""),
                volume=float(opp_dict.get("volume", 0.0)),
            )

            # ── Filters ────────────────────────────────────────────────────────

            if opp.estimated_edge < self.min_edge:
                reason = f"SKIP {opp.market_title[:35]} — edge {opp.estimated_edge:.0%} < min {self.min_edge:.0%}"
                log.info(f"[dim]{reason}[/]")
                skip_reasons.append(reason)
                skipped += 1
                continue

            if opp.confidence < self.min_confidence:
                reason = f"SKIP {opp.market_title[:35]} — conf {opp.confidence:.0%} < min {self.min_confidence:.0%}"
                log.info(f"[dim]{reason}[/]")
                skip_reasons.append(reason)
                skipped += 1
                continue

            if opp.volume < self.min_volume:
                reason = f"SKIP {opp.market_title[:35]} — vol {opp.volume:,.0f} < min {self.min_volume:,.0f}"
                log.info(f"[dim]{reason}[/]")
                skip_reasons.append(reason)
                skipped += 1
                continue

            # ── Build signal ───────────────────────────────────────────────────

            if opp.direction == "HIGHER":
                outcome = "Yes"
                entry = opp.current_price
                target = min(0.95, entry + opp.estimated_edge * TARGET_TAKE_PCT)
                stop = max(0.01, entry - self.stop_loss_pct)
            else:
                outcome = "No"
                entry = 1.0 - opp.current_price   # No price
                target = min(0.95, entry + opp.estimated_edge * TARGET_TAKE_PCT)
                stop = max(0.01, entry - self.stop_loss_pct)

            urgency_label = {
                "high": "⚡ HIGH URGENCY",
                "medium": "⏱ MEDIUM",
                "low": "🔵 LOW",
            }.get(opp.urgency, "⏱")

            reasoning = (
                f"[DAY TRADER | {opp.opportunity_type.upper()}] {urgency_label} | "
                f"Window: ~{opp.window_minutes}min | "
                f"Edge: {opp.estimated_edge:.0%} | "
                f"Max hold: {MAX_HOLD_HOURS}h | "
                f"{opp.reasoning}"
            )

            signal = Signal(
                market_id=opp.market_id,
                market_title=opp.market_title,
                exchange=opp.exchange,
                action=SignalAction.BUY,
                outcome=outcome,
                confidence=opp.confidence,
                target_price=target,
                stop_loss=stop,
                reasoning=reasoning,
                strategy="day_trader",
                agent_id=self.agent_id,
                execution_exchange=opp.exchange,
                timestamp=datetime.utcnow(),
            )

            signals.append(signal)
            arrow = "▲" if opp.direction == "HIGHER" else "▼"
            log.info(
                f"[bold yellow]DAY TRADER[/] {arrow} BUY {outcome} '{opp.market_title[:40]}' "
                f"entry={entry:.1%} target={target:.1%} stop={stop:.1%} "
                f"conf={opp.confidence:.0%} [{opp.opportunity_type}]"
            )

        elapsed = time.time() - start

        # ── Build summary ──────────────────────────────────────────────────────
        summary_lines = [
            f"⚡ Day Trader: {len(signals)} signal{'s' if len(signals) != 1 else ''} generated"
            + (f" ({skipped} skipped)" if skipped else "")
        ]
        for s in signals:
            opp_type = s.reasoning.split("|")[0].replace("[DAY TRADER | ", "").strip().lower()
            arrow = "▲" if s.outcome == "Yes" else "▼"
            summary_lines.append(
                f"   {arrow} BUY {s.outcome}: {s.market_title[:45]}\n"
                f"      [{opp_type}] Conf: {s.confidence:.0%} | "
                f"Target: {s.target_price:.1%} | Stop: {s.stop_loss:.1%}"
            )

        summary = "\n".join(summary_lines)

        log.info(
            f"[bold yellow]DAY TRADER[/] Done in {elapsed:.2f}s — "
            f"{len(signals)} signals, {skipped} skipped"
        )

        return AgentResult(
            success=True,
            output=summary,
            data=DayTraderResult(
                signals=signals,
                skipped=skipped,
                skip_reasons=skip_reasons,
                summary=summary,
            ).to_dict(),
            agent_name=self.name,
            elapsed_seconds=elapsed,
        )
