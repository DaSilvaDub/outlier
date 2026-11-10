"""Cleaning the Glass (CTG) import adapter: files on the NBA Drive hub -> local SQLite store.

There is no network code here on purpose. CTG has no public API and its terms do not address
automated access. This module only reads files that already exist on the NBA Drive hub
(``NBA_ROOT``), where ``nba-props-pipeline`` puts every NBA extract. The owner ruled on
2026-11-08 that the CTG game logs already there may be imported (docs/nba/data-sources.md
section 6). The hub is read, never written, and its ``cache`` folder (session cookies) is never
opened. Nothing is imported from ``nba-props-pipeline``; hub discovery and sync live in
``outlier_nba.ingest.ctg_hub``.

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
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
    """``"54.7%"`` -> 0.547, ``"+11.9"`` -> 11.9, numbers pass through, blank/dash/NaN -> None.

    Infinite values raise: no CTG stat is unbounded, so ``inf`` is always a broken cell.
    """
    if raw is None or isinstance(raw, bool):
        return None
    pct = False
    if isinstance(raw, int | float):
        number = _to_float(raw, raw)
    else:
        text = str(raw).strip()
        if text.lower() in _BLANK:
            return None
        pct = text.endswith("%")
        number = _to_float(text[:-1] if pct else text, raw)
    if math.isnan(number):
        return None
    if math.isinf(number):
        raise CTGParseError(f"not a finite CTG number: {raw!r}")
    return number / 100.0 if pct else number


def _to_float(value: Any, raw: Any) -> float:
    try:
        return float(value)
    except OverflowError as exc:  # an int too large for a float
        raise CTGParseError(f"not a finite CTG number: {raw!r}") from exc
    except ValueError as exc:
        raise CTGParseError(f"not a CTG number: {raw!r}") from exc


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


# A game can sit in both views of one file; the more specific label wins.
_LABEL_RANK = {"unspecified": 0, "regular": 1, "playoffs": 2}


def load_gamelog_doc(raw: bytes, *, name: str) -> Mapping[str, Any]:
    """Decode one game-log file to its JSON object (no row validation)."""
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CTGUnusableError(f"{name}: not JSON ({exc})") from exc
    if not isinstance(doc, Mapping):
        raise CTGUnusableError(f"{name}: not a game-log object")
    return doc


def _merge_game(by_date: dict[date, CTGGame], game: CTGGame, name: str) -> None:
    prior = by_date.get(game.game_date)
    if prior is None:
        by_date[game.game_date] = game
        return
    if replace(prior, season_type=game.season_type) != game:
        raise CTGParseError(f"{name}: conflicting rows for {game.game_date}")
    if _LABEL_RANK[game.season_type] > _LABEL_RANK[prior.season_type]:
        by_date[game.game_date] = game


def parse_gamelog(raw: bytes, *, team: str, name: str, season: str | None = None) -> list[CTGGame]:
    """Parse one team game-log JSON (either Drive layout), oldest game first."""
    return games_from_doc(load_gamelog_doc(raw, name=name), team=team, name=name, season=season)


def games_from_doc(
    doc: Mapping[str, Any], *, team: str, name: str, season: str | None = None
) -> list[CTGGame]:
    """Validate and parse the rows of a decoded game-log object, oldest game first."""
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
            _merge_game(by_date, game, name)
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

# Append-only. Every download is a capture; every game row it carried is a version of that game.
# Reads choose the latest version a cutoff allows, so re-downloading a game never moves it
# later in time and an older file imported late never loses its explicit playoff label.
SCHEMA_VERSION = 2
_SET_SCHEMA_VERSION = "PRAGMA user_version = 2"  # static SQL; must equal SCHEMA_VERSION
SCHEMA = """
CREATE TABLE IF NOT EXISTS ctg_capture (
    capture_id   INTEGER PRIMARY KEY,
    file_sha256  TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    season       TEXT NOT NULL,
    season_type  TEXT NOT NULL DEFAULT 'regular',   -- 'mixed' when a game log spans both
    team         TEXT,                 -- set for per-team tables (game logs)
    source_path  TEXT,                 -- relative to NBA_ROOT for hub files
    as_of_utc    TEXT NOT NULL,        -- earliest moment the file can be assumed to exist
    imported_utc TEXT NOT NULL,
    n_rows       INTEGER NOT NULL,
    UNIQUE (file_sha256, as_of_utc)    -- same bytes captured again later is a new capture
);
CREATE INDEX IF NOT EXISTS ix_ctg_capture_lookup
    ON ctg_capture(table_name, season, season_type, as_of_utc);
