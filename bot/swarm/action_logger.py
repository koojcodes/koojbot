"""
Structured action logging for swarm simulations.

Logs agent actions as JSONL (one JSON object per line) during simulation.
Used by the OASIS runner script and read by the SimulationRunner for
result collection.

Adapted from MiroFish backend/scripts/action_logger.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class ActionLogger:
    """
    Logs simulation agent actions to a JSONL file.

    Each line is a complete JSON object with:
    - round, timestamp, platform, agent_id, agent_name
    - action_type, action_args, result, success
    - entry_type: "action", "round_start", "round_end", "sim_start", "sim_end"
    """

    def __init__(self, sim_dir: str, platform: str = "twitter"):
        self.sim_dir = Path(sim_dir)
        self.platform = platform
        self.log_dir = self.sim_dir / platform
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "actions.jsonl"

    def _write(self, entry: Dict[str, Any]) -> None:
        """Append a single JSON line to the log file."""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def log_action(
        self,
        round_num: int,
        agent_id: int,
        agent_name: str,
        action_type: str,
        action_args: Optional[Dict[str, Any]] = None,
        result: str = "",
        success: bool = True,
    ) -> None:
        """Log a single agent action."""
        self._write({
            "entry_type": "action",
            "round": round_num,
            "timestamp": datetime.utcnow().isoformat(),
            "platform": self.platform,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "action_type": action_type,
            "action_args": action_args or {},
            "result": result,
            "success": success,
        })

    def log_round_start(self, round_num: int, active_agents: int = 0) -> None:
        """Log the start of a simulation round."""
        self._write({
            "entry_type": "round_start",
            "round": round_num,
            "timestamp": datetime.utcnow().isoformat(),
            "active_agents": active_agents,
        })

    def log_round_end(self, round_num: int, action_count: int = 0) -> None:
        """Log the end of a simulation round."""
        self._write({
            "entry_type": "round_end",
            "round": round_num,
            "timestamp": datetime.utcnow().isoformat(),
            "action_count": action_count,
        })

    def log_simulation_start(self, config: Dict[str, Any]) -> None:
        """Log simulation start with config summary."""
        self._write({
            "entry_type": "sim_start",
            "timestamp": datetime.utcnow().isoformat(),
            "topic": config.get("topic", ""),
            "max_rounds": config.get("max_rounds", 0),
            "num_agents": len(config.get("agent_configs", [])),
        })

    def log_simulation_end(self, total_actions: int = 0, status: str = "completed") -> None:
        """Log simulation completion."""
        self._write({
            "entry_type": "sim_end",
            "timestamp": datetime.utcnow().isoformat(),
            "total_actions": total_actions,
            "status": status,
        })


def read_actions(sim_dir: str, platform: str = "twitter") -> List[Dict[str, Any]]:
    """Read all actions from a simulation's JSONL log."""
    log_path = Path(sim_dir) / platform / "actions.jsonl"
    if not log_path.exists():
        return []

    actions = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    actions.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return actions


def get_agent_actions(sim_dir: str, platform: str = "twitter") -> List[Dict[str, Any]]:
    """Read only agent action entries (not round markers)."""
    return [a for a in read_actions(sim_dir, platform) if a.get("entry_type") == "action"]


def get_action_summary(sim_dir: str, platform: str = "twitter") -> Dict[str, Any]:
    """Get a summary of actions by type and agent."""
    actions = get_agent_actions(sim_dir, platform)

    by_type: Dict[str, int] = {}
    by_agent: Dict[str, int] = {}
    posts: List[str] = []

    for action in actions:
        atype = action.get("action_type", "UNKNOWN")
        by_type[atype] = by_type.get(atype, 0) + 1

        name = action.get("agent_name", "Unknown")
        by_agent[name] = by_agent.get(name, 0) + 1

        # Collect post content for analysis
        if atype == "CREATE_POST":
            content = action.get("action_args", {}).get("content", "")
            if content:
                posts.append(f"[{name}]: {content}")

    return {
        "total_actions": len(actions),
        "by_type": by_type,
        "by_agent": by_agent,
        "posts": posts,
        "num_rounds": max((a.get("round", 0) for a in actions), default=0) + 1,
    }
