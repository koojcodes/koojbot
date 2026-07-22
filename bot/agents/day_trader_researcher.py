"""Day Trader Researcher Agent — autonomously scans watched markets for short-term opportunities."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import anthropic

from bot.agents.base import Agent, AgentResult
from bot.config import settings
from bot.data.market_data import MarketDataFetcher, MarketSnapshot
from bot.data.news import NewsCollector
from bot.utils.logger import log

if TYPE_CHECKING:
    pass


# ── Opportunity types ──────────────────────────────────────────────────────────

OPPORTUNITY_TYPES = {
    "news_lag":            "Breaking news that the market hasn't priced in yet",
    "momentum":            "Sustained price drift with volume — ride the trend",
    "overreaction":        "Market overshot on a headline — reversal likely",
    "volume_spike":        "Unusual volume suggests informed activity",
    "resolution_imminent": "Market resolves very soon — mispriced vs likely outcome",
}


@dataclass
class DayTradeOpportunity:
    market_id: str
    market_title: str
    exchange: str
    opportunity_type: str        # one of OPPORTUNITY_TYPES keys
    direction: str               # "HIGHER" or "LOWER" (for Yes probability)
    current_price: float         # current Yes price (0–1)
    estimated_edge: float        # probability delta we believe exists (e.g. 0.10 = +10%)
    confidence: float            # 0–1
    urgency: str                 # "high" | "medium" | "low"
    window_minutes: int          # estimated window before opportunity closes
    reasoning: str               # human-readable justification
    volume: float = 0.0

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_title": self.market_title,
            "exchange": self.exchange,
            "opportunity_type": self.opportunity_type,
            "direction": self.direction,
            "current_price": self.current_price,
            "estimated_edge": self.estimated_edge,
            "confidence": self.confidence,
            "urgency": self.urgency,
            "window_minutes": self.window_minutes,
            "reasoning": self.reasoning,
            "volume": self.volume,
        }


@dataclass
class DayResearchResult:
    opportunities: list[DayTradeOpportunity]
    markets_scanned: int
    news_queries: list[str]
    scan_summary: str

    def to_dict(self) -> dict:
        return {
            "opportunities": [o.to_dict() for o in self.opportunities],
            "markets_scanned": self.markets_scanned,
            "news_queries": self.news_queries,
            "scan_summary": self.scan_summary,
            "opportunity_count": len(self.opportunities),
        }


# ── Agent ──────────────────────────────────────────────────────────────────────

class DayTraderResearcherAgent(Agent):
    """
    Day Trader Researcher: Autonomously scans the live watchlist for short-term
    prediction market opportunities.

    Runs on a cycle (not triggered by user input). Uses Claude with two tools:
      - scan_watched_markets(): formats all live snapshots as structured data
      - search_breaking_news(topic): finds articles from the last 2 hours

    Identifies four opportunity types: news_lag, momentum, overreaction,
    volume_spike, and resolution_imminent.
    """

    name = "day_trader_researcher"

    def __init__(
        self,
        market_data: MarketDataFetcher,
        news_collector: NewsCollector,
        anthropic_key: str = "",
    ):
        self.market_data = market_data
        self.news = news_collector
        self._claude = anthropic.AsyncAnthropic(
            api_key=anthropic_key or settings.ANTHROPIC_API_KEY
        )

    async def run(self, input_text: str = "", context: dict | None = None) -> AgentResult:
        """Scan watched markets and return short-term trading opportunities."""
        context = context or {}
        start = time.time()
        log.info("[bold cyan]DAY RESEARCHER[/] Starting autonomous market scan...")

        snapshots = self.market_data.get_all_snapshots()
        if not snapshots:
            return AgentResult(
                success=True,
                output="No markets in watchlist to scan.",
                data=DayResearchResult([], 0, [], "Empty watchlist.").to_dict(),
                agent_name=self.name,
                elapsed_seconds=time.time() - start,
            )

        tools = [
            {
                "name": "scan_watched_markets",
                "description": (
                    "Get current price data and recent price history for all watched markets. "
                    "Returns prices, volume, recent % movement, and time to resolution."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "search_breaking_news",
                "description": (
                    "Search for very recent news articles (last 2 hours) about a topic. "
                    "Use this to check if breaking news exists that markets haven't priced in."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Topic to search (e.g. 'Bitcoin price', 'Federal Reserve', 'CPI inflation')",
                        }
                    },
                    "required": ["topic"],
                },
            },
        ]

        # Load relevant knowledge base context
        kb_context = ""
        try:
            from bot.knowledge.knowledge_base import format_relevant_context
            kb_context = format_relevant_context(
                asset="BTC",
                timeframe="15m",
                agent_id="btc15m_kalshi",
            )
        except Exception:
            pass

        system_prompt = f"""You are a prediction market day trader. Your job is to scan watched markets
