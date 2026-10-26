from __future__ import annotations

import os


def _float(val: str | None, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val: str | None, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


NFL_POLL_INTERVAL: float = _float(os.getenv("NFL_POLL_INTERVAL"), 10.0)
NFL_MIN_DIVERGENCE: float = _float(os.getenv("NFL_MIN_DIVERGENCE"), 0.05)
NFL_VELOCITY_WINDOW: float = _float(os.getenv("NFL_VELOCITY_WINDOW"), 300.0)
NFL_VELOCITY_THRESHOLD: int = _int(os.getenv("NFL_VELOCITY_THRESHOLD"), 10)
NFL_MAX_POSITION_SIZE: float = _float(os.getenv("NFL_MAX_POSITION_SIZE"), 50.0)
NFL_GARBAGE_TIME_DIFF: int = _int(os.getenv("NFL_GARBAGE_TIME_DIFF"), 28)
NFL_MOMENTUM_DIVERGENCE: float = _float(os.getenv("NFL_MOMENTUM_DIVERGENCE"), 0.07)
