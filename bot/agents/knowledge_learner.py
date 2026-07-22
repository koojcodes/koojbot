"""
Knowledge Learner Agent — the core of the adaptive learning system.

Given a YouTube video transcript, this agent:
  1. Reads the current knowledge base (all 7 category documents)
  2. Analyzes the transcript against existing knowledge
  3. Identifies:
       ✅ CONFIRM  — new source agrees with an existing rule (raises confidence)
       🆕 ADD      — genuinely new rule not yet in the knowledge base
       ✏️ UPDATE   — existing rule needs refinement based on new info
       ⚡ CONTRADICT — new source conflicts with an existing rule
  4. Applies all updates to the knowledge base
  5. Returns a structured learning report for the user

This builds a continuously-improving, synthesized knowledge base rather than
a pile of individual video summaries.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import anthropic

from bot.agents.base import Agent, AgentResult
from bot.config import settings
from bot.knowledge import knowledge_base as kb
from bot.knowledge.categories import (
    AGENT_KNOWLEDGE_PROFILES,
    category_description,
    detect_categories,
    detect_target_agent,
    get_agent_category_names,
    get_agent_track,
)
from bot.utils.logger import log

# Lazy-loaded swarm graph integration
_swarm_graph = None


def _get_swarm_graph():
    """Lazy-init the async graph wrapper (only if swarm is enabled)."""
    global _swarm_graph
    if _swarm_graph is None and settings.SWARM_ENABLED and settings.ZEP_API_KEY:
        from bot.swarm.async_wrappers import SwarmGraphAsync
        _swarm_graph = SwarmGraphAsync()
    return _swarm_graph

# Max transcript length passed to Claude (keeps tokens reasonable)
_MAX_TRANSCRIPT_CHARS = 6000


@dataclass
class LearningReport:
    """Summary of what was learned from a single source."""
    video_title: str
    channel_name: str
    source_url: str
    video_summary: str          # Claude's 2-sentence summary of the content
    primary_categories: list[str]
    new_rules: int
    confirmed_rules: int
    updated_rules: int
    contradictions: int
    categories_touched: list[str]
    key_takeaway: str           # Claude's 1-sentence takeaway
    track: str = "traditional"  # "traditional" | "kalshi"
    target_agent: str = "stocks_etf_paper_shell"
    already_processed: bool = False

    @property
    def total_changes(self) -> int:
        return self.new_rules + self.confirmed_rules + self.updated_rules + self.contradictions

    def format_telegram_reply(self) -> str:
        """Format for Telegram MarkdownV2."""
        if self.already_processed:
            return (
                f"⚠️ This video has already been processed\\.\n"
                f"_{self._escape(self.video_title[:60])}_"
            )

        track_badge = (
            "🎯 Kalshi prediction market track"
            if self.track == "kalshi"
            else "📊 Traditional trading track"
        )
        agent_badge = AGENT_KNOWLEDGE_PROFILES.get(
            self.target_agent,
            {},
        ).get("label", self.target_agent)
        lines = [
            f"📚 *Knowledge base updated*",
            f"_{self._escape(track_badge)}_",
            f"🤖 Agent: *{self._escape(agent_badge)}* `[{self._escape(self.target_agent)}]`",
            f"_{self._escape(self.video_title[:60])}_",
            f"Channel: {self._escape(self.channel_name)}",
            "",
        ]

        if self.new_rules:
            lines.append(f"🆕 {self.new_rules} new rule{'s' if self.new_rules != 1 else ''} added")
        if self.confirmed_rules:
            lines.append(f"✅ {self.confirmed_rules} existing rule{'s' if self.confirmed_rules != 1 else ''} confirmed")
        if self.updated_rules:
            lines.append(f"✏️ {self.updated_rules} rule{'s' if self.updated_rules != 1 else ''} refined")
        if self.contradictions:
            lines.append(f"⚡ {self.contradictions} contradiction{'s' if self.contradictions != 1 else ''} filed for review")

        if self.categories_touched:
            cats = ", ".join(c.replace("_", " ") for c in self.categories_touched)
            lines.append(f"\nCategories updated: _{self._escape(cats)}_")

        if self.key_takeaway:
            lines.append(f"\n💡 _{self._escape(self.key_takeaway)}_")

        if self.total_changes == 0:
            lines.append("No new knowledge found — this video's content is already well\\-represented\\.")

        return "\n".join(lines)

    @staticmethod
    def _escape(text: str) -> str:
        special = r"\_*[]()~`>#+-=|{}.!"
        return "".join(f"\\{c}" if c in special else c for c in str(text))


class KnowledgeLearnerAgent(Agent):
    """
    Reads a YouTube transcript and cross-analyzes it against the current knowledge
    base to extract new learnings, confirmations, and contradictions.
    """

    name = "knowledge_learner"

    def __init__(self, anthropic_key: str = ""):
        self._claude = anthropic.AsyncAnthropic(
            api_key=anthropic_key or settings.ANTHROPIC_API_KEY
        )

    async def learn(
        self,
        transcript: str,
        video_title: str,
        channel_name: str,
        source_url: str,
        track: str = "auto",
        subreddit: str = "",
    ) -> LearningReport:
        """
        Main entry point. Given content and source metadata, analyze and
        update the knowledge base. Returns a LearningReport.

        track:     "auto" (detect from content+subreddit) | "kalshi" | "traditional"
        subreddit: Optional subreddit name used as a strong prior for track detection.
                   When provided and recognised, it overrides content keyword scoring.
        """
        start = time.time()
        log.info(f"[bold green]KNOWLEDGE LEARNER[/] Processing: '{video_title[:60]}' ({len(transcript)} chars)")

        # Resolve target agent first so dedup can happen at per-agent scope.
        resolved_agent = detect_target_agent(
            transcript + " " + video_title,
            subreddit=subreddit,
            hint=track,
        )
        resolved_track = get_agent_track(resolved_agent)
        log.info(f"[KNOWLEDGE LEARNER] Target agent: {resolved_agent} ({resolved_track} track)")

        # Deduplication check
        if kb.has_processed_video(source_url, agent_id=resolved_agent):
            log.info(f"[KNOWLEDGE LEARNER] Already processed: {source_url}")
            return LearningReport(
                video_title=video_title,
                channel_name=channel_name,
                source_url=source_url,
                video_summary="",
                primary_categories=[],
                new_rules=0,
                confirmed_rules=0,
                updated_rules=0,
                contradictions=0,
                categories_touched=[],
                key_takeaway="",
                target_agent=resolved_agent,
                already_processed=True,
            )

        # Truncate transcript if needed
        transcript_text = transcript[:_MAX_TRANSCRIPT_CHARS]
        if len(transcript) > _MAX_TRANSCRIPT_CHARS:
            transcript_text += f"\n[... transcript truncated at {_MAX_TRANSCRIPT_CHARS:,} chars]"

        # Build current knowledge base context for Claude (track-specific)
        current_kb = kb.format_for_prompt(track=resolved_track, agent_id=resolved_agent)

        # Pre-detect likely categories within the resolved track
        likely_categories = detect_categories(
            transcript_text + " " + video_title,
            track=resolved_track,
        )

        # Build the prompt — two distinct framings for the two tracks
        categories_list = "\n".join(
            f"  - {name}: {category_description(name)}"
            for name in get_agent_category_names(resolved_agent)
        )

        if resolved_track == "kalshi":
            track_framing = """
