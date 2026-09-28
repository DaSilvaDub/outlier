"""Build the prior-week matchup tape from nflverse box scores.

The matchup engine (``outlier_nfl.matchup``) grades each unit from
``data/NFL/tape/prior_week.json``. This module rebuilds that file from
nflverse's public release assets instead of hand-entered numbers:

- ``stats_team/stats_team_week_<season>.csv``: per-game team box stats
  (derived from official NFL play-by-play).
- ``schedules/games.csv``: game dates and final scores.

Each team row is the per-game average over every completed game before the
cutoff date, so a slate never sees its own results. ``pass_yards`` is gross
passing yards, matching the convention the engine thresholds were tuned on.
Role fields (``qb``, ``rb1``, ``te``, ``wr_slot``, ``wr_deep``) are carried
over from the existing tape so manual injury/depth edits survive a refresh.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.request import Request, urlopen

from outlier_nfl.config import normalize_team

logger = logging.getLogger(__name__)

NFLVERSE_RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
TEAM_WEEK_URL = NFLVERSE_RELEASES + "/stats_team/stats_team_week_{season}.csv"
SCHEDULES_URL = NFLVERSE_RELEASES + "/schedules/games.csv"

TAPE_NUMERIC_FIELDS: tuple[str, ...] = (
    "rush_yards",
    "pass_yards",
    "points",
    "opp_rush_yards_allowed",
    "opp_pass_yards_allowed",
    "opp_points_allowed",
    "sacks",
)
ROLE_FIELDS: tuple[str, ...] = ("qb", "rb1", "te", "wr_slot", "wr_deep")


def fetch_csv(url: str, timeout: float = 60.0) -> list[dict[str, str]]:
    """Download a CSV release asset (GitHub redirects are followed by urlopen)."""
    if not url.startswith("https://"):
        raise ValueError(f"Refusing non-https URL: {url!r}")
    req = Request(url, headers={"User-Agent": "outlier-nfl-tape/1.0"})  # nosec B310 - https only
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 - https only
        text = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _num(value: Any) -> float:
    if value in (None, "", "NA"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _team(code: Any) -> str:
    raw = str(code or "").strip().upper()
    return normalize_team(raw) or raw


def build_per_game(
    team_rows: Iterable[Mapping[str, str]],
    game_rows: Iterable[Mapping[str, str]],
    season: int,
    before: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Join team box rows with their opponent's row and the final score.

    Only regular-season games of ``season`` with a final score are kept, and
    when ``before`` is set only games played strictly before that date.
    """
    games: dict[str, Mapping[str, str]] = {}
    for g in game_rows:
        if str(g.get("season")) != str(season) or g.get("game_type") != "REG":
            continue
        if g.get("home_score") in (None, "", "NA") or g.get("away_score") in (None, "", "NA"):
            continue
        if before is not None:
            try:
                if date.fromisoformat(str(g.get("gameday"))) >= before:
                    continue
            except ValueError:
                continue
        games[str(g["game_id"])] = g

    rows = [
        r
        for r in team_rows
        if r.get("season_type") == "REG" and str(r.get("game_id")) in games
    ]
    by_key = {(str(r["game_id"]), str(r["team"])): r for r in rows}

    per_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        game = games[str(r["game_id"])]
        opp = by_key.get((str(r["game_id"]), str(r["opponent_team"])))
        if opp is None:
            logger.warning("Missing opponent row for %s in %s", r["team"], r["game_id"])
            continue
        is_home = game.get("home_team") == r["team"]
        points = _num(game["home_score"] if is_home else game["away_score"])
        opp_points = _num(game["away_score"] if is_home else game["home_score"])
        per_game[_team(r["team"])].append(
            {
                "week": int(_num(r.get("week"))),
                "opponent": _team(r["opponent_team"]),
                "rush_yards": _num(r.get("rushing_yards")),
                "pass_yards": _num(r.get("passing_yards")),
                "points": points,
                "opp_rush_yards_allowed": _num(opp.get("rushing_yards")),
                "opp_pass_yards_allowed": _num(opp.get("passing_yards")),
                "opp_points_allowed": opp_points,
                "sacks": _num(r.get("def_sacks")),
            }
        )
    for games_list in per_game.values():
        games_list.sort(key=lambda x: x["week"])
    return dict(per_game)


def aggregate_tape(
    per_game: Mapping[str, list[dict[str, Any]]],
    last_n: int | None = None,
    roles: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Average each team's games (optionally only the last ``last_n``)."""
    teams: dict[str, dict[str, Any]] = {}
    for team, games in sorted(per_game.items()):
        window = games[-last_n:] if last_n else games
        if not window:
            continue
        row: dict[str, Any] = {
            k: v for k, v in (roles or {}).get(team, {}).items() if k in ROLE_FIELDS and v
        }
        for field in TAPE_NUMERIC_FIELDS:
            row[field] = round(sum(g[field] for g in window) / len(window), 1)
        row["games"] = len(window)
        row["weeks"] = [g["week"] for g in window]
        row["opponents"] = [g["opponent"] for g in window]
        teams[team] = row
    return teams


def load_existing_roles(path: Path) -> dict[str, dict[str, Any]]:
    """Role fields from an existing tape file; empty when absent or unreadable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    teams = raw.get("teams", raw) if isinstance(raw, dict) else {}
    if not isinstance(teams, dict):
        return {}
    return {
        _team(code): {k: row[k] for k in ROLE_FIELDS if row.get(k)}
        for code, row in teams.items()
        if isinstance(row, dict)
    }


def build_tape_payload(
    season: int,
    before: date | None = None,
    last_n: int | None = None,
    roles: Mapping[str, Mapping[str, Any]] | None = None,
    team_rows: list[dict[str, str]] | None = None,
    game_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Fetch (unless rows are supplied) and assemble the tape JSON payload."""
    if team_rows is None:
        team_rows = fetch_csv(TEAM_WEEK_URL.format(season=season))
    if game_rows is None:
        game_rows = fetch_csv(SCHEDULES_URL)
    per_game = build_per_game(team_rows, game_rows, season, before)
    teams = aggregate_tape(per_game, last_n=last_n, roles=roles)
    weeks = sorted({w for row in teams.values() for w in row["weeks"]})
    return {
        "season": season,
        "week": f"{weeks[0]}-{weeks[-1]}" if weeks else None,
        "before": before.isoformat() if before else None,
        "last_n": last_n,
        "source": (
            "nflverse-data stats_team_week + schedules; per-game averages; "
            "pass_yards = gross passing"
        ),
        "teams": teams,
    }


def write_tape(path: Path, payload: Mapping[str, Any]) -> Path | None:
    """Atomically write ``payload``; keep the previous file as ``<name>.prev.json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        backup = path.with_name(path.stem + ".prev" + path.suffix)
        backup.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return backup


def refresh_prior_week_tape(
    nfl_dir: Path,
    season: int,
    before: date | None = None,
    last_n: int | None = None,
) -> Path:
    """Rebuild ``<nfl_dir>/tape/prior_week.json`` in place and return its path."""
    path = Path(nfl_dir) / "tape" / "prior_week.json"
    payload = build_tape_payload(
        season, before=before, last_n=last_n, roles=load_existing_roles(path)
    )
    if not payload["teams"]:
        raise ValueError(f"nflverse returned no completed {season} games before {before}")
    write_tape(path, payload)
    logger.info(
        "Refreshed matchup tape %s: %d teams, weeks %s",
        path,
        len(payload["teams"]),
        payload["week"],
    )
    return path
