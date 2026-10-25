"""
Resolution Checker — polls for resolved markets and scores simulation predictions.

Runs periodically within the engine's trading loop to check if any markets
that had simulation predictions have resolved. When they have, it records
the outcome and updates archetype accuracy stats.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from bot.config import settings
from bot.utils.logger import log


# How often to check for resolutions (seconds)
_CHECK_INTERVAL = 600  # 10 minutes


class ResolutionChecker:
    """
    Periodically checks unresolved simulation predictions against
    market outcomes and updates accuracy stats.

    Usage:
        checker = ResolutionChecker(exchange_client)
        # Call periodically from the engine loop:
        await checker.check_resolutions()
    """

    def __init__(self, exchange_client=None):
        self._exchange = exchange_client
        self._last_check: float = 0.0
        self._tracker = None

    def _ensure_tracker(self):
        if self._tracker is None:
            from .accuracy_tracker import AccuracyTracker
            self._tracker = AccuracyTracker()

    async def check_resolutions(self) -> int:
        """
        Check all unresolved predictions and score any that have resolved.

        Returns the number of newly resolved predictions.
        """
        now = time.time()
        if now - self._last_check < _CHECK_INTERVAL:
            return 0
        self._last_check = now

        if not settings.SWARM_ENABLED or not self._exchange:
            return 0

        try:
            self._ensure_tracker()
            await self._tracker.init()

            unresolved = await self._tracker.get_unresolved_predictions()
            if not unresolved:
                return 0

            resolved_count = 0
            # Deduplicate by market_id
            checked_markets: set[str] = set()

            for pred in unresolved:
                market_id = pred.get("market_id", "")
                if not market_id or market_id in checked_markets:
                    continue
                checked_markets.add(market_id)

                # Check if market has resolved
                outcome = await self._get_market_outcome(market_id)
                if outcome is None:
                    continue  # Not yet resolved

                results = await self._tracker.check_resolution(
                    market_id=market_id,
                    actual_outcome=outcome["result"],
                    market_title=outcome.get("title", ""),
                )
                resolved_count += len(results)

            if resolved_count > 0:
                log.info(
                    f"[RESOLUTION] Checked {len(checked_markets)} markets, "
                    f"resolved {resolved_count} predictions"
                )
            return resolved_count

        except Exception as e:
            log.warning(f"[RESOLUTION] Check failed: {e}")
            return 0

    async def _get_market_outcome(self, market_id: str) -> Optional[Dict[str, str]]:
        """
        Check if a market has resolved and return the outcome.

        Returns:
            {"result": "yes"|"no", "title": "..."} or None if not resolved
        """
        try:
            # Try Kalshi-style market fetch
            data = await self._exchange._request(
                "GET", f"/markets/{market_id}",
            )
            market = data.get("market", data)
            status = market.get("status", "").lower()

            if status in ("settled", "resolved", "closed"):
                result = market.get("result", "")
                if result:
                    return {
                        "result": result.lower(),
                        "title": market.get("title", market_id),
                    }
                # Some markets use yes_price = 1.0 or 0.0 at resolution
                settlement_price = market.get("yes_price", market.get("settlement_value"))
                if settlement_price is not None:
                    if float(settlement_price) >= 0.95:
                        return {"result": "yes", "title": market.get("title", market_id)}
                    elif float(settlement_price) <= 0.05:
                        return {"result": "no", "title": market.get("title", market_id)}

        except Exception:
            pass  # Market not found or API error — skip silently

        return None