CREATE TABLE IF NOT EXISTS ctg_capture_metric (
    capture_id INTEGER NOT NULL REFERENCES ctg_capture(capture_id),
    team       TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL,
    PRIMARY KEY (capture_id, team, metric)
);
CREATE TABLE IF NOT EXISTS ctg_game_version (
    capture_id   INTEGER NOT NULL REFERENCES ctg_capture(capture_id),
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
    PRIMARY KEY (capture_id, game_date, team)
);
CREATE INDEX IF NOT EXISTS ix_ctg_game_version_game
    ON ctg_game_version(season, game_date, team);
"""

# Version-1 stores (one mutable row per game) are copied into the tables above once. The legacy
# tables are left in place, unread. A v1 game row is dated by the import record of the file that
# last wrote it; a row with no import record has an unknown capture time and is dated at the
# migration, so it can only ever appear in a "latest" read, never in a historical one.
_MIGRATE_CAPTURES = """
INSERT OR IGNORE INTO ctg_capture (capture_id, file_sha256, table_name, season, season_type,
                                   team, source_path, as_of_utc, imported_utc, n_rows)
SELECT import_id, file_sha256, table_name, season, season_type, team, source_path, as_of_utc,
       imported_utc, n_rows
FROM ctg_import
"""
_MIGRATE_METRICS = """
INSERT OR IGNORE INTO ctg_capture_metric (capture_id, team, metric, value)
SELECT import_id, team, metric, value FROM ctg_team_metric
"""
_MIGRATE_ORPHAN_CAPTURES = """
INSERT OR IGNORE INTO ctg_capture (file_sha256, table_name, season, season_type, team,
                                   source_path, as_of_utc, imported_utc, n_rows)
SELECT file_sha256, 'gamelog', MIN(season),
       CASE WHEN COUNT(DISTINCT season_type) = 1 THEN MIN(season_type) ELSE 'mixed' END,
       MIN(team), NULL, ?, ?, COUNT(*)
FROM ctg_team_game
WHERE file_sha256 NOT IN (SELECT file_sha256 FROM ctg_capture)
GROUP BY file_sha256
"""
_MIGRATE_GAMES = """
INSERT OR IGNORE INTO ctg_game_version (capture_id, season, season_type, game_date, team, opp,
                                        venue, team_pts, opp_pts, off_pp100, def_pp100,
                                        metrics_json)
SELECT (SELECT MIN(c.capture_id) FROM ctg_capture AS c WHERE c.file_sha256 = g.file_sha256),
       g.season, g.season_type, g.game_date, g.team, g.opp, g.venue, g.team_pts, g.opp_pts,
       g.off_pp100, g.def_pp100, g.metrics_json
FROM ctg_team_game AS g
"""


def default_store() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    root = Path(os.environ.get("OUTLIER_NBA_CACHE", Path(base) / "outlier_nba"))
    return root / "ctg" / "ctg.sqlite3"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    hit = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
    return hit.fetchone() is not None


def _schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the v2 tables and, once, copy a v1 store into them (atomic, safe to re-run)."""
    conn.executescript(SCHEMA)
    if _schema_version(conn) >= SCHEMA_VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")  # a second process waits here, then sees the new version
    try:
        if _schema_version(conn) < SCHEMA_VERSION:
            _migrate_v1(conn)
            conn.execute(_SET_SCHEMA_VERSION)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_v1(conn: sqlite3.Connection) -> None:
    if _has_table(conn, "ctg_import"):
        conn.execute(_MIGRATE_CAPTURES)
    if _has_table(conn, "ctg_team_metric"):
        conn.execute(_MIGRATE_METRICS)
    if _has_table(conn, "ctg_team_game"):
        now = _iso(datetime.now(UTC))
        conn.execute(_MIGRATE_ORPHAN_CAPTURES, (now, now))
        conn.execute(_MIGRATE_GAMES)


@dataclass(frozen=True)
class ImportResult:
    inserted: bool  # False: this exact capture (same bytes, same as-of) was already stored
    capture_id: int


