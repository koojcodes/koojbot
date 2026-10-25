"""Base classes for the multi-agent trading pipeline."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentResult:
    """Output from any agent in the pipeline."""
    success: bool
    output: str          # Human-readable summary for Telegram
    data: dict           # Structured data passed to the next agent
    agent_name: str = ""
    elapsed_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class Agent(ABC):
    """Abstract base class for all pipeline agents."""

    name: str = "base"

    @abstractmethod
    async def run(self, input_text: str, context: dict) -> AgentResult:
        """
        Process input and return a result.

        Args:
            input_text: The raw text to process (social media content, user message, etc.)
            context: Structured data from the previous agent (empty dict for first agent)

        Returns:
            AgentResult with output summary and structured data for next agent
        """

    def __repr__(self) -> str:
        return f"<Agent: {self.name}>"
