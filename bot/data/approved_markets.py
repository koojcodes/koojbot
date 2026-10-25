"""
Approved Markets Store — persistent record of which markets the user
has approved for monitoring (or explicitly declined).

Three categories:
  - ALWAYS_APPROVED_SERIES: pre-approved series (e.g. KXBTC15M) that
    are seeded automatically on every startup — no user confirmation needed.
  - approved:  markets the user said ✅ to via Telegram inline button.
  - declined:  market IDs the user said ❌ to — won't be proposed again.

Storage: {project_root}/approved_markets.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

from bot.utils.logger import log


# ── Pre-approved series ────────────────────────────────────────────────────────
# These series are always seeded on startup without prompting the user.
# Add new series here as the bot expands into new markets.

ALWAYS_APPROVED_SERIES: dict[str, str] = {
    "KXBTC15M": "BTC 15-Minute Price Direction",
}


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ApprovedMarket:
    market_id: str
    series_ticker: str   # e.g. "KXBTC15M" or "" for one-off markets
    title: str
    exchange: str
    volume: float
    approved_at: str     # ISO 8601 timestamp
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovedMarket":
        return cls(
            market_id=d["market_id"],
            series_ticker=d.get("series_ticker", ""),
            title=d.get("title", ""),
            exchange=d.get("exchange", "kalshi"),
            volume=float(d.get("volume", 0)),
            approved_at=d.get("approved_at", ""),
            notes=d.get("notes", ""),
        )

    def format_summary(self) -> str:
        vol_str = f"${self.volume:,.0f}" if self.volume else "n/a"
        series_str = f" [{self.series_ticker}]" if self.series_ticker else ""
        return f"{self.title[:55]}{series_str} — vol {vol_str}"


# ── Store ──────────────────────────────────────────────────────────────────────

class ApprovedMarketsStore:
    """
    Persistent watchlist manager.

    Usage:
        store = ApprovedMarketsStore(Path("approved_markets.json"))

        # Check before trading
        if store.is_approved(market_id):
            ...

        # After user taps ✅ in Telegram
        store.approve(ApprovedMarket(market_id=..., title=..., ...))

        # After user taps ❌ in Telegram
        store.decline(market_id)
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._approved: dict[str, ApprovedMarket] = {}
        self._declined: set[str] = set()
        self._load()

    # ── Read ────────────────────────────────────────────────────────────────

    @property
    def always_approved_series(self) -> dict[str, str]:
        """Series tickers that are pre-approved — seeded automatically."""
        return ALWAYS_APPROVED_SERIES

    def is_approved(self, market_id: str) -> bool:
        return market_id in self._approved

    def is_declined(self, market_id: str) -> bool:
        return market_id in self._declined

    def is_known(self, market_id: str) -> bool:
        """True if the user has already made a decision on this market."""
        return self.is_approved(market_id) or self.is_declined(market_id)

    def get_all_approved(self) -> list[ApprovedMarket]:
        return list(self._approved.values())

    def count(self) -> int:
        return len(self._approved)

    # ── Write ────────────────────────────────────────────────────────────────

    def approve(self, market: ApprovedMarket):
        """Approve a market for monitoring and persist to disk."""
        self._declined.discard(market.market_id)
        self._approved[market.market_id] = market
        self._save()
        log.info(f"[WATCHLIST] ✅ Approved: {market.market_id} — {market.title[:50]}")

    def decline(self, market_id: str):
        """Decline a market — won't be proposed again."""
        self._approved.pop(market_id, None)
        self._declined.add(market_id)
        self._save()
        log.info(f"[WATCHLIST] ❌ Declined: {market_id}")

    def remove(self, market_id: str) -> ApprovedMarket | None:
        """
        Remove from approved list.
        The market reverts to 'unknown' — it may be proposed again next discovery cycle.
        """
        removed = self._approved.pop(market_id, None)
        if removed:
            self._save()
            log.info(f"[WATCHLIST] Removed: {market_id}")
        return removed

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for d in data.get("approved", []):
                try:
                    m = ApprovedMarket.from_dict(d)
                    self._approved[m.market_id] = m
                except Exception:
                    pass
            self._declined = set(data.get("declined", []))
            if self._approved or self._declined:
                log.info(
                    f"[WATCHLIST] Loaded {len(self._approved)} approved, "
                    f"{len(self._declined)} declined"
                )
        except Exception as e:
            log.warning(f"[WATCHLIST] Could not load {self._path}: {e}")

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "approved": [m.to_dict() for m in self._approved.values()],
                "declined": sorted(self._declined),
            }
            self._path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"[WATCHLIST] Could not save to {self._path}: {e}")
