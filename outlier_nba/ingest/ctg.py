"""Cleaning the Glass (CTG) import adapter: files on the NBA Drive hub -> local SQLite store.

There is no network code here on purpose. CTG has no public API and its terms do not address
automated access. This module only reads files that already exist on the NBA Drive hub
(``NBA_ROOT``), where ``nba-props-pipeline`` puts every NBA extract. The owner ruled on
2026-11-08 that the CTG game logs already there may be imported (docs/nba/data-sources.md
section 6). The hub is read, never written, and its ``cache`` folder (session cookies) is never
opened. Nothing is imported from ``nba-props-pipeline``; its folder layout is copied as
constants below and pinned by a test.

All CTG stats exclude garbage time and end-of-quarter heaves, and they are NOT
opponent-adjusted, so they must never be averaged into a column that holds ESPN full-game
numbers.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from outlier_nba.identity import resolve_team

ET = ZoneInfo("America/New_York")

# --------------------------------------------------------------------------- team names

# CTG prints city-style names; the shared registry resolves only 3 of 30 of them. Kept local
# (not added to the shared registry) so bare city names can never leak into another league's
# alias table.
CTG_TEAM_CITIES: dict[str, str] = {
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA",
    "Chicago": "CHI", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN",
    "Detroit": "DET", "Golden State": "GSW", "Houston": "HOU", "Indiana": "IND",
    "LA Clippers": "LAC", "LA Lakers": "LAL", "Memphis": "MEM", "Miami": "MIA",
    "Milwaukee": "MIL", "Minnesota": "MIN", "New Orleans": "NOP", "New York": "NYK",
    "Oklahoma City": "OKC", "Orlando": "ORL", "Philadelphia": "PHI", "Phoenix": "PHX",
    "Portland": "POR", "Sacramento": "SAC", "San Antonio": "SAS", "Toronto": "TOR",
    "Utah": "UTA", "Washington": "WAS",
}  # fmt: skip
ALL_TEAMS = frozenset(CTG_TEAM_CITIES.values())


def resolve_ctg_team(value: Any) -> str | None:
    """Canonical tricode for a CTG team label (city name or tricode), else None."""
    text = str(value or "").strip()
    if not text:
        return None
    return CTG_TEAM_CITIES.get(text) or resolve_team(text)


# --------------------------------------------------------------------------- cells

_BLANK = frozenset({"", "-", "\u2014", "nan", "none"})


class CTGParseError(ValueError):
    """A CTG export cell or file that does not have the documented shape."""


class CTGUnusableError(CTGParseError):
    """A file with no game rows or a broken header (seen on the Drive); skipped, not fatal."""


class CTGIdentityError(CTGParseError):
    """A file whose rows belong to a different team than the one it claims."""

    def __init__(self, message: str, *, actual: str | None = None) -> None:
        super().__init__(message)
        self.actual = actual


def parse_value(raw: Any) -> float | None:
    """``"54.7%"`` -> 0.547, ``"+11.9"`` -> 11.9, numbers pass through, blank/dash/NaN -> None."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return None if math.isnan(raw) else float(raw)
    text = str(raw).strip()
    if text.lower() in _BLANK:
        return None
    pct = text.endswith("%")
    try:
        number = float(text[:-1] if pct else text)
    except ValueError as exc:
        raise CTGParseError(f"not a CTG number: {raw!r}") from exc
    if math.isnan(number):
        return None
    return number / 100.0 if pct else number


def season_for(day: date) -> str:
    """``"2025-26"`` for any date from August 2025 through July 2026 (as ``run_context``)."""
    start = day.year if day.month >= 8 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _season_bounds(season: str) -> tuple[date, date]:
    start = int(season[:4])
    return date(start, 8, 1), date(start + 1, 7, 31)


# --------------------------------------------------------------------------- season tables

