from __future__ import annotations

from abc import ABC, abstractmethod

from bot.core.signals import Signal
from bot.data.market_data import MarketSnapshot
from bot.data.news import NewsAnalysis


class Strategy(ABC):
    """Base class for trading strategies."""

    name: str = "base"

    @abstractmethod
    async def evaluate(
        self,
        snapshot: MarketSnapshot,
        news: NewsAnalysis | None = None,
    ) -> Signal | None:
        """Evaluate a market and optionally return a trading signal."""

    def __repr__(self) -> str:
        return f"<Strategy: {self.name}>"
