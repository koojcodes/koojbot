from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradingAgentSpec:
    """Runtime configuration for a trading agent."""

    agent_id: str
    market_type: str
    exchange: str
    timeframe: str
    enabled: bool = True
    runner: str = ""
    notes: str = ""


@dataclass
class AgentQualityGate:
    """Simple go/no-go quality thresholds per agent."""

    min_signal_confidence: float = 0.55
    max_daily_drawdown_pct: float = 0.03
    min_sample_size: int = 25
    min_win_rate: float = 0.45
    min_net_edge_pct: float = 0.02

    def to_dict(self) -> dict:
        return {
            "min_signal_confidence": self.min_signal_confidence,
            "max_daily_drawdown_pct": self.max_daily_drawdown_pct,
            "min_sample_size": self.min_sample_size,
            "min_win_rate": self.min_win_rate,
            "min_net_edge_pct": self.min_net_edge_pct,
        }
