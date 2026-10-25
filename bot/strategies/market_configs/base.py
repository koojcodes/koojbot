"""
Base MarketConfig — defines the rules, parameters, and signal logic for a specific
prediction market series. Each market type gets its own subclass.

Design intent:
  - The generic DayTraderResearcherAgent is great for scanning unknown markets.
  - For markets we trade regularly (like BTC 15-min), we write a dedicated config
    with market-specific logic: the right data sources, entry timing, edge calculations,
    position sizing, and resolution awareness.
  - The DayTraderPipeline checks for a matching MarketConfig before falling back
    to the generic Claude scan.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from bot.agents.day_trader_researcher import DayTradeOpportunity


@dataclass
class EntryWindow:
    """Defines when in a contract's life we're allowed to enter."""
    earliest_minutes_after_open: float   # don't enter before this (spreads too wide early)
    latest_minutes_before_close: float   # don't enter after this (no time left)

    def is_open(self, minutes_elapsed: float, minutes_remaining: float) -> bool:
        return (
            minutes_elapsed >= self.earliest_minutes_after_open
            and minutes_remaining >= self.latest_minutes_before_close
        )


@dataclass
class FeeModel:
    """
    Fee structure for the market.
    Kalshi quadratic: fee = 0.07 * C * (1-C) per contract (capped at 0.035).
    """
    fee_type: str              # "quadratic" | "flat" | "percentage"
    rate: float = 0.07         # quadratic rate or flat rate

    def estimate_fee(self, yes_price: float) -> float:
        """Estimate one-way fee per $1 contract at given yes_price."""
        if self.fee_type == "quadratic":
            c = max(0.01, min(0.99, yes_price))
            return min(0.035, self.rate * c * (1 - c))
        elif self.fee_type == "flat":
            return self.rate
        elif self.fee_type == "percentage":
            return yes_price * self.rate
        return 0.0

    def round_trip_cost(self, yes_price: float, spread: float = 0.0) -> float:
        """Total cost to buy and hold to resolution (2× fee + spread)."""
        return 2 * self.estimate_fee(yes_price) + spread


@dataclass
class PositionSizingRules:
    """How to size positions for this market."""
    max_contracts: int = 50         # max contracts per trade
    max_pct_of_balance: float = 0.05   # max 5% of balance per trade
    scale_by_confidence: bool = True   # reduce size when confidence is lower
    min_contracts: int = 1


class MarketConfig(ABC):
    """
    Abstract base class for market-specific trading configurations.

    Subclasses implement:
      - generate_signal(): The core signal logic for this market type
      - get_current_market_id(): How to find the active contract
    """

    # Subclasses set these as class attributes
    series_ticker: str = ""          # e.g. "KXBTC15M"
    display_name: str = ""           # e.g. "BTC 15-Minute Price Direction"
    exchange: str = "kalshi"
    description: str = ""

    # When in a contract's life to trade
    entry_window: EntryWindow = None  # type: ignore  — set in subclass __init__

    # Fee model
    fees: FeeModel = None  # type: ignore

    # Position sizing
    sizing: PositionSizingRules = None  # type: ignore

    # Minimum edge (in probability terms) after accounting for fees+spread
    min_net_edge: float = 0.10

    # Minimum confidence to fire a signal
    min_confidence: float = 0.60

    # Whether to prefer holding to resolution vs. early exit
    prefer_hold_to_resolution: bool = True

    def __init__(self):
        """Default init — subclasses should call super().__init__() or set fields directly."""
        if self.entry_window is None:
            self.entry_window = EntryWindow(
                earliest_minutes_after_open=2.0,
                latest_minutes_before_close=3.0,
            )
        if self.fees is None:
            self.fees = FeeModel(fee_type="quadratic")
        if self.sizing is None:
            self.sizing = PositionSizingRules()

    @abstractmethod
    async def generate_signal(self, context: dict) -> DayTradeOpportunity | None:
        """
        Analyze the current market state and generate a trading opportunity,
        or return None if no edge is detected.

        context: any additional data (e.g. injected by the pipeline)
        """
        ...

    @abstractmethod
    async def get_current_market_id(self, exchange_client: Any) -> str | None:
        """
        Find the currently-active contract ID for this market series.
        Returns None if no active contract is found.
        """
        ...

    def describe(self) -> str:
        """Human-readable description of this config."""
        return (
            f"{self.display_name} [{self.series_ticker}]\n"
            f"  Exchange: {self.exchange}\n"
            f"  Entry window: {self.entry_window.earliest_minutes_after_open}min after open "
            f"to {self.entry_window.latest_minutes_before_close}min before close\n"
            f"  Min net edge: {self.min_net_edge:.0%} | Min confidence: {self.min_confidence:.0%}\n"
            f"  Fee model: {self.fees.fee_type} (rate={self.fees.rate})\n"
            f"  Hold to resolution: {self.prefer_hold_to_resolution}"
        )
