#!/usr/bin/env python3
"""Generate and persist a daily P&L snapshot report."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings
from bot.monitoring.daily_pnl_report import DailyPnLReporter
from bot.utils.logger import log


async def main():
    parser = argparse.ArgumentParser(description="MaxTheTrader daily P&L report")
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Skip Telegram delivery even if configured",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of plain text",
    )
    args = parser.parse_args()

    reporter = DailyPnLReporter()
    report = await reporter.generate()

    output_dir = Path(settings.DAILY_REPORT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"daily_report_{report.report_date}.json"
    text_path = output_dir / f"daily_report_{report.report_date}.txt"
    json_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    text_path.write_text(report.format_text(), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format_text())
        print(f"\nSaved: {text_path}")

    if not args.no_telegram and settings.DAILY_REPORT_TELEGRAM:
        sent = await reporter.deliver_telegram(report)
        if sent:
            log.info("Daily report sent via Telegram")
        else:
            log.info("Telegram delivery skipped or failed")


if __name__ == "__main__":
    asyncio.run(main())
