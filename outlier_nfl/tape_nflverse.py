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
Role fields (``qb``, ``rb1``, ``te``, ``wr_slot``, ``wr_deep``) come from the
latest nflverse depth chart on or before the slate date, skipping players
listed Out/Doubtful on that week's injury report; roles already in the tape
only fill gaps. The inactive list is stored under ``inactive`` so the pipeline
can keep those players out of matchup signals.

Two league-relative grades are added:

- ``pass_rush`` from PFR pressures per opponent dropback,
  ``62 + 15 * z`` (the engine's ``STRONG_PASS_RUSH_GRADE`` 70 is about the top
  30% of defenses); as an explicit grade it overrides the sacks fallback;
- ``qb_grade`` from the listed starter's play-weighted ESPN QBR,
  ``70 + 12 * z``. Informational: the weak-QB gate on the pass-rush signal was
  removed after a backtest showed it added nothing.

The raw ``pressure_rate`` and ``qbr`` are kept alongside for auditing. Teams
without data keep the engine's sack / pass-yard proxies.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import re
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
DEPTH_CHART_URL = NFLVERSE_RELEASES + "/depth_charts/depth_charts_{season}.csv"
INJURIES_URL = NFLVERSE_RELEASES + "/injuries/injuries_{season}.csv"
PFR_DEF_URL = NFLVERSE_RELEASES + "/pfr_advstats/advstats_week_def_{season}.csv"
QBR_URL = NFLVERSE_RELEASES + "/espn_data/qbr_week_level.csv"

PASS_RUSH_BASE, PASS_RUSH_PER_SD = 62.0, 15.0
QB_GRADE_BASE, QB_GRADE_PER_SD = 70.0, 12.0
MIN_QB_PLAYS = 20

INACTIVE_STATUSES: tuple[str, ...] = ("Out", "Doubtful")
# nflverse depth-chart pos_slot values for wide receivers: 1/2 outside (X/Z), 8 slot.
OUTSIDE_WR_SLOTS = {"1", "2"}
SLOT_WR_SLOTS = {"8"}
_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")

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


def fetch_bytes(url: str, timeout: float = 60.0) -> bytes:
    """Download a release asset over https (GitHub redirects are followed by urlopen)."""
    if not url.startswith("https://"):
        raise ValueError(f"Refusing non-https URL: {url!r}")
    req = Request(url, headers={"User-Agent": "outlier-nfl-tape/1.0"})  # nosec B310 - https only
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 - https only
        return resp.read()


