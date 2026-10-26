"""Tests for NFL Polymarket market mapper."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.data.nfl_markets import (
    NFLMarketMapper,
    _normalize_team,
    _match_team,
)


def test_normalize_team():
    assert _normalize_team("Chiefs") == "Kansas City Chiefs"
    assert _normalize_team("KC") == "Kansas City Chiefs"
    assert _normalize_team("49ers") == "San Francisco 49ers"
    assert _normalize_team("Niners") == "San Francisco 49ers"
    assert _normalize_team("NY Giants") == "New York Giants"


def test_match_team():
    assert _match_team("Will the Kansas City Chiefs win?", "Chiefs")
    assert _match_team("Chiefs vs Raiders", "Kansas City Chiefs")
    assert _match_team("49ers moneyline", "San Francisco 49ers")
    assert not _match_team("Eagles vs Cowboys", "Chiefs")


def test_short_alias_word_boundary():
    # "NE" should NOT match "Buccaneers" (substring "ne" in "buccaneers")
    assert not _match_team("Buccaneers Bears", "New England Patriots")
    # "SF" should NOT match "Suffolk"
    assert not _match_team("Suffolk Hawks", "San Francisco 49ers")
    # "KC" should match standalone
    assert _match_team("KC Chiefs", "Kansas City Chiefs")


SAMPLE_MONEYLINE = {
    "id": "mkt_123",
    "conditionId": "cond_123",
    "question": "Chiefs vs. Raiders",
    "sportsMarketType": "moneyline",
    "outcomes": '["Chiefs", "Raiders"]',
    "outcomePrices": '["0.65", "0.35"]',
    "clobTokenIds": '["tok_home_123", "tok_away_456"]',
    "_event_title": "Chiefs vs. Raiders",
}


@pytest.mark.asyncio
async def test_map_game():
    mapper = NFLMarketMapper()
    mapper._moneylines = [SAMPLE_MONEYLINE]
    mapper._last_refresh = MagicMock()

    mapping = await mapper.map_game("401547417", "Kansas City Chiefs", "Las Vegas Raiders")
    assert mapping is not None
    assert mapping.market_id == "mkt_123"
    assert mapping.condition_id == "cond_123"
    assert mapping.token_ids.get("home") == "tok_home_123"
    assert mapping.token_ids.get("away") == "tok_away_456"
    assert mapping.home_price == 0.65

    await mapper.close()


@pytest.mark.asyncio
async def test_no_match():
    mapper = NFLMarketMapper()
    mapper._moneylines = [SAMPLE_MONEYLINE]
    mapper._last_refresh = MagicMock()

    mapping = await mapper.map_game("999", "Green Bay Packers", "Detroit Lions")
    assert mapping is None

    await mapper.close()
