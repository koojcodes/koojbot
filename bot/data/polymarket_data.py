"""Polymarket Data API client — public wallet trades and activity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from bot.config import settings

DATA_API_BASE = "https://data-api.polymarket.com"


@dataclass
class LeaderTrade:
    """Normalized leader fill from Polymarket Data API."""

    market_id: str
    market_title: str
    side: str  # BUY or SELL
    outcome: str
    price: float
    size: float
    usdc_size: float
    timestamp: datetime
    tx_hash: str
    leader_wallet: str
    slug: str = ""
    event_slug: str = ""

    @property
    def trade_key(self) -> str:
        """Unique key for deduplication."""
        if self.tx_hash:
            return self.tx_hash
        return f"{self.leader_wallet}:{self.market_id}:{self.timestamp.isoformat()}:{self.side}:{self.size}"


def _parse_timestamp(raw: Any) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _normalize_trade(item: dict[str, Any], leader_wallet: str) -> LeaderTrade | None:
    side = str(item.get("side", "")).upper()
    if side not in ("BUY", "SELL"):
        return None

    price = float(item.get("price") or 0)
    size = float(item.get("size") or 0)
    if price <= 0 or size <= 0:
        return None

    market_id = str(
        item.get("conditionId")
        or item.get("condition_id")
        or item.get("market")
        or ""
    )
    if not market_id:
        return None

    return LeaderTrade(
        market_id=market_id,
        market_title=str(item.get("title") or item.get("question") or market_id),
        side=side,
        outcome=str(item.get("outcome") or "Yes"),
        price=price,
        size=size,
        usdc_size=float(item.get("usdcSize") or item.get("usdc_size") or price * size),
        timestamp=_parse_timestamp(item.get("timestamp")),
        tx_hash=str(item.get("transactionHash") or item.get("transaction_hash") or ""),
        leader_wallet=leader_wallet.lower(),
        slug=str(item.get("slug") or ""),
        event_slug=str(item.get("eventSlug") or item.get("event_slug") or ""),
    )


class PolymarketDataClient:
    """Read-only client for Polymarket Data API."""

    def __init__(self, base_url: str = DATA_API_BASE):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    async def get_wallet_trades(
        self,
        proxy_address: str,
        *,
        since_ts: int | None = None,
        limit: int = 50,
        market: str | None = None,
    ) -> list[LeaderTrade]:
        params: dict[str, Any] = {
            "user": proxy_address.lower(),
            "limit": min(limit, 500),
        }
        if market:
            params["market"] = market
        if since_ts is not None:
            params["start"] = since_ts

        resp = await self._client.get(f"{self.base_url}/trades", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("data", [])
        trades: list[LeaderTrade] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            trade = _normalize_trade(item, proxy_address)
            if trade:
                trades.append(trade)
        trades.sort(key=lambda t: t.timestamp)
        return trades

    async def get_wallet_activity(
        self,
        proxy_address: str,
        *,
        types: list[str] | None = None,
        since_ts: int | None = None,
        limit: int = 50,
    ) -> list[LeaderTrade]:
        params: dict[str, Any] = {
            "user": proxy_address.lower(),
            "limit": min(limit, 500),
        }
        if types:
            params["type"] = ",".join(types)
        if since_ts is not None:
            params["start"] = since_ts

        resp = await self._client.get(f"{self.base_url}/activity", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("data", [])
        trades: list[LeaderTrade] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "TRADE")).upper() != "TRADE":
                continue
            trade = _normalize_trade(item, proxy_address)
            if trade:
                trades.append(trade)
        trades.sort(key=lambda t: t.timestamp)
        return trades

    async def get_wallet_value(self, proxy_address: str) -> float:
        """Portfolio value in USDC for a proxy wallet."""
        resp = await self._client.get(
            f"{self.base_url}/value",
            params={"user": proxy_address.lower()},
        )
        if resp.status_code != 200:
            return 0.0
        data = resp.json()
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0) or 0)
        if isinstance(data, dict):
            return float(data.get("value", 0) or 0)
        return 0.0

    async def discover_active_wallets(
        self,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Pull recent global trades and rank wallets by activity.
        Useful when POLY_COPY_LEADERS is empty.
        """
        resp = await self._client.get(
            f"{self.base_url}/trades",
            params={"limit": min(limit * 5, 100)},
        )
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("data", [])

        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            wallet = str(item.get("proxyWallet") or "").lower()
            if not wallet.startswith("0x"):
                continue
            counts[wallet] = counts.get(wallet, 0) + 1
            if wallet not in names:
                names[wallet] = str(item.get("name") or item.get("pseudonym") or wallet[:10])

        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [
            {"wallet": wallet, "name": names.get(wallet, wallet[:10]), "recent_trades": count}
            for wallet, count in ranked
        ]
