"""
Scheduled Daily Simulations — auto-runs swarm simulations for long-horizon markets.

Discovers markets expiring in 1-7 days, runs daily simulations for each,
and stores results for pipeline enrichment. Runs as an async task within
the engine's event loop.

Configuration:
  - SWARM_DAILY_SIM_ENABLED: toggle on/off (defaults to SWARM_ENABLED)
  - SWARM_DAILY_SIM_HOUR: UTC hour to run daily sims (default: 8 = 8 AM UTC)
  - SWARM_DAILY_SIM_MAX: max sims per day (default: 3)
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.config import settings
from bot.utils.logger import log


# Defaults
_DEFAULT_SIM_HOUR = 8   # 8 AM UTC
_DEFAULT_MAX_SIMS = 3
_MIN_MARKET_HORIZON_HOURS = 24    # At least 1 day out
_MAX_MARKET_HORIZON_HOURS = 168   # At most 7 days out

# State file for tracking what was already simulated today
_STATE_DIR = Path(__file__).resolve().parent.parent.parent / "simulation_data"
_STATE_FILE = _STATE_DIR / "daily_sim_state.json"


@dataclass
class DailySimResult:
    """Result from a scheduled daily simulation."""
    market_id: str
    market_title: str
    topic: str
    simulation_id: str
    status: str
    total_actions: int = 0
    bullish_count: int = 0
    bearish_count: int = 0
    consensus: str = ""  # "bullish", "bearish", or "mixed"
    ran_at: str = ""


@dataclass
class DailySimState:
    """Persisted state for daily simulation scheduling."""
    last_run_date: str = ""  # ISO date
    simulations_today: List[Dict[str, Any]] = field(default_factory=list)
    total_runs: int = 0

    def already_ran_today(self) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.last_run_date == today

    def save(self):
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps({
            "last_run_date": self.last_run_date,
            "simulations_today": self.simulations_today,
            "total_runs": self.total_runs,
        }, indent=2))

    @staticmethod
    def load() -> "DailySimState":
        if _STATE_FILE.exists():
            try:
                data = json.loads(_STATE_FILE.read_text())
                return DailySimState(
                    last_run_date=data.get("last_run_date", ""),
                    simulations_today=data.get("simulations_today", []),
                    total_runs=data.get("total_runs", 0),
                )
            except Exception:
                pass
        return DailySimState()


class ScheduledSimulationRunner:
    """
    Runs daily swarm simulations for long-horizon markets.

    Discovers markets expiring in 1-7 days, picks the most interesting
    ones, and runs simulations to generate predictions.

    Usage:
        runner = ScheduledSimulationRunner(market_data_fetcher)
        # Call from engine loop:
        await runner.maybe_run_daily()  # Only runs once per day at the configured hour
    """

    def __init__(self, market_data_fetcher=None):
        self._market_data = market_data_fetcher
        self._state = DailySimState.load()
        self._sim_hour = int(
            getattr(settings, "SWARM_DAILY_SIM_HOUR", _DEFAULT_SIM_HOUR)
        )
        self._max_sims = int(
            getattr(settings, "SWARM_DAILY_SIM_MAX", _DEFAULT_MAX_SIMS)
        )

    async def maybe_run_daily(self) -> List[DailySimResult]:
        """
        Check if it's time to run daily simulations and run them if so.

        Returns list of simulation results (empty if not time yet or already ran).
        """
        if not settings.SWARM_ENABLED:
            return []

        # Check if already ran today
        if self._state.already_ran_today():
            return []

        # Check if it's the right hour
        now = datetime.now(timezone.utc)
        if now.hour < self._sim_hour:
            return []

        log.info("[DAILY SIM] Starting scheduled daily simulations")
        results = await self._run_daily_simulations()

        # Update state
        self._state.last_run_date = now.strftime("%Y-%m-%d")
        self._state.simulations_today = [
            {
                "market_id": r.market_id,
                "market_title": r.market_title,
                "simulation_id": r.simulation_id,
                "status": r.status,
                "consensus": r.consensus,
                "ran_at": r.ran_at,
            }
            for r in results
        ]
        self._state.total_runs += 1
        self._state.save()

        log.info(
            f"[DAILY SIM] Completed {len(results)} simulation(s) for today"
        )
        return results

    async def _run_daily_simulations(self) -> List[DailySimResult]:
        """Discover long-horizon markets and simulate them."""
        results: List[DailySimResult] = []

        # Discover candidate markets
        candidates = await self._discover_candidates()
        if not candidates:
            log.info("[DAILY SIM] No candidate markets found for daily simulation")
            return results

        # Pick the top N most interesting
        selected = candidates[:self._max_sims]

        from .simulation_manager import SimulationManager
        from .accuracy_tracker import AccuracyTracker
        from .async_wrappers import SwarmGraphAsync

        sim_manager = SimulationManager()
        tracker = AccuracyTracker()
        await tracker.init()

        graph = SwarmGraphAsync()
        graph_id = await graph.get_graph_id()

        for market in selected:
            topic = self._build_sim_topic(market)
            now = datetime.now(timezone.utc)

            try:
                sim_result = await sim_manager.run_simulation(
                    topic=topic,
                    max_rounds=5,
                    timeout=300,
                    graph_id=graph_id,
                )

                # Analyze consensus
                bullish = 0
                bearish = 0
                for post in sim_result.posts:
                    post_lower = post.lower()
                    if any(w in post_lower for w in ["bullish", "buy", "long", "higher", "rally"]):
                        bullish += 1
                    if any(w in post_lower for w in ["bearish", "sell", "short", "lower", "decline"]):
                        bearish += 1

                if bullish > bearish * 1.5:
                    consensus = "bullish"
                elif bearish > bullish * 1.5:
                    consensus = "bearish"
                else:
                    consensus = "mixed"

                result = DailySimResult(
                    market_id=market.get("id", ""),
                    market_title=market.get("title", ""),
                    topic=topic,
                    simulation_id=sim_result.simulation_id,
                    status=sim_result.status,
                    total_actions=sim_result.total_actions,
                    bullish_count=bullish,
                    bearish_count=bearish,
                    consensus=consensus,
                    ran_at=now.isoformat(),
                )
                results.append(result)

                # Record predictions for accuracy tracking
                if sim_result.status == "completed":
                    await tracker.record_predictions(
                        simulation_id=sim_result.simulation_id,
                        topic=topic,
                        posts=sim_result.posts,
                        markets=[market],
                        by_agent=sim_result.by_agent,
                    )

                log.info(
                    f"[DAILY SIM] {market.get('title', '')[:50]}: "
                    f"consensus={consensus} ({bullish}B/{bearish}Br)"
                )

            except Exception as e:
                log.warning(
                    f"[DAILY SIM] Simulation failed for {market.get('id', '')}: {e}"
                )
                results.append(DailySimResult(
                    market_id=market.get("id", ""),
                    market_title=market.get("title", ""),
                    topic=topic,
                    simulation_id="",
                    status="failed",
                    ran_at=now.isoformat(),
                ))

        return results

    async def _discover_candidates(self) -> List[Dict[str, Any]]:
        """
        Find markets expiring in 1-7 days that are good candidates
        for daily simulation.

        Prioritizes:
          - Markets with moderate prices (20-80% — not decided yet)
          - Higher volume markets
          - Interesting categories (crypto, politics, economics)
        """
        if not self._market_data:
            return []

        try:
            # Search for active markets across major categories
            all_markets = []
            for query in ["crypto bitcoin", "economy inflation", "politics election", "technology AI"]:
                try:
                    markets = await self._market_data.search_markets(query=query, limit=10)
                    all_markets.extend(markets)
                except Exception:
                    continue

            now = datetime.now(timezone.utc)
            candidates = []

            for m in all_markets:
                if not m.end_date:
                    continue

                end = m.end_date
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)

                hours_remaining = (end - now).total_seconds() / 3600

                if hours_remaining < _MIN_MARKET_HORIZON_HOURS:
                    continue
                if hours_remaining > _MAX_MARKET_HORIZON_HOURS:
                    continue

                yes_price = m.prices.get("Yes", 0.5)
                # Skip markets that are basically decided
                if yes_price > 0.90 or yes_price < 0.10:
                    continue

                candidates.append({
                    "id": m.id,
                    "title": m.title,
                    "exchange": m.exchange,
                    "yes_price": yes_price,
                    "volume": m.volume,
                    "end_date": end.isoformat(),
                    "hours_remaining": hours_remaining,
                    "category": m.category,
                })

            # Sort by volume (descending) — higher volume = more interesting
            candidates.sort(key=lambda c: -c.get("volume", 0))

            # Deduplicate by market ID
            seen = set()
            unique = []
            for c in candidates:
                if c["id"] not in seen:
                    seen.add(c["id"])
                    unique.append(c)

            return unique

        except Exception as e:
            log.warning(f"[DAILY SIM] Market discovery failed: {e}")
            return []

    def _build_sim_topic(self, market: Dict[str, Any]) -> str:
        """Build a simulation topic from a market."""
        title = market.get("title", "Unknown market")
        yes_price = market.get("yes_price", 0.5)
        hours = market.get("hours_remaining", 0)
        days = hours / 24

        return (
            f"{title} — Currently trading at {yes_price:.0%} Yes with "
            f"{days:.1f} days remaining. What's the likely outcome?"
        )

    def get_today_results(self) -> List[Dict[str, Any]]:
        """Get today's simulation results (from state)."""
        if self._state.already_ran_today():
            return self._state.simulations_today
        return []

    def get_stats(self) -> Dict[str, Any]:
        """Get daily simulation statistics."""
        return {
            "total_runs": self._state.total_runs,
            "last_run_date": self._state.last_run_date,
            "simulations_today": len(self._state.simulations_today),
            "sim_hour_utc": self._sim_hour,
            "max_sims_per_day": self._max_sims,
        }
