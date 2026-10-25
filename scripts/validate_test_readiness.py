#!/usr/bin/env python3
"""Smoke-test the paper-mode API surface for milestone-1 readiness."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings

API_URL = f"http://{settings.API_HOST if settings.API_HOST != '0.0.0.0' else 'localhost'}:{settings.API_PORT}"

CORE_ENDPOINTS = [
    "/api/health",
    "/api/portfolio",
    "/api/portfolio/history?limit=5",
    "/api/agents/registry",
    "/api/agents/quality-gates",
    "/api/orchestration/status",
    "/api/markets/watchlist",
    "/api/copy/stats",
]

OPTIONAL_ENDPOINTS = [
    "/api/graph/stats",
    "/api/swarm/simulations",
    "/api/accuracy/archetypes",
    "/api/weights",
    "/api/daily-simulations",
]


async def check_endpoint(client: httpx.AsyncClient, path: str) -> tuple[bool, str]:
    try:
        response = await client.get(f"{API_URL}{path}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            return False, str(payload["error"])
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


async def main() -> int:
    print(f"Milestone-1 readiness check against {API_URL}\n")

    async with httpx.AsyncClient(timeout=10.0) as client:
        print("Core endpoints")
        core_ok = True
        for path in CORE_ENDPOINTS:
            ok, detail = await check_endpoint(client, path)
            core_ok = core_ok and ok
            print(f"  {'PASS' if ok else 'FAIL'} {path} -> {detail}")

        print("\nOptional swarm/graph endpoints")
        for path in OPTIONAL_ENDPOINTS:
            ok, detail = await check_endpoint(client, path)
            label = "PASS" if ok else "SKIP"
            print(f"  {label} {path} -> {detail}")

    if not core_ok:
        print("\nResult: core test-readiness checks failed.")
        return 1

    print("\nResult: core test-readiness checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
