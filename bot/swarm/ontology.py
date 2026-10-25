"""
Trading-specific ontology for the knowledge graph.

Defines entity types and relationship types relevant to prediction market
trading. This replaces MiroFish's social-media-focused ontology with one
designed for Max the Trader's domain.

The ontology is applied once when the graph is first created, then reused
for all subsequent ingestion.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ── Trading Entity Types ────────────────────────────────────────────

TRADING_ENTITY_TYPES: List[Dict[str, Any]] = [
    {
        "name": "Asset",
        "description": "A tradeable asset like BTC, ETH, or a stock ticker.",
        "attributes": [
            {"name": "ticker", "type": "text", "description": "Ticker symbol (e.g. BTC, ETH)"},
            {"name": "asset_class", "type": "text", "description": "Asset class (crypto, equity, commodity)"},
        ],
        "examples": ["Bitcoin", "Ethereum", "S&P 500"],
    },
    {
        "name": "Exchange",
        "description": "A trading platform or prediction market exchange.",
        "attributes": [
            {"name": "exchange_type", "type": "text", "description": "Type: prediction_market, spot, futures"},
            {"name": "website", "type": "text", "description": "URL or identifier"},
        ],
        "examples": ["Kalshi", "Polymarket", "Coinbase"],
    },
    {
        "name": "Indicator",
        "description": "A technical or fundamental indicator used in analysis.",
        "attributes": [
            {"name": "indicator_type", "type": "text", "description": "Type: technical, fundamental, sentiment"},
            {"name": "parameters", "type": "text", "description": "Key parameters (e.g. RSI period=14)"},
        ],
        "examples": ["RSI(14)", "MACD", "CPI"],
    },
    {
        "name": "Event",
        "description": "A market-moving event or scheduled announcement.",
        "attributes": [
            {"name": "event_type", "type": "text", "description": "Type: policy, earnings, economic_data, news"},
            {"name": "scheduled_date", "type": "text", "description": "When the event occurs or occurred"},
        ],
        "examples": ["FOMC Meeting", "BTC Halving", "CPI Release"],
    },
    {
        "name": "Contract",
        "description": "A specific prediction market contract or binary option.",
        "attributes": [
            {"name": "contract_id", "type": "text", "description": "Contract identifier (e.g. KXBTC15M)"},
            {"name": "resolution_criteria", "type": "text", "description": "How the contract resolves"},
        ],
        "examples": ["KXBTC15M", "Will BTC exceed 100k by EOY"],
    },
    {
        "name": "Strategy",
        "description": "A trading strategy or systematic approach.",
        "attributes": [
            {"name": "timeframe", "type": "text", "description": "Trading timeframe (scalp, intraday, swing)"},
            {"name": "strategy_type", "type": "text", "description": "Type: momentum, mean_reversion, event_driven"},
        ],
        "examples": ["BTC 15M Momentum", "News-Driven Fade", "RSI Oversold Reversal"],
    },
    {
        "name": "DataSource",
        "description": "A source of market data, news, or analysis.",
        "attributes": [
            {"name": "source_type", "type": "text", "description": "Type: api, social_media, news_outlet, research"},
            {"name": "reliability", "type": "text", "description": "Assessed reliability level"},
        ],
        "examples": ["Coinbase API", "Bloomberg Terminal", "r/wallstreetbets"],
    },
    {
        "name": "MarketParticipant",
        "description": "A known market participant, trader type, or institution.",
        "attributes": [
            {"name": "participant_type", "type": "text", "description": "Type: whale, institution, retail, market_maker"},
            {"name": "influence_level", "type": "text", "description": "Estimated market influence"},
        ],
        "examples": ["MicroStrategy", "Retail traders", "Citadel"],
    },
    # Fallback types (required by Zep — catch-all for entities not matching above)
    {
        "name": "Person",
        "description": "Any individual person not fitting other specific types.",
        "attributes": [
            {"name": "full_name", "type": "text", "description": "Full name of the person"},
            {"name": "role", "type": "text", "description": "Role or occupation"},
        ],
        "examples": ["Jerome Powell", "Satoshi Nakamoto"],
    },
    {
        "name": "Organization",
        "description": "Any organization not fitting other specific types.",
        "attributes": [
            {"name": "org_name", "type": "text", "description": "Name of the organization"},
            {"name": "org_type", "type": "text", "description": "Type of organization"},
        ],
        "examples": ["Federal Reserve", "SEC"],
    },
]


# ── Trading Edge (Relationship) Types ───────────────────────────────

TRADING_EDGE_TYPES: List[Dict[str, Any]] = [
    {
        "name": "AFFECTS",
        "description": "One entity affects or influences another.",
        "source_targets": [
            {"source": "Event", "target": "Asset"},
            {"source": "Event", "target": "Contract"},
            {"source": "Indicator", "target": "Asset"},
            {"source": "Person", "target": "Asset"},
            {"source": "Organization", "target": "Asset"},
        ],
        "attributes": [],
    },
    {
        "name": "CORRELATES_WITH",
        "description": "Two entities show statistical or observed correlation.",
        "source_targets": [
            {"source": "Asset", "target": "Asset"},
            {"source": "Indicator", "target": "Indicator"},
            {"source": "Asset", "target": "Indicator"},
        ],
        "attributes": [],
    },
    {
        "name": "PREDICTS",
        "description": "One entity is a leading indicator or predictor of another.",
        "source_targets": [
            {"source": "Indicator", "target": "Asset"},
            {"source": "Event", "target": "Asset"},
            {"source": "DataSource", "target": "Event"},
        ],
        "attributes": [],
    },
    {
        "name": "CONTRADICTS",
        "description": "One signal or rule contradicts another.",
        "source_targets": [
            {"source": "Strategy", "target": "Strategy"},
            {"source": "Indicator", "target": "Indicator"},
            {"source": "DataSource", "target": "DataSource"},
        ],
        "attributes": [],
    },
    {
        "name": "CONFIRMS",
        "description": "One signal or rule confirms or supports another.",
        "source_targets": [
            {"source": "Strategy", "target": "Strategy"},
            {"source": "Indicator", "target": "Indicator"},
            {"source": "DataSource", "target": "DataSource"},
        ],
        "attributes": [],
    },
    {
        "name": "TRADES_ON",
        "description": "A contract or asset trades on a specific exchange.",
        "source_targets": [
            {"source": "Contract", "target": "Exchange"},
            {"source": "Asset", "target": "Exchange"},
        ],
        "attributes": [],
    },
    {
        "name": "RESOLVES_VIA",
        "description": "A contract resolves based on an asset or data source.",
        "source_targets": [
            {"source": "Contract", "target": "Asset"},
            {"source": "Contract", "target": "DataSource"},
        ],
        "attributes": [],
    },
    {
        "name": "PUBLISHED_BY",
        "description": "Information or data published by a source.",
        "source_targets": [
            {"source": "Event", "target": "Organization"},
            {"source": "Event", "target": "Person"},
            {"source": "Event", "target": "DataSource"},
        ],
        "attributes": [],
    },
    {
        "name": "USES",
        "description": "A strategy uses a specific indicator or data source.",
        "source_targets": [
            {"source": "Strategy", "target": "Indicator"},
            {"source": "Strategy", "target": "DataSource"},
        ],
        "attributes": [],
    },
    {
        "name": "TARGETS",
        "description": "A strategy or participant targets a specific asset or contract.",
        "source_targets": [
            {"source": "Strategy", "target": "Asset"},
            {"source": "Strategy", "target": "Contract"},
            {"source": "MarketParticipant", "target": "Asset"},
        ],
        "attributes": [],
    },
]


def get_trading_ontology() -> Dict[str, Any]:
    """Return the complete trading ontology definition for Zep."""
    return {
        "entity_types": TRADING_ENTITY_TYPES,
        "edge_types": TRADING_EDGE_TYPES,
    }