# Required columns -> canonical metric name. Only value columns; "... Rank" columns are dropped
# (a rank is a function of the other 29 teams and adds nothing a model can use).
SEASON_TABLES: dict[str, dict[str, str]] = {
    "fourfactors": {
        "W": "w", "L": "l", "Diff": "diff",
        "OFFENSE: Pts/Poss": "off_pts_poss", "OFFENSE: eFG%": "off_efg",
        "OFFENSE: TOV%": "off_tov", "OFFENSE: ORB%": "off_orb", "OFFENSE: FT Rate": "off_ftr",
        "DEFENSE: Pts/Poss": "def_pts_poss", "DEFENSE: eFG%": "def_efg",
        "DEFENSE: TOV%": "def_tov", "DEFENSE: ORB%": "def_orb", "DEFENSE: FT Rate": "def_ftr",
    },
    "summary": {
        "W": "w", "L": "l", "Point Diff": "point_diff", "Exp W": "exp_w",
        "Offense": "off_pts_poss", "Defense": "def_pts_poss",
        "LAST 2 WEEKS: W": "l2w_w", "LAST 2 WEEKS: L": "l2w_l",
        "LAST 2 WEEKS: Offense": "l2w_off_pts_poss", "LAST 2 WEEKS: Defense": "l2w_def_pts_poss",
    },
}  # fmt: skip
# "Spread Diff" is deliberately not imported: it is derived from market lines, never a model input.


@dataclass(frozen=True)
class TeamRow:
    team: str
    metrics: dict[str, float | None]


@dataclass(frozen=True)
class SeasonTable:
    name: str
    rows: tuple[TeamRow, ...]
    sha256: str
    extra_columns: tuple[str, ...] = field(default=())


def _detect(header: list[str]) -> str:
    cols = set(header)
    hits = [name for name, req in SEASON_TABLES.items() if "Team" in cols and set(req) <= cols]
    if len(hits) != 1:
        raise CTGParseError(f"unrecognised CTG table (header starts {header[:6]})")
    return hits[0]


def read_season_table(path: Path) -> SeasonTable:
    """Parse a FOURFACTORS or SUMMARY CSV export; the league ``Average`` row is dropped."""
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    header = list(reader.fieldnames or [])
    name = _detect(header)
    wanted = SEASON_TABLES[name]
    rows: list[TeamRow] = []
    for rec in reader:
        label = (rec.get("Team") or "").strip()
        if label == "Average":
            continue
        team = resolve_ctg_team(label)
        if team is None:
            raise CTGParseError(f"{path.name}: unresolved CTG team {label!r}")
        rows.append(TeamRow(team, {m: parse_value(rec.get(c)) for c, m in wanted.items()}))
    if len({r.team for r in rows}) != len(rows):
        raise CTGParseError(f"{path.name}: duplicate team rows")
    known = set(wanted) | {"Team"} | {c for c in header if c.endswith(" Rank")}
    extra = tuple(c for c in header if c not in known)
    return SeasonTable(name, tuple(rows), hashlib.sha256(raw).hexdigest(), extra)


# --------------------------------------------------------------------------- game logs

# Verified on the Drive (docs/nba/data-sources.md section 6). Percentile columns are dropped.
GAMELOG_REQUIRED = (
    "Date",
    "Opp",
    "Team Pts",
    "Opp Pts",
    "Offense Pts/Poss #",
    "Defense Pts/Poss #",
)
GAMELOG_METRICS: dict[str, str] = {
    "Offense eFG% #": "off_efg", "Offense TOV% #": "off_tov", "Offense ORB% #": "off_orb",
    "FT Rate #": "off_ftr", "Defense eFG% #": "def_efg", "Defense TOV% #": "def_tov",
    "Defense ORB% #": "def_orb", "FT Rate #_1": "def_ftr",
    "Spread": "spread",  # market data of unknown book/time: metadata only, never a model input
}  # fmt: skip
VENUE_KEYS = ("column_2", "")  # new layout / old layout (blank header)
VENUES = {"vs.": "home", "@": "away"}


