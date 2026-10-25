"""
Simulation subprocess runner.

Launches the OASIS simulation script as an async subprocess,
monitors its progress, and collects results when complete.

Adapted from MiroFish backend/app/services/simulation_runner.py.
Threading replaced with asyncio subprocess management.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.utils.logger import log

from .action_logger import read_actions, get_action_summary, get_agent_actions
from .simulation_ipc import IPCClient


class RunnerStatus(str, Enum):
    """Status of a simulation subprocess."""
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class RunState:
    """Current state of a running simulation."""
    simulation_id: str
    status: str = RunnerStatus.PENDING.value
    pid: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    current_round: int = 0
    max_rounds: int = 0
    total_actions: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, sim_dir: str) -> None:
        path = Path(sim_dir) / "run_state.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, sim_dir: str) -> Optional["RunState"]:
        path = Path(sim_dir) / "run_state.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None


# Path to the OASIS runner script
_SCRIPT_DIR = Path(__file__).parent / "scripts"


class SimulationRunner:
    """
    Launches and monitors OASIS simulation subprocesses.

    Each simulation runs in its own directory under
    {project_root}/simulation_data/{simulation_id}/
    """

    # Base directory for simulation data
    DATA_DIR = Path(__file__).resolve().parent.parent.parent / "simulation_data"

    def __init__(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._processes: Dict[str, asyncio.subprocess.Process] = {}

    def get_sim_dir(self, simulation_id: str) -> Path:
        """Get the data directory for a simulation."""
        sim_dir = self.DATA_DIR / simulation_id
        sim_dir.mkdir(parents=True, exist_ok=True)
        return sim_dir

    async def start_simulation(
        self,
        simulation_id: str,
        config_path: str,
        timeout: int = 300,
    ) -> RunState:
        """
        Launch the OASIS simulation as an async subprocess.

        Args:
            simulation_id: Unique simulation identifier
            config_path: Path to the simulation_config.json
            timeout: Max seconds before killing the process

        Returns:
            RunState with initial status
        """
        sim_dir = self.get_sim_dir(simulation_id)
        script_path = _SCRIPT_DIR / "run_simulation.py"

        if not script_path.exists():
            return RunState(
                simulation_id=simulation_id,
                status=RunnerStatus.FAILED.value,
                error=f"Simulation script not found: {script_path}",
            )

        state = RunState(
            simulation_id=simulation_id,
            status=RunnerStatus.STARTING.value,
            start_time=time.time(),
        )
        state.save(str(sim_dir))

        log.info(f"[SWARM RUNNER] Starting simulation {simulation_id}")

        try:
            # Launch subprocess
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script_path),
                "--config", config_path,
                "--sim-dir", str(sim_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent.parent.parent),
            )

            self._processes[simulation_id] = process

            state.pid = process.pid
            state.status = RunnerStatus.RUNNING.value
            state.save(str(sim_dir))

            log.info(f"[SWARM RUNNER] Subprocess launched (PID={process.pid})")

            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout,
                )

                # Save stdout/stderr
                (sim_dir / "stdout.log").write_bytes(stdout or b"")
                (sim_dir / "stderr.log").write_bytes(stderr or b"")

                if process.returncode == 0:
                    state.status = RunnerStatus.COMPLETED.value
                    # Read action summary
                    summary = get_action_summary(str(sim_dir))
                    state.total_actions = summary["total_actions"]
                    state.current_round = summary["num_rounds"]
                    log.info(
                        f"[SWARM RUNNER] Simulation {simulation_id} completed: "
                        f"{state.total_actions} actions over {state.current_round} rounds"
                    )
                else:
                    state.status = RunnerStatus.FAILED.value
                    state.error = (stderr or b"").decode("utf-8", errors="replace")[:500]
                    log.error(
                        f"[SWARM RUNNER] Simulation {simulation_id} failed "
                        f"(exit code {process.returncode})"
                    )

            except asyncio.TimeoutError:
                process.kill()
                state.status = RunnerStatus.FAILED.value
                state.error = f"Timeout after {timeout}s"
                log.warning(f"[SWARM RUNNER] Simulation {simulation_id} timed out")

            state.end_time = time.time()
            state.save(str(sim_dir))

        except Exception as e:
            state.status = RunnerStatus.FAILED.value
            state.error = str(e)
            state.save(str(sim_dir))
            log.error(f"[SWARM RUNNER] Failed to start simulation: {e}")

        finally:
            self._processes.pop(simulation_id, None)

        return state

    async def stop_simulation(self, simulation_id: str) -> bool:
        """Stop a running simulation."""
        process = self._processes.get(simulation_id)
        if process:
            process.kill()
            self._processes.pop(simulation_id, None)

            sim_dir = self.get_sim_dir(simulation_id)
            state = RunState.load(str(sim_dir))
            if state:
                state.status = RunnerStatus.STOPPED.value
                state.end_time = time.time()
                state.save(str(sim_dir))

            log.info(f"[SWARM RUNNER] Stopped simulation {simulation_id}")
            return True
        return False

    def get_status(self, simulation_id: str) -> Optional[RunState]:
        """Get the current status of a simulation."""
        sim_dir = self.get_sim_dir(simulation_id)
        return RunState.load(str(sim_dir))

    def get_actions(
        self, simulation_id: str, platform: str = "twitter",
    ) -> List[Dict[str, Any]]:
        """Get all agent actions from a completed simulation."""
        sim_dir = self.get_sim_dir(simulation_id)
        return get_agent_actions(str(sim_dir), platform)

    def get_summary(
        self, simulation_id: str, platform: str = "twitter",
    ) -> Dict[str, Any]:
        """Get action summary for a simulation."""
        sim_dir = self.get_sim_dir(simulation_id)
        return get_action_summary(str(sim_dir), platform)

    def list_simulations(self) -> List[Dict[str, Any]]:
        """List all simulations with their status."""
        results = []
        if not self.DATA_DIR.exists():
            return results

        for sim_dir in sorted(self.DATA_DIR.iterdir()):
            if sim_dir.is_dir() and sim_dir.name.startswith("sim_"):
                state = RunState.load(str(sim_dir))
                if state:
                    results.append(state.to_dict())
        return results
