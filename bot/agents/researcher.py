"""Researcher Agent — takes social media content and finds supporting evidence + relevant markets."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import anthropic

from bot.agents.base import Agent, AgentResult
from bot.config import settings
from bot.data.market_data import MarketDataFetcher
from bot.data.news import NewsCollector, NewsArticle
from bot.exchanges.base import Market
from bot.utils.logger import log

# Lazy-loaded swarm graph for knowledge graph search
_swarm_graph = None


def _get_swarm_graph():
    """Lazy-init the async graph wrapper (only if swarm is enabled)."""
    global _swarm_graph
    if _swarm_graph is None and settings.SWARM_ENABLED and settings.ZEP_API_KEY:
        from bot.swarm.async_wrappers import SwarmGraphAsync
        _swarm_graph = SwarmGraphAsync()
    return _swarm_graph


@dataclass
class ResearchResult:
    original_content: str
    content_type: str          # "news", "trading_advice", "analysis", "other"
    summary: str               # 2-3 sentence research summary
    key_topics: list[str]      # e.g. ["bitcoin", "fed rate cut"]
    related_articles: list[NewsArticle] = field(default_factory=list)
    relevant_markets: list[Market] = field(default_factory=list)
    source_platform: str = "text"

    def to_dict(self) -> dict:
        return {
            "original_content": self.original_content[:500],
            "content_type": self.content_type,
            "summary": self.summary,
            "key_topics": self.key_topics,
            "num_articles": len(self.related_articles),
            "relevant_markets": [
                {
                    "id": m.id,
                    "title": m.title,
                    "exchange": m.exchange,
                    "yes_price": m.prices.get("Yes", 0.5),
                    "volume": m.volume,
                    "end_date": m.end_date.isoformat() if m.end_date else None,
                }
                for m in self.relevant_markets[:5]
            ],
            "source_platform": self.source_platform,
        }


class ResearcherAgent(Agent):
    """
    Researcher Agent: Analyzes social media content to find news context and relevant markets.

    Uses Claude with tool_use to:
    1. Identify key topics in the content
    2. Search for supporting news articles
    3. Find relevant Kalshi/prediction markets
    """

    name = "researcher"

    def __init__(
        self,
        news_collector: NewsCollector,
        market_fetcher: MarketDataFetcher,
        anthropic_key: str = "",
    ):
        self.news = news_collector
        self.markets = market_fetcher
        self._claude = anthropic.AsyncAnthropic(
            api_key=anthropic_key or settings.ANTHROPIC_API_KEY
        )

    async def run(self, input_text: str, context: dict) -> AgentResult:
        start = time.time()
        source_platform = context.get("source_platform", "text")
        pinned_markets: list[dict] = context.get("pinned_markets", [])
        extra_hints: list[str] = context.get("extra_search_hints", [])
        log.info(
            f"[cyan]RESEARCHER[/] Analyzing: {input_text[:80]}..."
            + (f" | {len(pinned_markets)} pinned market(s)" if pinned_markets else "")
        )

        # Seed pinned markets directly into collected_markets so they're always present
        # Convert from dict → Market-like objects using a lightweight wrapper
        collected_articles: list[NewsArticle] = []
        collected_markets: list[Market] = []

        for pm in pinned_markets:
            # Re-hydrate as a Market from the dict the bot passed in
            from bot.exchanges.base import Market as MktClass
            collected_markets.append(MktClass(
                id=pm.get("id", ""),
                title=pm.get("title", ""),
                description=pm.get("description", ""),
                exchange=pm.get("exchange", "kalshi"),
                outcomes=["Yes", "No"],
                prices={"Yes": pm.get("yes_price", 0.5)},
                volume=pm.get("volume", 0.0),
                end_date=pm.get("end_date"),
                category=pm.get("category", ""),
            ))

        # Build pinned market note for the system prompt
        pinned_note = ""
        if pinned_markets:
            pinned_lines = "\n".join(
                f"  - [{m.get('exchange','kalshi')}] {m.get('title','')} "
                f"| Yes: {m.get('yes_price',0.5):.1%} | id: {m.get('id','')}"
                for m in pinned_markets
            )
            pinned_note = (
                f"\n\nPINNED MARKETS (user specifically tagged these — always include in your analysis):\n"
                f"{pinned_lines}\n"
                f"Focus your analysis on how the content affects these specific markets."
            )

        # Build extra search hint note
        hints_note = ""
        if extra_hints:
            hints_note = f"\n\nExtra search topics from user tags: {', '.join(extra_hints)}"

        # Define tools available to the researcher
        tools = [
            {
                "name": "search_news",
                "description": "Search for recent news articles about a topic. Returns article titles and descriptions.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g. 'Bitcoin ETF inflows record', 'Fed rate cut June')"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "search_markets",
                "description": "Search for prediction markets related to a topic. Returns market titles and current prices.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for prediction markets (e.g. 'bitcoin price', 'federal reserve')"
                        }
                    },
                    "required": ["query"]
                }
            },
        ]

        # Add knowledge graph search tool if swarm is enabled
        swarm = _get_swarm_graph()
        if swarm:
            tools.append({
                "name": "search_knowledge_graph",
                "description": (
                    "Search the trading knowledge graph for relevant facts, entities, "
                    "and relationships. Returns trading rules, market entity connections, "
                    "and historical insights learned from past analysis."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g. 'RSI oversold BTC', 'FOMC rate decision impact')"
                        }
                    },
                    "required": ["query"]
                }
            })

        system_prompt = f"""You are a financial research analyst for a prediction market trading bot.