@dataclass(frozen=True)
class CTGGame:
    season: str
    season_type: str  # "regular" | "playoffs" | "unspecified" (old layout mixes both)
    game_date: date  # Eastern calendar date as printed by CTG
    team: str
    opp: str
    venue: str | None  # CTG never marks neutral sites; orientation always comes from ESPN
    team_pts: int
    opp_pts: int
    off_pp100: float | None  # garbage time + heaves excluded (CTG definition)
    def_pp100: float | None
    metrics: dict[str, float | None] = field(default_factory=dict)


def infer_log_owner(opponents: Iterable[str]) -> str | None:
    """The team a log belongs to: the only tricode never listed as an opponent, if unique."""
    missing = ALL_TEAMS - set(opponents)
    return next(iter(missing)) if len(missing) == 1 else None


def _views(doc: Mapping[str, Any]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    views = doc.get("season_views")
    if isinstance(views, Mapping):
        out = []
        for key, label in (("regular_season", "regular"), ("playoffs", "playoffs")):
            view = views.get(key)
            rows = view.get("four_factors") if isinstance(view, Mapping) else None
            out.append((label, list(rows) if isinstance(rows, list) else []))
        return out
    rows = doc.get("four_factors")
    return [("unspecified", list(rows) if isinstance(rows, list) else [])]


def _game_from_row(rec: Mapping[str, Any], *, team: str, season_type: str, name: str) -> CTGGame:
    try:
        day = datetime.strptime(str(rec["Date"]).strip(), "%m/%d/%y").date()
    except ValueError as exc:
        raise CTGParseError(f"{name}: bad date {rec.get('Date')!r}") from exc
    opp = resolve_ctg_team(rec["Opp"])
    if opp is None:
        raise CTGParseError(f"{name}: unresolved opponent {rec['Opp']!r}")
    venue_raw = next((str(rec[k]).strip() for k in VENUE_KEYS if k in rec), "")
    team_pts, opp_pts = parse_value(rec["Team Pts"]), parse_value(rec["Opp Pts"])
    if team_pts is None or opp_pts is None:
        raise CTGParseError(f"{name}: missing score on {rec['Date']}")
    return CTGGame(
        season=season_for(day),
        season_type=season_type,
        game_date=day,
        team=team,
        opp=opp,
        venue=VENUES.get(venue_raw),
        team_pts=int(team_pts),
        opp_pts=int(opp_pts),
        off_pp100=parse_value(rec["Offense Pts/Poss #"]),
        def_pp100=parse_value(rec["Defense Pts/Poss #"]),
        metrics={m: parse_value(rec.get(c)) for c, m in GAMELOG_METRICS.items()},
    )


def _check_identity(doc: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], team: str,
                    name: str) -> None:  # fmt: skip
    stated = doc.get("team")
    if stated is not None and resolve_ctg_team(stated) != team:
        raise CTGIdentityError(f"{name}: folder says {team} but the file says {stated!r}")
    opps = [resolve_ctg_team(r.get("Opp")) or "" for r in rows]
    if team in opps:
        owner = infer_log_owner(opps)
        raise CTGIdentityError(
            f"{name}: claims {team} but {team} appears as its own opponent; "
            f"rows belong to {owner or 'an unidentified team'}",
            actual=owner,
        )


