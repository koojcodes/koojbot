#!/usr/bin/env python3
"""Discover active Polymarket wallets from recent trades."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.data.polymarket_data import PolymarketDataClient


async def main():
    client = PolymarketDataClient()
    try:
        wallets = await client.discover_active_wallets(limit=15)
        if not wallets:
            print("No wallets found")
            return
        print("Active Polymarket wallets (add to POLY_COPY_LEADERS):")
        for row in wallets:
            print(f"  {row['wallet']}  ({row['recent_trades']} recent trades, {row['name']})")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
