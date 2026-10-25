from collections import deque
import asyncio


class LogBroadcaster:
    def __init__(self, maxlen: int = 500):
        self._buffer: deque[str] = deque(maxlen=maxlen)
        self._connections: set = set()

    def record(self, line: str):
        """Called from the logging handler (sync context). Adds to buffer only."""
        self._buffer.append(line)

    def get_recent(self, n: int = 200) -> list[str]:
        lines = list(self._buffer)
        return lines[-n:]

    async def add_connection(self, ws):
        self._connections.add(ws)

    async def remove_connection(self, ws):
        self._connections.discard(ws)

    async def broadcast(self, line: str):
        self._buffer.append(line)
        dead = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(line)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)


# Module-level singleton
broadcaster = LogBroadcaster()
