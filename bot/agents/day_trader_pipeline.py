"""Day Trader Pipeline — autonomous Researcher → DayTrader loop for short-term opportunities."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable, Any

from bot.agents.base import AgentResult
from bot.agents.day_trader_researcher import DayTraderResearcherAgent, DayTradeOpportunity
from bot.agents.day_trader import DayTraderAgent
from bot.core.signals import Signal, SignalAction
from bot.strategies.market_configs.base import MarketConfig
from bot.utils.logger import log


ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class DayTradePipelineResult:
    research: AgentResult | None = None
    trade: AgentResult | None = None
    signals: list[Signal] = field(default_factory=list)
    markets_scanned: int = 0
    opportunities_found: int = 0
    success: bool = False
    error: str = ""
    total_elapsed: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def format_telegram_reply(self) -> str:
        """Format the result as a Telegram MarkdownV2 message."""
        lines = []

        if self.research and self.research.success:
            lines.append(f"🔭 *Day Scan* ({self.research.elapsed_seconds:.1f}s)")
            lines.append(self.research.output)
            lines.append("")

        if self.trade and self.trade.success:
            lines.append(f"⚡ *Day Trader* ({self.trade.elapsed_seconds:.1f}s)")
            lines.append(self.trade.output)
            lines.append("")

        if self.signals:
            lines.append(f"✅ *{len(self.signals)} day trade signal(s) queued*")
        elif self.success and self.opportunities_found == 0:
            lines.append("ℹ️ No short-term edges found right now — markets look fairly priced")
        elif self.success:
            lines.append("ℹ️ Opportunities found but none met execution thresholds")
        else:
            lines.append(f"❌ Scan failed: {self.error}")

        lines.append(f"\n⏱ Scan time: {self.total_elapsed:.1f}s")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "markets_scanned": self.markets_scanned,
            "opportunities_found": self.opportunities_found,
            "signals_generated": len(self.signals),
            "success": self.success,
            "error": self.error,
            "total_elapsed": self.total_elapsed,
            "timestamp": self.timestamp.isoformat(),
        }


class DayTraderPipeline:
    """
    Autonomous two-step pipeline: DayTraderResearcher → DayTrader.

    Unlike the social media AgentPipeline, this is triggered by the engine on a
    scheduled cycle — no user input required. It scans the live watchlist and
    fires trade signals when short-term opportunities are found.

    Usage:
        pipeline = DayTraderPipeline(researcher, day_trader)

        # In trading engine or via Telegram /scan command:
        result = await pipeline.scan(on_progress=telegram_callback)
        for signal in result.signals:
            engine._add_signal(signal)
    """

    def __init__(
        self,
        researcher: DayTraderResearcherAgent,
        day_trader: DayTraderAgent,
        market_configs: list[MarketConfig] | None = None,
        exchange_client: Any = None,
    ):
        self.researcher = researcher
        self.day_trader = day_trader
        # Named market configs keyed by series_ticker, e.g. {"KXBTC15M": BTC15MConfig()}
        self._market_configs: dict[str, MarketConfig] = {
            cfg.series_ticker: cfg for cfg in (market_configs or [])
        }
        self._exchange_client = exchange_client

    def register_market_config(self, config: MarketConfig):
        """Register a market-specific config. Replaces any existing one for same series."""
        self._market_configs[config.series_ticker] = config
        log.info(f"[DAY PIPELINE] Registered config for {config.series_ticker}: {config.display_name}")

    async def scan_configured_only(
        self,
        on_progress: ProgressCallback | None = None,
    ) -> DayTradePipelineResult:
        """
        Run ONLY the registered market-specific configs — no generic Claude scan.

        This is the lightweight version used by the engine on every cycle.
        It's fast (just fetches price data + runs deterministic logic) and doesn't
        burn Claude API tokens. Use scan() for the full pipeline including generic research.
        """
        overall_start = time.time()
        result = DayTradePipelineResult()

        if not self._market_configs:
            result.success = True
            result.total_elapsed = time.time() - overall_start
            return result

        configured_signals = await self._run_configured_markets(on_progress)
        result.signals.extend(configured_signals)
        result.markets_scanned = len(self._market_configs)
        result.opportunities_found = len(configured_signals)
        result.success = True
        result.total_elapsed = time.time() - overall_start

        if configured_signals:
            log.info(
                f"[bold green]DAY PIPELINE[/] Configured scan complete — "
                f"{len(configured_signals)} signal(s) in {result.total_elapsed:.1f}s"
            )

        return result

    async def scan(
        self,
        on_progress: ProgressCallback | None = None,
    ) -> DayTradePipelineResult:
        """
        Run a full day-trading scan cycle.

        Returns DayTradePipelineResult with any generated Signal objects.
        """
        overall_start = time.time()
        result = DayTradePipelineResult()

        log.info(
            f"[bold green]DAY PIPELINE[/] Starting scan — "
            f"{len(self._market_configs)} configured markets + generic scan"
        )

        # ── Step 0: Run configured market strategies first ─────────────────────
        configured_signals = await self._run_configured_markets(on_progress)
        result.signals.extend(configured_signals)

        # ── Step 1: Researcher scans all watched markets ───────────────────────
        try:
            if on_progress:
                await on_progress("🔭 *Day Trader scanning watched markets...*")

            research = await self.researcher.run()
            result.research = research
            result.markets_scanned = research.data.get("markets_scanned", 0)
            result.opportunities_found = research.data.get("opportunity_count", 0)

            if not research.success:
                result.error = f"Researcher failed: {research.output}"
                result.total_elapsed = time.time() - overall_start
                return result

            if on_progress:
                await on_progress(research.output)

        except Exception as e:
            log.error(f"[DAY PIPELINE] Researcher error: {e}")
            result.error = str(e)
            result.total_elapsed = time.time() - overall_start
            return result

        # Short-circuit: if no opportunities found, skip trader step
        if result.opportunities_found == 0:
            log.info("[DAY PIPELINE] No opportunities found — skipping trader step")
            result.success = True
            result.total_elapsed = time.time() - overall_start
            return result

        # ── Step 2: Day Trader converts opportunities to signals ───────────────
        try:
            opp_count = result.opportunities_found
            if on_progress:
                await on_progress(
                    f"⚡ *Day Trader processing {opp_count} opportunit{'y' if opp_count == 1 else 'ies'}...*"
                )

            trade = await self.day_trader.run(context=research.data)
            result.trade = trade

            # Reconstruct Signal objects from trader result
            signals_data = trade.data.get("signals", [])
            for s in signals_data:
                try:
                    result.signals.append(Signal(
                        market_id=s["market_id"],
                        market_title=s.get("market_title", ""),
                        exchange=s.get("exchange", "kalshi"),
                        action=SignalAction(s["action"]),
                        outcome=s["outcome"],
                        confidence=s["confidence"],
                        target_price=s.get("target_price"),
                        stop_loss=s.get("stop_loss"),
                        reasoning=s.get("reasoning", ""),
                        strategy="day_trader",
                        agent_id=s.get("agent_id", "btc15m_kalshi"),
                        execution_exchange=s.get("execution_exchange"),
                    ))
                except Exception as e:
                    log.warning(f"[DAY PIPELINE] Could not reconstruct signal: {e}")

            if on_progress:
                await on_progress(trade.output)

        except Exception as e:
            log.error(f"[DAY PIPELINE] Trader error: {e}")
            result.error = str(e)
            result.total_elapsed = time.time() - overall_start
            return result

        result.success = True
        result.total_elapsed = time.time() - overall_start

        log.info(
            f"[bold green]DAY PIPELINE COMPLETE[/] {result.total_elapsed:.1f}s | "
            f"{result.markets_scanned} scanned | "
            f"{result.opportunities_found} opportunities | "
            f"{len(result.signals)} signals"
        )

        return result

    async def _run_configured_markets(
        self,
        on_progress: ProgressCallback | None = None,
    ) -> list[Signal]:
        """
        Run each registered MarketConfig's generate_signal() method.
        These are high-precision, market-specific strategies (e.g. BTC15M).
        Returns any signals generated.
        """
        if not self._market_configs:
            return []

        signals: list[Signal] = []
        for series_ticker, config in self._market_configs.items():
            try:
                if on_progress:
                    await on_progress(
                        f"⚡ *{config.display_name}* — running specialist strategy..."
                    )

                log.info(f"[DAY PIPELINE] Running configured strategy: {config.display_name}")

                # Step 1: get the currently-active contract from the exchange
                market_id = None
                if self._exchange_client:
                    market_id = await config.get_current_market_id(self._exchange_client)

                if not market_id:
                    log.info(f"[DAY PIPELINE] No active market for {series_ticker} — skipping")
                    continue

                # Step 2: fetch fresh market data for context
                context = await self._build_market_context(market_id)
                context["market_id"] = market_id

                # Step 3: run the market-specific signal generator
                opportunity = await config.generate_signal(context)

                if opportunity is None:
                    log.info(f"[DAY PIPELINE] {series_ticker}: no opportunity this cycle")
                    continue

                # Step 4: convert opportunity → signal via DayTraderAgent
                trade_result = await self.day_trader.run(
                    context={"opportunities": [opportunity.to_dict()]}
                )
                for s in trade_result.data.get("signals", []):
                    try:
                        signals.append(Signal(
                            market_id=s["market_id"],
                            market_title=s.get("market_title", ""),
                            exchange=s.get("exchange", "kalshi"),
                            action=SignalAction(s["action"]),
                            outcome=s["outcome"],
                            confidence=s["confidence"],
                            target_price=s.get("target_price"),
                            stop_loss=s.get("stop_loss"),
                            reasoning=s.get("reasoning", ""),
                            strategy="day_trader",
                            agent_id=s.get("agent_id", "btc15m_kalshi"),
                            execution_exchange=s.get("execution_exchange"),
                        ))
                    except Exception as e:
                        log.warning(f"[DAY PIPELINE] Signal reconstruct error: {e}")

                if on_progress and signals:
                    await on_progress(trade_result.output)

            except Exception as e:
                log.error(f"[DAY PIPELINE] Configured market {series_ticker} error: {e}")

        return signals

    async def _build_market_context(self, market_id: str) -> dict:
        """
        Fetch fresh market data from the exchange and return as context dict.
        Falls back to empty context on error.
        """
        if not self._exchange_client:
            return {}
        try:
            data = await self._exchange_client._request("GET", f"/markets/{market_id}")
            m = data.get("market", data)
            return {
                "market_title": m.get("title", ""),
                "yes_ask": float(m.get("yes_ask_dollars", m.get("yes_ask", 0) / 100 if m.get("yes_ask") else 0)),
                "yes_bid": float(m.get("yes_bid_dollars", m.get("yes_bid", 0) / 100 if m.get("yes_bid") else 0)),
                "no_ask": float(m.get("no_ask_dollars", m.get("no_ask", 0) / 100 if m.get("no_ask") else 0)),
                "no_bid": float(m.get("no_bid_dollars", m.get("no_bid", 0) / 100 if m.get("no_bid") else 0)),
                "floor_strike": float(m.get("floor_strike", 0)),
                "open_time": m.get("open_time"),
                "close_time": m.get("close_time"),
                "volume": float(m.get("volume_fp", m.get("volume_24h_fp", 0))),
                "status": m.get("status", ""),
            }
        except Exception as e:
            log.warning(f"[DAY PIPELINE] Could not fetch context for {market_id}: {e}")
            return {}
