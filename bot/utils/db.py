from __future__ import annotations

import json
import aiosqlite
from pathlib import Path
from datetime import datetime

from bot.config.settings import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    exchange TEXT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    pnl REAL DEFAULT 0,
    strategy TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    leader_wallet TEXT DEFAULT '',
    paper INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    market_title TEXT DEFAULT '',
    exchange TEXT NOT NULL,
    strategy TEXT NOT NULL,
    agent_id TEXT DEFAULT '',
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    target_price REAL,
    reasoning TEXT,
    leader_wallet TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    balance REAL NOT NULL,
    total_exposure REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    num_positions INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_platform TEXT NOT NULL DEFAULT 'text',
    raw_content TEXT NOT NULL DEFAULT '',
    extracted_text TEXT NOT NULL DEFAULT '',
    research_summary TEXT NOT NULL DEFAULT '',
    trading_thesis TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    signals_generated INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    elapsed_seconds REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS copy_trade_cursors (
    leader_wallet TEXT PRIMARY KEY,
    last_timestamp INTEGER NOT NULL DEFAULT 0,
    last_tx_hash TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS copy_trade_seen (
    trade_key TEXT PRIMARY KEY,
    leader_wallet TEXT NOT NULL,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    mirrored INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_pnl_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = str(db_path or DB_PATH)

    async def init(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            # Add market_title column to existing signals tables (migration)
            try:
                await db.execute("ALTER TABLE signals ADD COLUMN market_title TEXT DEFAULT ''")
                await db.commit()
            except Exception:
                pass  # Column already exists
            try:
                await db.execute("ALTER TABLE signals ADD COLUMN agent_id TEXT DEFAULT ''")
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE trades ADD COLUMN agent_id TEXT DEFAULT ''")
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE agent_inputs ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''")
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE signals ADD COLUMN leader_wallet TEXT DEFAULT ''")
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE trades ADD COLUMN leader_wallet TEXT DEFAULT ''")
                await db.commit()
            except Exception:
                pass
            await db.commit()

    async def log_trade(
        self,
        trade_id: str,
        market_id: str,
        exchange: str,
        side: str,
        outcome: str,
        price: float,
        size: float,
        pnl: float = 0.0,
        strategy: str = "",
        agent_id: str = "",
        paper: bool = True,
        leader_wallet: str = "",
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO trades (id, market_id, exchange, side, outcome, price, size, pnl, strategy, agent_id, paper, leader_wallet, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, market_id, exchange, side, outcome, price, size, pnl, strategy, agent_id, int(paper), leader_wallet, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def log_signal(
        self,
        market_id: str,
        exchange: str,
        strategy: str,
        action: str,
        confidence: float,
        target_price: float | None = None,
        reasoning: str = "",
        market_title: str = "",
        agent_id: str = "",
        leader_wallet: str = "",
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO signals (market_id, market_title, exchange, strategy, agent_id, action, confidence, target_price, reasoning, leader_wallet, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (market_id, market_title, exchange, strategy, agent_id, action, confidence, target_price, reasoning, leader_wallet, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def snapshot_portfolio(
        self,
        balance: float,
        total_exposure: float,
        unrealized_pnl: float,
        realized_pnl: float,
        num_positions: int,
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO portfolio_snapshots (balance, total_exposure, unrealized_pnl, realized_pnl, num_positions, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (balance, total_exposure, unrealized_pnl, realized_pnl, num_positions, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_trades(self, limit: int = 100, exchange: str = "", agent_id: str = "") -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM trades"
            params: list = []
            where: list[str] = []
            if exchange:
                where.append("exchange LIKE ?")
                params.append(f"%{exchange}%")
            if agent_id:
                where.append("agent_id = ?")
                params.append(agent_id)
            if where:
                query += " WHERE " + " AND ".join(where)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_signals(self, limit: int = 50, agent_id: str = "") -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if agent_id:
                cursor = await db.execute(
                    "SELECT * FROM signals WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                    (agent_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def log_agent_input(
        self,
        source_platform: str,
        raw_content: str,
        extracted_text: str = "",
        research_summary: str = "",
        trading_thesis: str = "",
        agent_id: str = "",
        signals_generated: int = 0,
        success: bool = False,
        error: str = "",
        elapsed_seconds: float = 0.0,
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO agent_inputs
                   (source_platform, raw_content, extracted_text, research_summary,
                    trading_thesis, agent_id, signals_generated, success, error, elapsed_seconds, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_platform, raw_content, extracted_text, research_summary,
                    trading_thesis, agent_id, signals_generated, int(success), error,
                    elapsed_seconds, datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def get_agent_inputs(self, limit: int = 20, agent_id: str = "") -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if agent_id:
                cursor = await db.execute(
                    "SELECT * FROM agent_inputs WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                    (agent_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM agent_inputs ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_portfolio_history(self, limit: int = 200) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_copy_cursor(self, leader_wallet: str) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM copy_trade_cursors WHERE leader_wallet = ?",
                (leader_wallet.lower(),),
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"leader_wallet": leader_wallet.lower(), "last_timestamp": 0, "last_tx_hash": ""}

    async def update_copy_cursor(
        self,
        leader_wallet: str,
        *,
        last_timestamp: int,
        last_tx_hash: str = "",
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO copy_trade_cursors (leader_wallet, last_timestamp, last_tx_hash)
                   VALUES (?, ?, ?)
                   ON CONFLICT(leader_wallet) DO UPDATE SET
                     last_timestamp = MAX(copy_trade_cursors.last_timestamp, excluded.last_timestamp),
                     last_tx_hash = excluded.last_tx_hash""",
                (leader_wallet.lower(), last_timestamp, last_tx_hash),
            )
            await db.commit()

    async def is_copy_trade_seen(self, trade_key: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM copy_trade_seen WHERE trade_key = ?",
                (trade_key,),
            )
            return await cursor.fetchone() is not None

    async def mark_copy_trade_seen(
        self,
        *,
        trade_key: str,
        leader_wallet: str,
        market_id: str,
        side: str,
        price: float,
        size: float,
        mirrored: bool,
        skip_reason: str = "",
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO copy_trade_seen
                   (trade_key, leader_wallet, market_id, side, price, size, mirrored, skip_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade_key,
                    leader_wallet.lower(),
                    market_id,
                    side,
                    price,
                    size,
                    int(mirrored),
                    skip_reason,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def get_copy_leaders_stats(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    leader_wallet,
                    COUNT(*) AS trades_seen,
                    SUM(mirrored) AS trades_mirrored,
                    MAX(created_at) AS last_seen_at
                FROM copy_trade_seen
                GROUP BY leader_wallet
                ORDER BY last_seen_at DESC
                """
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_copy_trades(self, limit: int = 100, leader_wallet: str = "") -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if leader_wallet:
                cursor = await db.execute(
                    """SELECT * FROM trades
                       WHERE agent_id = 'polymarket_copy' AND leader_wallet = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (leader_wallet.lower(), limit),
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM trades
                       WHERE agent_id = 'polymarket_copy'
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_copy_stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            seen_cursor = await db.execute(
                "SELECT COUNT(*) AS total, SUM(mirrored) AS mirrored FROM copy_trade_seen"
            )
            seen = dict(await seen_cursor.fetchone())
            trade_cursor = await db.execute(
                "SELECT COUNT(*) AS total FROM trades WHERE agent_id = 'polymarket_copy'"
            )
            trades = dict(await trade_cursor.fetchone())
            skip_cursor = await db.execute(
                """SELECT skip_reason, COUNT(*) AS count
                   FROM copy_trade_seen WHERE mirrored = 0 AND skip_reason != ''
                   GROUP BY skip_reason ORDER BY count DESC LIMIT 10"""
            )
            skip_rows = await skip_cursor.fetchall()
            return {
                "leader_trades_seen": seen.get("total") or 0,
                "leader_trades_mirrored": seen.get("mirrored") or 0,
                "copy_executions": trades.get("total") or 0,
                "skip_reasons": [dict(r) for r in skip_rows],
            }

    async def get_portfolio_snapshot_latest(self) -> dict | None:
        rows = await self.get_portfolio_history(limit=1)
        return rows[0] if rows else None

    async def get_portfolio_snapshot_near(self, target: datetime) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            target_iso = target.isoformat()
            cursor = await db.execute(
                """
                SELECT * FROM portfolio_snapshots
                WHERE created_at <= ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (target_iso,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_trade_stats_since(self, since_iso: str) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) AS trade_count,
                    COALESCE(SUM(price * size), 0) AS volume_usd,
                    COALESCE(SUM(pnl), 0) AS realized_pnl
                FROM trades
                WHERE created_at >= ?
                """,
                (since_iso,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else {"trade_count": 0, "volume_usd": 0, "realized_pnl": 0}

    async def get_signal_count_since(self, since_iso: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE created_at >= ?",
                (since_iso,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def get_trade_stats_by_agent_since(self, since_iso: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    COALESCE(NULLIF(agent_id, ''), 'core_engine') AS agent_id,
                    COUNT(*) AS trades,
                    COALESCE(SUM(price * size), 0) AS volume_usd,
                    COALESCE(SUM(pnl), 0) AS realized_pnl
                FROM trades
                WHERE created_at >= ?
                GROUP BY agent_id
                ORDER BY trades DESC
                """,
                (since_iso,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def save_daily_pnl_report(self, report_date: str, payload: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO daily_pnl_reports (report_date, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(report_date) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (report_date, payload, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_daily_pnl_reports(self, limit: int = 30) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT report_date, payload, created_at FROM daily_pnl_reports ORDER BY report_date DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item["payload"] = json.loads(item["payload"])
                except Exception:
                    pass
                result.append(item)
            return result
