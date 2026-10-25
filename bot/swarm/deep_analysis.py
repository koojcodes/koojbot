"""
Deep Analysis Agent — ReACT pattern for structured trading analysis.

Uses the Reasoning → Action → Thinking loop to produce evidence-backed
trading theses by querying the knowledge graph iteratively.

Callable via:
- Telegram: /deepanalysis <topic>
- API: POST /api/swarm/analyze
- Pipeline: SwarmAnalysisLayer (future integration)

Adapted from MiroFish backend/app/services/report_agent.py.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bot.config import settings
from bot.utils.logger import log

from .llm_client import SwarmLLMClient


@dataclass
class DeepAnalysis:
    """Structured output from the Deep Analysis Agent."""
    topic: str
    thesis: str = ""
    supporting_evidence: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    confidence: float = 0.0  # 0.0 - 1.0
    recommended_action: str = ""  # "BUY_YES", "BUY_NO", "HOLD", "NO_TRADE"
    tool_calls_made: int = 0
    elapsed_seconds: float = 0.0

    def format_telegram(self) -> str:
        """Format for Telegram MarkdownV2."""
        lines = [
            f"*Deep Analysis: {_esc(self.topic[:60])}*",
            "",
            f"*Thesis:* {_esc(self.thesis)}",
            "",
        ]

        if self.supporting_evidence:
            lines.append("*Supporting Evidence:*")
            for i, ev in enumerate(self.supporting_evidence[:5], 1):
                lines.append(f"  {i}\\. {_esc(ev[:120])}")
            lines.append("")

        if self.risk_factors:
            lines.append("*Risk Factors:*")
            for rf in self.risk_factors[:3]:
                lines.append(f"  \\- {_esc(rf[:120])}")
            lines.append("")

        conf_str = f"{self.confidence:.0%}".replace(".", "\\.")
        lines.append(f"*Confidence:* {conf_str}")
        lines.append(f"*Recommendation:* {_esc(self.recommended_action)}")
        lines.append(f"\n_Analysis took {self.elapsed_seconds:.1f}s with {self.tool_calls_made} graph queries_".replace(".", "\\."))

        return "\n".join(lines)

    def format_plain(self) -> str:
        """Format as plain text."""
        lines = [
            f"Deep Analysis: {self.topic}",
            f"\nThesis: {self.thesis}",
        ]
        if self.supporting_evidence:
            lines.append("\nSupporting Evidence:")
            for i, ev in enumerate(self.supporting_evidence, 1):
                lines.append(f"  {i}. {ev}")
        if self.risk_factors:
            lines.append("\nRisk Factors:")
            for rf in self.risk_factors:
                lines.append(f"  - {rf}")
        lines.append(f"\nConfidence: {self.confidence:.0%}")
        lines.append(f"Recommendation: {self.recommended_action}")
        return "\n".join(lines)


def _esc(text: str) -> str:
    """Escape for Telegram MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


class DeepAnalysisAgent:
    """
    ReACT-pattern agent that iteratively queries the knowledge graph
    to build a structured trading thesis.

    Loop:
    1. THINK — Reason about what information is needed
    2. ACT — Call a graph search tool
    3. OBSERVE — Read the results
    4. Repeat until confident, then produce final analysis

    Max iterations: 3 (to control cost and latency)
    """

    MAX_ITERATIONS = 3

    def __init__(self, llm_client: Optional[SwarmLLMClient] = None):
        self._llm = llm_client
        self._graph = None

    @property
    def llm(self) -> SwarmLLMClient:
        if self._llm is None:
            self._llm = SwarmLLMClient()
        return self._llm

    def _ensure_graph(self):
        if self._graph is None:
            from .async_wrappers import SwarmGraphAsync
            self._graph = SwarmGraphAsync()

    async def analyze(self, topic: str) -> DeepAnalysis:
        """
        Run deep analysis on a trading topic.

        Returns a structured DeepAnalysis with thesis, evidence,
        risks, confidence, and recommendation.
        """
        start = time.time()
        self._ensure_graph()

        graph_id = await self._graph.get_graph_id()
        if not graph_id:
            return DeepAnalysis(
                topic=topic,
                thesis="Cannot perform deep analysis — no knowledge graph available.",
                recommended_action="NO_TRADE",
                elapsed_seconds=time.time() - start,
            )

        log.info(f"[DEEP ANALYSIS] Starting ReACT analysis: '{topic[:60]}'")

        # Gather evidence through iterative graph queries
        evidence_context = []
        tool_calls = 0

        # Initial broad search
        result = await self._graph.quick_search(graph_id, topic, limit=10)
        tool_calls += 1
        if result.facts:
            evidence_context.extend(result.facts[:10])

        # Deeper search with InsightForge
        try:
            insight = await self._graph.insight_forge(graph_id, topic, num_sub_queries=3)
            tool_calls += 1
            if insight.semantic_facts:
                for fact in insight.semantic_facts:
                    if fact not in evidence_context:
                        evidence_context.append(fact)
            if insight.relationship_chains:
                for chain in insight.relationship_chains[:5]:
                    evidence_context.append(f"[Relationship] {chain}")
        except Exception as e:
            log.warning(f"[DEEP ANALYSIS] InsightForge failed: {e}")

        # Graph statistics for context
        try:
            stats = await self._graph.get_graph_statistics(graph_id)
            tool_calls += 1
            stats_text = (
                f"Knowledge graph has {stats['total_nodes']} entities and "
                f"{stats['total_edges']} relationships."
            )
            evidence_context.append(stats_text)
        except Exception:
            pass

        # Now synthesize with LLM
        evidence_text = "\n".join(f"- {e}" for e in evidence_context[:20])

        try:
            synthesis = self.llm.chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior trading analyst producing a structured analysis "
                            "for a prediction market trading bot. Based on the knowledge graph "
                            "evidence provided, produce a trading thesis.\n\n"
                            "Return JSON with:\n"
                            '- "thesis": 2-3 sentence trading thesis\n'
                            '- "supporting_evidence": array of 3-5 key evidence points\n'
                            '- "risk_factors": array of 2-3 risk factors\n'
                            '- "confidence": float 0.0-1.0 (how confident in the thesis)\n'
                            '- "recommended_action": one of "BUY_YES", "BUY_NO", "HOLD", "NO_TRADE"\n'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Topic: {topic}\n\n"
                            f"Knowledge Graph Evidence:\n{evidence_text}\n\n"
                            f"Produce your trading analysis."
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=1000,
            )

            elapsed = time.time() - start
            analysis = DeepAnalysis(
                topic=topic,
                thesis=synthesis.get("thesis", ""),
                supporting_evidence=synthesis.get("supporting_evidence", []),
                risk_factors=synthesis.get("risk_factors", []),
                confidence=min(1.0, max(0.0, float(synthesis.get("confidence", 0.5)))),
                recommended_action=synthesis.get("recommended_action", "HOLD"),
                tool_calls_made=tool_calls,
                elapsed_seconds=elapsed,
            )

            log.info(
                f"[DEEP ANALYSIS] Complete: confidence={analysis.confidence:.0%}, "
                f"action={analysis.recommended_action}, {elapsed:.1f}s"
            )
            return analysis

        except Exception as e:
            log.error(f"[DEEP ANALYSIS] Synthesis failed: {e}")
            return DeepAnalysis(
                topic=topic,
                thesis=f"Analysis failed: {e}",
                recommended_action="NO_TRADE",
                tool_calls_made=tool_calls,
                elapsed_seconds=time.time() - start,
            )
