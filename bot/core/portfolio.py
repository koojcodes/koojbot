from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bot.exchanges.base import Exchange, Position
from bot.utils.db import Database
from bot.utils.logger import log


@dataclass
class PortfolioState:
    balance: float
    positions: list[Position]
    total_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def total_value(self) -> float:
        return self.balance + self.total_exposure + self.unrealized_pnl

    @property
    def num_positions(self) -> int:
        return len(self.positions)

    def to_dict(self) -> dict:
        return {
            "balance": self.balance,
            "total_value": self.total_value,
            "total_exposure": self.total_exposure,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "num_positions": self.num_positions,
            "timestamp": self.timestamp.isoformat(),
            "positions": [
                {
                    "market_id": p.market_id,
                    "market_title": p.market_title,
                    "outcome": p.outcome,
                    "size": p.size,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                    "exchange": p.exchange,
                }
                for p in self.positions
            ],
        }


class PortfolioTracker:
    """Tracks portfolio state and snapshots for the dashboard."""

    def __init__(self, db: Database):
        self.db = db
        self._realized_pnl = 0.0
        self._history: list[PortfolioState] = []

    async def get_state(self, exchange: Exchange) -> PortfolioState:
        """Get current portfolio state from the exchange."""
        balance = await exchange.get_balance()
        positions = await exchange.get_positions()

        total_exposure = sum(p.size * p.avg_entry_price for p in positions)
        unrealized_pnl = sum(p.unrealized_pnl for p in positions)

        state = PortfolioState(
            balance=balance,
            positions=positions,
            total_exposure=total_exposure,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=self._realized_pnl,
        )
        return state

    async def snapshot(self, exchange: Exchange) -> PortfolioState:
        """Take a portfolio snapshot and save to database."""
        state = await self.get_state(exchange)

        await self.db.snapshot_portfolio(
            balance=state.balance,
            total_exposure=state.total_exposure,
            unrealized_pnl=state.unrealized_pnl,
            realized_pnl=state.realized_pnl,
            num_positions=state.num_positions,
        )

        self._history.append(state)
        # Keep last 500 snapshots in memory
        if len(self._history) > 500:
            self._history = self._history[-500:]

        return state

    def record_realized_pnl(self, pnl: float):
        self._realized_pnl += pnl

    async def get_history(self, limit: int = 100) -> list[dict]:
        return await self.db.get_portfolio_history(limit=limit)

    def get_recent_states(self, limit: int = 50) -> list[dict]:
        return [s.to_dict() for s in self._history[-limit:]]
