"""
Simulation configuration generator for trading scenarios.

Given a topic/scenario, uses GPT-4o-mini to generate seed content
(initial posts that kick off the trader discussion) and assembles
a complete simulation config for OASIS.

Simplified from MiroFish's simulation_config_generator.py — fewer
rounds, single platform (Twitter), trading-focused.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.utils.logger import log

from .llm_client import SwarmLLMClient
from .trader_profiles import (
    get_all_profiles,
    profiles_to_config_list,
    TraderProfile,
)


@dataclass
class SimulationConfig:
    """Complete configuration for one simulation run."""
    simulation_id: str
    topic: str
    graph_id: Optional[str] = None
    max_rounds: int = 5
    agents_per_round: int = 6
    platform: str = "twitter"

    # Agent configs (auto-populated from profiles)
    agent_configs: List[Dict[str, Any]] = field(default_factory=list)

    # Seed content — initial posts that start the discussion
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)

    # Hot topics for agents to reference
    hot_topics: List[str] = field(default_factory=list)

    # Narrative framing
    scenario_description: str = ""

    # Available actions for agents
    available_actions: List[str] = field(default_factory=lambda: [
        "CREATE_POST", "LIKE_POST", "REPOST", "QUOTE_POST", "DO_NOTHING",
    ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: str) -> None:
        """Save config to JSON file."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str) -> "SimulationConfig":
        """Load config from JSON file."""
        data = json.loads(Path(path).read_text())
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SimulationConfigGenerator:
    """
    Generates simulation configs for trading scenarios.

    Uses GPT-4o-mini to create seed content that kicks off realistic
    trader discussions around a given topic.
    """

    def __init__(self, llm_client: Optional[SwarmLLMClient] = None):
        self._llm = llm_client

    @property
    def llm(self) -> SwarmLLMClient:
        if self._llm is None:
            self._llm = SwarmLLMClient()
        return self._llm

    def generate(
        self,
        topic: str,
        max_rounds: int = 5,
        graph_id: Optional[str] = None,
    ) -> SimulationConfig:
        """
        Generate a complete simulation config for a trading scenario.

        Args:
            topic: The market scenario to simulate (e.g. "BTC breaks 100k")
            max_rounds: Number of simulation rounds (default 5)
            graph_id: Optional Zep graph ID for context enrichment

        Returns:
            SimulationConfig ready for OASIS execution
        """
        sim_id = f"sim_{uuid.uuid4().hex[:12]}"
        profiles = get_all_profiles()

        log.info(f"[SWARM CONFIG] Generating config for: '{topic[:60]}'")

        # Generate seed content via LLM
        seed_content = self._generate_seed_content(topic, profiles)

        config = SimulationConfig(
            simulation_id=sim_id,
            topic=topic,
            graph_id=graph_id,
            max_rounds=max_rounds,
            agents_per_round=min(6, len(profiles)),
            agent_configs=profiles_to_config_list(profiles),
            initial_posts=seed_content.get("initial_posts", []),
            hot_topics=seed_content.get("hot_topics", []),
            scenario_description=seed_content.get("scenario_description", topic),
        )

        log.info(
            f"[SWARM CONFIG] Generated: {len(config.initial_posts)} seed posts, "
            f"{len(config.hot_topics)} hot topics, {max_rounds} rounds"
        )
        return config

    def _generate_seed_content(
        self,
        topic: str,
        profiles: List[TraderProfile],
    ) -> Dict[str, Any]:
        """Use LLM to generate seed posts that kick off the discussion."""
        archetype_list = "\n".join(
            f"  - agent_id={p.agent_id}: {p.name} ({p.archetype}) — {p.trading_style}"
            for p in profiles
        )

        try:
            result = self.llm.chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a trading simulation designer. Given a market scenario and "
                            "a list of trader archetypes, generate seed content to kick off a "
                            "realistic Crypto Twitter discussion.\n\n"
                            "Return JSON with:\n"
                            '- "scenario_description": 1-2 sentence framing of the scenario\n'
                            '- "hot_topics": array of 3-5 keywords/phrases traders would discuss\n'
                            '- "initial_posts": array of 3-4 seed posts, each with:\n'
                            '  - "content": the tweet text (realistic trading Twitter style)\n'
                            '  - "poster_agent_id": which trader archetype posts this (use agent_id)\n'
                            '  - "poster_name": the trader name\n\n'
                            "Make seed posts feel authentic — different traders react differently. "
                            "The NewsTrader breaks the news, the MacroAnalyst provides context, "
                            "the RetailDegen gets hyped, etc."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Market scenario: {topic}\n\n"
                            f"Available trader archetypes:\n{archetype_list}\n\n"
                            f"Generate seed content for this simulation."
                        ),
                    },
                ],
                temperature=0.7,
                max_tokens=1500,
            )
            return result

        except Exception as e:
            log.warning(f"[SWARM CONFIG] Seed generation failed, using defaults: {e}")
            return {
                "scenario_description": topic,
                "hot_topics": ["BTC", "crypto", "prediction market"],
                "initial_posts": [
                    {
                        "content": f"BREAKING: {topic}",
                        "poster_agent_id": 5,  # NewsTrader
                        "poster_name": "Breaking Crypto",
                    },
                ],
            }