Your job: analyze social media content/news/trading advice/video transcripts to find prediction market opportunities.

Steps:
1. Identify the key financial topics (e.g. BTC, Fed rates, inflation, specific stocks)
2. Classify the content type — choose ONE:
   - "news": factual market-moving event (e.g. Fed announcement, ETF inflow report)
   - "trading_advice": directional calls (e.g. "buy BTC now", "short this level")
   - "analysis": technical/fundamental analysis with charts, indicators, patterns
   - "day_trading_strategy": specific entry/exit rules, chart patterns, setups for short-term trades
     (e.g. "enter when RSI crosses 30", "stop loss below support", "scalp this level")
   - "other": general market commentary not actionable for prediction markets
3. Search for news articles to verify and expand on the claims
4. Search for relevant prediction markets that could be affected

For "day_trading_strategy" content:
- Extract the specific trading rules, entry conditions, exit conditions, and market context
- Identify which asset/market the strategy targets (e.g. BTC, ETH)
- Note the timeframe (scalp/15m/1h/daily)
- Search for prediction markets that match the targeted asset and timeframe

Use search_news and search_markets tools. Be efficient — 1-2 searches each is enough.
After tool calls, summarize your findings concisely.{pinned_note}{hints_note}"""

        messages = [
            {
                "role": "user",
                "content": f"""Analyze this {source_platform} content for trading opportunities:

"{input_text}"

