"""Market data fetcher — caches price history and provides auto-discovery for watchlist."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field

from bot.exchanges.base import Exchange, Market, OrderBook
from bot.utils.logger import log


@dataclass
class MarketSnapshot:
    market: Market
    orderbook: OrderBook | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    price_history: list[tuple[datetime, float]] = field(default_factory=list)
    auto_discovered: bool = False  # True if added via auto-discovery


# Queries used for auto-discovering tradeable markets
_AUTO_DISCOVER_QUERIES = [
    "bitcoin",
    "btc",
    "crypto",
    "federal reserve",
    "inflation",
    "interest rate",
]

_MIN_AUTO_VOLUME = 5_000
_AUTO_DISCOVER_MAX_MARKETS = 10  # max markets to add per discovery run


class MarketDataFetcher:
    """Fetches and caches market data from exchanges."""

    def __init__(self, exchanges: list[Exchange]):
        self.exchanges = exchanges
        self._cache: dict[str, MarketSnapshot] = {}       # market_id -> snapshot
        self._watchlist: set[str] = set()                 # manually added market IDs
        self._auto_watchlist: set[str] = set()            # auto-discovered market IDs
        self._series_contracts: dict[str, str] = {}       # series_ticker → active market ID

    @property
    def full_watchlist(self) -> set[str]:
        return self._watchlist | self._auto_watchlist

    def add_to_watchlist(self, market_id: str):
        self._watchlist.add(market_id)

    def remove_from_watchlist(self, market_id: str):
        self._watchlist.discard(market_id)
        self._auto_watchlist.discard(market_id)

    async def search_markets(self, query: str, limit: int = 20) -> list[Market]:
        """Search all exchanges for markets matching query."""
        results = []
        for exchange in self.exchanges:
            try:
                markets = await exchange.get_markets(query=query, limit=limit)
                results.extend(markets)
            except Exception as e:
                log.warning(f"Failed to search {exchange.name}: {e}")
        return results

    async def seed_series_market(self, series_ticker: str, exchange_client) -> str | None:
        """
        Find the currently-open contract for a series (e.g. KXBTC15M) and add it to
        the watchlist. Rotates out the previous contract when a new one opens.

        Returns the active market ID, or None if not found.
        """
        try:
            # Try open status first, then fall back to any status
            for status_filter in [{"status": "open"}, {}]:
                data = await exchange_client._request(
                    "GET", "/markets",
                    params={"series_ticker": series_ticker, "limit": 2, **status_filter},
                )
                markets = data.get("markets", [])
                # Pick the one with the soonest close time that hasn't closed yet
                now = datetime.now(timezone.utc)
                for m in markets:
                    close_raw = m.get("close_time", "")
                    if not close_raw:
                        continue
                    try:
                        close_dt = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
                        if close_dt > now:
                            market_id = m["ticker"]
                            break
                    except Exception:
                        continue
                else:
                    continue
                break
            else:
                return None

            # Rotate: remove the old contract, add the new one
            old_id = self._series_contracts.get(series_ticker)
            if old_id and old_id != market_id:
                self._watchlist.discard(old_id)
                self._auto_watchlist.discard(old_id)
                self._cache.pop(old_id, None)
                log.info(
                    f"[bold cyan]SERIES ROTATE[/] {series_ticker}: "
                    f"{old_id} → {market_id}"
                )

            self._watchlist.add(market_id)
            self._series_contracts[series_ticker] = market_id

            if old_id != market_id:
                log.info(
                    f"[bold cyan]SERIES SEEDED[/] {series_ticker} → {market_id}"
                )

            return market_id

        except Exception as e:
            log.warning(f"Could not seed series {series_ticker}: {e}")
            return None

    def get_series_market_id(self, series_ticker: str) -> str | None:
        """Return the currently-seeded active contract ID for a series."""
        return self._series_contracts.get(series_ticker)

    async def auto_discover_markets(
        self,
        approved_markets=None,
        on_market_proposed=None,
    ) -> list[str]:
        """
        Search for high-volume tradeable markets and manage the watchlist.

        New behavior vs old:
          - Post-filter: discards results whose title doesn't contain any word
            from the search query (fixes Kalshi returning unrelated markets).
          - Approved markets (from ApprovedMarketsStore) are added to the
            watchlist automatically — no re-prompting.
          - Declined markets are silently skipped.
          - New/unknown markets are passed to on_market_proposed(market) instead
            of being auto-added. The Telegram bot handles the approval flow.

        Args:
            approved_markets: ApprovedMarketsStore instance (optional).
            on_market_proposed: async callable(Market) — called for each new
                market that passes filters but hasn't been approved/declined yet.

        Returns list of market IDs added to the watchlist this run.
        """
        log.info("[bold]Auto-discovering markets...[/]")
        candidates: dict[str, Market] = {}

        for query in _AUTO_DISCOVER_QUERIES:
            # Build a set of query words for title relevance check (lowercase)
            query_words = {w.lower() for w in query.split() if len(w) > 2}

            for exchange in self.exchanges:
                try:
                    markets = await exchange.get_markets(query=query, limit=15)
                    for m in markets:
                        # ── Post-filter: title must contain at least one query word ──
                        title_lower = m.title.lower()
                        if not any(word in title_lower for word in query_words):
                            continue

                        if m.volume < _MIN_AUTO_VOLUME:
                            continue
                        if m.id in self.full_watchlist:
                            continue
                        if m.end_date is not None:
                            end = m.end_date
                            if end.tzinfo is None:
                                end = end.replace(tzinfo=timezone.utc)
                            hours_left = (end - datetime.now(timezone.utc)).total_seconds() / 3600
                            if hours_left < 2:
                                continue
                        candidates[m.id] = m
                except Exception as e:
                    log.warning(f"Auto-discover search failed ({query}): {e}")

        # Sort by volume desc, take top N
        sorted_candidates = sorted(candidates.values(), key=lambda m: m.volume, reverse=True)
        newly_added = []

        for market in sorted_candidates[:_AUTO_DISCOVER_MAX_MARKETS]:
            if market.id in self.full_watchlist:
                continue

            # ── Already approved by user → add directly ──────────────────────
            if approved_markets and approved_markets.is_approved(market.id):
                self._auto_watchlist.add(market.id)
                newly_added.append(market.id)
                log.info(
                    f"[dim]Watchlist restored: [bold]{market.title[:60]}[/] "
                    f"(vol={market.volume:,.0f})[/]"
                )
                continue

            # ── Explicitly declined by user → skip silently ───────────────────
            if approved_markets and approved_markets.is_declined(market.id):
                continue

            # ── Unknown market → propose to user via Telegram ─────────────────
            if on_market_proposed:
                try:
                    await on_market_proposed(market)
                    log.info(
                        f"[dim]Proposed to user: [bold]{market.title[:60]}[/] "
                        f"(vol={market.volume:,.0f})[/]"
                    )
                except Exception as e:
                    log.warning(f"Could not propose market {market.id}: {e}")
            else:
                # No Telegram callback — fall back to auto-adding (original behavior)
                self._auto_watchlist.add(market.id)
                newly_added.append(market.id)
                log.info(
                    f"[dim]Auto-discovered: [bold]{market.title[:60]}[/] "
                    f"(vol={market.volume:,.0f}, exchange={market.exchange})[/]"
                )

        log.info(f"[bold]Auto-discovery complete: added {len(newly_added)} markets[/]")
        return newly_added

    async def fetch_market(self, market_id: str, exchange: Exchange) -> MarketSnapshot | None:
        """Fetch full market data including orderbook."""
        try:
            market = await exchange.get_market(market_id)
            orderbook = None
            try:
                orderbook = await exchange.get_orderbook(market_id)
            except Exception:
                pass

            snapshot = MarketSnapshot(market=market, orderbook=orderbook)

            # Carry over price history from cache
            existing = self._cache.get(market_id)
            if existing:
                snapshot.price_history = existing.price_history.copy()
                snapshot.auto_discovered = existing.auto_discovered

            # Mark if auto-discovered
            if market_id in self._auto_watchlist:
                snapshot.auto_discovered = True

            # Append current price to history
            yes_price = market.prices.get("Yes", 0.0)
            if yes_price > 0:
                snapshot.price_history.append((datetime.utcnow(), yes_price))
                if len(snapshot.price_history) > 1000:
                    snapshot.price_history = snapshot.price_history[-1000:]

            self._cache[market_id] = snapshot
            return snapshot
        except Exception as e:
            log.error(f"Failed to fetch market {market_id} from {exchange.name}: {e}")
            return None

    async def refresh_watchlist(self) -> dict[str, MarketSnapshot]:
        """Refresh all watchlist markets. Returns updated snapshots."""
        tasks = []
        for market_id in self.full_watchlist:
            for exchange in self.exchanges:
                tasks.append(self.fetch_market(market_id, exchange))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        updated = {}
        for result in results:
            if isinstance(result, MarketSnapshot):
                updated[result.market.id] = result
        return updated

    def get_snapshot(self, market_id: str) -> MarketSnapshot | None:
        return self._cache.get(market_id)

    def get_all_snapshots(self) -> dict[str, MarketSnapshot]:
        return dict(self._cache)

    def get_price_momentum(self, market_id: str, lookback: int = 10) -> float | None:
        """Calculate simple price momentum (current - lookback periods ago)."""
        snapshot = self._cache.get(market_id)
        if not snapshot or len(snapshot.price_history) < lookback + 1:
            return None
        current = snapshot.price_history[-1][1]
        past = snapshot.price_history[-(lookback + 1)][1]
        return current - past

    def select_polymarket_candidates(
        self,
        snapshots: dict[str, MarketSnapshot] | None = None,
        *,
        min_volume: float = 10_000,
        max_spread: float = 0.08,
        min_minutes_to_close: int = 60,
    ) -> list[MarketSnapshot]:
        """
        Return Polymarket snapshots that pass basic execution sanity filters.
        """
        pool = snapshots or self._cache
        now = datetime.now(timezone.utc)
        candidates: list[MarketSnapshot] = []
        for snapshot in pool.values():
            market = snapshot.market
            if market.exchange != "polymarket":
                continue
            if market.volume < min_volume:
                continue
            if market.end_date is not None:
                end = market.end_date if market.end_date.tzinfo else market.end_date.replace(tzinfo=timezone.utc)
                minutes_left = (end - now).total_seconds() / 60
                if minutes_left < min_minutes_to_close:
                    continue
            if snapshot.orderbook:
                spread = snapshot.orderbook.spread
                if spread is not None and spread > max_spread:
                    continue
            candidates.append(snapshot)
        return candidates
