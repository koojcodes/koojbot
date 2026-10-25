"""Sequential Agent Pipeline — orchestrates Researcher → Synthesizer → Trader."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable

from bot.agents.base import AgentResult
from bot.agents.researcher import ResearcherAgent
from bot.agents.synthesizer import SynthesizerAgent
from bot.agents.trader import TraderAgent
from bot.core.signals import Signal
from bot.utils.logger import log


def _md(text: str) -> str:
    """Escape a plain-text string for Telegram MarkdownV2."""
    # All characters that Telegram MarkdownV2 requires to be escaped
    for ch in r'\_*[]()~`>#+-=|{}.!':
        text = text.replace(ch, f'\\{ch}')
    return text


@dataclass
class PipelineResult:
    input_text: str
    source_platform: str
    research: AgentResult | None = None
    synthesis: AgentResult | None = None
    trade: AgentResult | None = None
    signals: list[Signal] = field(default_factory=list)
    success: bool = False
    error: str = ""
    total_elapsed: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def format_telegram_reply(self) -> str:
        """Format the full pipeline result as a Telegram MarkdownV2 message."""
        lines = []

        # Research section
        if self.research and self.research.success:
            lines.append(f"🔍 *Research* \\({self.research.elapsed_seconds:.1f}s\\)")
            lines.append(_md(self.research.output))
            lines.append("")

        # Synthesis section
        if self.synthesis and self.synthesis.success:
            lines.append(f"🧠 *Synthesis* \\({self.synthesis.elapsed_seconds:.1f}s\\)")
            lines.append(_md(self.synthesis.output))
            lines.append("")

        # Trade signals section
        if self.trade and self.trade.success:
            lines.append(f"🎯 *Trader Agent* \\({self.trade.elapsed_seconds:.1f}s\\)")
            lines.append(_md(self.trade.output))
            lines.append("")

        if self.signals:
            lines.append(f"✅ *{len(self.signals)} signal\\(s\\) queued for execution*")
        elif self.success:
            lines.append("ℹ️ No actionable signals — market may already be pricing this in")
        else:
            lines.append(f"❌ Pipeline failed: {_md(self.error)}")

        lines.append(f"\n⏱ Total: {self.total_elapsed:.1f}s".replace(".", "\\."))
        return "\n".join(lines)


# Progress callback type: async function that receives a status message
ProgressCallback = Callable[[str], Awaitable[None]]


class AgentPipeline:
    """
    Sequential pipeline: Researcher → [Swarm Analysis] → Synthesizer → Trader.

    Each agent's output feeds as context into the next.
    The pipeline sends progress updates via an optional callback (for Telegram).
    The optional swarm_layer enriches the pipeline with knowledge graph
    context and simulation results between Researcher and Synthesizer.
    """

    def __init__(
        self,
        researcher: ResearcherAgent,
        synthesizer: SynthesizerAgent,
        trader: TraderAgent,
        swarm_layer=None,
    ):
        self.researcher = researcher
        self.synthesizer = synthesizer
        self.trader = trader
        self.swarm_layer = swarm_layer  # Optional SwarmAnalysisLayer

    async def process(
        self,
        content: str,
        source_platform: str = "text",
        on_progress: ProgressCallback | None = None,
        pinned_markets: list[dict] | None = None,
        search_hints: list[str] | None = None,
        force_swarm: bool = False,
    ) -> PipelineResult:
        """
        Run the full agent pipeline on the given content.

        Args:
            content: The text to analyze (social media post, news article, user message)
            source_platform: Where the content came from ("TikTok", "X/Twitter", etc.)
            on_progress: Optional async callback called at each step with a status update
            pinned_markets: Markets to guarantee in the analysis, from #tag resolution.
                Each dict: {id, title, exchange, yes_price, volume, end_date}
            search_hints: Extra search topics injected from #tags (e.g. ["btc", "crypto"])

        Returns:
            PipelineResult with all agent outputs and generated signals
        """
        import time
        overall_start = time.time()

        result = PipelineResult(
            input_text=content,
            source_platform=source_platform,
        )

        pinned_markets = pinned_markets or []
        search_hints = search_hints or []

        tag_note = ""
        if pinned_markets:
            names = [m.get("title", m.get("id", "?")) for m in pinned_markets]
            tag_note = f" [pinned: {', '.join(names[:2])}]"
        log.info(f"[bold]PIPELINE[/] Starting for {source_platform}{tag_note}: {content[:60]}...")

        # ── Step 1: Researcher ──────────────────────────────────────────────
        try:
            if on_progress:
                await on_progress(f"🔍 *Researcher* analyzing {source_platform} content...")

            researcher_context = {
                "source_platform": source_platform,
                "pinned_markets": pinned_markets,
                "extra_search_hints": search_hints,
            }

            research = await self.researcher.run(
                content,
                researcher_context,
            )
            result.research = research

            if not research.success:
                result.error = f"Researcher failed: {research.output}"
                result.total_elapsed = time.time() - overall_start
                return result

            if on_progress:
                await on_progress(research.output)

        except Exception as e:
            log.error(f"Researcher step error: {e}")
            result.error = str(e)
            result.total_elapsed = time.time() - overall_start
            return result

        # ── Step 1.5: Swarm Analysis (optional) ────────────────────────────
        swarm_context = ""
        if self.swarm_layer:
            try:
                if on_progress:
                    await on_progress("🌊 *Swarm analysis* querying knowledge graph...")

                swarm_insight = await self.swarm_layer.analyze(
                    research_data=research.data,
                    force_simulation=force_swarm,
                )

                if swarm_insight.has_content():
                    swarm_context = swarm_insight.format_for_synthesizer()
                    if on_progress:
                        parts = []
                        if swarm_insight.graph_facts:
                            parts.append(f"{len(swarm_insight.graph_facts)} graph facts")
                        if swarm_insight.simulation_ran:
                            parts.append("simulation complete")
                        await on_progress(f"🌊 Swarm: {', '.join(parts)}")

            except Exception as e:
                log.warning(f"Swarm analysis failed (non-fatal): {e}")

        # ── Step 2: Synthesizer ─────────────────────────────────────────────
        try:
            num_markets = len(research.data.get("relevant_markets", []))
            if on_progress:
                await on_progress(f"🧠 *Synthesizer* processing {num_markets} markets...")

            # Enrich synthesizer input with swarm context
            synth_input = research.data.get("summary", content)
            synth_data = research.data.copy()
            if swarm_context:
                synth_data["swarm_context"] = swarm_context

            synthesis = await self.synthesizer.run(
                synth_input,
                synth_data,
            )
            result.synthesis = synthesis

            if not synthesis.success:
                result.error = f"Synthesizer failed: {synthesis.output}"
                result.total_elapsed = time.time() - overall_start
                return result

            if on_progress:
                await on_progress(synthesis.output)

        except Exception as e:
            log.error(f"Synthesizer step error: {e}")
            result.error = str(e)
            result.total_elapsed = time.time() - overall_start
            return result

        # ── Step 3: Trader Agent ────────────────────────────────────────────
        try:
            num_implications = len(synthesis.data.get("market_implications", []))
            if on_progress:
                await on_progress(f"🎯 *Trader Agent* generating signals from {num_implications} implications...")

            trade_context = synthesis.data.copy()
            relevant = research.data.get("relevant_markets", []) if research and research.data else []
            if relevant:
                market_exchange = {m.get("id"): m.get("exchange", "") for m in relevant}
                trade_context["default_exchange"] = relevant[0].get("exchange", "")
                enriched = []
                for imp in trade_context.get("market_implications", []):
                    imp_copy = imp.copy()
                    imp_copy.setdefault("exchange", market_exchange.get(imp_copy.get("market_id", ""), ""))
                    enriched.append(imp_copy)
                trade_context["market_implications"] = enriched

            trade = await self.trader.run("", trade_context)
            result.trade = trade

            # Extract Signal objects from trader result
            signals_data = trade.data.get("signals", [])
            if signals_data:
                # Convert back to Signal objects for engine injection
                from bot.core.signals import Signal as Sig, SignalAction
                for s in signals_data:
                    try:
                        result.signals.append(Sig(
                            market_id=s["market_id"],
                            market_title=s.get("market_title", ""),
                            exchange=s.get("exchange", "kalshi"),
                            action=SignalAction(s["action"]),
                            outcome=s["outcome"],
                            confidence=s["confidence"],
                            target_price=s.get("target_price"),
                            stop_loss=s.get("stop_loss"),
                            reasoning=s.get("reasoning", ""),
                            strategy=s.get("strategy", "agent_pipeline"),
                            agent_id=s.get("agent_id", "pipeline_news_trader"),
                            execution_exchange=s.get("execution_exchange"),
                        ))
                    except Exception as e:
                        log.warning(f"Could not reconstruct signal: {e}")

            if on_progress:
                await on_progress(trade.output)

        except Exception as e:
            log.error(f"Trader step error: {e}")
            result.error = str(e)
            result.total_elapsed = time.time() - overall_start
            return result

        result.success = True
        result.total_elapsed = time.time() - overall_start

        log.info(
            f"[bold green]PIPELINE COMPLETE[/] {result.total_elapsed:.1f}s total | "
            f"{len(result.signals)} signals generated"
        )

        return result
