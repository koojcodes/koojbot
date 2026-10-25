"""
Simulation lifecycle manager.

Orchestrates the full simulation workflow:
1. Generate config (seed content, agent profiles)
2. Save config + profiles to simulation directory
3. Launch OASIS subprocess
4. Collect and summarize results

This is the high-level interface that Telegram commands and the
API call into. It delegates to SimulationConfigGenerator and
SimulationRunner for the actual work.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.utils.logger import log

from .simulation_config import SimulationConfig, SimulationConfigGenerator
from .simulation_runner import SimulationRunner, RunState
from .trader_profiles import get_all_profiles, save_profiles_csv


class SimulationPhase(str, Enum):
    """High-level simulation lifecycle phase."""
    CREATED = "created"
    CONFIGURING = "configuring"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SimulationResult:
    """Summary result from a completed simulation."""
    simulation_id: str
    topic: str
    status: str
    total_actions: int = 0
    num_rounds: int = 0
    posts: List[str] = None
    by_agent: Dict[str, int] = None
    by_type: Dict[str, int] = None
    error: str = ""

    def __post_init__(self):
        if self.posts is None:
            self.posts = []
        if self.by_agent is None:
            self.by_agent = {}
        if self.by_type is None:
            self.by_type = {}

    def format_summary(self) -> str:
        """Format a human-readable summary for Telegram."""
        if self.status == "failed":
            return f"Simulation failed: {self.error}"

        lines = [
            f"Simulation: {self.topic}",
            f"Status: {self.status}",
            f"Rounds: {self.num_rounds} | Actions: {self.total_actions}",
            "",
        ]

        if self.by_agent:
            lines.append("Agent activity:")
            for name, count in sorted(self.by_agent.items(), key=lambda x: -x[1]):
                lines.append(f"  {name}: {count} actions")
            lines.append("")

        if self.posts:
            lines.append("Key posts:")
            for post in self.posts[:8]:
                lines.append(f"  {post[:120]}")

        return "\n".join(lines)


class SimulationManager:
    """
    High-level simulation orchestrator.

    Usage:
        manager = SimulationManager()
        result = await manager.run_simulation("BTC breaks 100k")
    """

    def __init__(self):
        self._config_gen = SimulationConfigGenerator()
        self._runner = SimulationRunner()
        self._active_sim: Optional[str] = None

    async def run_simulation(
        self,
        topic: str,
        max_rounds: int = 5,
        timeout: int = 300,
        graph_id: Optional[str] = None,
    ) -> SimulationResult:
        """
        Run a complete simulation from topic to results.

        This is the main entry point. It:
        1. Generates the config (seed posts via LLM)
        2. Saves profiles and config to disk
        3. Launches the OASIS subprocess
        4. Waits for completion
        5. Returns summarized results

        Args:
            topic: Market scenario to simulate
            max_rounds: Number of simulation rounds
            timeout: Max seconds for the simulation
            graph_id: Optional Zep graph ID for context

        Returns:
            SimulationResult with posts, actions, and summary
        """
        log.info(f"[SWARM] Starting simulation: '{topic[:60]}'")

        # Step 1: Generate config
        try:
            config = self._config_gen.generate(
                topic=topic,
                max_rounds=max_rounds,
                graph_id=graph_id,
            )
        except Exception as e:
            log.error(f"[SWARM] Config generation failed: {e}")
            return SimulationResult(
                simulation_id="", topic=topic,
                status=SimulationPhase.FAILED.value,
                error=f"Config generation failed: {e}",
            )

        sim_dir = self._runner.get_sim_dir(config.simulation_id)
        self._active_sim = config.simulation_id

        # Step 2: Save config and profiles
        try:
            config_path = str(sim_dir / "simulation_config.json")
            config.save(config_path)

            profiles = get_all_profiles()
            save_profiles_csv(profiles, str(sim_dir / "twitter_profiles.csv"))

            log.info(
                f"[SWARM] Config saved: {len(config.initial_posts)} seed posts, "
                f"{len(profiles)} agents, {config.max_rounds} rounds"
            )
        except Exception as e:
            log.error(f"[SWARM] Failed to save config: {e}")
            return SimulationResult(
                simulation_id=config.simulation_id, topic=topic,
                status=SimulationPhase.FAILED.value,
                error=f"Config save failed: {e}",
            )

        # Step 3: Launch simulation
        run_state = await self._runner.start_simulation(
            simulation_id=config.simulation_id,
            config_path=config_path,
            timeout=timeout,
        )

        self._active_sim = None

        # Step 4: Collect results
        if run_state.status == "completed":
            summary = self._runner.get_summary(config.simulation_id)
            return SimulationResult(
                simulation_id=config.simulation_id,
                topic=topic,
                status=SimulationPhase.COMPLETED.value,
                total_actions=summary.get("total_actions", 0),
                num_rounds=summary.get("num_rounds", 0),
                posts=summary.get("posts", []),
                by_agent=summary.get("by_agent", {}),
                by_type=summary.get("by_type", {}),
            )
        else:
            return SimulationResult(
                simulation_id=config.simulation_id,
                topic=topic,
                status=SimulationPhase.FAILED.value,
                error=run_state.error,
            )

    async def stop_current(self) -> bool:
        """Stop the currently running simulation."""
        if self._active_sim:
            return await self._runner.stop_simulation(self._active_sim)
        return False

    def get_status(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific simulation."""
        state = self._runner.get_status(simulation_id)
        return state.to_dict() if state else None

    def list_simulations(self) -> List[Dict[str, Any]]:
        """List all simulations."""
        return self._runner.list_simulations()
