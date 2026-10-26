"""NFL Swing Engine — orchestrates live feed, market mapping, strategy, and execution."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from bot.config import settings
from bot.core.signals import Signal
from bot.data.espn_nfl import ESPNNFLFeed, GameEvent
from bot.data.nfl_markets import NFLMarketMapper
from bot.exchanges.base import Exchange, Side
from bot.nfl.config import NFL_MAX_POSITION_SIZE, NFL_POLL_INTERVAL
from bot.risk.manager import RiskManager
from bot.strategies.nfl_velocity import NFLVelocityStrategy
from bot.utils.logger import log


class NFLSwingEngine:
    """
    Main NFL live trading loop.

    Cycle: poll ESPN → map to Polymarket → evaluate velocity signals
           → risk check → execute → alert
    """

    def __init__(
        self,
        exchange: Exchange,
        risk: RiskManager,
        poll_interval: float = 0.0,
        game_ids: list[str] | None = None,
    ):
        self.exchange = exchange
        self.risk = risk
        self.poll_interval = poll_interval or NFL_POLL_INTERVAL
        self.game_ids = set(game_ids) if game_ids else None
        self.feed = ESPNNFLFeed(poll_interval=self.poll_interval)
        self.mapper = NFLMarketMapper()
        self.strategy = NFLVelocityStrategy()
        self._running = False
        self._trades: list[dict] = []
        self._callbacks: list = []

    def on_trade(self, callback) -> None:
        self._callbacks.append(callback)

    async def _notify(self, signal: Signal, executed: bool) -> None:
        for cb in self._callbacks:
            try:
                await cb(signal, executed)
            except Exception as exc:
                log.warning(f"[NFL Engine] Callback error: {exc}")

    async def run(self) -> None:
        """Main loop — runs until no live games remain or stopped."""
        self._running = True
        log.info("[bold cyan]NFL Swing Engine[/] starting...")
        initial_games, _ = await self.feed.poll()
        live_count = sum(1 for g in initial_games if g.is_live)
        log.info(f"[NFL Engine] Found {len(initial_games)} games, {live_count} live")

        await self.mapper.refresh()

        while self._running:
            try:
                await self._cycle()
            except Exception as exc:
                log.error(f"[NFL Engine] Cycle error: {exc}")

            await asyncio.sleep(self.poll_interval)

            # Stop if no live games
            live = self.feed.get_live_games()
            if self.game_ids:
                live = [g for g in live if g.game_id in self.game_ids]
            if not live and self._trades:
                log.info("[NFL Engine] No more live games — stopping")
                break

        log.info(f"[NFL Engine] Stopped. Total trades: {len(self._trades)}")

    async def run_game(self, game_id: str) -> None:
        """Track a single game."""
        self.game_ids = {game_id}
        await self.run()

    async def _cycle(self) -> None:
        games, events = await self.feed.poll()

        # Filter to tracked games if specified
        if self.game_ids:
            games = [g for g in games if g.game_id in self.game_ids]
            events = [e for e in events if e.game_id in self.game_ids]

        # Track scoring events for momentum
        for event in events:
            self.strategy.track_event(event)
            log.info(
                f"[NFL] Scoring: {event.team} +{event.points} ({event.event_type.value}) "
                f"→ {event.new_away_score}-{event.new_home_score}"
            )

        # Evaluate each live game
        for game in games:
            if not game.is_live:
                continue

            mapping = await self.mapper.map_game(
                game.game_id, game.home_team, game.away_team
            )
            if not mapping:
                continue

            # Get current Polymarket price for home team
            # Prefer cached price from mapper (no auth required), fall back to exchange
            poly_home_price = mapping.home_price if hasattr(mapping, 'home_price') else None
            if poly_home_price is None or poly_home_price == 0.5:
                poly_home_price = await self._get_poly_price(mapping)
            if poly_home_price is None:
                continue

            velocity = self.feed.get_velocity(game.game_id)

            signal = self.strategy.evaluate_game(
                game=game,
                mapping=mapping,
                poly_home_price=poly_home_price,
                velocity=velocity,
            )

            if signal and signal.is_actionable:
                await self._execute(signal)

    async def _get_poly_price(self, mapping) -> float | None:
        """Get current Polymarket price for the home team outcome."""
        try:
            if hasattr(self.exchange, 'get_orderbook'):
                book = await self.exchange.get_orderbook(mapping.market_id)
                if book and book.best_ask is not None:
                    return book.best_ask
            markets = await self.exchange.get_markets(query=mapping.question[:50], limit=5)
            for m in markets:
                if m.id == mapping.market_id:
                    return m.prices.get("Yes", m.prices.get(mapping.home_team, 0.5))
        except Exception as exc:
            log.debug(f"[NFL Engine] Price fetch failed: {exc}")
        return None

    async def _execute(self, signal: Signal) -> None:
        """Risk check and execute a signal."""
        check = self.risk.check(signal, self.exchange)
        if not check.approved:
            log.info(f"[NFL Engine] Risk rejected: {check.reason}")
            await self._notify(signal, executed=False)
            return

        size = min(check.adjusted_size, NFL_MAX_POSITION_SIZE)
        try:
            order = await self.exchange.place_order(
                market_id=signal.market_id,
                side=Side.BUY,
                price=signal.target_price or 0.5,
                size=size,
                outcome=signal.outcome,
            )
            self._trades.append({
                "signal": signal.to_dict(),
                "order_id": order.id if order else None,
                "size": size,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            log.info(
                f"[bold green]NFL TRADE[/] {signal.action.value.upper()} {signal.outcome} "
                f"on {signal.market_title} size={size:.1f} conf={signal.confidence:.0%}"
            )
            await self._notify(signal, executed=True)
        except Exception as exc:
            log.error(f"[NFL Engine] Order failed: {exc}")

    def stop(self) -> None:
        self._running = False

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    @property
    def trades(self) -> list[dict]:
        return list(self._trades)
