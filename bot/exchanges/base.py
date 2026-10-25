from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Market:
    id: str
    title: str
    description: str
    exchange: str  # "kalshi" or "polymarket"
    outcomes: list[str]  # e.g. ["Yes", "No"]
    prices: dict[str, float]  # outcome -> price (0-1)
    volume: float = 0.0
    end_date: datetime | None = None
    category: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    market_id: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


@dataclass
class Order:
    id: str
    market_id: str
    side: Side
    price: float
    size: float
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    outcome: str = "Yes"
    exchange: str = ""


@dataclass
class Position:
    market_id: str
    market_title: str
    outcome: str
    size: float
    avg_entry_price: float
    current_price: float = 0.0
    exchange: str = ""
    entered_at: datetime | None = None  # when the position was first opened

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_entry_price) * self.size

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_entry_price == 0:
            return 0.0
        return (self.current_price - self.avg_entry_price) / self.avg_entry_price


class Exchange(ABC):
    """Abstract base class for exchange integrations."""

    name: str = "base"

    @abstractmethod
    async def get_markets(
        self, query: str = "", limit: int = 50, category: str = ""
    ) -> list[Market]:
        """Search/list available markets."""

    @abstractmethod
    async def get_market(self, market_id: str) -> Market:
        """Get a single market by ID."""

    @abstractmethod
    async def get_orderbook(self, market_id: str) -> OrderBook:
        """Get the order book for a market."""

    @abstractmethod
    async def get_price(self, market_id: str, outcome: str = "Yes") -> float:
        """Get the current price for an outcome (0-1 scale)."""

    @abstractmethod
    async def place_order(
        self,
        market_id: str,
        side: Side,
        size: float,
        price: float,
        outcome: str = "Yes",
    ) -> Order:
        """Place a limit order. Returns the order with its ID."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Get available balance in USD."""

    @abstractmethod
    async def get_open_orders(self) -> list[Order]:
        """Get all open/pending orders."""
