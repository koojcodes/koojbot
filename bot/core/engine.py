"""Main async trading engine — fetch → analyze → conviction score → risk → execute."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from bot.config import settings
from bot.core.agent_registry import AgentQualityGate, TradingAgentSpec
from bot.core.portfolio import PortfolioTracker
from bot.core.signals import Signal, SignalAction
from bot.data.market_data import MarketDataFetcher
from bot.data.news import NewsCollector
from bot.exchanges.base import Exchange, Side
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy
from bot.strategies.conviction import ConvictionScorer
from bot.strategies.news_driven import NewsDrivenStrategy
from bot.strategies.momentum import MomentumStrategy
from bot.utils.db import Database
from bot.utils.logger import log

if TYPE_CHECKING:
    from bot.agents.day_trader_pipeline import DayTraderPipeline
    from bot.agents.polymarket_copy_agent import PolymarketCopyAgent
    from bot.agents.stocks_etf_paper_agent import StocksEtfPaperAgent

# How often (in cycles) to re-run auto-discovery
_AUTO_DISCOVER_EVERY_N_CYCLES = 360  # ~6 hrs at 60s poll

# How often (in cycles) to run the day trader configured-market scan
# Default: every cycle. With 30s poll interval this runs every 30 seconds.
_DAY_TRADER_EVERY_N_CYCLES = 1

# How often (in cycles) to re-seed series market contracts (rotate expired ones)
# 30 cycles × 30s = 15 min — matches KXBTC15M window length
_DAY_TRADER_RESEED_EVERY_N_CYCLES = 30


class TradingEngine:
    """
    Main async trading loop.

    Cycle: fetch data → news analysis → run strategies → conviction score
           → check risk → execute trades → log
    """

    def __init__(
        self,
        exchange: Exchange,
        strategies: list[Strategy],
        market_data: MarketDataFetcher,
        news: NewsCollector,
        risk: RiskManager,
        portfolio: PortfolioTracker,
        db: Database,
        poll_interval: float = 30.0,
        snapshot_interval: float = 300.0,
        day_trader_pipeline: "DayTraderPipeline | None" = None,
        polymarket_copy_agent: "PolymarketCopyAgent | None" = None,
        stocks_etf_agent: "StocksEtfPaperAgent | None" = None,
        agent_registry: list[TradingAgentSpec] | None = None,
        exchange_map: dict[str, Exchange] | None = None,
        quality_gates: dict[str, AgentQualityGate] | None = None,
        approved_markets=None,
    ):
        self.exchange = exchange
        self.strategies = strategies
        self.market_data = market_data
        self.news = news
        self.risk = risk
        self.portfolio = portfolio
        self.db = db
        self.poll_interval = poll_interval
        self.snapshot_interval = snapshot_interval
        self.day_trader_pipeline = day_trader_pipeline
        self.polymarket_copy_agent = polymarket_copy_agent
        self.stocks_etf_agent = stocks_etf_agent
        self._last_copy_poll_at: float = 0.0
        self.agent_registry = agent_registry or []
        self._exchange_map = exchange_map or {}
        self._quality_gates = quality_gates or {}
        self.approved_markets = approved_markets   # ApprovedMarketsStore | None
        # Set by TelegramBot after construction — called when a new market is found
        self.on_market_proposed = None
        self._running = False
        self._signals: list[Signal] = []
        self._cycle_count = 0
        self._scorer = ConvictionScorer()

        # Phase 6: Resolution checker for simulation accuracy tracking
        self._resolution_checker = None
        self._weight_adjuster = None
        if settings.SWARM_ENABLED:
            try:
                from bot.swarm.resolution_checker import ResolutionChecker
                self._resolution_checker = ResolutionChecker(exchange)
            except Exception:
                pass
            try:
                from bot.swarm.weight_adjuster import WeightAdjuster
                self._weight_adjuster = WeightAdjuster()
            except Exception:
                pass
            try:
                from bot.swarm.scheduled_simulations import ScheduledSimulationRunner
                self._scheduled_sims = ScheduledSimulationRunner(market_data)
            except Exception:
                self._scheduled_sims = None

        # Identify strategy types
        self._news_strategy = next(
            (s for s in strategies if isinstance(s, NewsDrivenStrategy)), None
        )
        self._momentum_strategy = next(
            (s for s in strategies if isinstance(s, MomentumStrategy)), None
        )
        # Any other strategies (fallback individual evaluation)
        self._other_strategies = [
            s for s in strategies
            if not isinstance(s, (NewsDrivenStrategy, MomentumStrategy))
        ]

    async def start(self):
        """Start the trading engine."""
        log.info("[bold green]Starting MaxTheTrader engine[/]")
        log.info(f"Exchange: [cyan]{self.exchange.name}[/]")
        log.info(f"Strategies: {[s.name for s in self.strategies]}")
        log.info(f"Paper mode: [yellow]{settings.PAPER_MODE}[/]")
        log.info(f"Poll interval: {self.poll_interval}s")
        if self.day_trader_pipeline:
            configs = list(self.day_trader_pipeline._market_configs.keys())
            log.info(f"[bold cyan]Day Trader:[/] active — {len(configs)} market config(s): {configs}")
        if self.agent_registry:
            enabled = [a.agent_id for a in self.agent_registry if a.enabled]
            log.info(f"[bold cyan]Agents:[/] active — {enabled}")

        await self.db.init()
        self._running = True

        # Restore previously-approved markets into the live watchlist
        if self.approved_markets:
            restored = self.approved_markets.get_all_approved()
            for m in restored:
                self.market_data.add_to_watchlist(m.market_id)
            if restored:
                log.info(f"[WATCHLIST] Restored {len(restored)} approved market(s) to watchlist")

        # Auto-discover markets at startup
        try:
            await self.market_data.auto_discover_markets(
                approved_markets=self.approved_markets,
                on_market_proposed=self.on_market_proposed,
            )
        except Exception as e:
            log.warning(f"Initial auto-discovery failed: {e}")

        # Seed active contracts for all day trader series (e.g. KXBTC15M)
        if self.day_trader_pipeline:
            await self._reseed_day_trader_markets()

        await asyncio.gather(
            self._trading_loop(),
            self._snapshot_loop(),
        )

    async def stop(self):
        self._running = False

    async def _trading_loop(self):
        while self._running:
            try:
                await self._cycle()
                self._cycle_count += 1
                # Re-run auto-discovery periodically
                if self._cycle_count % _AUTO_DISCOVER_EVERY_N_CYCLES == 0:
                    try:
                        await self.market_data.auto_discover_markets(
                            approved_markets=self.approved_markets,
                            on_market_proposed=self.on_market_proposed,
                        )
                    except Exception as e:
                        log.warning(f"Periodic auto-discovery failed: {e}")

                # Phase 6: Check for resolved markets and score predictions
                if self._resolution_checker and self._cycle_count % 20 == 0:
                    try:
                        await self._resolution_checker.check_resolutions()
                    except Exception as e:
                        log.warning(f"Resolution check failed: {e}")

                # Phase 6: Adjust archetype weights every ~6 hours
                if (
                    self._weight_adjuster
                    and self._cycle_count > 0
                    and self._cycle_count % _AUTO_DISCOVER_EVERY_N_CYCLES == 0
                ):
                    try:
                        await self._weight_adjuster.apply_adjustments()
                    except Exception as e:
                        log.warning(f"Weight adjustment failed: {e}")

                # Phase 6: Scheduled daily simulations (check every ~30 min)
                if (
                    getattr(self, '_scheduled_sims', None)
                    and self._cycle_count > 0
                    and self._cycle_count % 60 == 0  # Every 60 cycles (~30 min at 30s)
                ):
                    try:
                        await self._scheduled_sims.maybe_run_daily()
                    except Exception as e:
                        log.warning(f"Scheduled simulation failed: {e}")
            except Exception as e:
                log.error(f"Trading cycle error: {e}")

            # Re-seed day trader series contracts on schedule
            if (
                self.day_trader_pipeline
                and self._cycle_count > 0
                and self._cycle_count % _DAY_TRADER_RESEED_EVERY_N_CYCLES == 0
            ):
                try:
                    await self._reseed_day_trader_markets()
                except Exception as e:
                    log.warning(f"Day trader reseed failed: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _snapshot_loop(self):
        while self._running:
            try:
                state = await self.portfolio.snapshot(self.exchange)
                log.info(
                    f"[dim]Portfolio: ${state.total_value:.2f} "
                    f"(balance: ${state.balance:.2f}, "
                    f"exposure: ${state.total_exposure:.2f}, "
                    f"P&L: ${state.unrealized_pnl:+.2f})[/]"
                )
            except Exception as e:
                log.error(f"Snapshot error: {e}")
            await asyncio.sleep(self.snapshot_interval)

    async def _cycle(self):
        """Single trading cycle."""

        # 1. Refresh market data
        snapshots = await self.market_data.refresh_watchlist()
        if not snapshots:
            return

        # 2. Check stop losses and time-based exits first
        exit_signals = await self.risk.check_stop_losses(self.exchange)
        for signal in exit_signals:
            await self._execute_signal(signal)

        # 3. Run day trader configured-market scan (fast, no Claude API cost)
        await self._run_registered_agents(snapshots)

        # 4. Run strategies on each market
        for market_id, snapshot in snapshots.items():
            if snapshot.market.exchange == "polymarket":
                if self._is_agent_enabled("polymarket_news"):
                    continue
                if self._is_agent_enabled("polymarket_copy"):
                    continue
            await self._evaluate_market(snapshot)

    def _is_agent_enabled(self, agent_id: str) -> bool:
        return any(agent.agent_id == agent_id and agent.enabled for agent in self.agent_registry)

    async def _run_registered_agents(self, snapshots: dict[str, object]):
        if not self.agent_registry:
            if self.day_trader_pipeline and self._cycle_count % _DAY_TRADER_EVERY_N_CYCLES == 0:
                await self._run_day_trader_cycle()
            return

        for agent in self.agent_registry:
            if not agent.enabled:
                continue
            if agent.runner == "day_trader":
                if self.day_trader_pipeline and self._cycle_count % _DAY_TRADER_EVERY_N_CYCLES == 0:
                    await self._run_day_trader_cycle(agent_id=agent.agent_id)
            elif agent.runner == "polymarket_news":
                await self._run_polymarket_news_agent(agent.agent_id, snapshots)
            elif agent.runner == "polymarket_copy":
                await self._run_polymarket_copy_agent(snapshots)
            elif agent.runner == "stocks_etf_shell":
                await self._run_stocks_etf_shell(agent.agent_id)

    async def _reseed_day_trader_markets(self):
        """
        Refresh the watchlist with the currently-active contract for each configured
        day trader series. Called at startup and every ~15 minutes.

        For KXBTC15M: rotates out the expired contract and adds the fresh one so the
        Researcher agent always finds a valid, tradeable market ID in its searches.
        """
        exchange_client = self.day_trader_pipeline._exchange_client
        if not exchange_client:
            return
        for series_ticker in self.day_trader_pipeline._market_configs:
            market_id = await self.market_data.seed_series_market(
                series_ticker, exchange_client
            )
            if market_id:
                log.info(
                    f"[bold cyan]DAY TRADER[/] Active contract: "
                    f"{series_ticker} → {market_id}"
                )

    async def _run_day_trader_cycle(self, agent_id: str = "btc15m_kalshi"):
        """
        Run the day trader's configured-market strategies (e.g. BTC15M).

        This is the fast path — no generic Claude scan. Runs on every cycle
        and only makes HTTP calls to Kalshi + Coinbase, not the Anthropic API.
        Any signals generated are injected directly into the execution queue.
        """
        try:
            result = await self.day_trader_pipeline.scan_configured_only()
            for signal in result.signals:
                signal.agent_id = agent_id
                signal.execution_exchange = signal.execution_exchange or signal.exchange
                self._add_signal(signal)
                await self._execute_signal(signal)
        except Exception as e:
            log.error(f"[DAY TRADER] Cycle error: {e}")

    async def _run_polymarket_news_agent(self, agent_id: str, snapshots: dict[str, object]):
        try:
            candidates = self.market_data.select_polymarket_candidates(
                snapshots=snapshots,
                min_volume=settings.AGENT_POLY_MIN_VOLUME,
                max_spread=settings.AGENT_POLY_MAX_SPREAD,
                min_minutes_to_close=settings.AGENT_POLY_MIN_MINUTES_TO_CLOSE,
            )
            if not candidates:
                await self._prime_polymarket_watchlist()
                return
            gate = self._quality_gates.get(agent_id)
            for snapshot in candidates:
                signal = await self._evaluate_market(snapshot, auto_execute=False, agent_id=agent_id)
                if not signal:
                    continue
                if gate and signal.confidence < gate.min_signal_confidence:
                    continue
                signal.execution_exchange = "polymarket"
                self._add_signal(signal)
                await self._execute_signal(signal)
        except Exception as e:
            log.error(f"[POLYMARKET NEWS] Cycle error: {e}")

    async def _run_polymarket_copy_agent(self, snapshots: dict[str, object]):
        if not self.polymarket_copy_agent:
            return
        import time
        now = time.monotonic()
        if now - self._last_copy_poll_at < settings.POLY_COPY_POLL_INTERVAL:
            return
        self._last_copy_poll_at = now
        try:
            result = await self.polymarket_copy_agent.scan(snapshots)
            for signal in result.signals:
                self._add_signal(signal)
                await self._execute_signal(signal)
        except Exception as e:
            log.error(f"[POLYMARKET COPY] Cycle error: {e}")

    async def _prime_polymarket_watchlist(self):
        if self._cycle_count % 10 != 0:
            return
        try:
            seeded = 0
            for query in ("bitcoin", "crypto", "fed", "inflation"):
                markets = await self.market_data.search_markets(query=query, limit=5)
                for market in markets:
                    if market.exchange != "polymarket":
                        continue
                    if market.volume < settings.AGENT_POLY_MIN_VOLUME:
                        continue
                    self.market_data.add_to_watchlist(market.id)
                    seeded += 1
                    if seeded >= 5:
                        break
                if seeded >= 5:
                    break
            if seeded:
                log.info(f"[polymarket_news] Seeded {seeded} markets into watchlist")
        except Exception as e:
            log.warning(f"[polymarket_news] Watchlist seeding failed: {e}")

    async def _run_stocks_etf_shell(self, agent_id: str):
        if not self.stocks_etf_agent:
            return
        if self._cycle_count % 20 != 0:
            return
        try:
            result = await self.stocks_etf_agent.scan()
            log.info(f"[{agent_id}] {result.note}")
        except Exception as e:
            log.error(f"[{agent_id}] Cycle error: {e}")

    async def _evaluate_market(self, snapshot, *, auto_execute: bool = True, agent_id: str = "core_engine"):
        """Evaluate a single market across all strategies and produce a conviction signal."""
        market_id = snapshot.market.id

        # Fetch news analysis (shared between news strategy and conviction scorer)
        news_analysis = None
        if self._news_strategy is not None:
            try:
                news_analysis = await self.news.analyze_market_news(
                    market_title=snapshot.market.title,
                    market_id=market_id,
                    current_price=snapshot.market.prices.get("Yes", 0.5),
                )
            except Exception as e:
                log.error(f"News analysis error for {market_id}: {e}")

        # Get individual strategy signals
        news_signal = None
        momentum_signal = None

        if self._news_strategy is not None:
            try:
                news_signal = await self._news_strategy.evaluate(snapshot, news_analysis)
            except Exception as e:
                log.error(f"News strategy error on {market_id}: {e}")

        if self._momentum_strategy is not None:
            try:
                momentum_signal = await self._momentum_strategy.evaluate(snapshot, None)
            except Exception as e:
                log.error(f"Momentum strategy error on {market_id}: {e}")

        # Run conviction scorer if we have news + momentum strategies
        if self._news_strategy is not None or self._momentum_strategy is not None:
            conviction = self._scorer.score(news_signal, momentum_signal)
            if conviction and conviction.is_actionable:
                conviction.agent_id = agent_id
                conviction.execution_exchange = conviction.execution_exchange or snapshot.market.exchange
                if auto_execute:
                    self._add_signal(conviction)
                    await self._execute_signal(conviction)
                else:
                    return conviction

        # Run any other strategies independently (no conviction scoring)
        for strategy in self._other_strategies:
            try:
                signal = await strategy.evaluate(snapshot, news_analysis)
                if signal and signal.is_actionable:
                    signal.agent_id = agent_id
                    signal.execution_exchange = signal.execution_exchange or snapshot.market.exchange
                    if auto_execute:
                        self._add_signal(signal)
                        await self._execute_signal(signal)
                    else:
                        return signal
            except Exception as e:
                log.error(f"Strategy {strategy.name} error on {market_id}: {e}")
        return None

    def _add_signal(self, signal: Signal):
        self._signals.append(signal)
        if len(self._signals) > 100:
            self._signals = self._signals[-100:]

    async def _execute_signal(self, signal: Signal):
        """Execute a trading signal through risk management."""
        execution_exchange = self._get_exchange_for_signal(signal)
        execution_exchange_name = (
            signal.execution_exchange
            or signal.exchange
            or getattr(execution_exchange, "name", self.exchange.name)
        )

        await self.db.log_signal(
            market_id=signal.market_id,
            market_title=signal.market_title,
            exchange=execution_exchange_name,
            strategy=signal.strategy,
            action=signal.action.value,
            confidence=signal.confidence,
            target_price=signal.target_price,
            reasoning=signal.reasoning,
            agent_id=signal.agent_id,
            leader_wallet=signal.leader_wallet,
        )

        risk_check = await self.risk.check_signal(signal, execution_exchange)
        if not risk_check.approved:
            log.info(f"[dim]Signal rejected: {risk_check.reason}[/]")
            return

        side = Side.BUY if signal.action == SignalAction.BUY else Side.SELL
        price = signal.target_price or 0.0

        try:
            order = await execution_exchange.place_order(
                market_id=signal.market_id,
                side=side,
                size=risk_check.adjusted_size,
                price=price,
                outcome=signal.outcome,
            )

            log.info(
                f"[bold green]ORDER[/] {order.status.value}: {side.value} "
                f"{risk_check.adjusted_size:.2f} {signal.outcome} @ {price:.4f} "
                f"on [bold]{signal.market_title}[/]"
            )

            await self.db.log_trade(
                trade_id=order.id,
                market_id=signal.market_id,
                exchange=execution_exchange_name,
                side=side.value,
                outcome=signal.outcome,
                price=price,
                size=risk_check.adjusted_size,
                strategy=signal.strategy,
                agent_id=signal.agent_id,
                paper=settings.PAPER_MODE,
                leader_wallet=signal.leader_wallet,
            )

        except Exception as e:
            log.error(f"Order execution failed: {e}")

    def _get_exchange_for_signal(self, signal: Signal) -> Exchange:
        exchange_name = (signal.execution_exchange or signal.exchange or "").lower()
        if exchange_name in self._exchange_map:
            return self._exchange_map[exchange_name]
        if exchange_name.startswith("paper:"):
            stripped = exchange_name.split("paper:", 1)[1]
            if stripped in self._exchange_map:
                return self._exchange_map[stripped]
        return self.exchange

    def get_recent_signals(self, limit: int = 20) -> list[dict]:
        return [s.to_dict() for s in self._signals[-limit:]]