def parse_gamelog(raw: bytes, *, team: str, name: str, season: str | None = None) -> list[CTGGame]:
    """Parse one team game-log JSON (either Drive layout), oldest game first."""
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CTGUnusableError(f"{name}: not JSON ({exc})") from exc
    if not isinstance(doc, Mapping):
        raise CTGUnusableError(f"{name}: not a game-log object")
    views = [(label, rows) for label, rows in _views(doc) if rows]
    if not views:
        raise CTGUnusableError(f"{name}: no game rows")
    all_rows = [r for _, rows in views for r in rows]
    for i, rec in enumerate(all_rows):
        if not isinstance(rec, Mapping):
            raise CTGUnusableError(f"{name}: row {i} is not an object")
        missing = [k for k in GAMELOG_REQUIRED if k not in rec]
        if missing:
            raise CTGUnusableError(f"{name}: row {i} missing columns {missing}")
    _check_identity(doc, all_rows, team, name)
    by_date: dict[date, CTGGame] = {}
    for label, rows in views:
        for rec in rows:
            try:
                game = _game_from_row(rec, team=team, season_type=label, name=name)
            except CTGParseError:
                raise
            except (TypeError, ValueError, OverflowError) as exc:
                raise CTGParseError(f"{name}: bad row {rec.get('Date')!r} ({exc})") from exc
            prior = by_date.get(game.game_date)
            if prior is not None and prior != game:
                raise CTGParseError(f"{name}: conflicting rows for {game.game_date}")
            by_date[game.game_date] = game
    games = sorted(by_date.values(), key=lambda g: g.game_date)
    seasons = {g.season for g in games}
    if len(seasons) != 1:
        raise CTGParseError(f"{name}: rows span seasons {sorted(seasons)}")
    if season is not None:
        lo, hi = _season_bounds(season)
        outside = [g.game_date for g in games if not lo <= g.game_date <= hi]
        if outside:
            raise CTGParseError(f"{name}: {len(outside)} games outside season {season}")
    return games


def read_gamelog(path: Path, *, team: str, season: str | None = None) -> list[CTGGame]:
    return parse_gamelog(path.read_bytes(), team=team, name=path.name, season=season)


# --------------------------------------------------------------------------- store

SCHEMA = """
CREATE TABLE IF NOT EXISTS ctg_import (
    import_id    INTEGER PRIMARY KEY,
    file_sha256  TEXT NOT NULL UNIQUE,
    table_name   TEXT NOT NULL,
    season       TEXT NOT NULL,
    season_type  TEXT NOT NULL DEFAULT 'regular',
    team         TEXT,                 -- set for per-team tables (game logs)
    source_path  TEXT,                 -- relative to NBA_ROOT for hub files
    as_of_utc    TEXT NOT NULL,        -- upper bound on what the file can know
    imported_utc TEXT NOT NULL,
    n_rows       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ctg_team_metric (
    import_id INTEGER NOT NULL REFERENCES ctg_import(import_id),
    team      TEXT NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL,
    PRIMARY KEY (import_id, team, metric)
);
CREATE INDEX IF NOT EXISTS ix_ctg_import_lookup
    ON ctg_import(table_name, season, season_type, as_of_utc);
CREATE TABLE IF NOT EXISTS ctg_team_game (
    season       TEXT NOT NULL,
    season_type  TEXT NOT NULL,
    game_date    TEXT NOT NULL,
    team         TEXT NOT NULL,
    opp          TEXT NOT NULL,
    venue        TEXT CHECK (venue IS NULL OR venue IN ('home', 'away')),
    team_pts     INTEGER NOT NULL,
    opp_pts      INTEGER NOT NULL,
    off_pp100    REAL,
    def_pp100    REAL,
    metrics_json TEXT NOT NULL,
    file_sha256  TEXT NOT NULL,
    as_of_utc    TEXT,                 -- as-of of the file that supplied the current values
    PRIMARY KEY (season, game_date, team)
);
"""


def default_store() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    root = Path(os.environ.get("OUTLIER_NBA_CACHE", Path(base) / "outlier_nba"))
    return root / "ctg" / "ctg.sqlite3"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ctg_team_game)")}
    if "as_of_utc" not in cols:  # stores created before per-row as-of existed
        conn.execute("ALTER TABLE ctg_team_game ADD COLUMN as_of_utc TEXT")
        conn.commit()


