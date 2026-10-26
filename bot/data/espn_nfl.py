"""Live ESPN NFL scoreboard feed with scoring event detection and velocity tracking."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from bot.utils.logger import log

_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


class GameStatus(str, Enum):
    PRE = "pre"
    IN_PROGRESS = "in"
    POST = "post"


class ScoringEventType(str, Enum):
    TOUCHDOWN = "touchdown"
    FIELD_GOAL = "field_goal"
    SAFETY = "safety"
    TWO_POINT = "two_point"
    EXTRA_POINT = "extra_point"
    OTHER = "other"


@dataclass
class GameState:
    game_id: str
    home_team: str
    away_team: str
    home_abbrev: str
    away_abbrev: str
    home_score: int
    away_score: int
    quarter: int
    time_remaining: str
    possession: str  # team abbrev or ""
    down_distance: str
    last_play: str
    game_status: GameStatus
    home_win_probability: float  # 0-1
    start_time: datetime | None = None

    @property
    def score_diff(self) -> int:
        return abs(self.home_score - self.away_score)

    @property
    def is_live(self) -> bool:
        return self.game_status == GameStatus.IN_PROGRESS

    @property
    def time_remaining_seconds(self) -> float:
        """Parse MM:SS into seconds remaining in the current quarter."""
        try:
            parts = self.time_remaining.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return 0.0

    @property
    def total_seconds_remaining(self) -> float:
        quarters_left = max(0, 4 - self.quarter)
        return quarters_left * 900 + self.time_remaining_seconds


@dataclass
class GameEvent:
    game_id: str
    event_type: ScoringEventType
    team: str  # team abbrev
    points: int
    timestamp: datetime
    new_home_score: int
    new_away_score: int


@dataclass
class VelocityWindow:
    """Rolling window of scoring events for velocity calculation."""
    events: list[GameEvent] = field(default_factory=list)
    window_seconds: float = 300.0

    def add(self, event: GameEvent) -> None:
        self.events.append(event)
        self._prune(event.timestamp)

    def _prune(self, now: datetime) -> None:
        from datetime import timedelta
        cutoff = now - timedelta(seconds=self.window_seconds)
        self.events = [e for e in self.events if e.timestamp >= cutoff]

    def points_by_team(self, now: datetime) -> dict[str, int]:
        self._prune(now)
        totals: dict[str, int] = defaultdict(int)
        for e in self.events:
            totals[e.team] += e.points
        return dict(totals)


def _classify_score_change(points: int) -> ScoringEventType:
    if points == 6:
        return ScoringEventType.TOUCHDOWN
    if points == 7:
        return ScoringEventType.TOUCHDOWN
    if points == 3:
        return ScoringEventType.FIELD_GOAL
    if points == 2:
        return ScoringEventType.SAFETY
    if points == 1:
        return ScoringEventType.EXTRA_POINT
    if points == 8:
        return ScoringEventType.TWO_POINT
    return ScoringEventType.OTHER


def _parse_game(competition: dict, event: dict) -> GameState:
    competitors = {c["homeAway"]: c for c in competition.get("competitors", [])}
    home = competitors.get("home", {})
    away = competitors.get("away", {})

    status = event.get("status", {})
    status_type = status.get("type", {}).get("state", "pre")
    period = status.get("period", 0)
    clock = status.get("displayClock", "0:00")

    # Win probability
    home_wp = 0.5
    if "situation" in competition:
        sit = competition["situation"]
        home_wp = sit.get("lastPlay", {}).get("probability", {}).get("homeWinPercentage", 0.5)
    # Also check predictor
    for pred in event.get("predictor", []):
        if "homeTeam" in pred:
            home_wp = pred["homeTeam"].get("gameProjection", home_wp)
            if isinstance(home_wp, str):
                try:
                    home_wp = float(home_wp) / 100.0
                except ValueError:
                    home_wp = 0.5

    situation = competition.get("situation", {})
    possession_id = str(situation.get("possession", ""))
    down_text = situation.get("downDistanceText", "")
    last_play_text = situation.get("lastPlay", {}).get("text", "")

    # Map possession ID to team abbrev
    home_id = home.get("id", "")
    away_id = away.get("id", "")
    home_abbrev = home.get("team", {}).get("abbreviation", "")
    away_abbrev = away.get("team", {}).get("abbreviation", "")
    poss_abbrev = ""
    if possession_id == home_id:
        poss_abbrev = home_abbrev
    elif possession_id == away_id:
        poss_abbrev = away_abbrev

    game_status = GameStatus.PRE
    if status_type == "in":
        game_status = GameStatus.IN_PROGRESS
    elif status_type == "post":
        game_status = GameStatus.POST

    start_time = None
    date_str = event.get("date", "")
    if date_str:
        try:
            start_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    return GameState(
        game_id=event.get("id", ""),
        home_team=home.get("team", {}).get("displayName", ""),
        away_team=away.get("team", {}).get("displayName", ""),
        home_abbrev=home_abbrev,
        away_abbrev=away_abbrev,
        home_score=int(home.get("score", 0)),
        away_score=int(away.get("score", 0)),
        quarter=period,
        time_remaining=clock,
        possession=poss_abbrev,
        down_distance=down_text,
        last_play=last_play_text,
        game_status=game_status,
        home_win_probability=home_wp,
        start_time=start_time,
    )


class ESPNNFLFeed:
    """Polls ESPN NFL scoreboard and detects scoring events."""

    def __init__(self, poll_interval: float = 10.0, velocity_window: float = 300.0):
        self.poll_interval = poll_interval
        self._client = httpx.AsyncClient(timeout=15.0)
        self._prev_scores: dict[str, tuple[int, int]] = {}  # game_id -> (home, away)
        self._velocity: dict[str, VelocityWindow] = defaultdict(
            lambda: VelocityWindow(window_seconds=velocity_window)
        )
        self._games: dict[str, GameState] = {}

    async def poll(self) -> tuple[list[GameState], list[GameEvent]]:
        """Fetch current scoreboard and return all games + any new scoring events."""
        try:
            resp = await self._client.get(_SCOREBOARD_URL)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(f"[NFL Feed] ESPN poll failed: {exc}")
            return list(self._games.values()), []

        games: list[GameState] = []
        events: list[GameEvent] = []
        now = datetime.now(timezone.utc)

        for event in data.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            state = _parse_game(comp, event)
            games.append(state)
            self._games[state.game_id] = state

            prev = self._prev_scores.get(state.game_id)
            if prev is not None:
                prev_home, prev_away = prev
                if state.home_score > prev_home:
                    pts = state.home_score - prev_home
                    ev = GameEvent(
                        game_id=state.game_id,
                        event_type=_classify_score_change(pts),
                        team=state.home_abbrev,
                        points=pts,
                        timestamp=now,
                        new_home_score=state.home_score,
                        new_away_score=state.away_score,
                    )
                    events.append(ev)
                    self._velocity[state.game_id].add(ev)
                if state.away_score > prev_away:
                    pts = state.away_score - prev_away
                    ev = GameEvent(
                        game_id=state.game_id,
                        event_type=_classify_score_change(pts),
                        team=state.away_abbrev,
                        points=pts,
                        timestamp=now,
                        new_home_score=state.home_score,
                        new_away_score=state.away_score,
                    )
                    events.append(ev)
                    self._velocity[state.game_id].add(ev)

            self._prev_scores[state.game_id] = (state.home_score, state.away_score)

        return games, events

    def get_live_games(self) -> list[GameState]:
        return [g for g in self._games.values() if g.is_live]

    def get_velocity(self, game_id: str) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        return self._velocity[game_id].points_by_team(now)

    def get_game(self, game_id: str) -> GameState | None:
        return self._games.get(game_id)

    async def close(self) -> None:
        await self._client.aclose()


async def _main():
    """Standalone test: poll once and print results."""
    feed = ESPNNFLFeed()
    games, events = await feed.poll()
    print(f"Found {len(games)} NFL games:")
    for g in games:
        status = g.game_status.value
        print(f"  {g.away_team} {g.away_score} @ {g.home_team} {g.home_score} [{status}] Q{g.quarter} {g.time_remaining}")
        if g.is_live:
            print(f"    Home WP: {g.home_win_probability:.1%} | Possession: {g.possession}")
    if events:
        print(f"\nScoring events: {len(events)}")
        for e in events:
            print(f"  {e.team} +{e.points} ({e.event_type.value})")
    await feed.close()


if __name__ == "__main__":
    asyncio.run(_main())