IMPORTANT — KALSHI PREDICTION MARKET CONTEXT:
This bot trades Kalshi BTC 15-minute binary contracts. A contract resolves YES if BTC
is above the strike price at expiry, NO if below. The bot bets YES or NO at current
market prices (e.g. YES at 0.65 = market says 65% chance BTC goes up).

Rules MUST be about:
  - How bettors systematically misprice YES/NO contracts (crowd biases)
  - Which BTC signals (momentum, RSI, volume, news) reliably predict 15-min outcomes
  - When fee-adjusted expected value justifies entering a contract
  - Contract timing: when in the 15-min window to enter/exit
  - Bankroll and exposure management for sequential binary bets

DO NOT extract rules about:
  - Stop losses, take profit targets, or position exits (binary contracts auto-resolve)
  - Chart patterns like FVG, liquidity grabs, head-and-shoulders (wrong market type)
  - Leverage, margin, or exchange order management
  - Multi-day trends or swing trading concepts
"""
        else:
            track_framing = """
CONTEXT: Traditional day trading / spot market concepts.
These rules apply to buying and selling actual assets (stocks, crypto, futures).
Extract chart patterns, indicator signals, entry/exit setups, and risk management
concepts that a human trader would use on a real exchange.
"""

        prompt = f"""You are a trading knowledge analyst building an adaptive knowledge base for a trading bot.

