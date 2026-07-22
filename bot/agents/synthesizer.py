"""Synthesis Agent — converts research results into a trading thesis with probability estimates."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import anthropic

from bot.agents.base import Agent, AgentResult
from bot.config import settings
from bot.utils.logger import log


@dataclass
class MarketImplication:
    market_id: str
    market_title: str
    current_price: float       # current Yes price (0-1)
    direction: str             # "HIGHER" or "LOWER"
    probability_delta: float   # how much to shift probability (+0.15 = +15%)
    confidence: float          # 0-1 confidence in this specific implication


@dataclass
class SynthesisResult:
    trading_thesis: str                         # "Fed pivot bullish for BTC markets"
    content_type: str                           # "news" | "trading_advice" | "analysis" | "other"
    market_implications: list[MarketImplication] = field(default_factory=list)
    overall_confidence: float = 0.5
    time_horizon: str = "days"                  # "hours", "days", "weeks"
    risk_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trading_thesis": self.trading_thesis,
            "content_type": self.content_type,
            "market_implications": [
                {
                    "market_id": mi.market_id,
                    "market_title": mi.market_title,
                    "current_price": mi.current_price,
                    "direction": mi.direction,
                    "probability_delta": mi.probability_delta,
                    "confidence": mi.confidence,
                }
                for mi in self.market_implications
            ],
            "overall_confidence": self.overall_confidence,
            "time_horizon": self.time_horizon,
            "risk_factors": self.risk_factors,
        }


class SynthesizerAgent(Agent):
    """
    Synthesis Agent: Takes research output and produces a structured trading thesis.

    No tools — pure Claude reasoning based on researcher's output.
    Outputs structured probability deltas for each relevant market.
    """

    name = "synthesizer"

    def __init__(self, anthropic_key: str = ""):
        self._claude = anthropic.AsyncAnthropic(
            api_key=anthropic_key or settings.ANTHROPIC_API_KEY
        )

    async def run(self, input_text: str, context: dict) -> AgentResult:
        start = time.time()
        log.info(f"[magenta]SYNTHESIZER[/] Processing research for trading thesis...")

        # Extract research data from context
        summary = context.get("summary", input_text)
        markets = context.get("relevant_markets", [])
        key_topics = context.get("key_topics", [])
        content_type = context.get("content_type", "other")
        num_articles = context.get("num_articles", 0)

        # Detect short-term markets (resolve in minutes) and annotate them
        short_term_ids: set[str] = set()
        markets_lines = []
        for m in markets[:6]:
            title = m.get("title", "")
            is_short = any(kw in title.lower() for kw in ["15 min", "15min", "1 hour", "1hr", "up in next"])
            tag = " ⚡[RESOLVES IN ~15 MINUTES — short-term only]" if is_short else ""
            if is_short:
                short_term_ids.add(m.get("id", ""))
            markets_lines.append(
                f"  - [{m['exchange']}] {m['title']} | Yes: {m['yes_price']:.1%} | Vol: {m['volume']:,.0f}{tag}"
            )
        markets_text = "\n".join(markets_lines) if markets_lines else "  (no specific markets found)"

        short_term_note = ""
        if short_term_ids:
            short_term_note = (
                "\nSHORT-TERM MARKET NOTE: Markets tagged ⚡ resolve in ~15 minutes. "
                "Only include them in market_implications if the content implies an "
                "IMMEDIATE (next 15-30 min) directional move in BTC price. "
                "If included, use time_horizon: \"minutes\" and set probability_delta "
                "based on how strongly the content predicts short-term direction."
            )

        # Build the appropriate prompt based on content type
        if content_type == "day_trading_strategy":
            prompt = f"""You are a day trading strategy analyst for a prediction market bot.

A content creator has shared a day trading strategy (via video transcript or post). Your job is to:
1. Extract the exact trading rules from the content
2. Assess whether those rules currently apply to the pinned markets
3. Estimate a probability delta IF the strategy signals an entry right now

STRATEGY CONTENT SUMMARY:
{summary}

KEY TOPICS: {', '.join(key_topics)}

PREDICTION MARKETS TO EVALUATE (apply the strategy to these):
{markets_text}
{short_term_note}

Extract the strategy's rules and assess current market fit. Respond in this EXACT JSON format:
{{
  "trading_thesis": "<Concise description of the strategy and whether conditions are met now>",
  "strategy_rules": {{
    "entry_conditions": ["<condition1>", "<condition2>"],
    "exit_conditions": ["<stop_loss_rule>", "<take_profit_rule>"],
    "asset": "<BTC|ETH|etc>",
    "timeframe": "<1m|5m|15m|1h|daily>",
    "signal_type": "<long|short|neutral>"
  }},
  "overall_confidence": <0.0-1.0 — how well current conditions match the strategy's entry rules>,
  "time_horizon": "<minutes|hours|days|weeks>",
  "risk_factors": ["<risk1>", "<risk2>"],
  "market_implications": [
    {{
      "market_id": "<id>",
      "market_title": "<title>",
      "current_price": <yes price>,
      "direction": "<HIGHER|LOWER>",
      "probability_delta": <0.01-0.30>,
      "confidence": <0.0-1.0>
    }}
  ]
}}

Only include market_implications if the strategy currently signals an entry.
If conditions are not currently met, return empty market_implications and explain in trading_thesis."""
        else:
            prompt = f"""You are a prediction market trading analyst. Convert this research into a trading thesis.

RESEARCH SUMMARY:
{summary}

