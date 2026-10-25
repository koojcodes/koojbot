"""Polymarket copy trading agent — mirror leader wallet fills."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.config import settings
from bot.core.signals import Signal, SignalAction
from bot.data.polymarket_data import LeaderTrade, PolymarketDataClient
from bot.exchanges.base import Exchange, Side
from bot.utils.db import Database
from bot.utils.logger import log

if TYPE_CHECKING:
    from bot.data.market_data import MarketDataFetcher, MarketSnapshot


@dataclass
class CopySkipReason:
    trade: LeaderTrade
    reason: str


@dataclass
class CopyScanResult:
    agent_id: str
    leaders_polled: int
    new_trades: int
    signals: list[Signal] = field(default_factory=list)
    skipped: list[CopySkipReason] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "leaders_polled": self.leaders_polled,
            "new_trades": self.new_trades,
            "signals_generated": len(self.signals),
            "skipped_count": len(self.skipped),
            "timestamp": self.timestamp.isoformat(),
        }


class PolymarketCopyAgent:
    """Poll leader wallets and emit mirror signals for new fills."""

    agent_id = "polymarket_copy"

    def __init__(
        self,
        market_data: "MarketDataFetcher",
        exchange: Exchange,
        db: Database,
        data_client: PolymarketDataClient | None = None,
    ):
        self.market_data = market_data
        self.exchange = exchange
        self.db = db
        self.data_client = data_client or PolymarketDataClient()
        self._last_poll_at: datetime | None = None
        self._open_positions_per_leader: dict[str, int] = {}

    async def close(self):
        await self.data_client.close()

    async def scan(
        self,
        snapshots: dict[str, "MarketSnapshot"] | None = None,
    ) -> CopyScanResult:
        leaders = settings.POLY_COPY_LEADERS
        if not leaders:
            if self._last_poll_at is None:
                log.warning(
                    f"[{self.agent_id}] No leaders configured — set POLY_COPY_LEADERS in .env"
                )
            return CopyScanResult(agent_id=self.agent_id, leaders_polled=0, new_trades=0)

        self._last_poll_at = datetime.now(timezone.utc)
        result = CopyScanResult(
            agent_id=self.agent_id,
            leaders_polled=len(leaders),
            new_trades=0,
        )
        pool = snapshots or self.market_data.get_all_snapshots()

        for leader in leaders:
            cursor = await self.db.get_copy_cursor(leader)
            since_ts = cursor.get("last_timestamp", 0)
            try:
                trades = await self.data_client.get_wallet_trades(
                    leader,
                    since_ts=since_ts if since_ts > 0 else None,
                    limit=50,
                )
            except Exception as e:
                log.error(f"[{self.agent_id}] Failed to poll {leader[:10]}...: {e}")
                continue

            if since_ts == 0 and trades:
                latest = max(int(t.timestamp.timestamp()) for t in trades)
                await self.db.update_copy_cursor(
                    leader,
                    last_timestamp=latest,
                    last_tx_hash=trades[-1].tx_hash,
                )
                log.info(
                    f"[{self.agent_id}] Initialized cursor for {leader[:10]}… "
                    f"(skipped {len(trades)} historical trades)"
                )
                continue

            for trade in trades:
                trade_ts = int(trade.timestamp.timestamp())
                if trade_ts <= since_ts and cursor.get("last_tx_hash") == trade.tx_hash:
                    continue
                if await self.db.is_copy_trade_seen(trade.trade_key):
                    continue

                result.new_trades += 1
                signal, skip_reason = await self._trade_to_signal(trade, pool)
                await self.db.mark_copy_trade_seen(
                    trade_key=trade.trade_key,
                    leader_wallet=leader,
                    market_id=trade.market_id,
                    side=trade.side,
                    price=trade.price,
                    size=trade.size,
                    mirrored=signal is not None,
                    skip_reason=skip_reason or "",
                )

                if signal:
                    result.signals.append(signal)
                elif skip_reason:
                    result.skipped.append(CopySkipReason(trade=trade, reason=skip_reason))
                    log.info(
                        f"[{self.agent_id}] Skip {leader[:8]}… {trade.side} "
                        f"{trade.market_title[:40]}: {skip_reason}"
                    )

                await self.db.update_copy_cursor(
                    leader,
                    last_timestamp=max(trade_ts, since_ts),
                    last_tx_hash=trade.tx_hash,
                )

        if result.signals:
            log.info(
                f"[{self.agent_id}] Generated {len(result.signals)} mirror signal(s) "
                f"from {result.new_trades} new leader trade(s)"
            )
        return result

    async def _trade_to_signal(
        self,
        trade: LeaderTrade,
        snapshots: dict[str, "MarketSnapshot"],
    ) -> tuple[Signal | None, str]:
        now = datetime.now(timezone.utc)
        lag_seconds = (now - trade.timestamp).total_seconds()
        if lag_seconds > settings.POLY_COPY_MAX_LAG_SECONDS:
            return None, f"stale trade ({lag_seconds:.0f}s lag)"

        snapshot = snapshots.get(trade.market_id)
        if snapshot is None:
            for snap in snapshots.values():
                if snap.market.id == trade.market_id:
                    snapshot = snap
                    break

        if snapshot is None:
            poly_ex = getattr(self.exchange, "real", self.exchange)
            if hasattr(poly_ex, "name") and poly_ex.name == "polymarket":
                fetched = await self.market_data.fetch_market(trade.market_id, poly_ex)
                if fetched:
                    snapshot = fetched
                    self.market_data.add_to_watchlist(trade.market_id)
                    snapshots[trade.market_id] = fetched

        if snapshot is None:
            self.market_data.add_to_watchlist(trade.market_id)
            return None, "market data unavailable"

        market = snapshot.market
        if market.volume < settings.AGENT_POLY_MIN_VOLUME:
            return None, f"low volume ({market.volume:.0f})"

        if market.end_date is not None:
            end = market.end_date
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            minutes_left = (end - now).total_seconds() / 60
            if minutes_left < settings.AGENT_POLY_MIN_MINUTES_TO_CLOSE:
                return None, f"too close to resolution ({minutes_left:.0f}m)"

        if snapshot.orderbook and snapshot.orderbook.spread is not None:
            if snapshot.orderbook.spread > settings.AGENT_POLY_MAX_SPREAD:
                return None, f"spread too wide ({snapshot.orderbook.spread:.3f})"

        current_price = market.prices.get(trade.outcome, trade.price)
        slippage = abs(current_price - trade.price)
        if slippage > settings.POLY_COPY_MAX_SLIPPAGE:
            return None, f"slippage {slippage:.3f} > max {settings.POLY_COPY_MAX_SLIPPAGE}"

        action = SignalAction.BUY if trade.side == "BUY" else SignalAction.SELL

        if action == SignalAction.SELL:
            positions = await self.exchange.get_positions()
            has_position = any(
                p.market_id == trade.market_id and p.outcome == trade.outcome and p.size > 0
                for p in positions
            )
            if not has_position:
                return None, "no position to sell"

        leader_open = self._open_positions_per_leader.get(trade.leader_wallet, 0)
        if action == SignalAction.BUY and leader_open >= settings.POLY_COPY_MAX_POSITIONS_PER_LEADER:
            return None, f"max positions per leader ({settings.POLY_COPY_MAX_POSITIONS_PER_LEADER})"

        size = self._compute_mirror_size(trade, current_price)
        if size <= 0:
            return None, "mirror size zero"

        if action == SignalAction.BUY:
            self._open_positions_per_leader[trade.leader_wallet] = leader_open + 1

        meta = {
            "leader_wallet": trade.leader_wallet,
            "leader_tx_hash": trade.tx_hash,
            "leader_price": trade.price,
            "leader_size": trade.size,
            "lag_seconds": round(lag_seconds, 1),
            "slippage": round(slippage, 4),
        }

        return Signal(
            market_id=trade.market_id,
            market_title=trade.market_title,
            exchange="polymarket",
            action=action,
            outcome=trade.outcome,
            confidence=0.85,
            target_price=current_price,
            size_suggestion=size,
            reasoning=json.dumps(meta),
            strategy="copy_trade",
            agent_id=self.agent_id,
            execution_exchange="polymarket",
            leader_wallet=trade.leader_wallet,
            timestamp=now,
        ), ""

    def _compute_mirror_size(self, trade: LeaderTrade, current_price: float) -> float:
        if current_price <= 0:
            return 0.0

        mode = settings.POLY_COPY_SIZING_MODE
        if mode == "pct_leader":
            return trade.size * settings.POLY_COPY_SIZING_PCT
        if mode == "pct_portfolio":
            balance = settings.INITIAL_BALANCE
            if hasattr(self.exchange, "balance"):
                balance = getattr(self.exchange, "balance", balance) or balance
            target_usd = balance * settings.POLY_COPY_SIZING_PCT
            return target_usd / current_price

        fixed_usd = settings.POLY_COPY_FIXED_USD
        return fixed_usd / current_price
