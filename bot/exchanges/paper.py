"""Paper trading exchange wrapper — uses real market data but simulates order fills locally."""
from __future__ import annotations

import uuid
from datetime import datetime, date, timezone
from dataclasses import dataclass, field

from bot.config import settings
from bot.exchanges.base import (
    Exchange, Market, OrderBook, Order, Position,
    Side, OrderStatus,
)


@dataclass
class PaperPosition:
    market_id: str
    market_title: str
    outcome: str
    size: float
    avg_entry_price: float
    exchange: str
    entered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PaperTrade:
    id: str
    market_id: str
    side: Side
    outcome: str
    price: float
    size: float
    timestamp: datetime
    exchange: str


class PaperExchange(Exchange):
    """Paper trading wrapper. Uses real market data but simulates order execution locally."""

    name = "paper"

    def __init__(self, real_exchange: Exchange, initial_balance: float = 0.0):
        self.real = real_exchange
        self.balance = initial_balance or settings.INITIAL_BALANCE
        self.initial_balance = self.balance
        self._positions: dict[str, PaperPosition] = {}  # key: f"{market_id}:{outcome}"
        self._orders: dict[str, Order] = {}
        self._trades: list[PaperTrade] = []
        self._daily_pnl: dict[str, float] = {}  # date_str -> realized pnl

    # --- Delegate read-only calls to the real exchange ---

    async def get_markets(self, query: str = "", limit: int = 50, category: str = "") -> list[Market]:
        return await self.real.get_markets(query=query, limit=limit, category=category)

    async def get_market(self, market_id: str) -> Market:
        return await self.real.get_market(market_id)

    async def get_orderbook(self, market_id: str) -> OrderBook:
        return await self.real.get_orderbook(market_id)

    async def get_price(self, market_id: str, outcome: str = "Yes") -> float:
        return await self.real.get_price(market_id, outcome)

    # --- Simulated trading ---

    async def place_order(
        self,
        market_id: str,
        side: Side,
        size: float,
        price: float,
        outcome: str = "Yes",
    ) -> Order:
        order_id = f"paper-{uuid.uuid4().hex[:12]}"

        cost = price * size
        if side == Side.BUY and cost > self.balance:
            return Order(
                id=order_id, market_id=market_id, side=side, price=price,
                size=size, status=OrderStatus.REJECTED, outcome=outcome,
                exchange=f"paper:{self.real.name}",
            )

        order = Order(
            id=order_id, market_id=market_id, side=side, price=price,
            size=size, status=OrderStatus.FILLED, filled_size=size,
            outcome=outcome, exchange=f"paper:{self.real.name}",
        )

        self._execute_fill(order)
        self._orders[order_id] = order
        return order

    def _execute_fill(self, order: Order):
        """Update balance and positions after a fill."""
        key = f"{order.market_id}:{order.outcome}"
        cost = order.price * order.filled_size

        if order.side == Side.BUY:
            self.balance -= cost
            existing = self._positions.get(key)
            if existing:
                # Average in — keep original entered_at
                total_size = existing.size + order.filled_size
                existing.avg_entry_price = (
                    (existing.avg_entry_price * existing.size + order.price * order.filled_size)
                    / total_size
                )
                existing.size = total_size
            else:
                self._positions[key] = PaperPosition(
                    market_id=order.market_id,
                    market_title="",
                    outcome=order.outcome,
                    size=order.filled_size,
                    avg_entry_price=order.price,
                    exchange=f"paper:{self.real.name}",
                    entered_at=datetime.now(timezone.utc),
                )
        else:  # SELL
            existing = self._positions.get(key)
            if existing and existing.size >= order.filled_size:
                pnl = (order.price - existing.avg_entry_price) * order.filled_size
                self.balance += cost
                existing.size -= order.filled_size
                if existing.size <= 0.001:
                    del self._positions[key]

                today = date.today().isoformat()
                self._daily_pnl[today] = self._daily_pnl.get(today, 0.0) + pnl
            else:
                self.balance += cost  # Short sell proceeds

        # Record trade
        self._trades.append(PaperTrade(
            id=order.id,
            market_id=order.market_id,
            side=order.side,
            outcome=order.outcome,
            price=order.price,
            size=order.filled_size,
            timestamp=datetime.now(timezone.utc),
            exchange=f"paper:{self.real.name}",
        ))

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    async def get_positions(self) -> list[Position]:
        positions = []
        for key, pp in self._positions.items():
            current_price = 0.0
            try:
                current_price = await self.real.get_price(pp.market_id, pp.outcome)
            except Exception:
                pass
            positions.append(Position(
                market_id=pp.market_id,
                market_title=pp.market_title,
                outcome=pp.outcome,
                size=pp.size,
                avg_entry_price=pp.avg_entry_price,
                current_price=current_price,
                exchange=pp.exchange,
                entered_at=pp.entered_at,
            ))
        return positions

    async def get_balance(self) -> float:
        return self.balance

    async def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == OrderStatus.PENDING]

    # --- Paper-specific properties ---

    @property
    def total_trades(self) -> int:
        return len(self._trades)

    @property
    def realized_pnl(self) -> float:
        return sum(self._daily_pnl.values())

    @property
    def today_pnl(self) -> float:
        return self._daily_pnl.get(date.today().isoformat(), 0.0)

    def get_trade_history(self) -> list[dict]:
        return [
            {
                "id": t.id,
                "market_id": t.market_id,
                "side": t.side.value,
                "outcome": t.outcome,
                "price": t.price,
                "size": t.size,
                "timestamp": t.timestamp.isoformat(),
                "exchange": t.exchange,
            }
            for t in self._trades
        ]

    async def close(self):
        await self.real.close()
