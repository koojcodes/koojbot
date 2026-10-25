"""
Post-Resolution Accuracy Tracker — compares simulation predictions to actual market outcomes.

Tracks:
  - Per-simulation prediction records (what each archetype predicted)
  - Market resolution outcomes (did YES or NO win?)
  - Per-archetype accuracy stats over time
  - Overall simulation accuracy metrics

The tracker writes to SQLite tables (same DB as the rest of Max).
The weight adjuster (Phase 6.4) reads from these stats.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

from bot.config.settings import DB_PATH
from bot.utils.logger import log


_ACCURACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS simulation_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    market_id TEXT DEFAULT '',
    archetype TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    reasoning TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_title TEXT DEFAULT '',
    predicted_direction TEXT NOT NULL,
    actual_outcome TEXT DEFAULT '',
    was_correct INTEGER DEFAULT -1,
    resolved_at TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archetype_accuracy (
    archetype TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    accuracy REAL DEFAULT 0.0,
    avg_confidence REAL DEFAULT 0.5,
    last_updated TEXT NOT NULL
);
"""


@dataclass
class PredictionRecord:
    """A single archetype's prediction from a simulation."""
    simulation_id: str
    topic: str
    market_id: str
    archetype: str
    agent_name: str
    predicted_direction: str  # "bullish" or "bearish"
    confidence: float
    reasoning: str


@dataclass
class OutcomeRecord:
    """The actual outcome of a prediction."""
    simulation_id: str
    market_id: str
    market_title: str
    predicted_direction: str
    actual_outcome: str  # "yes" or "no"
    was_correct: bool


@dataclass
class ArchetypeStats:
    """Accuracy stats for a single archetype."""
    archetype: str
    total_predictions: int
    correct_predictions: int
    accuracy: float
    avg_confidence: float