@dataclass(frozen=True)
class Snapshot:
    as_of_utc: datetime
    teams: dict[str, dict[str, float | None]]


@dataclass(frozen=True)
class _Capture:
    sha256: str
    table_name: str
    season: str
    season_type: str
    as_of_utc: datetime
    n_rows: int
    team: str | None = None
    source_path: str | None = None


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds")


def et_midnight_utc(day: date) -> datetime:
    """The Eastern midnight that opens ``day``, in UTC."""
    return datetime.combine(day, time(0), tzinfo=ET).astimezone(UTC)


def _check_as_of(as_of_utc: datetime) -> None:
    if as_of_utc.tzinfo is None:
        raise CTGParseError("as_of_utc must be timezone-aware")
    if as_of_utc > datetime.now(UTC):
        raise CTGParseError(f"as_of {as_of_utc} is in the future")


_INSERT_CAPTURE = """
INSERT INTO ctg_capture (file_sha256, table_name, season, season_type, team, source_path,
                         as_of_utc, imported_utc, n_rows)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (file_sha256, as_of_utc) DO NOTHING
"""


def _insert_capture(conn: sqlite3.Connection, cap: _Capture) -> ImportResult:
    """Record a capture, or find the identical one already stored.

    The duplicate check is the ``INSERT`` itself, under SQLite's write lock, so two processes
    importing the same file can never both insert it or fail on the unique key. Callers hold
    the transaction so the capture and its rows commit together.
    """
    as_of = _iso(cap.as_of_utc)
    cur = conn.execute(
        _INSERT_CAPTURE,
        (cap.sha256, cap.table_name, cap.season, cap.season_type, cap.team, cap.source_path,
         as_of, _iso(datetime.now(UTC)), cap.n_rows),
    )  # fmt: skip
    if cur.rowcount == 1 and cur.lastrowid is not None:
        return ImportResult(True, int(cur.lastrowid))
    row = conn.execute(
        "SELECT capture_id FROM ctg_capture WHERE file_sha256 = ? AND as_of_utc = ?",
        (cap.sha256, as_of),
    ).fetchone()
    return ImportResult(False, int(row[0]))


def import_season_table(
    conn: sqlite3.Connection, table: SeasonTable, *, season: str, as_of_utc: datetime,
    season_type: str = "regular",
) -> ImportResult:  # fmt: skip
    """Store one season-table capture.

    The same bytes captured at a new time are a new capture, so a table that changes and then
    changes back reads as its latest state, not as the change in between.
    """
    _check_as_of(as_of_utc)
    cap = _Capture(table.sha256, table.name, season, season_type, as_of_utc, len(table.rows))
    with conn:
        result = _insert_capture(conn, cap)
        if result.inserted:
            conn.executemany(
                "INSERT INTO ctg_capture_metric (capture_id, team, metric, value)"
                " VALUES (?, ?, ?, ?)",
                [(result.capture_id, r.team, m, v) for r in table.rows
                 for m, v in r.metrics.items()],
            )  # fmt: skip
    return result


def slate_cutoff_utc(slate_date: str) -> datetime:
    """Eastern midnight that opens the slate date: anything downloaded later is unusable."""
    return et_midnight_utc(date.fromisoformat(slate_date))


def snapshot_asof(
    conn: sqlite3.Connection, table: str, season: str, slate_date: str,
    season_type: str = "regular",
) -> Snapshot | None:  # fmt: skip
    """Latest season-table capture taken strictly before the slate's Eastern midnight."""
    row = conn.execute(
        "SELECT capture_id, as_of_utc FROM ctg_capture WHERE table_name = ? AND season = ?"
        " AND season_type = ? AND team IS NULL AND as_of_utc < ?"
        " ORDER BY as_of_utc DESC, capture_id DESC LIMIT 1",
        (table, season, season_type, _iso(slate_cutoff_utc(slate_date))),
    ).fetchone()
    if row is None:
        return None
    teams: dict[str, dict[str, float | None]] = {}
    for team, metric, value in conn.execute(
        "SELECT team, metric, value FROM ctg_capture_metric WHERE capture_id = ?", (row[0],)
    ):
        teams.setdefault(team, {})[metric] = value
    return Snapshot(datetime.fromisoformat(row[1]), teams)