Find relevant news and prediction markets. Classify the content type."""
                + (
                    f"\n\nNote: The user tagged this content for: "
                    + ", ".join(m.get("title", m.get("id", "")) for m in pinned_markets)
                    if pinned_markets else ""
                ),
            }
        ]

        # Agentic loop — Claude uses tools until done
        max_iterations = 4
        for _ in range(max_iterations):
            response = await self._claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1000,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            # Check if Claude wants to use tools
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_result = await self._call_tool(
                            block.name,
                            block.input,
                            collected_articles,
                            collected_markets,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": tool_result,
                        })

                # Add assistant response and tool results to messages
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            else:
                # Claude is done with tools — extract final summary
                break

        # Extract Claude's final text response
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        # Parse structured output from Claude's response
        content_type = self._extract_content_type(final_text)
        key_topics = self._extract_key_topics(input_text, collected_markets)

        result = ResearchResult(
            original_content=input_text,
            content_type=content_type,
            summary=final_text[:600].strip() if final_text else f"Analyzed {source_platform} content about {', '.join(key_topics[:3])}",
            key_topics=key_topics,
            related_articles=collected_articles[:5],
            relevant_markets=collected_markets[:6],
            source_platform=source_platform,
        )

        elapsed = time.time() - start
        log.info(
            f"[cyan]RESEARCHER[/] Done in {elapsed:.1f}s — "
            f"{len(collected_articles)} articles, {len(collected_markets)} markets, "
            f"type={content_type}"
        )

        output_summary = (
            f"📰 Found {len(collected_articles)} articles + {len(collected_markets)} markets\n"
            f"Type: {content_type} | Topics: {', '.join(key_topics[:3])}"
        )

        return AgentResult(
            success=True,
            output=output_summary,
            data=result.to_dict(),
            agent_name=self.name,
            elapsed_seconds=elapsed,
        )

    async def _call_tool(
        self,
        tool_name: str,
        tool_input: dict,
        collected_articles: list,
        collected_markets: list,
    ) -> str:
        """Execute a tool call and return result as string."""
        try:
            if tool_name == "search_news":
                query = tool_input.get("query", "")
                articles = await self.news.fetch_news(query, limit=5)
                collected_articles.extend(articles)
                if articles:
                    items = [f"- {a.title} ({a.source})" for a in articles[:5]]
                    return f"Found {len(articles)} articles:\n" + "\n".join(items)
                return "No articles found."

            elif tool_name == "search_markets":
                query = tool_input.get("query", "")
                markets = await self.markets.search_markets(query, limit=8)
                # Deduplicate by id
                existing_ids = {m.id for m in collected_markets}
                for m in markets:
                    if m.id not in existing_ids:
                        collected_markets.append(m)
                        existing_ids.add(m.id)
                if markets:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    items = []
                    for m in markets[:6]:
                        ttl = ""
                        if m.end_date:
                            try:
                                end = m.end_date
                                if end.tzinfo is None:
                                    end = end.replace(tzinfo=timezone.utc)
                                mins = (end - now).total_seconds() / 60
                                if mins < 30:
                                    ttl = f" | ⚡ closes in {mins:.0f}min"
                                elif mins < 90:
                                    ttl = f" | closes in {mins:.0f}min"
                            except Exception:
                                pass
                        items.append(
                            f"- [{m.exchange}] {m.title} | Yes={m.prices.get('Yes', 0):.0%}"
                            f" | Vol={m.volume:,.0f}{ttl} | id={m.id}"
                        )
                    return f"Found {len(markets)} markets:\n" + "\n".join(items)
                return "No markets found."

            elif tool_name == "search_knowledge_graph":
                query = tool_input.get("query", "")
                swarm = _get_swarm_graph()
                if swarm:
                    graph_id = await swarm.get_graph_id()
                    if graph_id:
                        result = await swarm.quick_search(graph_id, query, limit=8)
                        if result.facts:
                            items = [f"- {fact}" for fact in result.facts[:8]]
                            return (
                                f"Found {len(result.facts)} knowledge graph facts:\n"
                                + "\n".join(items)
                            )
                return "No knowledge graph data available."

        except Exception as e:
            log.error(f"Tool call {tool_name} failed: {e}")
            return f"Tool error: {e}"

        return "Unknown tool."

    def _extract_content_type(self, text: str) -> str:
        """Extract content classification from Claude's response."""
        text_lower = text.lower()
        # Check day_trading_strategy first (more specific than generic analysis)
        if any(w in text_lower for w in [
            "day_trading_strategy", "day trading strategy", "entry condition", "exit condition",
            "entry rule", "stop loss", "take profit", "scalp", "setup", "rsi crosses",
            "support level", "resistance level", "trading rule",
        ]):
            return "day_trading_strategy"
        elif any(w in text_lower for w in ["news", "article", "report", "announced", "published"]):
            return "news"
        elif any(w in text_lower for w in ["advice", "strategy", "tactic", "recommend", "should buy", "should sell"]):
            return "trading_advice"
        elif any(w in text_lower for w in ["analysis", "technical", "chart", "pattern", "indicator"]):
            return "analysis"
        return "other"

    def _extract_key_topics(self, text: str, markets: list[Market]) -> list[str]:
        """Extract key topics from the content and found markets."""
        topics = []
        text_lower = text.lower()

        topic_keywords = {
            "bitcoin/BTC": ["bitcoin", "btc"],
            "Ethereum/ETH": ["ethereum", "eth"],
            "Federal Reserve": ["fed", "federal reserve", "fomc", "rate cut", "rate hike"],
            "inflation": ["inflation", "cpi", "pce"],
            "crypto": ["crypto", "cryptocurrency", "altcoin"],
            "stocks": ["nasdaq", "s&p", "dow", "stocks", "equities"],
            "oil": ["oil", "crude", "wti", "opec"],
        }

        for topic, keywords in topic_keywords.items():
            if any(kw in text_lower for kw in keywords):
                topics.append(topic)

        # Also add exchange categories from found markets
        for m in markets[:3]:
            if m.category and m.category not in topics:
                topics.append(m.category)

        return topics[:5] if topics else ["general market"]