class AccuracyTracker:
    """
    Tracks simulation prediction accuracy against market outcomes.

    Usage:
        tracker = AccuracyTracker()
        await tracker.init()

        # After a simulation completes, extract and record predictions
        await tracker.record_predictions(simulation_id, topic, posts, markets)

        # When a market resolves, check predictions against outcome
        resolved = await tracker.check_resolution(market_id, actual_outcome)

        # Get per-archetype stats
        stats = await tracker.get_archetype_stats()
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or DB_PATH)

    async def init(self):
        """Create accuracy tracking tables."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_ACCURACY_SCHEMA)
            await db.commit()

    async def record_predictions(
        self,
        simulation_id: str,
        topic: str,
        posts: List[str],
        markets: List[Dict[str, Any]],
        by_agent: Dict[str, int] | None = None,
    ) -> int:
        """
        Extract predictions from simulation posts and record them.

        Analyzes each post for bullish/bearish sentiment and maps it
        to the relevant markets from the simulation context.

        Returns the number of predictions recorded.
        """
        predictions = self._extract_predictions(
            simulation_id, topic, posts, markets, by_agent,
        )
        if not predictions:
            return 0

        async with aiosqlite.connect(self.db_path) as db:
            for p in predictions:
                await db.execute(
                    """INSERT INTO simulation_predictions
                       (simulation_id, topic, market_id, archetype, agent_name,
                        predicted_direction, confidence, reasoning, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        p.simulation_id, p.topic, p.market_id,
                        p.archetype, p.agent_name, p.predicted_direction,
                        p.confidence, p.reasoning,
                        datetime.utcnow().isoformat(),
                    ),
                )
            await db.commit()

        log.info(
            f"[ACCURACY] Recorded {len(predictions)} predictions from "
            f"simulation {simulation_id[:8]}"
        )
        return len(predictions)

    async def check_resolution(
        self,
        market_id: str,
        actual_outcome: str,
        market_title: str = "",
    ) -> List[OutcomeRecord]:
        """
        Check all predictions for a market against its actual outcome.

        Args:
            market_id: The resolved market ID
            actual_outcome: "yes" or "no"
            market_title: Human-readable market title

        Returns:
            List of OutcomeRecords created
        """
        outcomes: List[OutcomeRecord] = []
        actual_lower = actual_outcome.lower()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM simulation_predictions
                   WHERE market_id = ?
                   AND simulation_id NOT IN (
                       SELECT simulation_id FROM prediction_outcomes WHERE market_id = ?
                   )""",
                (market_id, market_id),
            )
            predictions = await cursor.fetchall()

            for pred in predictions:
                direction = pred["predicted_direction"].lower()
                # "bullish" maps to "yes" outcome, "bearish" maps to "no"
                predicted_outcome = "yes" if direction == "bullish" else "no"
                was_correct = predicted_outcome == actual_lower

                outcome = OutcomeRecord(
                    simulation_id=pred["simulation_id"],
                    market_id=market_id,
                    market_title=market_title,
                    predicted_direction=direction,
                    actual_outcome=actual_lower,
                    was_correct=was_correct,
                )
                outcomes.append(outcome)

                await db.execute(
                    """INSERT INTO prediction_outcomes
                       (simulation_id, market_id, market_title, predicted_direction,
                        actual_outcome, was_correct, resolved_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pred["simulation_id"], market_id, market_title,
                        direction, actual_lower, int(was_correct),
                        datetime.utcnow().isoformat(),
                        datetime.utcnow().isoformat(),
                    ),
                )

                # Update archetype accuracy
                await self._update_archetype_accuracy(
                    db, pred["archetype"], was_correct, pred["confidence"],
                )

            await db.commit()

        if outcomes:
            correct = sum(1 for o in outcomes if o.was_correct)
            log.info(
                f"[ACCURACY] Resolved {market_id}: {len(outcomes)} predictions, "
                f"{correct}/{len(outcomes)} correct"
            )
        return outcomes

    async def get_archetype_stats(self) -> List[ArchetypeStats]:
        """Get accuracy stats for all archetypes."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM archetype_accuracy ORDER BY accuracy DESC"
            )
            rows = await cursor.fetchall()
            return [
                ArchetypeStats(
                    archetype=r["archetype"],
                    total_predictions=r["total_predictions"],
                    correct_predictions=r["correct_predictions"],
                    accuracy=r["accuracy"],
                    avg_confidence=r["avg_confidence"],
                )
                for r in rows
            ]

    async def get_archetype_accuracy_map(self) -> Dict[str, float]:
        """Get a simple archetype -> accuracy mapping for weight adjustment."""
        stats = await self.get_archetype_stats()
        return {s.archetype: s.accuracy for s in stats if s.total_predictions >= 3}

    async def get_prediction_history(
        self, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get recent prediction outcomes for display."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT po.*, sp.archetype, sp.agent_name, sp.topic, sp.confidence
                   FROM prediction_outcomes po
                   JOIN simulation_predictions sp
                     ON po.simulation_id = sp.simulation_id
                     AND po.market_id = sp.market_id
                     AND po.predicted_direction = sp.predicted_direction
                   ORDER BY po.resolved_at DESC
                   LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_simulation_accuracy(self, simulation_id: str) -> Dict[str, Any]:
        """Get accuracy breakdown for a specific simulation."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                "SELECT COUNT(*) as total FROM prediction_outcomes WHERE simulation_id = ?",
                (simulation_id,),
            )
            total_row = await cursor.fetchone()
            total = total_row["total"] if total_row else 0

            cursor = await db.execute(
                "SELECT COUNT(*) as correct FROM prediction_outcomes WHERE simulation_id = ? AND was_correct = 1",
                (simulation_id,),
            )
            correct_row = await cursor.fetchone()
            correct = correct_row["correct"] if correct_row else 0

            return {
                "simulation_id": simulation_id,
                "total_predictions": total,
                "correct_predictions": correct,
                "accuracy": correct / max(total, 1),
                "resolved": total > 0,
            }

    async def get_unresolved_predictions(self) -> List[Dict[str, Any]]:
        """Get predictions that haven't been resolved yet (for polling)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT DISTINCT sp.market_id, sp.topic, sp.simulation_id
                   FROM simulation_predictions sp
                   WHERE sp.market_id != ''
                   AND sp.simulation_id NOT IN (
                       SELECT DISTINCT simulation_id FROM prediction_outcomes
                       WHERE market_id = sp.market_id
                   )
                   ORDER BY sp.created_at DESC
                   LIMIT 50"""
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Internal helpers ──────────────────────────────────────────────

    def _extract_predictions(
        self,
        simulation_id: str,
        topic: str,
        posts: List[str],
        markets: List[Dict[str, Any]],
        by_agent: Dict[str, int] | None,
    ) -> List[PredictionRecord]:
        """
        Analyze simulation posts to extract per-archetype predictions.

        Each post is scored for bullish/bearish sentiment. The agent name
        is extracted from the post prefix pattern "AgentName: post text".
        """
        from .trader_profiles import get_all_profiles

        predictions: List[PredictionRecord] = []
        profiles = {p.name: p for p in get_all_profiles()}

        # Map primary market (first in list, if available)
        primary_market_id = markets[0]["id"] if markets else ""

        for post in posts:
            # Extract agent name from "Name: post text" pattern
            agent_name, _, text = post.partition(":")
            agent_name = agent_name.strip()
            text = text.strip()

            if not text or agent_name not in profiles:
                continue

            profile = profiles[agent_name]

            # Score sentiment
            bullish_words = [
                "bullish", "buy", "long", "moon", "pump", "rally",
                "accumulate", "breakout", "upside", "higher",
            ]
            bearish_words = [
                "bearish", "sell", "short", "dump", "crash", "fear",
                "decline", "downside", "lower", "capitulat",
            ]
            text_lower = text.lower()
            bull_hits = sum(1 for w in bullish_words if w in text_lower)
            bear_hits = sum(1 for w in bearish_words if w in text_lower)

            if bull_hits == 0 and bear_hits == 0:
                continue  # Neutral — no prediction

            direction = "bullish" if bull_hits > bear_hits else "bearish"
            strength = abs(bull_hits - bear_hits)
            confidence = min(0.95, 0.50 + strength * 0.10)

            predictions.append(PredictionRecord(
                simulation_id=simulation_id,
                topic=topic,
                market_id=primary_market_id,
                archetype=profile.archetype,
                agent_name=agent_name,
                predicted_direction=direction,
                confidence=confidence,
                reasoning=text[:200],
            ))

        return predictions

    async def _update_archetype_accuracy(
        self,
        db: aiosqlite.Connection,
        archetype: str,
        was_correct: bool,
        confidence: float,
    ) -> None:
        """Update the running accuracy for an archetype."""
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM archetype_accuracy WHERE archetype = ?",
            (archetype,),
        )
        row = await cursor.fetchone()

        if row:
            total = row["total_predictions"] + 1
            correct = row["correct_predictions"] + (1 if was_correct else 0)
            # Running average for confidence
            avg_conf = (
                (row["avg_confidence"] * row["total_predictions"] + confidence) / total
            )
            await db.execute(
                """UPDATE archetype_accuracy
                   SET total_predictions = ?, correct_predictions = ?,
                       accuracy = ?, avg_confidence = ?, last_updated = ?
                   WHERE archetype = ?""",
                (
                    total, correct, correct / total, avg_conf,
                    datetime.utcnow().isoformat(), archetype,
                ),
            )
        else:
            await db.execute(
                """INSERT INTO archetype_accuracy
                   (archetype, total_predictions, correct_predictions,
                    accuracy, avg_confidence, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    archetype, 1, 1 if was_correct else 0,
                    1.0 if was_correct else 0.0, confidence,
                    datetime.utcnow().isoformat(),
                ),
            )
