"""
Graph Context Provider — fetches knowledge graph context for use in market configs.

Provides a lightweight, cached interface for market-specific strategies (like BTC15M)
to query the knowledge graph for relevant facts that can modify confidence scores.

This is separate from the full SwarmAnalysisLayer — it's designed for the fast path
(30s trading loop) and returns simple numeric modifiers rather than rich context.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from bot.config import settings
from bot.utils.logger import log


@dataclass
class GraphSignal:
    """A simple signal derived from knowledge graph facts."""
    direction_bias: float  # -0.10 to +0.10 (bearish to bullish)
    confidence_modifier: float  # -0.05 to +0.10 added to base confidence
    relevant_facts: int
    top_fact: str  # Most relevant fact for logging


# Cache for graph queries (avoid hammering Zep every 30s)
_graph_cache: Dict[str, tuple[float, GraphSignal]] = {}
_CACHE_TTL = 120.0  # 2 minutes


async def get_btc_graph_signal() -> Optional[GraphSignal]:
    """
    Query the knowledge graph for BTC-related context and return a signal modifier.

    Returns None if swarm is disabled, graph is empty, or query fails.
    """
    if not settings.SWARM_ENABLED or not settings.ZEP_API_KEY:
        return None

    # Check cache
    now = time.time()
    cache_key = "btc_signal"
    if cache_key in _graph_cache:
        cached_time, cached_signal = _graph_cache[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_signal

    try:
        from bot.swarm.async_wrappers import SwarmGraphAsync

        graph = SwarmGraphAsync()
        graph_id = await graph.get_graph_id()
        if not graph_id:
            return None

        # Search for BTC-specific knowledge
        result = await graph.quick_search(graph_id, "BTC bitcoin price momentum trading", limit=8)

        if not result.facts:
            return None

        # Analyze facts for directional bias
        bullish_keywords = [
            "bullish", "buy", "long", "accumulate", "support", "breakout",
            "uptrend", "higher", "rally", "strong",
        ]
        bearish_keywords = [
            "bearish", "sell", "short", "resistance", "breakdown",
            "downtrend", "lower", "decline", "weak", "risk",
        ]

        bull_score = 0
        bear_score = 0
        for fact in result.facts:
            fact_lower = fact.lower()
            bull_score += sum(1 for w in bullish_keywords if w in fact_lower)
            bear_score += sum(1 for w in bearish_keywords if w in fact_lower)

        total_signals = bull_score + bear_score
        if total_signals == 0:
            signal = GraphSignal(
                direction_bias=0.0,
                confidence_modifier=0.02 if len(result.facts) >= 3 else 0.0,
                relevant_facts=len(result.facts),
                top_fact=result.facts[0][:100] if result.facts else "",
            )
        else:
            # Scale direction bias: max ±0.10
            raw_bias = (bull_score - bear_score) / max(total_signals, 1)
            direction_bias = max(-0.10, min(0.10, raw_bias * 0.10))

            # Confidence modifier: more facts = more confidence (if aligned)
            fact_density_bonus = min(0.05, len(result.facts) * 0.01)
            alignment_bonus = abs(raw_bias) * 0.05  # Strong consensus = more confident
            confidence_modifier = fact_density_bonus + alignment_bonus

            signal = GraphSignal(
                direction_bias=direction_bias,
                confidence_modifier=confidence_modifier,
                relevant_facts=len(result.facts),
                top_fact=result.facts[0][:100] if result.facts else "",
            )

        # Cache result
        _graph_cache[cache_key] = (now, signal)
        return signal

    except Exception as e:
        log.warning(f"[GRAPH CONTEXT] BTC graph query failed: {e}")
        return None