Your job: analyze this content and cross-reference it against the existing knowledge base.
Identify what is new, what confirms existing knowledge, what needs updating, and what contradicts existing knowledge.
{track_framing}
SOURCE INFORMATION:
Title: {video_title}
Channel/Source: {channel_name}
Likely topics: {', '.join(likely_categories[:4]) if likely_categories else 'general trading'}

CONTENT:
{transcript_text}

━━━ CURRENT KNOWLEDGE BASE ━━━
{current_kb}
━━━ END OF KNOWLEDGE BASE ━━━

KNOWLEDGE CATEGORIES (use EXACTLY these names):
{categories_list}

INSTRUCTIONS:
For each piece of relevant knowledge in the content, determine:

1. ADD — if it's a new rule/insight not yet in the knowledge base
2. CONFIRM — if it agrees with an existing rule (use its rule_id from the KB above)
3. UPDATE — if the content refines an existing rule with better specifics (use rule_id)
4. CONTRADICT — if it conflicts with an existing rule (use rule_id + explain the conflict)

Focus on SPECIFIC, ACTIONABLE rules (e.g. "RSI below 32 = entry" not "RSI is important").
Ignore vague motivational content or general platitudes.
For each rule, identify the asset (BTC, ETH, or "general") and timeframe (1m, 5m, 15m, 1h, daily, or "general").

Respond ONLY in this exact JSON format:
{{
  "video_summary": "<2 sentences: what this content is about and its main focus>",
  "primary_categories": ["<cat1>", "<cat2>"],
  "key_takeaway": "<single most important thing learned>",
  "updates": [
    {{
      "category": "<category_name>",
      "action": "ADD",
      "rule": "<specific, actionable rule text>",
      "asset": "<BTC|ETH|general>",
      "timeframe": "<1m|5m|15m|1h|daily|general>",
      "confidence": <0.50-0.90>,
      "reasoning": "<why this is a valid rule>"
    }},
    {{
      "category": "<category_name>",
      "action": "CONFIRM",
      "rule_id": "<existing rule_id from KB>",
      "reasoning": "<how this content confirms it>"
    }},
    {{
      "category": "<category_name>",
      "action": "UPDATE",
      "rule_id": "<existing rule_id>",
      "rule": "<improved rule text>",
      "confidence": <0.50-0.90>,
      "reasoning": "<what changed and why>"
    }},
    {{
      "category": "<category_name>",
      "action": "CONTRADICT",
      "rule_id": "<existing rule_id>",
      "rule": "<what this content claims instead>",
      "reasoning": "<describe the conflict clearly>"
    }}
  ]
}}

