"""Maps ESPN NFL game IDs to Polymarket condition/token IDs via Gamma API."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from bot.config import settings
from bot.utils.logger import log

try:
    from thefuzz import fuzz
except ImportError:
    fuzz = None  # type: ignore

# NFL series ID on Polymarket (from /sports endpoint)
NFL_SERIES_ID = "12185"

GAMMA_EVENTS_URL = f"{settings.POLYMARKET_GAMMA_URL}/events"

TEAM_ALIASES: dict[str, list[str]] = {
    "Arizona Cardinals": ["Cardinals", "ARI", "Arizona"],
    "Atlanta Falcons": ["Falcons", "ATL", "Atlanta"],
    "Baltimore Ravens": ["Ravens", "BAL", "Baltimore"],
    "Buffalo Bills": ["Bills", "BUF", "Buffalo"],
    "Carolina Panthers": ["Panthers", "CAR", "Carolina"],
    "Chicago Bears": ["Bears", "CHI", "Chicago"],
    "Cincinnati Bengals": ["Bengals", "CIN", "Cincinnati"],
    "Cleveland Browns": ["Browns", "CLE", "Cleveland"],
    "Dallas Cowboys": ["Cowboys", "DAL", "Dallas"],
    "Denver Broncos": ["Broncos", "DEN", "Denver"],
    "Detroit Lions": ["Lions", "DET", "Detroit"],
    "Green Bay Packers": ["Packers", "GB", "Green Bay"],
    "Houston Texans": ["Texans", "HOU", "Houston"],
    "Indianapolis Colts": ["Colts", "IND", "Indianapolis"],
    "Jacksonville Jaguars": ["Jaguars", "JAX", "Jacksonville", "Jags"],
    "Kansas City Chiefs": ["Chiefs", "KC", "Kansas City"],
    "Las Vegas Raiders": ["Raiders", "LV", "Las Vegas", "Oakland"],
    "Los Angeles Chargers": ["Chargers", "LAC", "LA Chargers"],
    "Los Angeles Rams": ["Rams", "LAR", "LA Rams"],
    "Miami Dolphins": ["Dolphins", "MIA", "Miami"],
    "Minnesota Vikings": ["Vikings", "MIN", "Minnesota"],
    "New England Patriots": ["Patriots", "NE", "New England", "Pats"],
    "New Orleans Saints": ["Saints", "NO", "New Orleans"],
    "New York Giants": ["Giants", "NYG", "NY Giants"],
    "New York Jets": ["Jets", "NYJ", "NY Jets"],
    "Philadelphia Eagles": ["Eagles", "PHI", "Philadelphia", "Philly"],
    "Pittsburgh Steelers": ["Steelers", "PIT", "Pittsburgh"],
    "San Francisco 49ers": ["49ers", "SF", "San Francisco", "Niners"],
    "Seattle Seahawks": ["Seahawks", "SEA", "Seattle"],
    "Tampa Bay Buccaneers": ["Buccaneers", "TB", "Tampa Bay", "Bucs"],
    "Tennessee Titans": ["Titans", "TEN", "Tennessee"],
    "Washington Commanders": ["Commanders", "WAS", "Washington"],
}

_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in TEAM_ALIASES.items():
    _ALIAS_TO_CANONICAL[_canonical.lower()] = _canonical
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.lower()] = _canonical


@dataclass
class NFLMarketMapping:
    espn_game_id: str
    market_id: str
    condition_id: str
    token_ids: dict[str, str]  # {"home": clob_token_id, "away": clob_token_id}
    home_team: str
    away_team: str
    question: str = ""
    home_price: float = 0.5
    away_price: float = 0.5


def _normalize_team(name: str) -> str:
    return _ALIAS_TO_CANONICAL.get(name.lower(), name)


def _parse_json_field(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _match_team(text: str, team_name: str) -> bool:
    """Check if text references a given team (canonical or alias)."""
    import re
    canonical = _normalize_team(team_name)
    text_lower = text.lower()

    if canonical.lower() in text_lower:
        return True
    for alias in TEAM_ALIASES.get(canonical, []):
        alias_lower = alias.lower()
        # Short aliases (<=3 chars like NE, SF, GB) need word-boundary matching
        if len(alias_lower) <= 3:
            if re.search(r'\b' + re.escape(alias_lower) + r'\b', text_lower):
                return True
        else:
            if alias_lower in text_lower:
                return True

    if fuzz:
        return fuzz.partial_ratio(canonical.lower(), text_lower) >= 85

    return False


class NFLMarketMapper:
    """Fetches NFL moneyline markets from Polymarket Gamma API and matches to ESPN games."""

    def __init__(self, refresh_interval: float = 300.0):
        self.refresh_interval = refresh_interval
        self._client = httpx.AsyncClient(timeout=15.0)
        self._moneylines: list[dict[str, Any]] = []  # moneyline markets only
        self._mappings: dict[str, NFLMarketMapping] = {}
        self._last_refresh: datetime | None = None

    async def refresh(self) -> None:
        """Fetch active NFL moneyline markets from Gamma API using series_id."""
        all_events: list[dict] = []
        offset = 0
        page_limit = 100

        try:
            while True:
                resp = await self._client.get(
                    GAMMA_EVENTS_URL,
                    params={
                        "series_id": NFL_SERIES_ID,
                        "limit": page_limit,
                        "offset": offset,
                        "closed": "false",
                        "order": "startDate",
                        "ascending": "false",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                batch = data if isinstance(data, list) else data.get("data", [])
                all_events.extend(batch)
                if len(batch) < page_limit:
                    break
                offset += page_limit
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(f"[NFL Markets] Gamma API refresh failed: {exc}")
            return

        # Extract moneyline markets only
        self._moneylines = []
        for event in all_events:
            for mkt in event.get("markets", []):
                if mkt.get("sportsMarketType") == "moneyline":
                    mkt["_event_title"] = event.get("title", "")
                    self._moneylines.append(mkt)

        self._last_refresh = datetime.now(timezone.utc)
        log.info(
            f"[NFL Markets] Refreshed: {len(all_events)} events, "
            f"{len(self._moneylines)} moneyline markets"
        )

    async def _ensure_fresh(self) -> None:
        if self._last_refresh is None:
            await self.refresh()
            return
        elapsed = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
        if elapsed > self.refresh_interval:
            await self.refresh()

    async def map_game(
        self, espn_game_id: str, home_team: str, away_team: str
    ) -> NFLMarketMapping | None:
        """Find the Polymarket moneyline market matching an ESPN game."""
        await self._ensure_fresh()

        if espn_game_id in self._mappings:
            return self._mappings[espn_game_id]

        for mkt in self._moneylines:
            # Check outcomes array for both teams (more precise than title matching)
            outcomes = _parse_json_field(mkt.get("outcomes", []))
            if len(outcomes) < 2:
                continue

            outcomes_str = " ".join(outcomes)
            home_in_outcomes = _match_team(outcomes_str, home_team)
            away_in_outcomes = _match_team(outcomes_str, away_team)
            if not (home_in_outcomes and away_in_outcomes):
                continue

            question = mkt.get("question", "") or mkt.get("_event_title", "")

            outcomes = _parse_json_field(mkt.get("outcomes", []))
            prices = _parse_json_field(mkt.get("outcomePrices", []))
            clob_tokens = _parse_json_field(mkt.get("clobTokenIds", []))

            if len(outcomes) < 2 or len(clob_tokens) < 2:
                continue

            token_map: dict[str, str] = {}
            home_price = 0.5
            away_price = 0.5

            for i, outcome_name in enumerate(outcomes):
                token_id = clob_tokens[i] if i < len(clob_tokens) else ""
                price = float(prices[i]) if i < len(prices) else 0.5

                if _match_team(outcome_name, home_team):
                    token_map["home"] = token_id
                    home_price = price
                elif _match_team(outcome_name, away_team):
                    token_map["away"] = token_id
                    away_price = price

            if "home" not in token_map or "away" not in token_map:
                continue

            mapping = NFLMarketMapping(
                espn_game_id=espn_game_id,
                market_id=mkt.get("id", ""),
                condition_id=mkt.get("conditionId", ""),
                token_ids=token_map,
                home_team=home_team,
                away_team=away_team,
                question=question,
                home_price=home_price,
                away_price=away_price,
            )
            self._mappings[espn_game_id] = mapping
            log.info(
                f"[NFL Markets] Mapped {away_team} @ {home_team} -> {question} "
                f"(home={home_price:.1%}, away={away_price:.1%})"
            )
            return mapping

        log.debug(f"[NFL Markets] No Polymarket match for {away_team} @ {home_team}")
        return None

    async def get_all_mappings(
        self, games: list[Any]
    ) -> dict[str, NFLMarketMapping]:
        for game in games:
            await self.map_game(game.game_id, game.home_team, game.away_team)
        return dict(self._mappings)

    def get_cached_price(self, espn_game_id: str) -> float | None:
        """Get the cached home team price from the last refresh."""
        mapping = self._mappings.get(espn_game_id)
        return mapping.home_price if mapping else None

    async def close(self) -> None:
        await self._client.aclose()


async def _main():
    """Standalone test: fetch and print NFL moneyline markets."""
    mapper = NFLMarketMapper()
    await mapper.refresh()
    print(f"Found {len(mapper._moneylines)} NFL moneyline markets on Polymarket:\n")
    for mkt in mapper._moneylines[:15]:
        q = mkt.get("question", "")
        outcomes = _parse_json_field(mkt.get("outcomes", []))
        prices = _parse_json_field(mkt.get("outcomePrices", []))
        clob = _parse_json_field(mkt.get("clobTokenIds", []))
        cond = mkt.get("conditionId", "")[:20]

        price_str = " | ".join(
            f"{o}: {float(p):.1%}" for o, p in zip(outcomes, prices)
        ) if outcomes and prices else "no prices"
        print(f"  {q}")
        print(f"    {price_str}  (cond={cond}...)")
        print(f"    tokens: {len(clob)}")
    await mapper.close()


if __name__ == "__main__":
    asyncio.run(_main())
