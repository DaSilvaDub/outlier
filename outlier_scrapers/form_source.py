from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"
MLB_API_BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_PATHS = {
    "MLB": "baseball/mlb",
    "WNBA": "basketball/wnba",
}
USER_AGENT = "outlier-results-collector/1.0"
TEAM_ALIASES = {
    "ARI": "AZ",
    "CHW": "CWS",
    "GSV": "GS",
    "KCR": "KC",
    "LAS": "LA",
    "LVA": "LV",
    "SDP": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "WSH": "WAS",
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
    player_teams: dict[str, str] = field(default_factory=dict)


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


_ESPN_TO_ALIAS = {espn: outlier for outlier, espn in TEAM_ALIASES.items()}


def canon_team(sport: str, value: Any) -> str:
    """Map ESPN abbreviations and display names onto Outlier aliases when known."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from outlier_scrapers.registry import get_sport_config, normalize_team

        config = get_sport_config(sport, allow_disabled=True)
        canonical = normalize_team(config, raw)
        if canonical:
            return canonical
        espn_token = _token(raw)
        mapped = _ESPN_TO_ALIAS.get(espn_token)
        if mapped:
            back = normalize_team(config, mapped)
            if back:
                return back
            return mapped
    except ValueError:
        pass
    return _team_token(raw)


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


def _parse_boxscore(payload: dict[str, Any]) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    raw_boxscore = payload.get("boxscore")
    boxscore: dict[str, Any] = raw_boxscore if isinstance(raw_boxscore, dict) else {}
    teams = boxscore.get("players")
    if not isinstance(teams, list):
        return {}, {}
    players: dict[str, dict[str, float]] = defaultdict(dict)
    player_teams: dict[str, str] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        team_alias = _competitor_alias({"team": team.get("team")}) or str(
            team.get("displayName") or team.get("name") or ""
        )
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
                player_key = _token(name)
                bucket = players[player_key]
                if team_alias:
                    player_teams[player_key] = team_alias
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
    return dict(players), player_teams


def _boxscore_players(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    players, _teams = _parse_boxscore(payload)
    return players


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


class UnsupportedSport(ResultsError):
    """Raised when a league is intentionally not collected in v1."""


def iter_recent_finals(sport: str, teams: set[str], n: int = 10, as_of: date | None = None) -> list[FinalEvent]:
    if not as_of:
        as_of = date.today()
    if sport == "MLB":
        raise UnsupportedSport("MLB is not in the v1 slate-strategy plan")

    sport_path = SPORT_PATHS.get(sport)
    if not sport_path:
        raise UnsupportedSport(f"{sport} is not in the v1 slate-strategy plan")

    wanted = {canon_team(sport, team) for team in teams if canon_team(sport, team)}
    events_by_team: dict[str, list[FinalEvent]] = defaultdict(list)
    all_events: dict[str, FinalEvent] = {}
    days_ok = 0
    days_failed = 0

    for days_back in range(1, 22):
        day = as_of - timedelta(days=days_back)
        scoreboard_url = f"{ESPN_BASE_URL}/{sport_path}/scoreboard?{urlencode({'dates': day.strftime('%Y%m%d')})}"
        try:
            payload = _fetch_json(scoreboard_url)
            day_events = _scoreboard_events(payload, sport, day)
            days_ok += 1
        except ResultsError:
            days_failed += 1
            continue

        for event in day_events:
            if event.provider_event_id in all_events:
                continue
            away_key = canon_team(sport, event.away)
            home_key = canon_team(sport, event.home)
            if away_key not in wanted and home_key not in wanted:
                continue

            summary_url = f"{ESPN_BASE_URL}/{sport_path}/summary?{urlencode({'event': event.provider_event_id})}"
            try:
                players, player_teams = _parse_boxscore(_fetch_json(summary_url))
            except ResultsError:
                players, player_teams = {}, {}

            enriched = FinalEvent(
                provider_event_id=event.provider_event_id,
                sport=event.sport,
                event_date=event.event_date,
                away=event.away,
                home=event.home,
                away_score=event.away_score,
                home_score=event.home_score,
                players=players,
                player_teams=player_teams,
            )
            all_events[event.provider_event_id] = enriched
            if away_key in wanted:
                events_by_team[away_key].append(enriched)
            if home_key in wanted:
                events_by_team[home_key].append(enriched)

        if wanted and all(len(events_by_team[team]) >= n for team in wanted):
            break

    if days_ok == 0 and days_failed > 0:
        raise ResultsError(f"every ESPN scoreboard day failed for {sport}")

    result_map: dict[str, FinalEvent] = {}
    for team_events in events_by_team.values():
        for ev in team_events[:n]:
            result_map[ev.provider_event_id] = ev
    return sorted(result_map.values(), key=lambda item: item.event_date)