Be precise and selective — only include high-quality, specific, actionable rules.
5-15 updates is typical for content-rich material. Avoid duplicating rules already in the KB."""

        try:
            response = await self._claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            parsed = self._parse_response(text)

        except Exception as e:
            log.error(f"[KNOWLEDGE LEARNER] Claude call failed: {e}")
            return LearningReport(
                video_title=video_title,
                channel_name=channel_name,
                source_url=source_url,
                video_summary="",
                primary_categories=[],
                new_rules=0,
                confirmed_rules=0,
                updated_rules=0,
                contradictions=0,
                categories_touched=[],
                key_takeaway=f"Error during analysis: {e}",
            )

        # Apply updates to the correct track's knowledge base
        update_report = kb.apply_updates(
            updates=parsed.get("updates", []),
            source_url=source_url,
            video_title=video_title,
            channel_name=channel_name,
            track=resolved_track,
            agent_id=resolved_agent,
        )

        # Dual-write: also ingest into Zep knowledge graph (if enabled)
        swarm = _get_swarm_graph()
        if swarm:
            try:
                graph_id = await swarm.get_or_create_graph()
                # Build text blob for graph ingestion: title + summary + rules
                graph_text_parts = [
                    f"Source: {video_title} by {channel_name}",
                    f"Agent: {resolved_agent}",
                    f"Track: {resolved_track}",
                    parsed.get("video_summary", ""),
                ]
                for update in parsed.get("updates", []):
                    rule = update.get("rule", "")
                    action = update.get("action", "")
                    category = update.get("category", "")
                    if rule:
                        graph_text_parts.append(
                            f"[{action}] ({category}) {rule}"
                        )
                graph_text = "\n".join(p for p in graph_text_parts if p)

                if graph_text.strip():
                    from bot.swarm.ontology import get_trading_ontology
                    await swarm.add_episodes(
                        graph_id,
                        graph_text,
                        ontology=get_trading_ontology(),
                        chunk_size=400,
                        chunk_overlap=40,
                        batch_size=2,
                    )
                    log.info(
                        f"[KNOWLEDGE LEARNER] Graph ingestion complete for: {video_title[:40]}"
                    )
            except Exception as e:
                # Graph ingestion failure should not break the main KB flow
                log.warning(f"[KNOWLEDGE LEARNER] Graph ingestion failed (non-fatal): {e}")

        elapsed = time.time() - start
        report = LearningReport(
            video_title=video_title,
            channel_name=channel_name,
            source_url=source_url,
            video_summary=parsed.get("video_summary", ""),
            primary_categories=parsed.get("primary_categories", []),
            new_rules=update_report["new_rules"],
            confirmed_rules=update_report["confirmed_rules"],
            updated_rules=update_report["updated_rules"],
            contradictions=update_report["contradictions"],
            categories_touched=update_report["categories_touched"],
            key_takeaway=parsed.get("key_takeaway", ""),
            track=resolved_track,
            target_agent=resolved_agent,
        )

        log.info(
            f"[bold green]KNOWLEDGE LEARNER[/] Done in {elapsed:.1f}s — "
            f"+{report.new_rules} new, ✅{report.confirmed_rules} confirmed, "
            f"✏️{report.updated_rules} updated, ⚡{report.contradictions} contradictions"
        )

        return report

    async def run(self, input_text: str, context: dict) -> AgentResult:
        """AgentResult-compatible wrapper (used if called through pipeline)."""
        transcript = context.get("transcript", input_text)
        video_title = context.get("video_title", "Unknown Video")
        channel_name = context.get("channel_name", "Unknown Channel")
        source_url = context.get("source_url", "")

        start = time.time()
        report = await self.learn(transcript, video_title, channel_name, source_url)

        return AgentResult(
            success=True,
            output=report.format_telegram_reply(),
            data={
                "new_rules": report.new_rules,
                "confirmed_rules": report.confirmed_rules,
                "contradictions": report.contradictions,
                "categories_touched": report.categories_touched,
                "key_takeaway": report.key_takeaway,
            },
            agent_name=self.name,
            elapsed_seconds=time.time() - start,
        )

    def _parse_response(self, text: str) -> dict:
        """Parse Claude's JSON response, handling markdown code blocks."""
        try:
            json_str = text
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0].strip()
            elif "{" in text:
                start = text.index("{")
                end = text.rindex("}") + 1
                json_str = text[start:end]
            return json.loads(json_str)
        except Exception as e:
            log.warning(f"[KNOWLEDGE LEARNER] Could not parse JSON response: {e}")
            return {"updates": [], "video_summary": "", "key_takeaway": "", "primary_categories": []}
