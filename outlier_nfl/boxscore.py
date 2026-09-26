"""NFL box-score parsing and market-to-stat grading helpers for shadow settle.

Accepts a simplified fixture schema (preferred for CI) and an ESPN-shaped
summary payload (same athlete/labels layout used by outlier_scrapers.results).
Live ESPN fetch is optional and may be blocked by upstream; do not invent stats.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ESPN_NFL_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
USER_AGENT = "outlier-nfl-shadow-settle/0.1"


class BoxScoreError(RuntimeError):
    """Raised when a box-score payload or live feed cannot be consumed safely."""


@dataclass(frozen=True)
class NflBoxScoreEvent:
    """Final NFL event with player-level numeric stats keyed by tokenized name."""

    provider_event_id: str
    event_date: date
    away: str
    home: str
    away_score: float
    home_score: float
    players: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def team_codes(self) -> frozenset[str]:
        return frozenset({_team_token(self.away), _team_token(self.home)})


def _token(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^A-Z0-9]", "", text.encode("ascii", "ignore").decode().upper())


def _team_token(value: Any) -> str:
    """Canonical NFL team code (nflverse LA → LAR, etc.)."""
    from outlier_nfl.config import normalize_team

    raw = str(value or "").strip()
    canonical = normalize_team(raw)
    return canonical or _token(raw)


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed != parsed or abs(parsed) == math.inf:
        return None
    return parsed


def _made(value: Any) -> float | None:
    text = str(value or "").strip()
    if "-" in text and not text.lstrip("-").replace(".", "", 1).isdigit():
        text = text.split("-", 1)[0]
    return _number(text)


def parse_simplified_events(payload: Mapping[str, Any] | list[Any]) -> list[NflBoxScoreEvent]:
    """Parse the CI-friendly box-score bundle used by shadow-settle fixtures."""
    raw_events = payload.get("events") if isinstance(payload, Mapping) else payload
    if not isinstance(raw_events, list):
        raise BoxScoreError("Box-score payload is missing an events list")
    events: list[NflBoxScoreEvent] = []
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        event_date_raw = raw.get("event_date") or raw.get("date")
        if not event_date_raw:
            raise BoxScoreError("Box-score event is missing event_date")
        event_date = date.fromisoformat(str(event_date_raw)[:10])
        away = str(raw.get("away") or "").strip()
        home = str(raw.get("home") or "").strip()
        away_score = _number(raw.get("away_score"))
        home_score = _number(raw.get("home_score"))
        if not away or not home or away_score is None or home_score is None:
            raise BoxScoreError("Box-score event requires away/home and scores")
        players_raw = raw.get("players") or {}
        if not isinstance(players_raw, Mapping):
            raise BoxScoreError("Box-score players must be an object keyed by player name")
        players: dict[str, dict[str, float]] = {}
        for name, stats in players_raw.items():
            if not isinstance(stats, Mapping):
                continue
            bucket: dict[str, float] = {}
            for key, value in stats.items():
                parsed = _number(value)
                if parsed is None:
                    continue
                key_text = str(key)
                bucket[_token(key_text)] = parsed
                if ":" in key_text:
                    group, label = key_text.split(":", 1)
                    bucket[f"{_token(group)}:{_token(label)}"] = parsed
            if bucket:
                players[_token(name)] = bucket
        events.append(
            NflBoxScoreEvent(
                provider_event_id=str(raw.get("provider_event_id") or raw.get("event_id") or ""),
                event_date=event_date,
                away=_team_token(away),
                home=_team_token(home),
                away_score=away_score,
                home_score=home_score,
                players=players,
            )
        )
    return events


def parse_espn_boxscore_players(payload: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Extract tokenized player stats from an ESPN game summary boxscore block."""
    raw_boxscore = payload.get("boxscore")
    boxscore: Mapping[str, Any] = raw_boxscore if isinstance(raw_boxscore, Mapping) else {}
    teams = boxscore.get("players")
    if not isinstance(teams, list):
        return {}
    players: dict[str, dict[str, float]] = defaultdict(dict)
    for team in teams:
        if not isinstance(team, Mapping):
            continue
        groups = team.get("statistics")
        if isinstance(groups, Mapping):
            groups = [groups]
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            group_name = _token(
                group.get("type") or group.get("name") or group.get("displayName") or "STATS"
            )
            labels = group.get("labels") or group.get("names") or []
            if not isinstance(labels, list):
                continue
            for athlete_row in group.get("athletes") or []:
                if not isinstance(athlete_row, Mapping):
                    continue
                raw_athlete = athlete_row.get("athlete")
                athlete = raw_athlete if isinstance(raw_athlete, Mapping) else athlete_row
                name = str(athlete.get("displayName") or athlete.get("fullName") or "").strip()
                values = athlete_row.get("stats")
                if not name or not isinstance(values, list) or len(values) != len(labels):
                    continue
                bucket = players[_token(name)]
                for label, value in zip(labels, values, strict=True):
                    key = _token(label)
                    parsed = _made(value)
                    if parsed is not None:
                        bucket[key] = parsed
                        bucket[f"{group_name}:{key}"] = parsed
    return dict(players)


def _stat(stats: Mapping[str, float], *keys: str) -> float | None:
    for key in keys:
        if key in stats:
            return stats[key]
        token = _token(key)
        if token in stats:
            return stats[token]
    return None


