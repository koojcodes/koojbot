from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from bot.config import settings
from bot.exchanges.base import (
    Exchange, Market, OrderBook, OrderBookLevel, Order, Position,
    Side, OrderStatus,
)


class PolymarketExchange(Exchange):
    """Polymarket CLOB API integration."""

    name = "polymarket"

    def __init__(
        self,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
    ):
        self.private_key = private_key or settings.POLYMARKET_PRIVATE_KEY
        self.api_key = api_key or settings.POLYMARKET_API_KEY
        self.api_secret = api_secret or settings.POLYMARKET_API_SECRET
        self.api_passphrase = api_passphrase or settings.POLYMARKET_API_PASSPHRASE
        self.clob_url = settings.POLYMARKET_CLOB_URL
        self.gamma_url = settings.POLYMARKET_GAMMA_URL
        self._client = httpx.AsyncClient(timeout=30.0)
        self._clob_client = None  # Lazy init py-clob-client

    def _init_clob_client(self):
        """Initialize the py-clob-client for authenticated operations."""
        if self._clob_client is not None:
            return
        if not self.private_key:
            return
        try:
            from py_clob_client.client import ClobClient
            self._clob_client = ClobClient(
                self.clob_url,
                key=self.private_key,
                chain_id=137,  # Polygon mainnet
            )
            if self.api_key:
                self._clob_client.set_api_creds({
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "passphrase": self.api_passphrase,
                })
        except ImportError:
            pass  # py-clob-client not installed, read-only mode

    async def get_markets(self, query: str = "", limit: int = 50, category: str = "") -> list[Market]:
        params: dict[str, Any] = {"limit": limit, "active": True, "closed": False}
        if query:
            params["title"] = query
        if category:
            params["tag"] = category

        resp = await self._client.get(f"{self.gamma_url}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()

        markets = []
        for m in data if isinstance(data, list) else data.get("markets", []):
            outcomes = []
            prices = {}
            for token in m.get("tokens", []):
                outcome = token.get("outcome", "Unknown")
                outcomes.append(outcome)
                prices[outcome] = float(token.get("price", 0))

            if not outcomes:
                outcomes = ["Yes", "No"]

            markets.append(Market(
                id=m.get("condition_id", m.get("id", "")),
                title=m.get("question", m.get("title", "")),
                description=m.get("description", ""),
                exchange="polymarket",
                outcomes=outcomes,
                prices=prices,
                volume=float(m.get("volume", 0)),
                end_date=datetime.fromisoformat(m["end_date_iso"]) if m.get("end_date_iso") else None,
                category=m.get("category", ""),
                extra=m,
            ))
        return markets

    async def get_market(self, market_id: str) -> Market:
        resp = await self._client.get(f"{self.gamma_url}/markets/{market_id}")
        resp.raise_for_status()
        m = resp.json()

        outcomes = []
        prices = {}
        for token in m.get("tokens", []):
            outcome = token.get("outcome", "Unknown")
            outcomes.append(outcome)
            prices[outcome] = float(token.get("price", 0))

        return Market(
            id=m.get("condition_id", m.get("id", "")),
            title=m.get("question", m.get("title", "")),
            description=m.get("description", ""),
            exchange="polymarket",
            outcomes=outcomes,
            prices=prices,
            volume=float(m.get("volume", 0)),
            end_date=datetime.fromisoformat(m["end_date_iso"]) if m.get("end_date_iso") else None,
            category=m.get("category", ""),
            extra=m,
        )

    async def get_orderbook(self, market_id: str) -> OrderBook:
        # Need the token_id from market data
        market = await self.get_market(market_id)
        token_id = market.extra.get("tokens", [{}])[0].get("token_id", market_id)

        resp = await self._client.get(f"{self.clob_url}/book", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()

        bids = [OrderBookLevel(price=float(l["price"]), size=float(l["size"]))
                for l in data.get("bids", [])]
        asks = [OrderBookLevel(price=float(l["price"]), size=float(l["size"]))
                for l in data.get("asks", [])]

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
        self._init_clob_client()
        if self._clob_client is None:
            raise RuntimeError("Polymarket CLOB client not initialized. Set POLYMARKET_PRIVATE_KEY.")

        # Get token_id for the outcome
        market = await self.get_market(market_id)
        token_id = None
        for token in market.extra.get("tokens", []):
            if token.get("outcome") == outcome:
                token_id = token["token_id"]
                break
        if not token_id:
            raise ValueError(f"Outcome '{outcome}' not found for market {market_id}")

        from py_clob_client.order_builder.constants import BUY as CLOB_BUY, SELL as CLOB_SELL

        order_args = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": CLOB_BUY if side == Side.BUY else CLOB_SELL,
        }

        signed_order = self._clob_client.create_order(order_args)
        result = self._clob_client.post_order(signed_order)

        order_id = result.get("orderID", result.get("id", ""))
        return Order(
            id=order_id,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.PENDING,
            outcome=outcome,
            exchange="polymarket",
        )

    async def cancel_order(self, order_id: str) -> bool:
        self._init_clob_client()
        if self._clob_client is None:
            return False
        try:
            self._clob_client.cancel(order_id)
            return True
        except Exception:
            return False

    async def get_positions(self) -> list[Position]:
        # Polymarket data API for positions
        if not self.api_key:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = await self._client.get(
            "https://data-api.polymarket.com/positions",
            headers=headers,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        positions = []
        for p in data if isinstance(data, list) else []:
            if float(p.get("size", 0)) == 0:
                continue
            positions.append(Position(
                market_id=p.get("asset", {}).get("condition_id", ""),
                market_title=p.get("title", ""),
                outcome=p.get("outcome", "Yes"),
                size=float(p.get("size", 0)),
                avg_entry_price=float(p.get("avgPrice", 0)),
                current_price=float(p.get("curPrice", 0)),
                exchange="polymarket",
            ))
        return positions

    async def get_balance(self) -> float:
        """Portfolio value via Polymarket Data API."""
        address = (settings.POLYMARKET_USER_ADDRESS or "").lower()
        if not address and self.private_key:
            try:
                from eth_account import Account
                address = Account.from_key(self.private_key).address.lower()
            except Exception:
                return 0.0
        if not address:
            return 0.0

        try:
            resp = await self._client.get(
                "https://data-api.polymarket.com/value",
                params={"user": address},
            )
            if resp.status_code != 200:
                return 0.0
            data = resp.json()
            if isinstance(data, list) and data:
                return float(data[0].get("value", 0) or 0)
            if isinstance(data, dict):
                return float(data.get("value", 0) or 0)
        except Exception:
            pass
        return 0.0

    async def get_open_orders(self) -> list[Order]:
        self._init_clob_client()
        if self._clob_client is None:
            return []
        try:
            data = self._clob_client.get_orders()
            orders = []
            for o in data if isinstance(data, list) else []:
                orders.append(Order(
                    id=o.get("id", ""),
                    market_id=o.get("asset_id", ""),
                    side=Side.BUY if o.get("side") == "BUY" else Side.SELL,
                    price=float(o.get("price", 0)),
                    size=float(o.get("original_size", 0)),
                    filled_size=float(o.get("size_matched", 0)),
                    status=OrderStatus.PENDING,
                    exchange="polymarket",
                ))
            return orders
        except Exception:
            return []

    async def close(self):
        await self._client.aclose()
