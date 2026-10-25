from __future__ import annotations

import time
import hashlib
import hmac
from datetime import datetime
from typing import Any

import httpx

from bot.config import settings
from bot.exchanges.base import (
    Exchange, Market, OrderBook, OrderBookLevel, Order, Position,
    Side, OrderStatus,
)


class KalshiExchange(Exchange):
    """Kalshi API integration. Defaults to demo environment for paper trading."""

    name = "kalshi"

    def __init__(self, api_key: str = "", private_key_path: str = "", env: str = ""):
        self.api_key = api_key or settings.KALSHI_API_KEY
        self.private_key_path = private_key_path or settings.KALSHI_PRIVATE_KEY_PATH
        self.env = env or settings.KALSHI_ENV
        self.base_url = settings.KALSHI_BASE_URL
        self._client = httpx.AsyncClient(timeout=30.0)
        self._private_key = None

    def _load_private_key(self):
        """Load RSA private key for request signing."""
        if self._private_key is not None:
            return
        if not self.private_key_path:
            return
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            with open(self.private_key_path, "rb") as f:
                self._private_key = load_pem_private_key(f.read(), password=None)
        except Exception as e:
            raise RuntimeError(f"Failed to load Kalshi private key: {e}")

    def _sign_request(self, method: str, url: str) -> dict[str, str]:
        """Generate auth headers for Kalshi API request using RSA-PSS (matches official SDK)."""
        import base64
        from urllib.parse import urlparse
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes

        timestamp = str(int(time.time() * 1000))
        headers = {"KALSHI-ACCESS-KEY": self.api_key, "KALSHI-ACCESS-TIMESTAMP": timestamp}

        if self._private_key is not None:
            # Extract path from full URL (strip query params) — matches SDK behavior
            parsed = urlparse(url)
            path = parsed.path

            message = f"{timestamp}{method.upper()}{path}".encode("utf-8")
            signature = self._private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
            headers["KALSHI-ACCESS-SIGNATURE"] = base64.b64encode(signature).decode("utf-8")

        return headers

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        self._load_private_key()
        url = f"{self.base_url}{path}"
        headers = self._sign_request(method, url)
        resp = await self._client.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def get_markets(self, query: str = "", limit: int = 50, category: str = "") -> list[Market]:
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["title"] = query
        if category:
            params["series_ticker"] = category

        data = await self._request("GET", "/markets", params=params)
        markets = []
        for m in data.get("markets", []):
            markets.append(Market(
                id=m["ticker"],
                title=m.get("title", ""),
                description=m.get("subtitle", ""),
                exchange="kalshi",
                outcomes=["Yes", "No"],
                prices={
                    "Yes": m.get("yes_ask", 0) / 100,
                    "No": m.get("no_ask", 0) / 100,
                },
                volume=float(m.get("volume", 0)),
                end_date=datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) if m.get("close_time") else None,
                category=m.get("series_ticker", ""),
                extra=m,
            ))
        return markets

    async def get_market(self, market_id: str) -> Market:
        data = await self._request("GET", f"/markets/{market_id}")
        m = data.get("market", data)
        return Market(
            id=m["ticker"],
            title=m.get("title", ""),
            description=m.get("subtitle", ""),
            exchange="kalshi",
            outcomes=["Yes", "No"],
            prices={
                "Yes": m.get("yes_ask", 0) / 100,
                "No": m.get("no_ask", 0) / 100,
            },
            volume=float(m.get("volume", 0)),
            end_date=datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) if m.get("close_time") else None,
            category=m.get("series_ticker", ""),
            extra=m,
        )

    async def get_orderbook(self, market_id: str) -> OrderBook:
        data = await self._request("GET", f"/markets/{market_id}/orderbook")
        ob = data.get("orderbook", data)
        bids = [OrderBookLevel(price=l[0] / 100, size=l[1]) for l in ob.get("yes", [])]
        asks = [OrderBookLevel(price=l[0] / 100, size=l[1]) for l in ob.get("no", [])]
        return OrderBook(market_id=market_id, bids=bids, asks=asks)

    async def get_price(self, market_id: str, outcome: str = "Yes") -> float:
        market = await self.get_market(market_id)
        return market.prices.get(outcome, 0.0)

    async def place_order(
        self,
        market_id: str,
        side: Side,
        size: float,
        price: float,
        outcome: str = "Yes",
    ) -> Order:
        payload = {
            "ticker": market_id,
            "action": "buy" if side == Side.BUY else "sell",
            "side": "yes" if outcome == "Yes" else "no",
            "type": "limit",
            "count": int(size),
            "yes_price": int(price * 100) if outcome == "Yes" else None,
            "no_price": int(price * 100) if outcome == "No" else None,
        }
        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}

        data = await self._request("POST", "/portfolio/orders", json=payload)
        o = data.get("order", data)
        return Order(
            id=o.get("order_id", ""),
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.PENDING,
            outcome=outcome,
            exchange="kalshi",
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._request("DELETE", f"/portfolio/orders/{order_id}")
            return True
        except httpx.HTTPStatusError:
            return False

    async def get_positions(self) -> list[Position]:
        data = await self._request("GET", "/portfolio/positions")
        positions = []
        for p in data.get("market_positions", []):
            if p.get("position", 0) == 0:
                continue
            pos = p.get("position", 0)
            side = "Yes" if pos > 0 else "No"
            positions.append(Position(
                market_id=p["ticker"],
                market_title=p.get("ticker", ""),
                outcome=side,
                size=abs(pos),
                avg_entry_price=p.get("average_price", 0) / 100,
                exchange="kalshi",
            ))
        return positions

    async def get_balance(self) -> float:
        data = await self._request("GET", "/portfolio/balance")
        return data.get("balance", 0) / 100  # cents -> dollars

    async def get_open_orders(self) -> list[Order]:
        data = await self._request("GET", "/portfolio/orders", params={"status": "resting"})
        orders = []
        for o in data.get("orders", []):
            orders.append(Order(
                id=o["order_id"],
                market_id=o["ticker"],
                side=Side.BUY if o.get("action") == "buy" else Side.SELL,
                price=o.get("yes_price", o.get("no_price", 0)) / 100,
                size=o.get("remaining_count", 0),
                status=OrderStatus.PENDING,
                outcome="Yes" if o.get("side") == "yes" else "No",
                exchange="kalshi",
            ))
        return orders

    async def close(self):
        await self._client.aclose()
