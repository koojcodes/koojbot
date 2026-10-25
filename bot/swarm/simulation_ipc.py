"""
File-based IPC between the main bot process and OASIS subprocess.

Communication uses JSON files in a shared directory:
- Commands:  {sim_dir}/ipc_commands/{command_id}.json
- Responses: {sim_dir}/ipc_responses/{command_id}.json

The main process writes commands, the OASIS subprocess polls for them
and writes responses. This avoids the complexity of sockets/pipes
while being reliable across platforms.

Adapted from MiroFish backend/app/services/simulation_ipc.py.
"""
from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.utils.logger import log


class CommandType(str, Enum):
    """Types of IPC commands."""
    INTERVIEW = "interview"
    STOP = "stop"


class CommandStatus(str, Enum):
    """Status of an IPC command."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IPCClient:
    """
    Client side (main bot process).

    Sends commands to the OASIS subprocess and polls for responses.
    """

    def __init__(self, sim_dir: str):
        self.sim_dir = Path(sim_dir)
        self.cmd_dir = self.sim_dir / "ipc_commands"
        self.resp_dir = self.sim_dir / "ipc_responses"
        self.cmd_dir.mkdir(parents=True, exist_ok=True)
        self.resp_dir.mkdir(parents=True, exist_ok=True)

    def send_command(
        self,
        command_type: CommandType,
        payload: Dict[str, Any],
        timeout: float = 60.0,
    ) -> Optional[Dict[str, Any]]:
        """Send a command and wait for response."""
        cmd_id = uuid.uuid4().hex[:12]
        cmd_file = self.cmd_dir / f"{cmd_id}.json"
        resp_file = self.resp_dir / f"{cmd_id}.json"

        # Write command
        cmd_data = {
            "command_id": cmd_id,
            "type": command_type.value,
            "payload": payload,
            "status": CommandStatus.PENDING.value,
            "timestamp": time.time(),
        }
        cmd_file.write_text(json.dumps(cmd_data))

        # Poll for response
        start = time.time()
        while time.time() - start < timeout:
            if resp_file.exists():
                try:
                    resp = json.loads(resp_file.read_text())
                    # Cleanup
                    cmd_file.unlink(missing_ok=True)
                    resp_file.unlink(missing_ok=True)
                    return resp
                except json.JSONDecodeError:
                    pass
            time.sleep(0.5)

        log.warning(f"[IPC] Command {cmd_id} timed out after {timeout}s")
        cmd_file.unlink(missing_ok=True)
        return None

    def send_stop(self) -> None:
        """Tell the subprocess to stop."""
        self.send_command(CommandType.STOP, {}, timeout=10)

    def is_subprocess_alive(self) -> bool:
        """Check if the subprocess has signaled it's alive."""
        status_file = self.sim_dir / "env_status.json"
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text())
                return data.get("alive", False)
            except Exception:
                pass
        return False


class IPCServer:
    """
    Server side (OASIS subprocess).

    Polls for commands from the main process and writes responses.
    """

    def __init__(self, sim_dir: str):
        self.sim_dir = Path(sim_dir)
        self.cmd_dir = self.sim_dir / "ipc_commands"
        self.resp_dir = self.sim_dir / "ipc_responses"
        self.cmd_dir.mkdir(parents=True, exist_ok=True)
        self.resp_dir.mkdir(parents=True, exist_ok=True)
        self._running = False

    def start(self) -> None:
        """Signal that the subprocess environment is alive."""
        self._running = True
        status_file = self.sim_dir / "env_status.json"
        status_file.write_text(json.dumps({"alive": True, "started_at": time.time()}))

    def stop(self) -> None:
        """Signal that the subprocess is shutting down."""
        self._running = False
        status_file = self.sim_dir / "env_status.json"
        status_file.write_text(json.dumps({"alive": False, "stopped_at": time.time()}))

    def poll_commands(self) -> List[Dict[str, Any]]:
        """Check for pending commands. Returns list of command dicts."""
        commands = []
        if not self.cmd_dir.exists():
            return commands

        for cmd_file in sorted(self.cmd_dir.glob("*.json")):
            try:
                cmd = json.loads(cmd_file.read_text())
                if cmd.get("status") == CommandStatus.PENDING.value:
                    commands.append(cmd)
            except Exception:
                pass
        return commands

    def send_response(
        self,
        command_id: str,
        result: Dict[str, Any],
        status: CommandStatus = CommandStatus.COMPLETED,
    ) -> None:
        """Write a response for a command."""
        resp_file = self.resp_dir / f"{command_id}.json"
        resp_data = {
            "command_id": command_id,
            "status": status.value,
            "result": result,
            "timestamp": time.time(),
        }
        resp_file.write_text(json.dumps(resp_data))

        # Mark command as processed
        cmd_file = self.cmd_dir / f"{command_id}.json"
        cmd_file.unlink(missing_ok=True)
