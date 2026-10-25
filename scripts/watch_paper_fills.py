#!/usr/bin/env python3
"""Poll /api/trades until a new paper fill appears (or timeout)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import settings

API = f"http://{'localhost' if settings.API_HOST == '0.0.0.0' else settings.API_HOST}:{settings.API_PORT}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait")
    parser.add_argument("--poll", type=float, default=15.0, help="Poll interval seconds")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    print(f"Watching paper fills on {API}/api/trades (timeout={args.timeout}s)")

    async with httpx.AsyncClient(timeout=10.0) as client:
        baseline_ids: set[str] = set()
        try:
            r = await client.get(f"{API}/api/trades", params={"limit": 50})
            r.raise_for_status()
            baseline_ids = {str(t.get("id")) for t in r.json()}
            print(f"Baseline trades: {len(baseline_ids)}")
        except Exception as e:
            print(f"WARN: could not load baseline trades: {e}")

        deadline = asyncio.get_event_loop().time() + args.timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await client.get(f"{API}/api/trades", params={"limit": 20})
                r.raise_for_status()
                trades = r.json()
            except Exception as e:
                print(f"poll error: {e}")
                await asyncio.sleep(args.poll)
                continue

            fresh = [
                t for t in trades
                if str(t.get("id")) not in baseline_ids and int(t.get("paper") or 0) == 1
            ]
            # Also accept recent created_at after watcher start
            if not fresh:
                for t in trades:
                    created = t.get("created_at") or ""
                    if int(t.get("paper") or 0) != 1:
                        continue
                    try:
                        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts >= started and str(t.get("id")) not in baseline_ids:
                            fresh.append(t)
                    except Exception:
                        pass

            if fresh:
                print("\nNEW PAPER FILL(S):")
                for t in fresh[:5]:
                    print(
                        f"  {t.get('created_at')} {t.get('side')} {t.get('outcome')} "
                        f"{t.get('market_id')} @ {t.get('price')} "
                        f"size={t.get('size')} strategy={t.get('strategy')} "
                        f"agent={t.get('agent_id')}"
                    )
                return 0

            elapsed = int((datetime.now(timezone.utc) - started).total_seconds())
            print(f"[{elapsed:>4}s] no new paper fills yet…")
            await asyncio.sleep(args.poll)

    print("Timed out waiting for paper fills.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
