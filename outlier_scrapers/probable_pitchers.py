"""Probable-pitchers scraper.

Fetches MLB probable starting pitchers from the public MLB Stats API
(statsapi.mlb.com — no auth required) and normalizes them into a per-team
lookup. game_totals.py reads that lookup to flag totals rows whose starter is
not yet confirmed, replacing the ad-hoc web search a reasoning pass otherwise
has to do per game.

Only MLB has a wired source today; other leagues (e.g. WNBA) don't have
starting pitchers, so ``export_probable_pitchers`` returns a no-op "skipped"
status for them rather than failing the pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .paths import league_paths
from .registry import get_sport_config, normalize_team

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
REQUEST_TIMEOUT = 15


def _fetch_json(url: str, *, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "outlier-scrapers/1.0"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https host)
        return json.loads(response.read().decode("utf-8"))


def fetch_probable_pitchers_raw(target_date: date) -> dict[str, Any]:
    """Fetch the raw MLB Stats API schedule payload, hydrated with probable pitchers."""
    url = (
        f"{MLB_SCHEDULE_URL}?sportId=1&date={target_date.isoformat()}"
        "&hydrate=probablePitcher,team"
    )
    return _fetch_json(url)


def _slate_is_complete(raw_payload: dict[str, Any]) -> bool:
    """True when the fetched date's games are all Final (or none are scheduled).

    Mirrors ``games._today_slate_is_complete``: a pipeline run late in the day
    (after the local slate has finished) must not keep matching starters
    against a day that's already over while props/cards have already
    auto-advanced to tomorrow's board.
    """
    dates = raw_payload.get("dates")
    games = dates[0].get("games") if isinstance(dates, list) and dates else []
    if not isinstance(games, list) or not games:
        return True
    return all(
        str((game.get("status") or {}).get("abstractGameState") or "").strip().lower() == "final"
        for game in games
        if isinstance(game, dict)
    )


def _pitcher_from_side(side_payload: dict[str, Any]) -> tuple[str | None, bool, int | None]:
    pitcher = side_payload.get("probablePitcher")
    if not isinstance(pitcher, dict):
        return None, False, None
    name = str(pitcher.get("fullName") or "").strip()
    raw_id = pitcher.get("id")
    pitcher_id: int | None
    try:
        pitcher_id = int(raw_id) if raw_id not in (None, "") else None
    except (TypeError, ValueError):
        pitcher_id = None
    return (name or None), bool(name), pitcher_id


def normalize_probable_pitchers(
    raw_payload: dict[str, Any], *, league: str = "MLB"
) -> dict[str, Any]:
    """Return ``{generated_at, date, record_count, games, by_team}``.

    ``by_team`` is keyed by the same canonical team code used elsewhere in the
    pipeline (e.g. ``registry.MLB_TEAM_ALIASES``), so callers can look a team
    up without re-deriving its abbreviation.
    """
    config = get_sport_config(league)
    games: list[dict[str, Any]] = []
    by_team: dict[str, dict[str, Any]] = {}

    dates = raw_payload.get("dates")
    schedule_date = ""
    raw_games: list[dict[str, Any]] = []
    if isinstance(dates, list) and dates:
        schedule_date = str(dates[0].get("date") or "")
        raw_games = dates[0].get("games") or []

    for raw_game in raw_games:
        teams = raw_game.get("teams") or {}
        away = teams.get("away") or {}
        home = teams.get("home") or {}

        away_team_raw = (away.get("team") or {}).get("name") or ""
        home_team_raw = (home.get("team") or {}).get("name") or ""
        away_team = normalize_team(config, away_team_raw) or ""
        home_team = normalize_team(config, home_team_raw) or ""

        away_pitcher, away_confirmed, away_pitcher_id = _pitcher_from_side(away)
        home_pitcher, home_confirmed, home_pitcher_id = _pitcher_from_side(home)

        games.append(
            {
                "game_pk": raw_game.get("gamePk"),
                "scheduled_time": raw_game.get("gameDate"),
                "away_team": away_team,
                "away_team_raw": away_team_raw,
                "away_pitcher": away_pitcher,
                "away_pitcher_id": away_pitcher_id,
                "away_pitcher_confirmed": away_confirmed,
                "home_team": home_team,
                "home_team_raw": home_team_raw,
                "home_pitcher": home_pitcher,
                "home_pitcher_id": home_pitcher_id,
                "home_pitcher_confirmed": home_confirmed,
            }
        )

        if away_team:
            by_team[away_team] = {
                "pitcher": away_pitcher,
                "pitcher_id": away_pitcher_id,
                "confirmed": away_confirmed,
                "opponent": home_team,
                "home_away": "AWAY",
                "game_pk": raw_game.get("gamePk"),
                "scheduled_time": raw_game.get("gameDate"),
            }
        if home_team:
            by_team[home_team] = {
                "pitcher": home_pitcher,
                "pitcher_id": home_pitcher_id,
                "confirmed": home_confirmed,
                "opponent": away_team,
                "home_away": "HOME",
                "game_pk": raw_game.get("gamePk"),
                "scheduled_time": raw_game.get("gameDate"),
            }

    return {
        "league": league,
        "date": schedule_date,
        "generated_at": datetime.now().astimezone().isoformat(),
        "record_count": len(games),
        "games": games,
        "by_team": by_team,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def export_probable_pitchers(
    league: str = "MLB",
    *,
    target_date: date | None = None,
    fetch_json: Any | None = None,
) -> dict[str, Any]:
    """Fetch, normalize, and write probable-pitcher data for one league."""
    if league.strip().upper() != "MLB":
        return {
            "status": "skipped",
            "reason": f"no probable-pitcher source for {league}",
            "record_count": 0,
        }

    auto_advance = target_date is None
    if target_date is None:
        target_date = datetime.now().astimezone().date()

    paths_for_league = league_paths(league)

    try:
        raw_payload = fetch_probable_pitchers_raw(target_date)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        logger.error("Failed to fetch probable pitchers for %s: %s", league, exc)
        return {"status": "error", "reason": str(exc)[:200], "record_count": 0}

    if auto_advance and _slate_is_complete(raw_payload):
        next_date = target_date + timedelta(days=1)
        try:
            next_payload = fetch_probable_pitchers_raw(next_date)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            logger.warning(
                "Auto-advance probable-pitcher fetch failed for %s %s: %s",
                league,
                next_date,
                exc,
            )
        else:
            next_dates = next_payload.get("dates")
            if isinstance(next_dates, list) and next_dates and next_dates[0].get("games"):
                raw_payload = next_payload
                target_date = next_date

    raw_out = {
        "source": "statsapi.mlb.com",
        "date": target_date.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "payload": raw_payload,
    }
    write_json(
        paths_for_league.timestamped(paths_for_league.raw, "probable_pitchers_raw"), raw_out
    )

    normalized = normalize_probable_pitchers(raw_payload, league=league)
    from .projections import enrich_probable_with_so_features

    enriched_by_team = enrich_probable_with_so_features(
        normalized.get("by_team") or {},
        season=target_date.year,
        fetch_json=fetch_json,
    )
    normalized = {**normalized, "by_team": enriched_by_team}
    write_json(paths_for_league.probable_pitchers_latest(), normalized)
    write_json(
        paths_for_league.timestamped(paths_for_league.normalized, "probable_pitchers"),
        normalized,
    )

    return {"status": "ok", "record_count": normalized["record_count"]}


def load_probable_pitcher_lookup(league: str) -> dict[str, dict[str, Any]]:
    """Return the ``{team_code: {pitcher, confirmed, opponent, ...}}`` lookup.

    Returns ``{}`` when no probable-pitchers file exists yet for this league
    (not yet scraped, or a league with no such source) rather than raising —
    callers treat a missing lookup as "unknown", not as an error.
    """
    path = league_paths(league).probable_pitchers_latest()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    by_team = payload.get("by_team")
    return by_team if isinstance(by_team, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch MLB probable starting pitchers")
    parser.add_argument("--league", default="MLB")
    parser.add_argument("--date", help="Target date in YYYY-MM-DD format (default: today local)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            return 1

    status = export_probable_pitchers(args.league, target_date=target_date)
    if status["status"] == "skipped":
        print(f"{args.league} probable pitchers: skipped ({status['reason']})")
        return 0
    print(
        f"{args.league} probable pitchers [{status['status']}]: "
        f"exported {status['record_count']} records"
    )
    return 1 if status["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