and identify SHORT-TERM trading opportunities right now.

Today: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
{(chr(10) + kb_context + chr(10)) if kb_context else ""}
OPPORTUNITY TYPES you look for:
- news_lag: Breaking news in the last 1-2 hours that the market price hasn't reflected yet
- momentum: Sustained directional price movement with volume (last 30-60 min)
- overreaction: Market moved sharply but has likely overshot; reversal expected
- volume_spike: Abnormally high volume suggesting informed traders know something
- resolution_imminent: Market resolves in <12 hours and is mispriced vs likely outcome

STRATEGY:
1. Call scan_watched_markets to see all current prices and movements
2. For markets with significant activity OR resolving soon, call search_breaking_news
3. Identify genuine opportunities — be SELECTIVE, not everything is a trade
4. Only flag opportunities where you see a clear, time-sensitive edge

IMPORTANT:
- Prediction markets resolve at 0 (No) or 1 (Yes). Day trading means getting in and out
  within 1-2 hours OR holding to resolution if it's imminent and you're highly confident.
- Ignore markets with very low volume (<2,000) — hard to enter/exit cleanly.
- A "news_lag" requires REAL recent news, not just general background noise.

After gathering data, respond ONLY in this JSON format:
{{
  "scan_summary": "<1-2 sentence summary of what you found>",
  "news_queries": ["<query1>", "<query2>"],
  "opportunities": [
    {{
      "market_id": "<id>",
      "market_title": "<title>",
      "exchange": "<exchange>",
      "opportunity_type": "<type>",
      "direction": "<HIGHER|LOWER>",
      "current_price": <0.0-1.0>,
      "estimated_edge": <0.03-0.25>,
      "confidence": <0.0-1.0>,
      "urgency": "<high|medium|low>",
      "window_minutes": <15-120>,
      "reasoning": "<concise explanation>"
    }}
  ]
}}

