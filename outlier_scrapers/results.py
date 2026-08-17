"""Automatic completed-game settlement collection.

The collector reads only unsettled ledger decisions, matches them to completed
MLB Stats API or ESPN WNBA events by sport/date/team identity, and grades
supported game, team, and player markets from final scores and box scores.
Every uncertain identity or unsupported statistic is skipped rather than
guessed.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from . import feedback

logger = logging.getLogger(__name__)

ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"
MLB_API_BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_PATHS = {
    "MLB": "baseball/mlb",
    "WNBA": "basketball/wnba",
}
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1] / "calibration" / "settlements" / "generated"
)
USER_AGENT = "outlier-results-collector/1.0"
EASTERN = ZoneInfo("America/New_York")
TEAM_ALIASES = {
    "ARI": "AZ",
    "CHW": "CWS",
    "CWS": "CWS",
    "GSV": "GS",
    "KCR": "KC",
    "LAS": "LA",
    "LVA": "LV",
    "PDX": "POR",
    "POR": "POR",
    "SDP": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "WAS": "WAS",
    "WSH": "WAS",
    "WSN": "WAS",
}


class ResultsError(RuntimeError):
    """Raised when the result provider contract cannot be consumed safely."""


@dataclass(frozen=True)
class FinalEvent:
    provider_event_id: str
    sport: str
    event_date: date
    away: str
    home: str
    away_score: float
    home_score: float
    players: dict[str, dict[str, float]]


def _fetch_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ResultsError(f"Results request failed for {url.split('?')[0]}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResultsError(f"Results provider returned a non-object for {url.split('?')[0]}")
    return payload


def _token(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^A-Z0-9]", "", text.encode("ascii", "ignore").decode().upper())


def _team_token(value: Any) -> str:
    token = _token(value)
    return TEAM_ALIASES.get(token, token)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _made(value: Any) -> float | None:
    """Parse either a scalar stat or the made side of an ESPN ``made-attempted`` stat."""
    text = str(value or "").strip()
    if "-" in text:
        text = text.split("-", 1)[0]
    return _number(text)


def _outs_from_ip(value: Any) -> float | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+)(?:\.(\d))?", text)
    if not match:
        return None
    whole = int(match.group(1))
    remainder = int(match.group(2) or 0)
    if remainder not in {0, 1, 2}:
        return None
    return float(whole * 3 + remainder)


def _competitor_alias(competitor: dict[str, Any]) -> str:
    raw_team = competitor.get("team")
    team: dict[str, Any] = raw_team if isinstance(raw_team, dict) else {}
    return str(team.get("abbreviation") or team.get("shortDisplayName") or team.get("name") or "")


def _scoreboard_events(payload: dict[str, Any], sport: str, event_date: date) -> list[FinalEvent]:
    events = payload.get("events")
    if not isinstance(events, list):
        raise ResultsError("Scoreboard payload is missing an events list")
    results: list[FinalEvent] = []
    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue
        raw_status = raw_event.get("status")
        status: dict[str, Any] = raw_status if isinstance(raw_status, dict) else {}
        raw_status_type = status.get("type")
        status_type: dict[str, Any] = raw_status_type if isinstance(raw_status_type, dict) else {}
        competitions = raw_event.get("competitions")
        competition = competitions[0] if isinstance(competitions, list) and competitions else {}
        if not isinstance(competition, dict):
            continue
        raw_comp_status = competition.get("status")
        comp_status: dict[str, Any] = raw_comp_status if isinstance(raw_comp_status, dict) else {}
        raw_comp_type = comp_status.get("type")
        comp_type: dict[str, Any] = raw_comp_type if isinstance(raw_comp_type, dict) else {}
        if not bool(status_type.get("completed") or comp_type.get("completed")):
            continue
        sides: dict[str, tuple[str, float]] = {}
        for competitor in competition.get("competitors") or []:
            if not isinstance(competitor, dict):
                continue
            home_away = str(competitor.get("homeAway") or "").lower()
            score = _number(competitor.get("score"))
            alias = _competitor_alias(competitor)
            if home_away in {"home", "away"} and alias and score is not None:
                sides[home_away] = (alias, score)
        event_id = str(raw_event.get("id") or competition.get("id") or "").strip()
        if event_id and set(sides) == {"home", "away"}:
            results.append(
                FinalEvent(
                    provider_event_id=event_id,
                    sport=sport,
                    event_date=event_date,
                    away=sides["away"][0],
                    home=sides["home"][0],
                    away_score=sides["away"][1],
                    home_score=sides["home"][1],
                    players={},
                )
            )
    return results


def _boxscore_players(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    raw_boxscore = payload.get("boxscore")
    boxscore: dict[str, Any] = raw_boxscore if isinstance(raw_boxscore, dict) else {}
    teams = boxscore.get("players")
    if not isinstance(teams, list):
        return {}
    players: dict[str, dict[str, float]] = defaultdict(dict)
    for team in teams:
        if not isinstance(team, dict):
            continue
        groups = team.get("statistics")
        if isinstance(groups, dict):
            groups = [groups]
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = _token(
                group.get("type") or group.get("name") or group.get("displayName") or "STATS"
            )
            labels = group.get("labels") or group.get("names") or []
            if not isinstance(labels, list):
                continue
            for athlete_row in group.get("athletes") or []:
                if not isinstance(athlete_row, dict):
                    continue
                raw_athlete = athlete_row.get("athlete")
                athlete: dict[str, Any] = (
                    raw_athlete if isinstance(raw_athlete, dict) else athlete_row
                )
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
                    if key in {"IP", "INNINGSPITCHED"}:
                        outs = _outs_from_ip(value)
                        if outs is not None:
                            bucket["OUTS"] = outs
                            bucket[f"{group_name}:OUTS"] = outs
    return dict(players)


def _mlb_boxscore_players(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return {}
    players: dict[str, dict[str, float]] = defaultdict(dict)
    for side in ("away", "home"):
        team = teams.get(side)
        if not isinstance(team, dict) or not isinstance(team.get("players"), dict):
            continue
        for player in team["players"].values():
            if not isinstance(player, dict):
                continue
            person = _mapping(player.get("person"))
            name = str(person.get("fullName") or "").strip()
            stats = _mapping(player.get("stats"))
            if not name:
                continue
            bucket = players[_token(name)]
            batting = _mapping(stats.get("batting"))
            pitching = _mapping(stats.get("pitching"))
            batting_fields = {
                "H": "hits",
                "R": "runs",
                "RBI": "rbi",
                "BB": "baseOnBalls",
                "2B": "doubles",
                "TB": "totalBases",
            }
            pitching_fields = {"K": "strikeOuts", "ER": "earnedRuns"}
            for key, source in batting_fields.items():
                value = _number(batting.get(source))
                if value is not None:
                    bucket[f"BATTING:{key}"] = value
            for key, source in pitching_fields.items():
                value = _number(pitching.get(source))
                if value is not None:
                    bucket[f"PITCHING:{key}"] = value
            outs = _outs_from_ip(pitching.get("inningsPitched"))
            if outs is not None:
                bucket["PITCHING:OUTS"] = outs
    return dict(players)


def _mlb_events(
    payload: dict[str, Any],
    event_date: date,
    fetch_json: Callable[[str], dict[str, Any]],
) -> list[FinalEvent]:
    dates = payload.get("dates")
    if not isinstance(dates, list):
        raise ResultsError("MLB schedule payload is missing a dates list")
    events: list[FinalEvent] = []
    for date_row in dates:
        if not isinstance(date_row, dict):
            continue
        for game in date_row.get("games") or []:
            if not isinstance(game, dict):
                continue
            status = _mapping(game.get("status"))
            state = _token(status.get("abstractGameState") or status.get("detailedState"))
            if state not in {"FINAL", "GAMEOVER", "COMPLETEDEARLY"}:
                continue
            game_pk = str(game.get("gamePk") or "").strip()
            teams = _mapping(game.get("teams"))
            away = _mapping(teams.get("away"))
            home = _mapping(teams.get("home"))
            away_team = _mapping(away.get("team"))
            home_team = _mapping(home.get("team"))
            away_alias = str(away_team.get("abbreviation") or "").strip()
            home_alias = str(home_team.get("abbreviation") or "").strip()
            away_score = _number(away.get("score"))
            home_score = _number(home.get("score"))
            if (
                not game_pk
                or not away_alias
                or not home_alias
                or away_score is None
                or home_score is None
            ):
                continue
            boxscore = fetch_json(f"{MLB_API_BASE_URL}/game/{game_pk}/boxscore")
            events.append(
                FinalEvent(
                    provider_event_id=game_pk,
                    sport="MLB",
                    event_date=event_date,
                    away=away_alias,
                    home=home_alias,
                    away_score=away_score,
                    home_score=home_score,
                    players=_mlb_boxscore_players(boxscore),
                )
            )
    return events


def _player_boxscore_key(event: FinalEvent, selection: str) -> str | None:
    """Resolve one boxscore player key for a selection, or None if ambiguous."""
    raw_name = selection.split(" - ", 1)[0].strip()
    parts = raw_name.split()
    full = _token(raw_name)
    if full and full in event.players:
        return full
    if len(parts) < 2:
        return None
    first = _token(parts[0])
    last = _token(parts[-1])
    if not first or len(last) < 4:
        return None
    matches = [
        name for name in event.players if name.endswith(last) and name.startswith(first)
    ]
    return matches[0] if len(matches) == 1 else None


def _player_event_candidates(events: Sequence[FinalEvent], selection: str) -> list[FinalEvent]:
    """Match a player-prop selection to exactly one completed event when possible.

    Exact normalized full-name hits win. If none, a unique first-initial /
    first-name + last-name key on a single event is accepted. Ambiguous
    collisions stay unmatched.
    """
    return [event for event in events if _player_boxscore_key(event, selection)]


def _event_match(event: FinalEvent, selection: str) -> bool:
    match = re.search(r"\b([A-Za-z0-9]+)\s+@\s+([A-Za-z0-9]+)\b", selection)
    return bool(
        match
        and _team_token(match.group(1)) == _team_token(event.away)
        and _team_token(match.group(2)) == _team_token(event.home)
    )


def _team_total_event_match(event: FinalEvent, selection: str) -> bool:
    match = re.search(r"^([A-Za-z0-9]+)\s+Team Total\b", selection, re.IGNORECASE)
    return bool(
        match and _team_token(match.group(1)) in {_team_token(event.away), _team_token(event.home)}
    )


def _side_result(actual: float, line: float, side: str) -> str:
    if actual == line:
        return "PUSH"
    won = actual > line if side == "OVER" else actual < line
    return "W" if won else "L"


def _player_actual(market: str, stats: dict[str, float], sport: str) -> float | None:
    token = _token(market)
    if sport == "WNBA":
        components = {
            "POINTS": ("PTS",),
            "PTS": ("PTS",),
            "REBOUNDS": ("REB",),
            "REB": ("REB",),
            "ASSISTS": ("AST",),
            "AST": ("AST",),
            "THREEPOINTERS": ("3PT",),
            "3PTS": ("3PT",),
            "OFFENSIVEREBOUNDS": ("OREB",),
            "OREB": ("OREB",),
            "DEFENSIVEREBOUNDS": ("DREB",),
            "DREB": ("DREB",),
            "STEALS": ("STL",),
            "STL": ("STL",),
            "BLOCKS": ("BLK",),
            "BLK": ("BLK",),
            "TURNOVERS": ("TO",),
            "TO": ("TO",),
            "POINTSREBOUNDS": ("PTS", "REB"),
            "PR": ("PTS", "REB"),
            "POINTSASSISTS": ("PTS", "AST"),
            "PA": ("PTS", "AST"),
            "REBOUNDSASSISTS": ("REB", "AST"),
            "RA": ("REB", "AST"),
            "PTSREBAST": ("PTS", "REB", "AST"),
            "PRA": ("PTS", "REB", "AST"),
        }
        keys = components.get(token)
        if token in {"DOUBLEDOUBLE", "DD"}:
            categories = [stats.get(key) for key in ("PTS", "REB", "AST", "STL", "BLK")]
            return float(sum(value is not None and value >= 10 for value in categories) >= 2)
        if not keys:
            return None
        values: list[float] = []
        for key in keys:
            if key == "3PT":
                value = stats.get("3PT", stats.get("3PM", stats.get("3PMA")))
            else:
                value = stats.get(key)
            if value is None:
                return None
            values.append(value)
        return sum(values)

    aliases = {
        "STRIKEOUTS": ("PITCHING:K", "PITCHING:SO", "K", "SO"),
        "SO": ("PITCHING:K", "PITCHING:SO", "K", "SO"),
        "HITS": ("BATTING:H", "H"),
        "H": ("BATTING:H", "H"),
        "TOTALBASES": ("BATTING:TB", "TB"),
        "TB": ("BATTING:TB", "TB"),
        "OUTS": ("PITCHING:OUTS", "OUTS"),
        "DOUBLES": ("BATTING:2B", "2B"),
        "2B": ("BATTING:2B", "2B"),
        "EARNEDRUNS": ("PITCHING:ER", "ER"),
        "ER": ("PITCHING:ER", "ER"),
        "BATTINGWALKS": ("BATTING:BB",),
        "WALKS": ("BATTING:BB",),
        "BB": ("BATTING:BB",),
        "BASES": ("BATTING:TB",),
    }
    if token in {"HITSRUNSRBIS", "HRR"}:
        hits = stats.get("BATTING:H")
        runs = stats.get("BATTING:R")
        rbis = stats.get("BATTING:RBI")
        return None if hits is None or runs is None or rbis is None else hits + runs + rbis
    for key in aliases.get(token, ()):
        if key in stats:
            return stats[key]
    return None


def _grade_row(row: sqlite3.Row, event: FinalEvent) -> tuple[float, str] | None:
    selection = str(row["selection"] or "")
    line = _number(row["line"])
    if line is None:
        return None
    market_type = _token(row["market_type"])
    player_match = re.fullmatch(
        r"(.+?)\s+-\s+(.+?)\s+(OVER|UNDER)\s+(-?\d+(?:\.\d+)?)", selection, re.IGNORECASE
    )
    if player_match and (
        market_type == "PLAYERPROP" or market_type not in {"GAMELINE", "TEAMPROP"}
    ):
        player_key = _player_boxscore_key(event, selection) or _token(player_match.group(1))
        stats = event.players.get(player_key)
        if not stats:
            return None
        actual = _player_actual(player_match.group(2), stats, event.sport)
        return (
            None
            if actual is None
            else (actual, _side_result(actual, line, player_match.group(3).upper()))
        )

    team_total = re.search(r"^([A-Za-z0-9]+)\s+Team Total\s+(OVER|UNDER)", selection, re.IGNORECASE)
    if team_total:
        team = _team_token(team_total.group(1))
        actual = (
            event.away_score
            if team == _team_token(event.away)
            else event.home_score
            if team == _team_token(event.home)
            else None
        )
        return (
            None
            if actual is None
            else (actual, _side_result(actual, line, team_total.group(2).upper()))
        )

    if not _event_match(event, selection):
        return None
    total = re.search(r"\b(?:Total O/U|TOTAL)\s+(OVER|UNDER)\b", selection, re.IGNORECASE)
    if total:
        actual = event.away_score + event.home_score
        return actual, _side_result(actual, line, total.group(1).upper())
    spread = re.search(r"\b(?:Spread|Run Line)\s+(HOME|AWAY)\b", selection, re.IGNORECASE)
    if spread:
        selected_score, other_score = (
            (event.home_score, event.away_score)
            if spread.group(1).upper() == "HOME"
            else (event.away_score, event.home_score)
        )
        margin = selected_score - other_score
        adjusted = margin + line
        result = "PUSH" if adjusted == 0 else "W" if adjusted > 0 else "L"
        return margin, result
    moneyline = re.search(r"\bMoney Line\s+(HOME|AWAY)\b", selection, re.IGNORECASE)
    if moneyline:
        selected_score, other_score = (
            (event.home_score, event.away_score)
            if moneyline.group(1).upper() == "HOME"
            else (event.away_score, event.home_score)
        )
        result = (
            "PUSH"
            if selected_score == other_score
            else "W"
            if selected_score > other_score
            else "L"
        )
        return selected_score - other_score, result
    return None


def _pending_rows(
    conn: sqlite3.Connection,
    leagues: Iterable[str],
    cutoff: datetime,
    upper_bound: datetime,
) -> list[sqlite3.Row]:
    league_tokens = sorted(
        {str(league).strip().upper() for league in leagues if str(league).strip()}
    )
    if not league_tokens:
        return []
    placeholders = ",".join("?" for _ in league_tokens)
    query = f"""
        SELECT d.decision_id, d.snapshot_id, s.sport, s.event_id, s.market_id,
               s.outcome_id, s.selection, s.line, s.price, s.book, s.player_id,
               s.market_type, s.event_starts_at, s.captured_at
        FROM decisions d
        JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
        LEFT JOIN settlements x ON x.decision_id = d.decision_id
        WHERE x.settlement_id IS NULL AND UPPER(s.sport) IN ({placeholders})
          AND s.event_starts_at >= ? AND s.event_starts_at <= ?
        ORDER BY s.event_starts_at, s.event_id, s.market_id
    """
    return conn.execute(
        query, (*league_tokens, cutoff.isoformat(), upper_bound.isoformat())
    ).fetchall()


def _latest_local_close(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[Any, Any]:
    close = conn.execute(
        """
        SELECT line, price FROM market_snapshots
        WHERE event_id = ? AND outcome_id = ? AND book = ?
          AND captured_at <= event_starts_at
        ORDER BY captured_at DESC LIMIT 1
        """,
        (row["event_id"], row["outcome_id"], row["book"]),
    ).fetchone()
    return (close["line"], close["price"]) if close else (None, None)


def collect_settlement_rows(
    conn: sqlite3.Connection,
    leagues: Iterable[str],
    *,
    lookback_days: int = 3,
    now: datetime | None = None,
    fetch_json: Callable[[str], dict[str, Any]] = _fetch_json,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if lookback_days < 1 or lookback_days > 30:
        raise ResultsError("lookback_days must be between 1 and 30")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pending = _pending_rows(conn, leagues, current - timedelta(days=lookback_days), current)
    by_sport_date: dict[tuple[str, date], list[sqlite3.Row]] = defaultdict(list)
    invalid_time = 0
    for row in pending:
        try:
            start = datetime.fromisoformat(str(row["event_starts_at"]).replace("Z", "+00:00"))
            if start.tzinfo is None:
                raise ValueError
        except ValueError:
            invalid_time += 1
            continue
        by_sport_date[(str(row["sport"]).upper(), start.astimezone(EASTERN).date())].append(row)

    settlements: list[dict[str, Any]] = []
    summary = {
        "pending_count": len(pending),
        "settlement_rows": 0,
        "unmatched_event_count": 0,
        "unsupported_count": 0,
        "invalid_time_count": invalid_time,
        "provider_event_count": 0,
    }
    for (sport, day), rows in by_sport_date.items():
        sport_path = SPORT_PATHS.get(sport)
        if not sport_path:
            summary["unsupported_count"] += len(rows)
            continue
        if sport == "MLB":
            schedule_url = (
                f"{MLB_API_BASE_URL}/schedule?"
                f"{urlencode({'sportId': 1, 'date': day.isoformat(), 'hydrate': 'team'})}"
            )
            events = _mlb_events(fetch_json(schedule_url), day, fetch_json)
        else:
            scoreboard_url = (
                f"{ESPN_BASE_URL}/{sport_path}/scoreboard?"
                f"{urlencode({'dates': day.strftime('%Y%m%d')})}"
            )
            events = _scoreboard_events(fetch_json(scoreboard_url), sport, day)
        summary["provider_event_count"] += len(events)
        needs_players = sport != "MLB" and any(" - " in str(row["selection"] or "") for row in rows)
        if needs_players:
            enriched: list[FinalEvent] = []
            for event in events:
                summary_url = f"{ESPN_BASE_URL}/{sport_path}/summary?{urlencode({'event': event.provider_event_id})}"
                players = _boxscore_players(fetch_json(summary_url))
                enriched.append(FinalEvent(**{**event.__dict__, "players": players}))
            events = enriched

        for row in rows:
            selection = str(row["selection"] or "")
            if " Team Total " in selection:
                candidates = [
                    event for event in events if _team_total_event_match(event, selection)
                ]
            else:
                candidates = [event for event in events if _event_match(event, selection)]
            if " - " in selection:
                candidates = _player_event_candidates(events, selection)
            if len(candidates) != 1:
                summary["unmatched_event_count"] += 1
                continue
            graded = _grade_row(row, candidates[0])
            if graded is None:
                summary["unsupported_count"] += 1
                continue
            actual, result = graded
            closing_line, closing_price = _latest_local_close(conn, row)
            settlements.append(
                {
                    "decision_id": row["decision_id"],
                    "snapshot_id": row["snapshot_id"],
                    "outcome_id": row["outcome_id"],
                    "event_id": row["event_id"],
                    "market_id": row["market_id"],
                    "actual_result": str(int(actual))
                    if float(actual).is_integer()
                    else str(actual),
                    "win_loss_push": result,
                    "closing_line": closing_line,
                    "closing_price": closing_price,
                }
            )
    summary["settlement_rows"] = len(settlements)
    return settlements, summary


def _write_audit(rows: list[dict[str, Any]], output_dir: Path, now: datetime) -> Path | None:
    if not rows:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"settlements_{now.strftime('%Y%m%dT%H%M%S%fZ')}.csv"
    fields = [
        "decision_id",
        "snapshot_id",
        "outcome_id",
        "event_id",
        "market_id",
        "actual_result",
        "win_loss_push",
        "closing_line",
        "closing_price",
    ]
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def collect_and_import(
    db_path: Path = feedback.DEFAULT_DB_PATH,
    *,
    leagues: Iterable[str] = ("MLB", "WNBA"),
    lookback_days: int = 3,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    now: datetime | None = None,
    fetch_json: Callable[[str], dict[str, Any]] = _fetch_json,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    feedback.initialize_database(db_path)
    with feedback.open_database(db_path) as conn:
        rows, summary = collect_settlement_rows(
            conn, leagues, lookback_days=lookback_days, now=current, fetch_json=fetch_json
        )
        imported = (
            feedback.import_settlements(conn, rows)
            if rows
            else {
                "unmatched_count": 0,
                "ambiguous_count": 0,
                "duplicate_count": 0,
                "updated_count": 0,
            }
        )
    audit = _write_audit(rows, output_dir, current)
    return {**summary, **imported, "audit_file": str(audit) if audit else None}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect and grade completed Outlier ledger rows")
    parser.add_argument("--db", type=Path, default=feedback.DEFAULT_DB_PATH)
    parser.add_argument("--leagues", default="MLB,WNBA")
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        result = collect_and_import(
            args.db,
            leagues=[token.strip().upper() for token in args.leagues.split(",")],
            lookback_days=args.lookback_days,
            output_dir=args.output,
        )
    except (OSError, sqlite3.Error, feedback.FeedbackError, ResultsError, ValueError) as exc:
        logger.error("Automatic result collection failed: %s", exc)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
