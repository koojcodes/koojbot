"""Stocks/ETF paper-only scaffold agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.utils.logger import log


@dataclass
class StocksEtfPaperScanResult:
    agent_id: str
    success: bool
    scanned_assets: int
    generated_signals: int
    note: str
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "success": self.success,
            "scanned_assets": self.scanned_assets,
            "generated_signals": self.generated_signals,
            "note": self.note,
            "timestamp": self.timestamp.isoformat(),
        }


class StocksEtfPaperAgent:
    """
    Paper-only shell for future stocks/ETF trading.

    This agent intentionally does not emit execution signals yet. It establishes the
    same runtime contract as other agents so it can be upgraded when a live broker
    adapter is added.
    """

    agent_id = "stocks_etf_paper_shell"

    async def scan(self) -> StocksEtfPaperScanResult:
        note = (
            "Stocks/ETF paper shell active. No broker adapter configured; "
            "signal generation is intentionally disabled."
        )
        log.info(f"[{self.agent_id}] {note}")
        return StocksEtfPaperScanResult(
            agent_id=self.agent_id,
            success=True,
            scanned_assets=0,
            generated_signals=0,
            note=note,
            timestamp=datetime.utcnow(),
        )
