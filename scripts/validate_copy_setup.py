#!/usr/bin/env python3
"""Smoke-test Polymarket Data API and copy trading config."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings
from bot.data.polymarket_data import PolymarketDataClient


async def main():
    print("=== Polymarket Copy Trading Validation ===\n")
    print(f"Copy agent enabled: {settings.AGENT_ENABLE_POLYMARKET_COPY}")
    print(f"Leaders configured: {len(settings.POLY_COPY_LEADERS)}")
    print(f"Sizing: {settings.POLY_COPY_SIZING_MODE} (${settings.POLY_COPY_FIXED_USD})")
    print(f"Poll interval: {settings.POLY_COPY_POLL_INTERVAL}s")
    print(f"Max lag: {settings.POLY_COPY_MAX_LAG_SECONDS}s\n")

    client = PolymarketDataClient()
    try:
        wallets = await client.discover_active_wallets(limit=5)
        print(f"Data API OK — {len(wallets)} active wallets discovered")
        for w in wallets[:3]:
            print(f"  {w['wallet'][:12]}… ({w['recent_trades']} trades)")

        leaders = settings.POLY_COPY_LEADERS or [w["wallet"] for w in wallets[:1]]
        for leader in leaders[:2]:
            trades = await client.get_wallet_trades(leader, limit=5)
            print(f"\nLeader {leader[:12]}… — {len(trades)} recent trade(s)")
            for t in trades[-3:]:
                print(
                    f"  {t.timestamp.isoformat()} {t.side} {t.outcome} "
                    f"@ {t.price:.3f} x {t.size:.1f} — {t.market_title[:50]}"
                )
    finally:
        await client.close()

    print("\nNext: add leader wallets to POLY_COPY_LEADERS in .env, then run:")
    print("  python3 -m bot.main --exchange polymarket --mode paper --api")


if __name__ == "__main__":
    asyncio.run(main())