KEY TOPICS: {', '.join(key_topics)}
ARTICLES FOUND: {num_articles}
CONTENT TYPE: {content_type}

RELEVANT PREDICTION MARKETS:
{markets_text}
{short_term_note}

Generate a trading thesis. For each relevant market, estimate:
- Direction (HIGHER or LOWER for Yes probability)
- Probability delta (how much to shift from current price, e.g. +0.12 means +12%)
- Confidence (0.0-1.0)

Consider:
- Is this truly new information or already priced in?
- How directly does this affect each market?
- What are the main risk factors?
- For ⚡ short-term markets: does the content suggest an immediate price move in the next 15 min?

Respond in this EXACT JSON format:
{{
  "trading_thesis": "<1-2 sentence thesis>",
  "overall_confidence": <0.0-1.0>,
  "time_horizon": "<minutes|hours|days|weeks>",
  "risk_factors": ["<risk1>", "<risk2>"],
  "market_implications": [
    {{
      "market_id": "<id from list above>",
      "market_title": "<title>",
      "current_price": <current yes price>,
      "direction": "<HIGHER|LOWER>",
      "probability_delta": <0.01-0.30>,
      "confidence": <0.0-1.0>
    }}
  ]
}}

Only include markets with probability_delta >= 0.05 and confidence >= 0.4.
If no markets are significantly affected, return empty market_implications array."""

        try:
            response = await self._claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()

            # Parse JSON from response
            synthesis_data = self._parse_synthesis(text, markets, content_type)

            elapsed = time.time() - start
            type_emoji = {
                "day_trading_strategy": "📐",
                "news": "📰",
                "trading_advice": "💡",
                "analysis": "📊",
            }.get(synthesis_data.content_type, "🧠")

            log.info(
                f"[magenta]SYNTHESIZER[/] Done in {elapsed:.1f}s — "
                f"thesis: '{synthesis_data.trading_thesis[:60]}...' "
                f"conf={synthesis_data.overall_confidence:.0%}, "
                f"{len(synthesis_data.market_implications)} implications"
            )

            implications_text = ""
            for mi in synthesis_data.market_implications:
                arrow = "▲" if mi.direction == "HIGHER" else "▼"
                implications_text += (
                    f"\n  {arrow} {mi.market_title[:50]} "
                    f"({mi.current_price:.0%}→{mi.current_price + mi.probability_delta:.0%}, "
                    f"conf={mi.confidence:.0%})"
                )

            output_summary = (
                f"{type_emoji} Thesis: {synthesis_data.trading_thesis}\n"
                f"Confidence: {synthesis_data.overall_confidence:.0%} | "
                f"Horizon: {synthesis_data.time_horizon}"
                f"{implications_text}"
            )

            return AgentResult(
                success=True,
                output=output_summary,
                data=synthesis_data.to_dict(),
                agent_name=self.name,
                elapsed_seconds=elapsed,
            )

        except Exception as e:
            log.error(f"[magenta]SYNTHESIZER[/] Failed: {e}")
            return AgentResult(
                success=False,
                output=f"Synthesis failed: {e}",
                data={"error": str(e)},
                agent_name=self.name,
                elapsed_seconds=time.time() - start,
            )

    def _parse_synthesis(
        self,
        text: str,
        markets_context: list[dict],
        content_type: str,
    ) -> SynthesisResult:
        """Parse Claude's JSON response into a SynthesisResult."""
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_str = text
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0].strip()
            elif "{" in text:
                # Find the JSON object
                start = text.index("{")
                end = text.rindex("}") + 1
                json_str = text[start:end]

            data = json.loads(json_str)

            implications = []
            for imp in data.get("market_implications", []):
                implications.append(MarketImplication(
                    market_id=imp.get("market_id", ""),
                    market_title=imp.get("market_title", ""),
                    current_price=float(imp.get("current_price", 0.5)),
                    direction=imp.get("direction", "HIGHER"),
                    probability_delta=min(0.30, max(0.01, float(imp.get("probability_delta", 0.05)))),
                    confidence=min(1.0, max(0.0, float(imp.get("confidence", 0.5)))),
                ))

            # For day_trading_strategy, embed the strategy rules into the thesis if present
            thesis = data.get("trading_thesis", "Market analysis complete")
            if content_type == "day_trading_strategy" and data.get("strategy_rules"):
                sr = data["strategy_rules"]
                entry = "; ".join(sr.get("entry_conditions", []))
                exit_ = "; ".join(sr.get("exit_conditions", []))
                signal = sr.get("signal_type", "")
                tf = sr.get("timeframe", "")
                asset = sr.get("asset", "")
                rules_summary = f" [Strategy: {signal.upper()} {asset} {tf} | Entry: {entry} | Exit: {exit_}]"
                thesis = thesis + rules_summary if len(thesis) + len(rules_summary) < 500 else thesis

            return SynthesisResult(
                trading_thesis=thesis,
                content_type=content_type,
                market_implications=implications,
                overall_confidence=min(1.0, max(0.0, float(data.get("overall_confidence", 0.5)))),
                time_horizon=data.get("time_horizon", "days"),
                risk_factors=data.get("risk_factors", []),
            )

        except Exception as e:
            log.warning(f"Could not parse synthesis JSON: {e}. Using defaults.")
            return SynthesisResult(
                trading_thesis="Unable to generate specific trading thesis from this content.",
                content_type=content_type,
                market_implications=[],
                overall_confidence=0.3,
                time_horizon="days",
                risk_factors=["Parse error — review manually"],
            )
