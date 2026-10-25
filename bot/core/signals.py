from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Signal:
    market_id: str
    market_title: str
    exchange: str
    action: SignalAction
    outcome: str  # "Yes" or "No"
    confidence: float  # 0-1
    target_price: float | None = None
    stop_loss: float | None = None
    size_suggestion: float | None = None  # suggested position size
    reasoning: str = ""
    strategy: str = ""
    agent_id: str = "core_engine"
    execution_exchange: str | None = None
    leader_wallet: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_actionable(self) -> bool:
        return self.action != SignalAction.HOLD and self.confidence > 0.0

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_title": self.market_title,
            "exchange": self.exchange,
            "action": self.action.value,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "size_suggestion": self.size_suggestion,
            "reasoning": self.reasoning,
            "strategy": self.strategy,
            "agent_id": self.agent_id,
            "execution_exchange": self.execution_exchange,
            "leader_wallet": self.leader_wallet,
            "timestamp": self.timestamp.isoformat(),
        }
