"""NFL velocity/momentum swing strategy — trades scoring surges and WP divergence."""
from __future__ import annotations

from datetime import datetime, timezone

from bot.core.signals import Signal, SignalAction
from bot.data.espn_nfl import GameState, GameEvent, VelocityWindow
from bot.data.market_data import MarketSnapshot
from bot.data.news import NewsAnalysis
from bot.data.nfl_markets import NFLMarketMapping
from bot.nfl.config import (
    NFL_GARBAGE_TIME_DIFF,
    NFL_MIN_DIVERGENCE,
    NFL_MOMENTUM_DIVERGENCE,
    NFL_VELOCITY_THRESHOLD,
)
from bot.strategies.base import Strategy
from bot.utils.logger import log


class NFLVelocityStrategy(Strategy):
    """
    Swing trading strategy based on scoring velocity and win probability divergence.

    Signals:
    1. Scoring velocity — team scores NFL_VELOCITY_THRESHOLD+ pts in 5-min window,
       Polymarket price lags ESPN WP
    2. Momentum swing — 2+ unanswered scoring events
    3. WP divergence — ESPN win_prob vs Polymarket price gap > threshold
    """

    name = "nfl_velocity"

    def __init__(
        self,
        min_divergence: float = 0.0,
        momentum_divergence: float = 0.0,
        velocity_threshold: int = 0,
        garbage_time_diff: int = 0,
    ):
        self.min_divergence = min_divergence or NFL_MIN_DIVERGENCE
        self.momentum_divergence = momentum_divergence or NFL_MOMENTUM_DIVERGENCE
        self.velocity_threshold = velocity_threshold or NFL_VELOCITY_THRESHOLD
        self.garbage_time_diff = garbage_time_diff or NFL_GARBAGE_TIME_DIFF
        self._unanswered: dict[str, list[str]] = {}  # game_id -> [team, team, ...]

    async def evaluate(
        self,
        snapshot: MarketSnapshot,
        news: NewsAnalysis | None = None,
    ) -> Signal | None:
        # Standard Strategy interface — not used for NFL live games
        return None

    def track_event(self, event: GameEvent) -> None:
        """Track consecutive scoring for momentum detection."""
        streak = self._unanswered.setdefault(event.game_id, [])
        streak.append(event.team)

    def reset_momentum(self, game_id: str) -> None:
        self._unanswered.pop(game_id, None)

    def _get_unanswered_count(self, game_id: str, team: str) -> int:
        streak = self._unanswered.get(game_id, [])
        count = 0
        for t in reversed(streak):
            if t == team:
                count += 1
            else:
                break
        return count

    def evaluate_game(
        self,
        game: GameState,
        mapping: NFLMarketMapping,
        poly_home_price: float,
        velocity: dict[str, int],
        recent_events: list[GameEvent] | None = None,
    ) -> Signal | None:
        """Evaluate an NFL game for trading signals."""
        if not game.is_live:
            return None

        # Garbage time filter
        if game.quarter >= 4 and game.score_diff > self.garbage_time_diff:
            return None

        # Near-certain late game filter
        if game.quarter == 4 and game.time_remaining_seconds < 120:
            if poly_home_price > 0.90 or poly_home_price < 0.10:
                return None

        # Halftime filter
        if game.quarter == 2 and game.time_remaining == "0:00":
            return None

        espn_wp = game.home_win_probability
        divergence = espn_wp - poly_home_price

        signal = None

        # 1. Scoring velocity check
        home_velocity = velocity.get(game.home_abbrev, 0)
        away_velocity = velocity.get(game.away_abbrev, 0)

        if home_velocity >= self.velocity_threshold and divergence > self.min_divergence:
            signal = self._build_signal(
                game, mapping, "home", poly_home_price, espn_wp,
                f"Home velocity surge: {home_velocity}pts in 5min window, "
                f"ESPN WP {espn_wp:.1%} vs Poly {poly_home_price:.1%}",
                velocity_bonus=0.1,
            )
        elif away_velocity >= self.velocity_threshold and divergence < -self.min_divergence:
            signal = self._build_signal(
                game, mapping, "away", poly_home_price, espn_wp,
                f"Away velocity surge: {away_velocity}pts in 5min window, "
                f"ESPN WP {1 - espn_wp:.1%} vs Poly {1 - poly_home_price:.1%}",
                velocity_bonus=0.1,
            )

        # 2. Momentum swing (2+ unanswered)
        if signal is None:
            home_unanswered = self._get_unanswered_count(game.game_id, game.home_abbrev)
            away_unanswered = self._get_unanswered_count(game.game_id, game.away_abbrev)

            if home_unanswered >= 2 and divergence > self.min_divergence:
                signal = self._build_signal(
                    game, mapping, "home", poly_home_price, espn_wp,
                    f"Momentum: {home_unanswered} unanswered scores by {game.home_abbrev}, "
                    f"ESPN WP {espn_wp:.1%} vs Poly {poly_home_price:.1%}",
                    velocity_bonus=0.05 * home_unanswered,
                )
            elif away_unanswered >= 2 and divergence < -self.min_divergence:
                signal = self._build_signal(
                    game, mapping, "away", poly_home_price, espn_wp,
                    f"Momentum: {away_unanswered} unanswered scores by {game.away_abbrev}, "
                    f"ESPN WP {1 - espn_wp:.1%} vs Poly {1 - poly_home_price:.1%}",
                    velocity_bonus=0.05 * away_unanswered,
                )

        # 3. Pure WP divergence
        if signal is None and abs(divergence) > self.momentum_divergence:
            side = "home" if divergence > 0 else "away"
            signal = self._build_signal(
                game, mapping, side, poly_home_price, espn_wp,
                f"WP divergence: ESPN {espn_wp:.1%} vs Poly {poly_home_price:.1%} "
                f"(gap {abs(divergence):.1%})",
                velocity_bonus=0.0,
            )

        if signal:
            log.info(
                f"[bold green]NFL VELOCITY[/] {signal.action.value.upper()} {signal.outcome} on "
                f"[bold]{game.away_team} @ {game.home_team}[/] — {signal.reasoning}"
            )

        return signal

    def _build_signal(
        self,
        game: GameState,
        mapping: NFLMarketMapping,
        side: str,  # "home" or "away"
        poly_home_price: float,
        espn_wp: float,
        reasoning: str,
        velocity_bonus: float = 0.0,
    ) -> Signal:
        divergence = abs(espn_wp - poly_home_price)
        confidence = min(0.85, divergence * 3 + velocity_bonus)

        if side == "home":
            outcome = game.home_team
            entry_price = poly_home_price
            target = min(0.95, espn_wp + 0.02)
            stop = max(0.05, poly_home_price - 0.08)
        else:
            outcome = game.away_team
            entry_price = 1.0 - poly_home_price
            target = min(0.95, (1 - espn_wp) + 0.02)
            stop = max(0.05, (1 - poly_home_price) - 0.08)

        return Signal(
            market_id=mapping.market_id,
            market_title=f"{game.away_team} @ {game.home_team}",
            exchange="polymarket",
            action=SignalAction.BUY,
            outcome=outcome,
            confidence=confidence,
            target_price=target,
            stop_loss=stop,
            reasoning=reasoning,
            strategy=self.name,
        )
