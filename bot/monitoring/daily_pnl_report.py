"""Daily P&L snapshot reporter for MaxTheTrader."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.config import settings
from bot.utils.db import Database
from bot.utils.logger import log


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _snapshot_total_value(row: dict) -> float:
    balance = float(row.get("balance") or 0)
    exposure = float(row.get("total_exposure") or 0)
    unrealized = float(row.get("unrealized_pnl") or 0)
    return balance + exposure + unrealized


@dataclass
class DailyPnLReport:
    report_date: str
    generated_at: str
    paper_mode: bool
    portfolio: dict[str, Any]
    activity_24h: dict[str, Any]
    by_agent: list[dict[str, Any]]
    copy_trading: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "generated_at": self.generated_at,
            "paper_mode": self.paper_mode,
            "portfolio": self.portfolio,
            "activity_24h": self.activity_24h,
            "by_agent": self.by_agent,
            "copy_trading": self.copy_trading,
            "notes": self.notes,
        }

    def format_text(self) -> str:
        p = self.portfolio
        a = self.activity_24h
        mode = "PAPER" if self.paper_mode else "LIVE"
        change = float(p.get("daily_change") or 0)
        sign = "+" if change >= 0 else ""

        lines = [
            f"MaxTheTrader Daily Report ({mode})",
            f"Date: {self.report_date}",
            "",
            "Portfolio",
            f"  Value:      ${float(p.get('current_value', 0)):,.2f}",
            f"  24h change: {sign}${change:,.2f} ({float(p.get('daily_change_pct', 0)):+.2f}%)",
            f"  Balance:    ${float(p.get('balance', 0)):,.2f}",
            f"  Exposure:   ${float(p.get('total_exposure', 0)):,.2f}",
            f"  Unrealized: ${float(p.get('unrealized_pnl', 0)):+,.2f}",
            f"  Positions:  {int(p.get('num_positions', 0))}",
            "",
            "Last 24h activity",
            f"  Trades:   {int(a.get('trades', 0))}",
            f"  Signals:  {int(a.get('signals', 0))}",
            f"  Volume:   ${float(a.get('volume_usd', 0)):,.2f}",
            f"  Realized: ${float(a.get('realized_pnl', 0)):+,.2f}",
        ]

        if self.by_agent:
            lines.append("")
            lines.append("By agent")
            for row in self.by_agent:
                agent = row.get("agent_id") or "unknown"
                lines.append(
                    f"  {agent}: {int(row.get('trades', 0))} trades, "
                    f"${float(row.get('volume_usd', 0)):,.0f} vol, "
                    f"${float(row.get('realized_pnl', 0)):+,.2f} realized"
                )

        ct = self.copy_trading
        if ct.get("leaders_configured") or ct.get("copy_executions"):
            lines.append("")
            lines.append("Copy trading")
            lines.append(f"  Leaders:  {int(ct.get('leaders_configured', 0))}")
            lines.append(f"  Seen:     {int(ct.get('leader_trades_seen', 0))}")
            lines.append(f"  Mirrored: {int(ct.get('leader_trades_mirrored', 0))}")
            lines.append(f"  Executed: {int(ct.get('copy_executions', 0))}")

        if self.notes:
            lines.append("")
            lines.append("Notes")
            for note in self.notes:
                lines.append(f"  - {note}")

        return "\n".join(lines)


class DailyPnLReporter:
    """Build daily profit/loss snapshots from SQLite history."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    async def generate(self, *, as_of: datetime | None = None) -> DailyPnLReport:
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        await self.db.init()
        report_date = now.date().isoformat()
        since = now - timedelta(hours=24)

        notes: list[str] = []
        current_snap = await self.db.get_portfolio_snapshot_latest()
        prior_snap = await self.db.get_portfolio_snapshot_near(since)

        if not current_snap:
            notes.append("No portfolio snapshots in database — is the bot running?")
            current_value = settings.INITIAL_BALANCE
            portfolio = {
                "current_value": current_value,
                "value_24h_ago": current_value,
                "daily_change": 0.0,
                "daily_change_pct": 0.0,
                "balance": current_value,
                "total_exposure": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "num_positions": 0,
                "snapshot_at": None,
            }
        else:
            current_value = _snapshot_total_value(current_snap)
            prior_value = _snapshot_total_value(prior_snap) if prior_snap else current_value
            daily_change = current_value - prior_value
            daily_change_pct = (daily_change / prior_value * 100) if prior_value else 0.0
            portfolio = {
                "current_value": round(current_value, 2),
                "value_24h_ago": round(prior_value, 2),
                "daily_change": round(daily_change, 2),
                "daily_change_pct": round(daily_change_pct, 2),
                "balance": round(float(current_snap.get("balance") or 0), 2),
                "total_exposure": round(float(current_snap.get("total_exposure") or 0), 2),
                "unrealized_pnl": round(float(current_snap.get("unrealized_pnl") or 0), 2),
                "realized_pnl": round(float(current_snap.get("realized_pnl") or 0), 2),
                "num_positions": int(current_snap.get("num_positions") or 0),
                "snapshot_at": current_snap.get("created_at"),
            }
            if not prior_snap:
                notes.append("No snapshot from 24h ago — daily change uses current value only")

        trade_stats = await self.db.get_trade_stats_since(since.isoformat())
        signal_count = await self.db.get_signal_count_since(since.isoformat())

        activity_24h = {
            "trades": trade_stats.get("trade_count", 0),
            "signals": signal_count,
            "volume_usd": round(float(trade_stats.get("volume_usd") or 0), 2),
            "realized_pnl": round(float(trade_stats.get("realized_pnl") or 0), 2),
        }

        by_agent = await self.db.get_trade_stats_by_agent_since(since.isoformat())
        copy_stats = await self.db.get_copy_stats()
        copy_stats["leaders_configured"] = len(settings.POLY_COPY_LEADERS)

        report = DailyPnLReport(
            report_date=report_date,
            generated_at=now.isoformat(),
            paper_mode=settings.PAPER_MODE,
            portfolio=portfolio,
            activity_24h=activity_24h,
            by_agent=by_agent,
            copy_trading=copy_stats,
            notes=notes,
        )

        await self.db.save_daily_pnl_report(report_date, json.dumps(report.to_dict()))
        return report

    async def deliver_telegram(self, report: DailyPnLReport) -> bool:
        token = settings.TELEGRAM_BOT_TOKEN
        user_ids = settings.TELEGRAM_ALLOWED_USER_IDS
        if not token or not user_ids or not settings.DAILY_REPORT_TELEGRAM:
            return False

        import httpx

        text = report.format_text()
        sent = False
        async with httpx.AsyncClient(timeout=30.0) as client:
            for user_id in user_ids:
                try:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": user_id, "text": text},
                    )
                    if resp.status_code == 200:
                        sent = True
                    else:
                        log.warning(f"Telegram report failed for {user_id}: {resp.text[:200]}")
                except Exception as e:
                    log.warning(f"Telegram report error for {user_id}: {e}")
        return sent
