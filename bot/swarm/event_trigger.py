"""
Event Trigger — auto-fires swarm simulations when the Researcher detects high-impact events.

Impact scoring criteria:
  - Content type: news > analysis > trading_advice > other
  - Topic relevance: crypto/macro topics score higher
  - Market count: more relevant markets = higher impact
  - Price sensitivity: markets with extreme prices (near 0 or 1) are volatile
  - Keyword signals: specific high-impact words boost the score

A simulation is auto-triggered when the impact score exceeds the threshold.
This eliminates the need for the #swarm tag on truly important events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from bot.config import settings
from bot.utils.logger import log


# Impact score threshold to auto-trigger a simulation (0-1 scale)
_AUTO_TRIGGER_THRESHOLD = 0.65

# Cooldown: don't auto-trigger more than once per N seconds
_COOLDOWN_SECONDS = 300  # 5 minutes

# High-impact keyword patterns (regex)
_HIGH_IMPACT_KEYWORDS = [
    r"\bcrash\b", r"\bsurge\b", r"\bplummet\b", r"\bsoar\b",
    r"\bblack swan\b", r"\bflash crash\b", r"\brecord high\b", r"\brecord low\b",
    r"\bhalt\b", r"\bban\b", r"\bapprove\b", r"\bdefault\b",
    r"\brate cut\b", r"\brate hike\b", r"\bemergency\b",
    r"\bbreaking\b", r"\bjust in\b", r"\bexclusive\b",
    r"\bliquidat", r"\bexploit\b", r"\bhack\b",
    r"\betf\b", r"\bsec\b", r"\bfomc\b", r"\bcpi\b",
    r"\bwar\b", r"\bsanction\b", r"\btariff\b",
]

# Content type impact weights
_CONTENT_TYPE_WEIGHTS = {
    "news": 0.30,
    "analysis": 0.15,
    "trading_advice": 0.10,
    "day_trading_strategy": 0.05,
    "other": 0.0,
}

# Topic boost weights
_TOPIC_BOOSTS = {
    "bitcoin/BTC": 0.10,
    "Ethereum/ETH": 0.08,
    "Federal Reserve": 0.15,
    "inflation": 0.12,
    "crypto": 0.05,
    "stocks": 0.05,
    "oil": 0.05,
}


@dataclass
class ImpactScore:
    """Result of impact scoring on research output."""
    total_score: float
    content_type_score: float
    topic_score: float
    market_score: float
    keyword_score: float
    should_trigger: bool
    trigger_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_score": round(self.total_score, 3),
            "content_type_score": round(self.content_type_score, 3),
            "topic_score": round(self.topic_score, 3),
            "market_score": round(self.market_score, 3),
            "keyword_score": round(self.keyword_score, 3),
            "should_trigger": self.should_trigger,
            "trigger_reason": self.trigger_reason,
        }


class EventTrigger:
    """
    Scores research output for impact and decides whether to auto-trigger
    a swarm simulation.

    Usage:
        trigger = EventTrigger()
        impact = trigger.score_impact(research_data)
        if impact.should_trigger:
            # run simulation automatically
    """

    def __init__(self, threshold: float = _AUTO_TRIGGER_THRESHOLD):
        self.threshold = threshold
        self._last_trigger_time: float = 0.0

    def score_impact(self, research_data: Dict[str, Any]) -> ImpactScore:
        """
        Score the impact of research output on a 0-1 scale.

        Args:
            research_data: The .data dict from ResearcherAgent output

        Returns:
            ImpactScore with breakdown and trigger decision
        """
        import time

        content_type = research_data.get("content_type", "other")
        topics = research_data.get("key_topics", [])
        markets = research_data.get("relevant_markets", [])
        summary = research_data.get("summary", "")
        original = research_data.get("original_content", "")
        text = f"{summary} {original}".lower()

        # 1. Content type score (0 - 0.30)
        ct_score = _CONTENT_TYPE_WEIGHTS.get(content_type, 0.0)

        # 2. Topic relevance score (0 - 0.25)
        topic_score = 0.0
        for topic in topics:
            topic_score += _TOPIC_BOOSTS.get(topic, 0.02)
        topic_score = min(0.25, topic_score)

        # 3. Market density score (0 - 0.20)
        num_markets = len(markets)
        if num_markets >= 4:
            market_score = 0.20
        elif num_markets >= 2:
            market_score = 0.12
        elif num_markets >= 1:
            market_score = 0.06
        else:
            market_score = 0.0

        # Bonus for markets with extreme prices (high volatility potential)
        for m in markets:
            yes_price = m.get("yes_price", 0.5)
            if yes_price > 0.85 or yes_price < 0.15:
                market_score = min(0.20, market_score + 0.03)

        # 4. Keyword signals (0 - 0.25)
        keyword_hits = 0
        for pattern in _HIGH_IMPACT_KEYWORDS:
            if re.search(pattern, text):
                keyword_hits += 1
        keyword_score = min(0.25, keyword_hits * 0.05)

        # Total
        total = ct_score + topic_score + market_score + keyword_score

        # Determine trigger
        now = time.time()
        cooldown_ok = (now - self._last_trigger_time) >= _COOLDOWN_SECONDS
        should_trigger = total >= self.threshold and cooldown_ok

        # Build reason
        if not should_trigger:
            if total < self.threshold:
                reason = f"Score {total:.2f} below threshold {self.threshold}"
            else:
                reason = "Cooldown active"
        else:
            reasons = []
            if ct_score >= 0.20:
                reasons.append(f"high-impact {content_type}")
            if keyword_hits >= 2:
                reasons.append(f"{keyword_hits} impact keywords")
            if num_markets >= 3:
                reasons.append(f"{num_markets} affected markets")
            if topic_score >= 0.15:
                reasons.append("major topic")
            reason = "Auto-trigger: " + ", ".join(reasons) if reasons else f"Impact score {total:.2f}"

        if should_trigger:
            self._last_trigger_time = now
            log.info(
                f"[EVENT TRIGGER] Auto-triggering simulation — "
                f"score={total:.2f} ({reason})"
            )

        return ImpactScore(
            total_score=total,
            content_type_score=ct_score,
            topic_score=topic_score,
            market_score=market_score,
            keyword_score=keyword_score,
            should_trigger=should_trigger,
            trigger_reason=reason,
        )

    def reset_cooldown(self):
        """Reset the cooldown timer (for testing)."""
        self._last_trigger_time = 0.0
