"""Read-only sync of CTG game logs from the NBA Drive hub (``NBA_ROOT``) into the CTG store.

The hub is where ``nba-props-pipeline`` writes every NBA extract. It is read, never written, and
its ``cache`` folder (session cookies) is never opened. Parsing and storage live in
``outlier_nba.ingest.ctg``; this module only finds files, dates them and reports outcomes.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from outlier_nba.ingest.ctg import (
    ET,
    CTGGame,
    CTGIdentityError,
    CTGParseError,
    CTGUnusableError,
    et_midnight_utc,
    games_from_doc,
    import_gamelog,
    load_gamelog_doc,
    resolve_ctg_team,
)

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
    file_date: date | None  # None: the filename date is impossible (reported as unusable)


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


def _file_date(m: re.Match[str]) -> date | None:
    try:
        return date(int(m[3]), int(m[1]), int(m[2]))
    except ValueError:  # e.g. GAME_LOGS_02_30_2026.json
        return None


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
            found.append(HubFile(path, team, _file_date(m)))
    return sorted(found, key=lambda f: (f.file_date or date.min, str(f.path)))


def _scraped_at(doc: Mapping[str, Any], name: str) -> datetime | None:
    """The export's own ``scraped_at`` (naive, Eastern: the scraper runs on that clock)."""
    raw = doc.get("scraped_at")
    if raw is None or raw == "":
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise CTGUnusableError(f"{name}: bad scraped_at {raw!r}") from exc
    return ts.replace(tzinfo=ET) if ts.tzinfo is None else ts


def _capture_as_of(f: HubFile, doc: Mapping[str, Any], games: Sequence[CTGGame]) -> datetime:
    """The earliest moment the file can be assumed to exist: the latest of its own evidence.

    The Drive mtime alone is not trusted (a copy can keep an older one), so it is floored by
    the export's ``scraped_at``, the filename date and the day after its last game. Raises
    ``OSError`` if the file has gone since it was read.
    """
    floors = [datetime.fromtimestamp(f.path.stat().st_mtime, UTC)]
    scraped = _scraped_at(doc, f.path.name)
    if scraped is not None:
        floors.append(scraped)
    if f.file_date is not None:
        floors.append(et_midnight_utc(f.file_date))
    floors.append(et_midnight_utc(games[-1].game_date + timedelta(days=1)))
    return max(floors)


def _sync_one(conn: sqlite3.Connection, f: HubFile, root: Path, season: str | None) -> FileOutcome:
    team = f.claimed_team
    if team is None:
        detail = f"folder {f.path.parent.parent.name!r} is not a team"
        return FileOutcome(f.path, None, "skipped_unusable", detail)
    if f.file_date is None:
        return FileOutcome(f.path, team, "skipped_unusable", "impossible date in filename")
    try:
        raw = f.path.read_bytes()
        doc = load_gamelog_doc(raw, name=f.path.name)
        games = games_from_doc(doc, team=team, name=f.path.name)
        as_of = _capture_as_of(f, doc, games)
    except OSError as exc:  # locked by the sync client, or removed after the read
        return FileOutcome(f.path, team, "skipped_unusable", f"unreadable: {exc}")
    except CTGIdentityError as exc:
        return FileOutcome(f.path, team, "rejected_mislabeled", str(exc), exc.actual)
    except CTGParseError as exc:
        return FileOutcome(f.path, team, "skipped_unusable", str(exc))
    if as_of > datetime.now(UTC):
        return FileOutcome(f.path, team, "skipped_unusable", f"dated in the future ({as_of})")
    if season is not None and games[0].season != season:
        return FileOutcome(f.path, team, "other_season", games[0].season)
    result = import_gamelog(
        conn, games, file_sha256=hashlib.sha256(raw).hexdigest(), as_of_utc=as_of,
        source_path=f.path.relative_to(root).as_posix(),
    )  # fmt: skip
    if not result.inserted:
        return FileOutcome(f.path, team, "already_imported")
    return FileOutcome(f.path, team, "imported", n_rows=len(games))


def sync_from_hub(conn: sqlite3.Connection, root: Path, *, season: str | None = None) -> SyncReport:
    """Import every new game-log capture from the hub. Read-only on ``root``; safe to re-run."""
    if not root.is_dir():
        raise FileNotFoundError(f"NBA_ROOT not found: {root}")
    outcomes = tuple(_sync_one(conn, f, root, season) for f in discover_gamelogs(root))
    counts: Counter[str] = Counter({k: 0 for k in SYNC_OUTCOMES})
    counts.update(o.outcome for o in outcomes)
    return SyncReport(outcomes, dict(counts))