@dataclass(frozen=True)
class ImportResult:
    inserted: bool
    import_id: int


@dataclass(frozen=True)
class Snapshot:
    as_of_utc: datetime
    teams: dict[str, dict[str, float | None]]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds")


def _check_as_of(as_of_utc: datetime) -> None:
    if as_of_utc.tzinfo is None:
        raise CTGParseError("as_of_utc must be timezone-aware")
    if as_of_utc > datetime.now(UTC):
        raise CTGParseError(f"as_of {as_of_utc} is in the future")


def _known_import(conn: sqlite3.Connection, sha256: str) -> int | None:
    hit = conn.execute("SELECT import_id FROM ctg_import WHERE file_sha256 = ?", (sha256,))
    row = hit.fetchone()
    return int(row[0]) if row else None


def import_season_table(
    conn: sqlite3.Connection, table: SeasonTable, *, season: str, as_of_utc: datetime,
    season_type: str = "regular",
) -> ImportResult:  # fmt: skip
    _check_as_of(as_of_utc)
    known = _known_import(conn, table.sha256)
    if known is not None:
        return ImportResult(False, known)
    with conn:
        cur = conn.execute(
            "INSERT INTO ctg_import (file_sha256, table_name, season, season_type, as_of_utc,"
            " imported_utc, n_rows) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (table.sha256, table.name, season, season_type, _iso(as_of_utc),
             _iso(datetime.now(UTC)), len(table.rows)),
        )  # fmt: skip
        import_id = int(cur.lastrowid or 0)
        conn.executemany(
            "INSERT INTO ctg_team_metric (import_id, team, metric, value) VALUES (?, ?, ?, ?)",
            [(import_id, r.team, m, v) for r in table.rows for m, v in r.metrics.items()],
        )
    return ImportResult(True, import_id)


def slate_cutoff_utc(slate_date: str) -> datetime:
    """Eastern midnight that opens the slate date: anything downloaded later is unusable."""
    return datetime.combine(date.fromisoformat(slate_date), time(0), tzinfo=ET).astimezone(UTC)


def snapshot_asof(
    conn: sqlite3.Connection, table: str, season: str, slate_date: str,
    season_type: str = "regular",
) -> Snapshot | None:  # fmt: skip
    """Latest season-table import downloaded strictly before the slate's Eastern midnight."""
    row = conn.execute(
        "SELECT import_id, as_of_utc FROM ctg_import WHERE table_name = ? AND season = ?"
        " AND season_type = ? AND team IS NULL AND as_of_utc < ? ORDER BY as_of_utc DESC LIMIT 1",
        (table, season, season_type, _iso(slate_cutoff_utc(slate_date))),
    ).fetchone()
    if row is None:
        return None
    teams: dict[str, dict[str, float | None]] = {}
    for team, metric, value in conn.execute(
        "SELECT team, metric, value FROM ctg_team_metric WHERE import_id = ?", (row[0],)
    ):
        teams.setdefault(team, {})[metric] = value
    return Snapshot(datetime.fromisoformat(row[1]), teams)


_UPSERT_GAME = """
INSERT INTO ctg_team_game (season, season_type, game_date, team, opp, venue, team_pts, opp_pts,
                           off_pp100, def_pp100, metrics_json, file_sha256, as_of_utc)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (season, game_date, team) DO UPDATE SET
    season_type = CASE
        WHEN ctg_team_game.season_type = 'playoffs' THEN 'playoffs'
        WHEN excluded.season_type = 'unspecified' THEN ctg_team_game.season_type
        ELSE excluded.season_type END,
    opp = excluded.opp, venue = COALESCE(excluded.venue, ctg_team_game.venue),
    team_pts = excluded.team_pts, opp_pts = excluded.opp_pts,
    off_pp100 = excluded.off_pp100, def_pp100 = excluded.def_pp100,
    metrics_json = excluded.metrics_json, file_sha256 = excluded.file_sha256,
    as_of_utc = excluded.as_of_utc
WHERE excluded.as_of_utc >= COALESCE(ctg_team_game.as_of_utc, '')
"""


