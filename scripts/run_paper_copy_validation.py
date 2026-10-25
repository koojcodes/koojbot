#!/usr/bin/env python3
"""
Paper copy trading validation runner.

Run the bot in paper mode for extended periods to validate leader profitability.
Usage:
  POLY_COPY_LEADERS=0x... python3 scripts/run_paper_copy_validation.py

Monitors /api/copy/stats every 60s and prints a summary. Stop with Ctrl+C.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings

API_URL = f"http://{settings.API_HOST if settings.API_HOST != '0.0.0.0' else 'localhost'}:{settings.API_PORT}"


async def fetch_stats(client: httpx.AsyncClient) -> dict:
    resp = await client.get(f"{API_URL}/api/copy/stats")
    resp.raise_for_status()
    return resp.json()


async def main():
    print("Paper copy validation monitor")
    print(f"API: {API_URL}")
    print(f"Leaders: {settings.POLY_COPY_LEADERS or '(none — set POLY_COPY_LEADERS)'}")
    print("Start the bot separately: python3 -m bot.main --exchange polymarket --mode paper --api\n")

    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                stats = await fetch_stats(client)
                print(
                    f"[{time.strftime('%H:%M:%S')}] "
                    f"seen={stats.get('leader_trades_seen', 0)} "
                    f"mirrored={stats.get('leader_trades_mirrored', 0)} "
                    f"executed={stats.get('copy_executions', 0)} "
                    f"leaders={stats.get('leaders_configured', 0)}"
                )
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] API error: {e}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
