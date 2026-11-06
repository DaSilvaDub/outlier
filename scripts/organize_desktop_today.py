"""Organize sports analytics deliverables into Desktop Today & Drive Today folders.

Enforces Deliverable Export Invariants:
- Fresh Deliverables: Today/Fresh_Data/<SPORT>/ (01_Packs, 02_Full_Pipeline, 03_Early_Pipeline, 04_Reports, 05_Audits).
- Historical Archives: Previous runs copied to Today/Older_Data/<SPORT>/Run_<TIMESTAMP>/ before fresh generation.
- Handles sports: NBA, NFL, NCAA, ALL.

Disk guards (added after the 2026-11-03 disk-full incident, when 11 identical
591 MB ledger copies filled C:):
- Database files (SQLite + WAL/SHM/journal) are never archived. They are living
  stores of record, not run deliverables; Fresh_Data keeps its copy.
- A run is archived only when the Fresh tree differs from the last complete
  archive (compared by relative path, size, and mtime).
- Archiving refuses to start when it would leave less than --min-free-gb free
  (default 5 GB, matching NBA-SYS-001 in export_nba_extra_pack.py).
- Copy errors are raised, and main() exits 1 when any sport fails.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DESKTOP_TODAY = Path(r"C:\Users\dasil\Desktop\Today")
DRIVE_ROOTS = [
    Path(r"G:\My Drive\today"),
    Path(r"C:\Users\dasil\My Drive (dasilvadub@gmail.com)\today"),
]
SPORTS = ["NBA", "NFL", "NCAA"]
SUBDIRS = ["01_Packs", "02_Full_Pipeline", "03_Early_Pipeline", "04_Reports", "05_Audits"]

EXCLUDED_SUFFIXES = (".sqlite3", ".sqlite", ".db", "-wal", "-shm", "-journal")
MANIFEST_NAME = "_archive_manifest.json"
DEFAULT_MIN_FREE_GB = 5.0
_GB = 1024**3

DiskUsageFn = Callable[[Path], "shutil._ntuple_diskusage"]
Manifest = dict[str, list[int]]


class OrganizerError(RuntimeError):
    """Base class for failures that must stop archiving for a sport."""


class InsufficientDiskSpace(OrganizerError):
    """Archiving would push free space below the configured floor."""


@dataclass(frozen=True)
class ArchiveResult:
    sport: str
    status: str  # "archived" | "unchanged" | "empty"
    archive_dir: Path | None = None
    bytes_copied: int = 0


def get_drive_today() -> Path | None:
    for dr in DRIVE_ROOTS:
        if dr.parent.exists():
            return dr
    return None


def is_excluded(name: str) -> bool:
    return name.lower().endswith(EXCLUDED_SUFFIXES)


def _ignore_excluded(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if is_excluded(n)}


def build_manifest(fresh_sport_dir: Path) -> Manifest:
    """Map each archivable file (relative posix path) to [size, mtime_ns]."""
    manifest: Manifest = {}
    for sub in SUBDIRS:
        src_sub = fresh_sport_dir / sub
        if not src_sub.is_dir():
            continue
        for path in src_sub.rglob("*"):
            if path.is_file() and not is_excluded(path.name):
                st = path.stat()
                manifest[path.relative_to(fresh_sport_dir).as_posix()] = [
                    st.st_size,
                    st.st_mtime_ns,
                ]
    return manifest


def _latest_manifest(older_sport_dir: Path) -> Manifest | None:
    """Manifest of the newest *complete* archive (the manifest is written last)."""
    if not older_sport_dir.is_dir():
        return None
    for run_dir in sorted(older_sport_dir.glob("Run_*"), reverse=True):
        manifest_path = run_dir / MANIFEST_NAME
        if manifest_path.is_file():
            try:
                return json.loads(manifest_path.read_text(encoding="utf-8"))
            except ValueError:  # truncated/corrupt manifest: treat that run as incomplete
                continue
    return None


def _check_free_space(
    target: Path, needed: int, min_free_gb: float, disk_usage: DiskUsageFn
) -> None:
    free = disk_usage(target).free
    if free - needed < min_free_gb * _GB:
        raise InsufficientDiskSpace(
            f"Insufficient disk space on {target.anchor or target}: {free / _GB:.2f} GB free, "
            f"archive needs {needed / _GB:.2f} GB and {min_free_gb:.1f} GB must remain free"
        )


def _unique_run_dir(older_sport_dir: Path, now: datetime.datetime) -> Path:
    base = older_sport_dir / f"Run_{now.strftime('%Y-%m-%d_%H%M%S')}"
    candidate, n = base, 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{n}")
        n += 1
    return candidate


def archive_and_refresh_sport(
    sport: str,
    slate_date: str | None = None,
    *,
    today_root: Path = DESKTOP_TODAY,
    disk_usage: DiskUsageFn | None = None,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    now: datetime.datetime | None = None,
) -> ArchiveResult:
    """Archive Fresh_Data/<sport> to Older_Data when it changed, then ensure fresh subdirs exist."""
    del slate_date  # accepted for CLI compatibility; archive naming uses wall-clock time
    sport = sport.upper()
    disk_usage = disk_usage or shutil.disk_usage
    fresh_sport_dir = today_root / "Fresh_Data" / sport
    older_sport_dir = today_root / "Older_Data" / sport

    manifest = build_manifest(fresh_sport_dir)
    if not manifest:
        result = ArchiveResult(sport, "empty")
    elif manifest == _latest_manifest(older_sport_dir):
        result = ArchiveResult(sport, "unchanged")
    else:
        needed = sum(size for size, _mtime in manifest.values())
        _check_free_space(today_root, needed, min_free_gb, disk_usage)
        archive_dest = _unique_run_dir(older_sport_dir, now or datetime.datetime.now())
        archive_dest.mkdir(parents=True)
        try:
            for sub in SUBDIRS:
                src_sub = fresh_sport_dir / sub
                if src_sub.is_dir():
                    shutil.copytree(
                        src_sub, archive_dest / sub, ignore=_ignore_excluded, dirs_exist_ok=True
                    )
            # Written last (atomically): a run without a manifest is incomplete and never the baseline.
            tmp_manifest = archive_dest / f"{MANIFEST_NAME}.tmp"
            tmp_manifest.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            tmp_manifest.replace(archive_dest / MANIFEST_NAME)
        except BaseException:
            # Remove only the partial copy this call created, so retries cannot stack them.
            shutil.rmtree(archive_dest, ignore_errors=True)
            raise
        result = ArchiveResult(sport, "archived", archive_dest, needed)

    for sub in SUBDIRS:
        (fresh_sport_dir / sub).mkdir(parents=True, exist_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--sport", default="ALL", choices=["NBA", "NFL", "NCAA", "ALL"], help="Target sport"
    )
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD")
    parser.add_argument(
        "--today-root", type=Path, default=DESKTOP_TODAY, help="Root holding Fresh_Data/Older_Data"
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=DEFAULT_MIN_FREE_GB,
        help="Free-space floor for archiving",
    )
    args = parser.parse_args(argv)

    target_sports = SPORTS if args.sport == "ALL" else [args.sport]
    failures = 0
    for sp in target_sports:
        try:
            result = archive_and_refresh_sport(
                sp, args.date, today_root=args.today_root, min_free_gb=args.min_free_gb
            )
        except (OrganizerError, OSError) as exc:  # shutil.Error subclasses OSError
            failures += 1
            print(f"[FAIL] {sp}: archiving aborted: {exc}", file=sys.stderr)
            continue
        if result.status == "archived":
            print(
                f"[OK] {sp}: archived {result.bytes_copied / 1024**2:.1f} MB to {result.archive_dir}"
            )
        else:
            print(f"[OK] {sp}: no archive needed ({result.status})")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
