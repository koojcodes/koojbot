"""Tests for ESPN NFL feed — parsing, event detection, velocity tracking."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from bot.data.espn_nfl import (
    ESPNNFLFeed,
    GameState,
    GameEvent,
    GameStatus,
    ScoringEventType,
    VelocityWindow,
    _classify_score_change,
    _parse_game,
)


# --- Sample ESPN API response ---
SAMPLE_EVENT = {
    "id": "401547417",
    "date": "2026-10-25T17:00Z",
    "status": {
        "period": 2,
        "displayClock": "7:32",
        "type": {"state": "in"},
    },
    "competitions": [
        {
            "competitors": [
                {
                    "homeAway": "home",
                    "score": "14",
                    "id": "12",
                    "team": {"displayName": "Kansas City Chiefs", "abbreviation": "KC"},
                },
                {
                    "homeAway": "away",
                    "score": "10",
                    "id": "33",
                    "team": {"displayName": "Las Vegas Raiders", "abbreviation": "LV"},
                },
            ],
            "situation": {
                "possession": "12",
                "downDistanceText": "2nd & 7 at KC 45",
                "lastPlay": {
                    "text": "P.Mahomes pass to T.Kelce for 12 yards",
                    "probability": {"homeWinPercentage": 0.72},
                },
            },
        }
    ],
}


def test_parse_game():
    state = _parse_game(SAMPLE_EVENT["competitions"][0], SAMPLE_EVENT)
    assert state.game_id == "401547417"
    assert state.home_team == "Kansas City Chiefs"
    assert state.away_team == "Las Vegas Raiders"
    assert state.home_score == 14
    assert state.away_score == 10
    assert state.quarter == 2
    assert state.time_remaining == "7:32"
    assert state.possession == "KC"
    assert state.game_status == GameStatus.IN_PROGRESS
    assert state.is_live
    assert state.home_win_probability == 0.72


def test_game_state_properties():
    state = GameState(
        game_id="1", home_team="A", away_team="B",
        home_abbrev="A", away_abbrev="B",
        home_score=21, away_score=7,
        quarter=3, time_remaining="5:00",
        possession="A", down_distance="", last_play="",
        game_status=GameStatus.IN_PROGRESS,
        home_win_probability=0.85,
    )
    assert state.score_diff == 14
    assert state.time_remaining_seconds == 300.0
    assert state.total_seconds_remaining == 1200.0  # Q3 5:00 = 1Q left + 5min


def test_classify_score_change():
    assert _classify_score_change(7) == ScoringEventType.TOUCHDOWN
    assert _classify_score_change(6) == ScoringEventType.TOUCHDOWN
    assert _classify_score_change(3) == ScoringEventType.FIELD_GOAL
    assert _classify_score_change(2) == ScoringEventType.SAFETY
    assert _classify_score_change(1) == ScoringEventType.EXTRA_POINT
    assert _classify_score_change(8) == ScoringEventType.TWO_POINT


def test_velocity_window():
    vw = VelocityWindow(window_seconds=300.0)
    now = datetime.now(timezone.utc)
    event = GameEvent(
        game_id="1", event_type=ScoringEventType.TOUCHDOWN,
        team="KC", points=7, timestamp=now,
        new_home_score=7, new_away_score=0,
    )
    vw.add(event)
    pts = vw.points_by_team(now)
    assert pts["KC"] == 7


@pytest.mark.asyncio
async def test_feed_poll_detects_scoring():
    feed = ESPNNFLFeed()
    # Seed initial scores
    feed._prev_scores["401547417"] = (7, 10)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"events": [SAMPLE_EVENT]}

    with patch.object(feed._client, "get", new_callable=AsyncMock, return_value=mock_resp):
        games, events = await feed.poll()

    assert len(games) == 1
    assert games[0].home_score == 14
    # Home went from 7 to 14 = +7 scoring event
    assert len(events) == 1
    assert events[0].team == "KC"
    assert events[0].points == 7
    assert events[0].event_type == ScoringEventType.TOUCHDOWN

    await feed.close()
