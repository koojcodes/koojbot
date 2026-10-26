"""Tests for NFL velocity strategy — signal generation, filters, momentum."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.core.signals import SignalAction
from bot.data.espn_nfl import GameState, GameEvent, GameStatus, ScoringEventType
from bot.data.nfl_markets import NFLMarketMapping
from bot.strategies.nfl_velocity import NFLVelocityStrategy


def _make_game(**overrides) -> GameState:
    defaults = dict(
        game_id="1", home_team="Kansas City Chiefs", away_team="Las Vegas Raiders",
        home_abbrev="KC", away_abbrev="LV",
        home_score=21, away_score=14,
        quarter=3, time_remaining="8:00",
        possession="KC", down_distance="", last_play="",
        game_status=GameStatus.IN_PROGRESS,
        home_win_probability=0.68,
    )
    defaults.update(overrides)
    return GameState(**defaults)


def _make_mapping() -> NFLMarketMapping:
    return NFLMarketMapping(
        espn_game_id="1", market_id="mkt_1", condition_id="cond_1",
        token_ids={"home": "tok_h", "away": "tok_a"},
        home_team="Kansas City Chiefs", away_team="Las Vegas Raiders",
    )


def test_velocity_surge_signal():
    strategy = NFLVelocityStrategy()
    game = _make_game(home_win_probability=0.72)
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.60,  # ESPN says 72%, Poly says 60% — 12% gap
        velocity={"KC": 14, "LV": 0},  # KC scored 14 in 5min
    )
    assert signal is not None
    assert signal.action == SignalAction.BUY
    assert "Kansas City Chiefs" in signal.outcome
    assert "velocity surge" in signal.reasoning.lower()


def test_wp_divergence_signal():
    strategy = NFLVelocityStrategy()
    game = _make_game(home_win_probability=0.75)
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.60,  # 15% divergence
        velocity={},
    )
    assert signal is not None
    assert "divergence" in signal.reasoning.lower()


def test_garbage_time_filter():
    strategy = NFLVelocityStrategy()
    game = _make_game(
        quarter=4, time_remaining="6:00",
        home_score=42, away_score=7, home_win_probability=0.99,
    )
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.50,
        velocity={"KC": 14},
    )
    assert signal is None


def test_late_game_certainty_filter():
    strategy = NFLVelocityStrategy()
    game = _make_game(
        quarter=4, time_remaining="1:30",
        home_win_probability=0.95,
    )
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.92,
        velocity={},
    )
    assert signal is None


def test_momentum_swing():
    strategy = NFLVelocityStrategy()
    now = datetime.now(timezone.utc)

    # Track 3 unanswered KC scores
    for _ in range(3):
        strategy.track_event(GameEvent(
            game_id="1", event_type=ScoringEventType.TOUCHDOWN,
            team="KC", points=7, timestamp=now,
            new_home_score=21, new_away_score=14,
        ))

    game = _make_game(home_win_probability=0.70)
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.60,  # 10% gap
        velocity={},
    )
    assert signal is not None
    assert "momentum" in signal.reasoning.lower() or "unanswered" in signal.reasoning.lower()


def test_no_signal_when_prices_aligned():
    strategy = NFLVelocityStrategy()
    game = _make_game(home_win_probability=0.65)
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.64,  # Only 1% gap — no signal
        velocity={},
    )
    assert signal is None


def test_pregame_no_signal():
    strategy = NFLVelocityStrategy()
    game = _make_game(game_status=GameStatus.PRE)
    mapping = _make_mapping()

    signal = strategy.evaluate_game(
        game=game, mapping=mapping,
        poly_home_price=0.50,
        velocity={},
    )
    assert signal is None