def player_actual(market: str, stats: Mapping[str, float]) -> float | None:
    """Map a canonical NFL prop market to a box-score actual, or None if unsupported.

    Prefer group-qualified keys (PASSING:YDS) so bare YDS/ATT/TD never cross units.
    """
    token = _token(market)
    if token == "PASSYDS":
        return _stat(stats, "PASSING:YDS")
    if token == "PASSTD":
        return _stat(stats, "PASSING:TD")
    if token == "PASSCOMP":
        return _stat(stats, "PASSING:CMP", "PASSING:COMP")
    if token == "PASSATT":
        return _stat(stats, "PASSING:ATT")
    if token == "INT":
        return _stat(stats, "PASSING:INT")
    if token == "RUSHYDS":
        return _stat(stats, "RUSHING:YDS")
    if token == "RUSHATT":
        return _stat(stats, "RUSHING:CAR", "RUSHING:ATT")
    if token == "RECYDS":
        return _stat(stats, "RECEIVING:YDS")
    if token == "REC":
        return _stat(stats, "RECEIVING:REC")
    if token == "FGM":
        return _stat(stats, "KICKING:FG")
    if token == "KICKPTS":
        return _stat(stats, "KICKING:PTS")
    if token == "SACKS":
        return _stat(stats, "DEFENSIVE:SACK", "DEFENSIVE:SK")
    if token in {"TKLAST", "TACKLESASSISTS", "DEFENSIVETACKLESASSISTS", "TACKLES"}:
        return _stat(stats, "DEFENSIVE:TOT", "DEFENSIVE:TKL", "DEFENSIVE:TACKLESASSISTS")
    if token in {"ASSISTS", "TACKLEASSISTS", "DEFENSIVEASSISTS"}:
        return _stat(stats, "DEFENSIVE:AST", "DEFENSIVE:ASSISTS")
    if token in {"PASSINGCOMPLETIONS", "COMPLETIONS", "COMP"}:
        return _stat(stats, "PASSING:CMP", "PASSING:COMP")
    if token in {"RUSHRECYDS", "RUSHINGRECEIVINGYARDS"}:
        rush = _stat(stats, "RUSHING:YDS")
        rec = _stat(stats, "RECEIVING:YDS")
        if rush is None or rec is None:
            return None
        return rush + rec
    if token in {"PASSRUSHYDS", "PASSINGRUSHINGYARDS"}:
        passing = _stat(stats, "PASSING:YDS")
        rush = _stat(stats, "RUSHING:YDS")
        if passing is None or rush is None:
            return None
        return passing + rush
    if token in {"ANYTIMETD", "FIRSTTD"}:
        # Anytime TD scorer: rush + receiving (+ return) TDs. Passing TDs do not count.
        has_any = False
        total = 0.0
        for key in (
            "RUSHING:TD",
            "RECEIVING:TD",
            "RETURNING:TD",
            "KICKRETURNS:TD",
            "PUNTRETURNS:TD",
        ):
            value = _stat(stats, key)
            if value is not None:
                has_any = True
                total += value
        return total if has_any else None
    return None


def grade_side(actual: float, line: float, position: str) -> str:
    """Grade OVER/UNDER/YES against an actual. Returns W, L, or P."""
    side = str(position or "").strip().upper()
    if side in {"YES", "Y"}:
        return "W" if actual >= 1.0 else "L"
    if side == "OVER":
        if actual > line:
            return "W"
        if actual < line:
            return "L"
        return "P"
    if side == "UNDER":
        if actual < line:
            return "W"
        if actual > line:
            return "L"
        return "P"
    raise BoxScoreError(f"Unsupported settle position '{position}'")


def resolve_player_stats(
    event: NflBoxScoreEvent, player_name: str
) -> tuple[dict[str, float] | None, str | None]:
    """Resolve unique player stats by tokenized name. Skip on ambiguity or miss."""
    needle = _token(player_name)
    if not needle:
        return None, "empty_player_name"
    if needle in event.players:
        return event.players[needle], None
    hits = [
        (key, stats)
        for key, stats in event.players.items()
        if needle in key or key in needle or key.endswith(needle) or needle.endswith(key)
    ]
    if len(hits) == 1:
        return hits[0][1], None
    if not hits:
        return None, "player_not_in_boxscore"
    return None, "ambiguous_player_match"


def fetch_espn_scoreboard(event_date: date, *, timeout: int = 30) -> dict[str, Any]:
    """Fetch ESPN NFL scoreboard JSON for a date. May raise BoxScoreError (e.g. 403)."""
    url = f"{ESPN_NFL_BASE}/scoreboard?dates={event_date.strftime('%Y%m%d')}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise BoxScoreError(
            f"ESPN NFL scoreboard blocked or failed ({exc.code}) for {event_date.isoformat()}"
        ) from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise BoxScoreError(
            f"ESPN NFL scoreboard request failed for {event_date.isoformat()}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BoxScoreError("ESPN scoreboard returned a non-object")
    return payload


__all__ = [
    "BoxScoreError",
    "NflBoxScoreEvent",
    "fetch_espn_scoreboard",
    "grade_side",
    "parse_espn_boxscore_players",
    "parse_simplified_events",
    "player_actual",
    "resolve_player_stats",
]