Only include opportunities with estimated_edge >= 0.05 AND confidence >= 0.50.
If nothing stands out, return an empty opportunities array.
"""

        messages: list[dict] = [
            {
                "role": "user",
                "content": "Scan all watched prediction markets for short-term day trading opportunities right now.",
            }
        ]

        news_queries: list[str] = []

        # Agentic loop
        max_iterations = 5
        response = None
        for _ in range(max_iterations):
            response = await self._claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1200,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result_str, query = await self._call_tool(block.name, block.input, snapshots)
                        if query:
                            news_queries.append(query)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        # Parse Claude's final JSON response
        final_text = ""
        if response:
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text

        research = self._parse_result(final_text, snapshots, news_queries)
        elapsed = time.time() - start

        opp_count = len(research.opportunities)
        log.info(
            f"[bold cyan]DAY RESEARCHER[/] Done in {elapsed:.1f}s — "
            f"{len(snapshots)} markets scanned, {opp_count} opportunit{'y' if opp_count == 1 else 'ies'} found"
        )

        output_lines = [f"🔭 Scanned {research.markets_scanned} markets | {opp_count} opportunities found"]
        for opp in research.opportunities:
            urgency_icon = "🔴" if opp.urgency == "high" else ("🟡" if opp.urgency == "medium" else "🟢")
            arrow = "▲" if opp.direction == "HIGHER" else "▼"
            output_lines.append(
                f"  {urgency_icon} {arrow} [{opp.opportunity_type}] {opp.market_title[:45]}"
                f" | edge={opp.estimated_edge:.0%} conf={opp.confidence:.0%}"
            )

        return AgentResult(
            success=True,
            output="\n".join(output_lines),
            data=research.to_dict(),
            agent_name=self.name,
            elapsed_seconds=elapsed,
        )

    # ── Tool implementations ────────────────────────────────────────────────────

    async def _call_tool(
        self,
        tool_name: str,
        tool_input: dict,
        snapshots: dict[str, MarketSnapshot],
    ) -> tuple[str, str]:
        """Execute a tool. Returns (result_string, news_query_or_empty)."""
        try:
            if tool_name == "scan_watched_markets":
                return self._format_market_scan(snapshots), ""

            elif tool_name == "search_breaking_news":
                topic = tool_input.get("topic", "")
                articles = await self.news.fetch_news(topic, limit=4)
                if articles:
                    items = [
                        f"- [{a.published_at.strftime('%H:%M')}] {a.title} ({a.source})"
                        if hasattr(a, "published_at") and a.published_at
                        else f"- {a.title} ({a.source})"
                        for a in articles[:4]
                    ]
                    return f"Recent news for '{topic}':\n" + "\n".join(items), topic
                return f"No recent news found for '{topic}'.", topic

        except Exception as e:
            log.error(f"[DAY RESEARCHER] Tool {tool_name} error: {e}")
            return f"Tool error: {e}", ""

        return "Unknown tool.", ""

    def _format_market_scan(self, snapshots: dict[str, MarketSnapshot]) -> str:
        """Format all market snapshots into a structured string for Claude."""
        now = datetime.now(timezone.utc)
        lines = [f"WATCHED MARKETS ({len(snapshots)} total) as of {now.strftime('%H:%M UTC')}:\n"]

        for mid, snap in snapshots.items():
            m = snap.market
            yes_price = m.prices.get("Yes", m.prices.get("yes", 0.5))
            no_price = 1.0 - yes_price

            # Calculate recent price movement
            history = snap.price_history
            price_change_30m = 0.0
            price_change_str = "n/a"
            if len(history) >= 2:
                # Find entry ~30 min ago
                cutoff = now - timedelta(minutes=30)
                older = next(
                    (p for t, p in reversed(history) if t <= cutoff),
                    history[0][1] if history else None,
                )
                if older is not None:
                    price_change_30m = yes_price - older
                    price_change_str = f"{price_change_30m:+.1%}"

            # Time to resolution
            resolution_str = "unknown"
            resolution_urgent = False
            if m.end_date:
                try:
                    if isinstance(m.end_date, str):
                        end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                    else:
                        end = m.end_date
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    hours_left = (end - now).total_seconds() / 3600
                    if hours_left < 0:
                        resolution_str = "RESOLVED"
                    elif hours_left < 6:
                        resolution_str = f"{hours_left:.1f}h ⚡"
                        resolution_urgent = True
                    elif hours_left < 24:
                        resolution_str = f"{hours_left:.0f}h"
                    else:
                        resolution_str = f"{hours_left/24:.0f}d"
                except Exception:
                    pass

            lines.append(
                f"  [{m.exchange}] {m.title[:55]}\n"
                f"    ID: {mid} | Yes: {yes_price:.1%} | No: {no_price:.1%} "
                f"| Vol: {m.volume:,.0f} | 30m change: {price_change_str} "
                f"| Resolves: {resolution_str}"
                + (" ← IMMINENT" if resolution_urgent else "")
            )

        return "\n".join(lines)

    def _parse_result(
        self,
        text: str,
        snapshots: dict[str, MarketSnapshot],
        news_queries: list[str],
    ) -> DayResearchResult:
        """Parse Claude's JSON output into a DayResearchResult."""
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

            data = json.loads(json_str)

            opportunities = []
            for o in data.get("opportunities", []):
                # Enrich with live volume from snapshot if available
                snap = snapshots.get(o.get("market_id", ""))
                volume = snap.market.volume if snap else 0.0

                opportunities.append(DayTradeOpportunity(
                    market_id=o.get("market_id", ""),
                    market_title=o.get("market_title", "Unknown"),
                    exchange=o.get("exchange", "kalshi"),
                    opportunity_type=o.get("opportunity_type", "momentum"),
                    direction=o.get("direction", "HIGHER"),
                    current_price=float(o.get("current_price", 0.5)),
                    estimated_edge=min(0.25, max(0.03, float(o.get("estimated_edge", 0.05)))),
                    confidence=min(1.0, max(0.0, float(o.get("confidence", 0.5)))),
                    urgency=o.get("urgency", "medium"),
                    window_minutes=int(o.get("window_minutes", 60)),
                    reasoning=o.get("reasoning", ""),
                    volume=volume,
                ))

            # Sort: high urgency first, then by confidence
            urgency_order = {"high": 0, "medium": 1, "low": 2}
            opportunities.sort(
                key=lambda o: (urgency_order.get(o.urgency, 1), -o.confidence)
            )

            return DayResearchResult(
                opportunities=opportunities,
                markets_scanned=len(snapshots),
                news_queries=news_queries or data.get("news_queries", []),
                scan_summary=data.get("scan_summary", f"Scanned {len(snapshots)} markets."),
            )

        except Exception as e:
            log.warning(f"[DAY RESEARCHER] Could not parse result JSON: {e}")
            return DayResearchResult(
                opportunities=[],
                markets_scanned=len(snapshots),
                news_queries=news_queries,
                scan_summary="Could not parse scan results.",
            )