def _upsert_games(
    conn: sqlite3.Connection, games: Sequence[CTGGame], file_sha256: str, as_of_utc: datetime
) -> None:
    """No transaction of its own: callers wrap it so the import record and rows commit together.

    A file older than the one that supplied a row never overwrites it (``WHERE`` above), and a
    ``playoffs`` label is never downgraded: one scraper view labels playoff games "regular".
    """
    conn.executemany(
        _UPSERT_GAME,
        [
            (g.season, g.season_type, g.game_date.isoformat(), g.team, g.opp, g.venue,
             g.team_pts, g.opp_pts, g.off_pp100, g.def_pp100,
             json.dumps(g.metrics, sort_keys=True), file_sha256, _iso(as_of_utc))
            for g in games
        ],
    )  # fmt: skip


def store_gamelog(
    conn: sqlite3.Connection,
    games: Sequence[CTGGame],
    *,
    file_sha256: str,
    as_of_utc: datetime | None = None,
) -> None:
    """Upsert game rows in one transaction (``as_of_utc`` defaults to now)."""
    as_of = as_of_utc or datetime.now(UTC)
    _check_as_of(as_of)
    with conn:
        _upsert_games(conn, games, file_sha256, as_of)


def load_team_games(
    conn: sqlite3.Connection,
    *,
    season: str | None = None,
    as_of_before: datetime | None = None,
) -> list[CTGGame]:
    """Stored game rows, oldest first.

    ``as_of_before`` keeps only rows whose supplying file is dated strictly before it, so a
    backtest can refuse values a later download revised. Rows without a per-row as-of (stores
    created before the column existed) are excluded whenever a cutoff is given.
    """
    sql = (
        "SELECT season, season_type, game_date, team, opp, venue, team_pts, opp_pts,"
        " off_pp100, def_pp100, metrics_json FROM ctg_team_game WHERE 1 = 1"
    )
    params: list[str] = []
    if season is not None:
        sql += " AND season = ?"
        params.append(season)
    if as_of_before is not None:
        sql += " AND as_of_utc < ?"
        params.append(_iso(as_of_before))
    rows = conn.execute(sql + " ORDER BY game_date, team", params).fetchall()
    return [
        CTGGame(r[0], r[1], date.fromisoformat(r[2]), r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                json.loads(r[10]))
        for r in rows
    ]  # fmt: skip


# --------------------------------------------------------------------------- NBA Drive hub

DEFAULT_NBA_ROOT = Path(r"G:\My Drive\Sports_Analytics\01_Active\NBA_Active")
# Copied from nba-props-pipeline core/output_paths.py ("teams", "cleaning_the_glass"); not
# imported (AGENTS.md). Hand-downloaded CSVs get a glob once their header is verified.
GAMELOG_GLOBS = (
    "TEAMS/*/CLEANINGdaGLASS/GAME_LOGS_*.json",
    "Current_Matchups/CLEANING_THE_GLASS/*/CLEANINGdaGLASS/GAME_LOGS_*.json",
)
_FILE_DATE = re.compile(r"_(\d{2})_(\d{2})_(\d{4})\.json$")
SYNC_OUTCOMES = (
    "imported", "already_imported", "rejected_mislabeled", "skipped_unusable", "other_season",
)  # fmt: skip


def nba_root() -> Path:
    """The NBA Drive hub: ``NBA_ROOT`` env (as nba-props-pipeline), else the Drive default."""
    env = os.environ.get("NBA_ROOT")
    return Path(env) if env else DEFAULT_NBA_ROOT


@dataclass(frozen=True)
class HubFile:
    path: Path
    claimed_team: str | None  # None: the folder name is not a team (reported as unusable)
    file_date: date