def parse_csv_bytes(data: bytes) -> list[dict[str, str]]:
    """Parse CSV bytes, transparently gunzipping ``.csv.gz`` payloads."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def fetch_csv(url: str, timeout: float = 60.0) -> list[dict[str, str]]:
    """Download and parse a CSV (or gzipped CSV) release asset."""
    return parse_csv_bytes(fetch_bytes(url, timeout))


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
                "opp_dropbacks": _num(opp.get("attempts")) + _num(opp.get("sacks_suffered")),
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


def _name_key(name: Any) -> str:
    text = re.sub(r"[^a-z ]", "", str(name or "").lower().replace("-", " "))
    return " ".join(_SUFFIX_RE.sub("", text).split())


def slate_week(
    game_rows: Iterable[Mapping[str, str]], season: int, before: date | None
) -> int | None:
    """Week of the first regular-season game on/after ``before`` (None if unknown)."""
    weeks: list[int] = []
    for g in game_rows:
        if str(g.get("season")) != str(season) or g.get("game_type") != "REG":
            continue
        try:
            if before is not None and date.fromisoformat(str(g.get("gameday"))) < before:
                continue
            weeks.append(int(_num(g.get("week"))))
        except ValueError:
            continue
    return min(weeks) if weeks else None


def inactive_players(
    injury_rows: Iterable[Mapping[str, str]],
    week: int | None,
    statuses: Iterable[str] = INACTIVE_STATUSES,
) -> dict[str, list[dict[str, str]]]:
    """Players ruled ``statuses`` on the given week's report, per team.

    With ``week=None`` the latest reported week is used.
    """
    rows = [r for r in injury_rows if r.get("season_type", "REG") == "REG"]
    if week is None:
        reported = [int(_num(r.get("week"))) for r in rows if r.get("week")]
        week = max(reported) if reported else None
    wanted = set(statuses)
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        if week is None or int(_num(r.get("week"))) != week:
            continue
        if r.get("report_status") not in wanted:
            continue
        out[_team(r.get("team"))].append(
            {
                "name": str(r.get("full_name") or ""),
                "gsis_id": str(r.get("gsis_id") or ""),
                "status": str(r.get("report_status")),
                "position": str(r.get("position") or ""),
            }
        )
    return dict(out)


def depth_chart_roles(
    depth_rows: Iterable[Mapping[str, str]],
    inactive: Mapping[str, list[dict[str, str]]] | None = None,
    as_of: date | None = None,
) -> dict[str, dict[str, str]]:
    """Top healthy QB/RB/TE plus outside and slot WR from the latest snapshot per team.

    ``as_of`` keeps only snapshots dated on or before that day (slate mornings count).
    """
    latest: dict[str, str] = {}
    by_team: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for r in depth_rows:
        dt = str(r.get("dt") or "")
        if not dt or (as_of is not None and dt[:10] > as_of.isoformat()):
            continue
        team = _team(r.get("team"))
        if dt > latest.get(team, ""):
            latest[team] = dt
            by_team[team] = []
        if dt == latest[team]:
            by_team[team].append(r)

    roles: dict[str, dict[str, str]] = {}
    for team, rows in by_team.items():
        out_ids = {p["gsis_id"] for p in (inactive or {}).get(team, []) if p.get("gsis_id")}
        out_names = {_name_key(p["name"]) for p in (inactive or {}).get(team, [])}

        def healthy(r: Mapping[str, str]) -> bool:
            gsis = str(r.get("gsis_id") or "")
            if gsis and gsis in out_ids:
                return False
            return _name_key(r.get("player_name")) not in out_names

        def best(pos: str, slots: set[str] | None = None, skip: str = "") -> str | None:
            cands = [
                r
                for r in rows
                if r.get("pos_abb") == pos
                and (slots is None or str(r.get("pos_slot")) in slots)
                and healthy(r)
                and r.get("player_name") != skip
            ]
            cands.sort(key=lambda r: _num(r.get("pos_rank")) or 99.0)
            return str(cands[0]["player_name"]) if cands else None

        team_roles: dict[str, str] = {}
        for role, pos in (("qb", "QB"), ("rb1", "RB"), ("te", "TE")):
            name = best(pos)
            if name:
                team_roles[role] = name
        deep = best("WR", OUTSIDE_WR_SLOTS) or best("WR")
        if deep:
            team_roles["wr_deep"] = deep
        slot = best("WR", SLOT_WR_SLOTS, skip=deep or "") or best("WR", skip=deep or "")
        if slot:
            team_roles["wr_slot"] = slot
        roles[team] = team_roles
    return roles


def _clamp_grade(value: float) -> float:
    return round(max(20.0, min(99.0, value)), 1)


def _zscores(values: Mapping[str, float]) -> dict[str, float]:
    if len(values) < 2:
        return {k: 0.0 for k in values}
    mean = sum(values.values()) / len(values)
    var = sum((v - mean) ** 2 for v in values.values()) / len(values)
    sd = var**0.5 or 1.0
    return {k: (v - mean) / sd for k, v in values.items()}


def team_pressures(pfr_def_rows: Iterable[Mapping[str, str]]) -> dict[tuple[str, int], float]:
    """Sum PFR ``def_pressures`` per (team, week) for regular-season games."""
    totals: dict[tuple[str, int], float] = defaultdict(float)
    for r in pfr_def_rows:
        if r.get("game_type", "REG") != "REG":
            continue
        totals[(_team(r.get("team")), int(_num(r.get("week"))))] += _num(r.get("def_pressures"))
    return dict(totals)


def pass_rush_grades(
    per_game: Mapping[str, list[dict[str, Any]]],
    pressures: Mapping[tuple[str, int], float],
    last_n: int | None = None,
) -> dict[str, dict[str, float]]:
    """League-relative pass-rush grade from pressures per opponent dropback.

    Only games with both a pressure total and opponent dropbacks count.
    """
    rates: dict[str, float] = {}
    for team, games in per_game.items():
        window = games[-last_n:] if last_n else games
        num = den = 0.0
        for g in window:
            key = (team, g["week"])
            if key in pressures and g.get("opp_dropbacks"):
                num += pressures[key]
                den += g["opp_dropbacks"]
        if den > 0:
            rates[team] = num / den
    z = _zscores(rates)
    return {
        t: {
            "pressure_rate": round(rates[t], 3),
            "pass_rush": _clamp_grade(PASS_RUSH_BASE + PASS_RUSH_PER_SD * z[t]),
        }
        for t in rates
    }


def qb_grades(
    qbr_rows: Iterable[Mapping[str, str]],
    starters: Mapping[str, str],
    season: int,
    before_week: int | None = None,
) -> dict[str, dict[str, float]]:
    """League-relative grade for each team's listed starter from play-weighted QBR.

    QBR is pooled per QB across teams for regular-season weeks before
    ``before_week``; QBs under ``MIN_QB_PLAYS`` plays are left ungraded.
    """
    plays: dict[str, float] = defaultdict(float)
    weighted: dict[str, float] = defaultdict(float)
    for r in qbr_rows:
        if str(r.get("season")) != str(season) or r.get("season_type") != "Regular":
            continue
        week = int(_num(r.get("week_num") or r.get("game_week")))
        if before_week is not None and week >= before_week:
            continue
        n = _num(r.get("qb_plays"))
        key = _name_key(r.get("name_display"))
        plays[key] += n
        weighted[key] += n * _num(r.get("qbr_total"))
    qbr = {k: weighted[k] / plays[k] for k in plays if plays[k] >= MIN_QB_PLAYS}
    z = _zscores(qbr)
    out: dict[str, dict[str, float]] = {}
    for team, name in starters.items():
        key = _name_key(name)
        if key in qbr:
            out[team] = {
                "qbr": round(qbr[key], 1),
                "qb_grade": _clamp_grade(QB_GRADE_BASE + QB_GRADE_PER_SD * z[key]),
            }
    return out


def _advanced_grades(
    season: int,
    before: date | None,
    game_rows: list[dict[str, str]],
    per_game: Mapping[str, list[dict[str, Any]]],
    starters: Mapping[str, str],
    last_n: int | None,
) -> dict[str, dict[str, float]]:
    """Pass-rush and QB grades merged per team; empty parts on fetch failure."""
    grades: dict[str, dict[str, float]] = defaultdict(dict)
    try:
        pressures = team_pressures(fetch_csv(PFR_DEF_URL.format(season=season)))
        for team, vals in pass_rush_grades(per_game, pressures, last_n).items():
            grades[team].update(vals)
    except Exception as exc:  # grades are optional; engine falls back to sacks
        logger.warning("Pressure grades unavailable: %s", exc)
    try:
        week = slate_week(game_rows, season, before) if before else None
        for team, vals in qb_grades(fetch_csv(QBR_URL), starters, season, week).items():
            grades[team].update(vals)
    except Exception as exc:  # engine falls back to the pass-yards proxy
        logger.warning("QBR grades unavailable: %s", exc)
    return dict(grades)


def _auto_roles(
    season: int, before: date | None, game_rows: list[dict[str, str]]
) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    """Depth-chart roles and inactives; empty on any fetch/parse failure."""
    try:
        injuries = inactive_players(
            fetch_csv(INJURIES_URL.format(season=season)), slate_week(game_rows, season, before)
        )
        roles = depth_chart_roles(
            fetch_csv(DEPTH_CHART_URL.format(season=season)), injuries, as_of=before
        )
        return roles, injuries
    except Exception as exc:  # roles are an enhancement; keep the tape build alive
        logger.warning("Auto roles unavailable, keeping existing roles: %s", exc)
        return {}, {}


def build_tape_payload(
    season: int,
    before: date | None = None,
    last_n: int | None = None,
    roles: Mapping[str, Mapping[str, Any]] | None = None,
    team_rows: list[dict[str, str]] | None = None,
    game_rows: list[dict[str, str]] | None = None,
    auto_roles: bool = True,
    depth_roles: Mapping[str, Mapping[str, str]] | None = None,
    inactive: Mapping[str, list[dict[str, str]]] | None = None,
    advanced: bool = True,
    grades: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Fetch (unless rows are supplied) and assemble the tape JSON payload.

    With ``auto_roles`` the depth chart + injury report are fetched unless
    ``depth_roles``/``inactive`` are supplied; depth-chart roles override
    ``roles`` field by field. With ``advanced`` the pass-rush and QB grades
    are fetched unless ``grades`` is supplied.
    """
    if team_rows is None:
        team_rows = fetch_csv(TEAM_WEEK_URL.format(season=season))
    if game_rows is None:
        game_rows = fetch_csv(SCHEDULES_URL)
    if auto_roles and depth_roles is None and inactive is None:
        depth_roles, inactive = _auto_roles(season, before, game_rows)
    merged: dict[str, dict[str, Any]] = {t: dict(r) for t, r in (roles or {}).items()}
    for team, team_roles in (depth_roles or {}).items():
        merged.setdefault(team, {}).update(team_roles)
    per_game = build_per_game(team_rows, game_rows, season, before)
    teams = aggregate_tape(per_game, last_n=last_n, roles=merged)
    if advanced and grades is None:
        starters = {t: str(r["qb"]) for t, r in teams.items() if r.get("qb")}
        grades = _advanced_grades(season, before, game_rows, per_game, starters, last_n)
    for team, vals in (grades or {}).items():
        if team in teams:
            teams[team].update(vals)
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
        "roles_source": "nflverse depth_charts + injuries" if depth_roles else "existing tape",
        "grades_source": "pfr_advstats pressures + espn qbr" if grades else None,
        "inactive": {t: sorted(p["name"] for p in ps) for t, ps in sorted((inactive or {}).items())},
        "teams": teams,
    }


def load_tape_inactives(nfl_dir: Path | str) -> dict[str, list[str]]:
    """``inactive`` block of ``<nfl_dir>/tape/prior_week.json`` (empty when absent)."""
    try:
        raw = json.loads((Path(nfl_dir) / "tape" / "prior_week.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    block = raw.get("inactive") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return {}
    return {_team(t): [str(n) for n in names] for t, names in block.items() if isinstance(names, list)}


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
