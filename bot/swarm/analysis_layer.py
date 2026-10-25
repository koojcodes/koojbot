"""
Swarm Analysis Layer — sits between Researcher and Synthesizer.

This layer enriches the trading pipeline with:
1. Knowledge graph context (always, if graph exists)
2. Swarm simulation results (on-demand, when triggered)

The layer is conditional — it adds latency only when the graph
has relevant data or a simulation is warranted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bot.config import settings
from bot.utils.logger import log


@dataclass
class SwarmInsight:
    """Result from swarm analysis — injected into Synthesizer context."""
    graph_facts: List[str] = field(default_factory=list)
    graph_entities: List[Dict[str, Any]] = field(default_factory=list)
    simulation_ran: bool = False
    simulation_summary: str = ""
    simulation_posts: List[str] = field(default_factory=list)
    confidence_modifier: float = 0.0  # -1.0 to +1.0, added to base confidence
    impact_score: Any = None  # ImpactScore from EventTrigger (Phase 6)

    def has_content(self) -> bool:
        return bool(self.graph_facts or self.simulation_ran)

    def format_for_synthesizer(self) -> str:
        """Format as context text for the Synthesizer agent."""
        parts: List[str] = []

        if self.graph_facts:
            parts.append("KNOWLEDGE GRAPH CONTEXT (from past analysis and learning):")
            for i, fact in enumerate(self.graph_facts[:10], 1):
                parts.append(f"  {i}. {fact}")
            parts.append("")

        if self.graph_entities:
            parts.append("RELEVANT ENTITIES:")
            for entity in self.graph_entities[:5]:
                parts.append(f"  - {entity.get('name', '?')} ({entity.get('type', '?')})")
            parts.append("")

        if self.simulation_ran:
            parts.append("SWARM SIMULATION RESULTS:")
            parts.append(f"  {self.simulation_summary}")
            if self.simulation_posts:
                parts.append("  Key trader reactions:")
                for post in self.simulation_posts[:6]:
                    parts.append(f"    {post[:150]}")
            parts.append("")

        if self.confidence_modifier != 0.0:
            direction = "increase" if self.confidence_modifier > 0 else "decrease"
            parts.append(
                f"SWARM CONFIDENCE: {direction} by {abs(self.confidence_modifier):.0%} "
                f"based on {'simulation + ' if self.simulation_ran else ''}graph evidence"
            )

        return "\n".join(parts)


class SwarmAnalysisLayer:
    """
    Conditional analysis layer for the agent pipeline.

    Always queries the knowledge graph for relevant context.
    Triggers a swarm simulation when:
      1. Explicitly requested (via #swarm tag or /simulate command)
      2. Auto-triggered by EventTrigger when impact score exceeds threshold
    """

    def __init__(self):
        self._graph = None
        self._sim_manager = None
        self._event_trigger = None

    def _ensure_event_trigger(self):
        if self._event_trigger is None:
            from .event_trigger import EventTrigger
            self._event_trigger = EventTrigger()

    def _ensure_graph(self):
        if self._graph is None:
            from .async_wrappers import SwarmGraphAsync
            self._graph = SwarmGraphAsync()

    def _ensure_sim_manager(self):
        if self._sim_manager is None:
            from .simulation_manager import SimulationManager
            self._sim_manager = SimulationManager()

    async def analyze(
        self,
        research_data: Dict[str, Any],
        force_simulation: bool = False,
    ) -> SwarmInsight:
        """
        Run swarm analysis on researcher output.

        Args:
            research_data: Output from ResearcherAgent (the .data dict)
            force_simulation: If True, always run a simulation (#swarm tag)

        Returns:
            SwarmInsight to inject into Synthesizer context
        """
        insight = SwarmInsight()

        # Extract key info from research
        topics = research_data.get("key_topics", [])
        summary = research_data.get("summary", "")
        content_type = research_data.get("content_type", "other")
        query = " ".join(topics[:3]) if topics else summary[:100]

        # Step 1: Always query knowledge graph
        await self._enrich_from_graph(insight, query)

        # Step 2: Determine whether to run simulation
        run_sim = force_simulation

        # Step 2b: Auto-trigger check (Phase 6)
        if not run_sim and settings.SWARM_ENABLED:
            try:
                self._ensure_event_trigger()
                impact = self._event_trigger.score_impact(research_data)
                insight.impact_score = impact
                if impact.should_trigger:
                    run_sim = True
                    log.info(
                        f"[SWARM LAYER] Event auto-trigger fired: "
                        f"{impact.trigger_reason}"
                    )
            except Exception as e:
                log.warning(f"[SWARM LAYER] Event trigger scoring failed: {e}")

        if run_sim:
            await self._run_simulation(
                insight, summary, query,
                markets=research_data.get("relevant_markets", []),
            )

        if insight.has_content():
            log.info(
                f"[SWARM LAYER] Enriched with {len(insight.graph_facts)} facts"
                + (f" + simulation" if insight.simulation_ran else "")
            )

        return insight

    async def _enrich_from_graph(self, insight: SwarmInsight, query: str) -> None:
        """Query the knowledge graph for relevant facts."""
        try:
            self._ensure_graph()
            graph_id = await self._graph.get_graph_id()
            if not graph_id:
                return

            result = await self._graph.quick_search(graph_id, query, limit=10)

            insight.graph_facts = result.facts[:10]

            # Extract entity info from nodes
            for node in result.nodes[:5]:
                insight.graph_entities.append({
                    "name": node.get("name", ""),
                    "type": next(
                        (l for l in node.get("labels", []) if l not in ("Entity", "Node")),
                        "Unknown",
                    ),
                    "summary": node.get("summary", ""),
                })

            # Confidence modifier based on fact density
            if len(insight.graph_facts) >= 5:
                insight.confidence_modifier += 0.05  # Strong graph support
            elif len(insight.graph_facts) >= 2:
                insight.confidence_modifier += 0.02  # Some graph support

        except Exception as e:
            log.warning(f"[SWARM LAYER] Graph query failed (non-fatal): {e}")

    async def _run_simulation(
        self, insight: SwarmInsight, topic: str, query: str,
        markets: List[Dict[str, Any]] | None = None,
    ) -> None:
        """Run a swarm simulation and add results to insight."""
        try:
            self._ensure_sim_manager()

            # Get graph_id for context enrichment
            self._ensure_graph()
            graph_id = await self._graph.get_graph_id()

            log.info(f"[SWARM LAYER] Running simulation: '{topic[:60]}'")

            result = await self._sim_manager.run_simulation(
                topic=topic,
                max_rounds=5,
                timeout=300,
                graph_id=graph_id,
            )

            insight.simulation_ran = True

            if result.status == "completed":
                insight.simulation_posts = result.posts[:8]
                insight.simulation_summary = (
                    f"Simulated {len(result.by_agent)} trader archetypes over "
                    f"{result.num_rounds} rounds. {result.total_actions} total actions. "
                    f"Key participants: {', '.join(list(result.by_agent.keys())[:4])}"
                )

                # Phase 6: Record predictions for accuracy tracking
                try:
                    from .accuracy_tracker import AccuracyTracker
                    tracker = AccuracyTracker()
                    await tracker.init()
                    await tracker.record_predictions(
                        simulation_id=result.simulation_id,
                        topic=topic,
                        posts=result.posts,
                        markets=markets or [],
                        by_agent=result.by_agent,
                    )
                except Exception as e:
                    log.warning(f"[SWARM LAYER] Prediction recording failed: {e}")

                # Analyze sentiment from posts
                bullish_signals = sum(
                    1 for p in result.posts
                    if any(w in p.lower() for w in ["bullish", "buy", "long", "moon", "pump", "rally"])
                )
                bearish_signals = sum(
                    1 for p in result.posts
                    if any(w in p.lower() for w in ["bearish", "sell", "short", "dump", "crash", "fear"])
                )
                total_posts = max(len(result.posts), 1)

                sentiment_ratio = (bullish_signals - bearish_signals) / total_posts
                insight.confidence_modifier += sentiment_ratio * 0.1  # Scale to ±10%

            else:
                insight.simulation_summary = f"Simulation failed: {result.error}"
                log.warning(f"[SWARM LAYER] Simulation failed: {result.error}")

        except Exception as e:
            insight.simulation_summary = f"Simulation error: {e}"
            log.error(f"[SWARM LAYER] Simulation error: {e}")

    async def run_standalone_simulation(self, topic: str) -> str:
        """
        Run a simulation outside the pipeline (for /simulate command).

        Returns a formatted summary string.
        """
        self._ensure_sim_manager()
        self._ensure_graph()
        graph_id = await self._graph.get_graph_id()

        result = await self._sim_manager.run_simulation(
            topic=topic,
            max_rounds=5,
            timeout=300,
            graph_id=graph_id,
        )

        return result.format_summary()