@dataclass(frozen=True)
class FileOutcome:
    path: Path
    claimed_team: str | None
    outcome: str
    detail: str = ""
    actual: str | None = None
    n_rows: int = 0


@dataclass(frozen=True)
class SyncReport:
    files: tuple[FileOutcome, ...]
    counts: dict[str, int]


def discover_gamelogs(root: Path) -> list[HubFile]:
    """Game-log files under the hub's CTG folders, oldest file date first."""
    base = root.resolve()
    found: list[HubFile] = []
    for pattern in GAMELOG_GLOBS:
        for path in root.glob(pattern):
            if path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(base):
                continue  # never follow a path out of the hub
            m = _FILE_DATE.search(path.name)
            if m is None:
                continue  # not a dated GAME_LOGS export
            team = resolve_ctg_team(path.parent.parent.name)
            found.append(HubFile(path, team, date(int(m[3]), int(m[1]), int(m[2]))))
    return sorted(found, key=lambda f: (f.file_date, str(f.path)))


def _record_gamelog_import(
    conn: sqlite3.Connection, f: HubFile, root: Path, sha256: str, games: Sequence[CTGGame]
) -> None:
    as_of = datetime.fromtimestamp(f.path.stat().st_mtime, UTC)
    season_types = {g.season_type for g in games}
    season_type = season_types.pop() if len(season_types) == 1 else "mixed"
    with conn:  # one transaction: a failure leaves neither the import record nor any rows
        conn.execute(
            "INSERT INTO ctg_import (file_sha256, table_name, season, season_type, team,"
            " source_path, as_of_utc, imported_utc, n_rows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sha256, "gamelog", games[0].season, season_type, f.claimed_team,
             f.path.relative_to(root).as_posix(), _iso(as_of), _iso(datetime.now(UTC)),
             len(games)),
        )  # fmt: skip
        _upsert_games(conn, games, sha256, as_of)


def _sync_one(conn: sqlite3.Connection, f: HubFile, root: Path, season: str | None) -> FileOutcome:
    if f.claimed_team is None:
        detail = f"folder {f.path.parent.parent.name!r} is not a team"
        return FileOutcome(f.path, None, "skipped_unusable", detail)
    try:
        raw = f.path.read_bytes()
    except OSError as exc:  # e.g. a Drive file locked by the sync client
        return FileOutcome(f.path, f.claimed_team, "skipped_unusable", f"unreadable: {exc}")
    try:
        games = parse_gamelog(raw, team=f.claimed_team, name=f.path.name)
    except CTGIdentityError as exc:
        return FileOutcome(f.path, f.claimed_team, "rejected_mislabeled", str(exc), exc.actual)
    except CTGParseError as exc:
        return FileOutcome(f.path, f.claimed_team, "skipped_unusable", str(exc))
    sha256 = hashlib.sha256(raw).hexdigest()
    if _known_import(conn, sha256) is not None:
        return FileOutcome(f.path, f.claimed_team, "already_imported")
    if season is not None and games[0].season != season:
        return FileOutcome(f.path, f.claimed_team, "other_season", games[0].season)
    _record_gamelog_import(conn, f, root, sha256, games)
    return FileOutcome(f.path, f.claimed_team, "imported", n_rows=len(games))


def sync_from_hub(conn: sqlite3.Connection, root: Path, *, season: str | None = None) -> SyncReport:
    """Import every new game-log file from the hub. Read-only on ``root``; safe to re-run."""
    if not root.is_dir():
        raise FileNotFoundError(f"NBA_ROOT not found: {root}")
    outcomes = tuple(_sync_one(conn, f, root, season) for f in discover_gamelogs(root))
    counts: Counter[str] = Counter({k: 0 for k in SYNC_OUTCOMES})
    counts.update(o.outcome for o in outcomes)
    return SyncReport(outcomes, dict(counts))