def _insert_versions(conn: sqlite3.Connection, capture_id: int, games: Sequence[CTGGame]) -> None:
    """No transaction of its own: callers wrap it so the capture and rows commit together."""
    conn.executemany(
        "INSERT INTO ctg_game_version (capture_id, season, season_type, game_date, team, opp,"
        " venue, team_pts, opp_pts, off_pp100, def_pp100, metrics_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (capture_id, g.season, g.season_type, g.game_date.isoformat(), g.team, g.opp,
             g.venue, g.team_pts, g.opp_pts, g.off_pp100, g.def_pp100,
             json.dumps(g.metrics, sort_keys=True))
            for g in games
        ],
    )  # fmt: skip


def import_gamelog(
    conn: sqlite3.Connection,
    games: Sequence[CTGGame],
    *,
    file_sha256: str,
    as_of_utc: datetime,
    source_path: str | None = None,
) -> ImportResult:
    """Store one game-log capture and its game rows in a single transaction."""
    _check_as_of(as_of_utc)
    if not games:
        raise CTGParseError("a game-log capture needs at least one game")
    season_types = {g.season_type for g in games}
    cap = _Capture(
        file_sha256, "gamelog", games[0].season,
        season_types.pop() if len(season_types) == 1 else "mixed",
        as_of_utc, len(games), games[0].team, source_path,
    )  # fmt: skip
    with conn:
        result = _insert_capture(conn, cap)
        if result.inserted:
            _insert_versions(conn, result.capture_id, games)
    return result


def store_gamelog(
    conn: sqlite3.Connection,
    games: Sequence[CTGGame],
    *,
    file_sha256: str,
    as_of_utc: datetime | None = None,
) -> ImportResult:
    """``import_gamelog`` with ``as_of_utc`` defaulting to now."""
    return import_gamelog(
        conn, games, file_sha256=file_sha256, as_of_utc=as_of_utc or datetime.now(UTC)
    )


# One static, fully parameterised query: a NULL parameter switches that filter off.
# Per game it keeps the latest version the cutoff allows (ties: the later import). The label is
# the most specific one any allowed version carries (playoffs > regular > unspecified), because
# one scraper view calls playoff games "regular"; the venue is the latest one any version knew.
_LOAD_GAMES = """
WITH eligible AS (
    SELECT v.*, c.as_of_utc AS captured
    FROM ctg_game_version AS v
    JOIN ctg_capture AS c ON c.capture_id = v.capture_id
    WHERE (? IS NULL OR v.season = ?)
      AND (? IS NULL OR c.as_of_utc < ?)
),
ranked AS (
    SELECT eligible.*,
           ROW_NUMBER() OVER (
               PARTITION BY season, game_date, team ORDER BY captured DESC, capture_id DESC
           ) AS pick,
           MAX(CASE season_type WHEN 'playoffs' THEN 2 WHEN 'regular' THEN 1 ELSE 0 END) OVER (
               PARTITION BY season, game_date, team
           ) AS label,
           FIRST_VALUE(venue) OVER (
               PARTITION BY season, game_date, team
               ORDER BY venue IS NULL, captured DESC, capture_id DESC
           ) AS known_venue
    FROM eligible
)
SELECT season,
       CASE label WHEN 2 THEN 'playoffs' WHEN 1 THEN 'regular' ELSE 'unspecified' END,
       game_date, team, opp, known_venue, team_pts, opp_pts, off_pp100, def_pp100, metrics_json
FROM ranked
WHERE pick = 1
ORDER BY game_date, team
"""


def load_team_games(
    conn: sqlite3.Connection,
    *,
    season: str | None = None,
    as_of_before: datetime | None = None,
) -> list[CTGGame]:
    """One row per stored game, oldest first.

    ``as_of_before`` reads the store as it stood just before that moment: each game comes from
    the latest capture dated strictly earlier, and games first captured later are absent.
    """
    cutoff = _iso(as_of_before) if as_of_before is not None else None
    rows = conn.execute(_LOAD_GAMES, (season, season, cutoff, cutoff)).fetchall()
    return [
        CTGGame(r[0], r[1], date.fromisoformat(r[2]), r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                json.loads(r[10]))
        for r in rows
    ]  # fmt: skip
