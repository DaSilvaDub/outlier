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
  (default 20 GB).
- A whole invocation adds fewer than 50 MB, including archive manifests.
- Retain three verified complete runs per sport; legacy/database archives are untouched.
- Copy errors are raised, and main() exits 1 when any sport fails.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import stat
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
DEFAULT_MIN_FREE_GB = 20.0
DEFAULT_RETAIN_RUNS = 3
MAX_ARCHIVE_BYTES = 50_000_000 - 1
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


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    if path.is_symlink():
        return True
    if not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    # OneDrive placeholders are files/directories, not redirecting links. Windows
    # reserves sixteen CLOUD tags (CLOUD through CLOUD_F); reject all other tags.
    tag = getattr(info, "st_reparse_tag", 0)
    return (tag & ~0x0000F000) != 0x9000001A


def _check_tree_path(path: Path, root: Path) -> None:
    """Allow a junction at the explicitly selected root, never beneath it."""
    root = root.resolve()
    if not path.resolve().is_relative_to(root):
        raise OrganizerError(f"Path escapes selected root: {path}")
    current = path.absolute()
    # The caller passes resolved roots, so every existing component below it is checked.
    while current != root:
        if (current.exists() or current.is_symlink()) and _is_reparse(current):
            raise OrganizerError(f"Reparse points are not archive inputs/targets: {current}")
        if current.parent == current:
            raise OrganizerError(f"Path is outside selected root: {path}")
        current = current.parent


def _verified_archives(older_sport_dir: Path) -> list[Path]:
    """Only runs whose regular files exactly match their completion receipt may rotate."""
    verified = []
    if not older_sport_dir.is_dir():
        return verified
    for run in older_sport_dir.glob("Run_*"):
        try:
            _check_tree_path(run, older_sport_dir)
            if not run.is_dir():
                continue
            receipt = run / MANIFEST_NAME
            if not receipt.is_file() or _is_reparse(receipt):
                continue
            manifest = json.loads(receipt.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or not manifest:
                continue
            actual = {}
            safe = True
            for path in run.rglob("*"):
                _check_tree_path(path, older_sport_dir)
                if path.is_file() and path != receipt:
                    if is_excluded(path.name):
                        safe = False
                        break
                    info = path.stat()
                    actual[path.relative_to(run).as_posix()] = [info.st_size, info.st_mtime_ns]
            if safe and actual == manifest:
                verified.append(run)
        except (OSError, ValueError, OrganizerError):
            continue
    return sorted(verified, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)


def rotate_archives(older_sport_dir: Path, retain_runs: int) -> None:
    if retain_runs < 1:
        raise OrganizerError("retain_runs must be at least 1")
    # Verify both the resolved absolute boundary and all reparse points before deletion.
    for run in _verified_archives(older_sport_dir)[retain_runs:]:
        _check_tree_path(run, older_sport_dir)
        shutil.rmtree(run)


def build_manifest(fresh_sport_dir: Path) -> Manifest:
    """Map each archivable file (relative posix path) to [size, mtime_ns]."""
    manifest: Manifest = {}
    for sub in SUBDIRS:
        src_sub = fresh_sport_dir / sub
        _check_tree_path(src_sub, fresh_sport_dir)
        if not src_sub.is_dir():
            continue
        for path in src_sub.rglob("*"):
            _check_tree_path(path, fresh_sport_dir)
            if path.is_file() and not is_excluded(path.name):
                st = path.stat()
                manifest[path.relative_to(fresh_sport_dir).as_posix()] = [
                    st.st_size,
                    st.st_mtime_ns,
                ]
    return manifest


def _latest_manifest(older_sport_dir: Path) -> Manifest | None:
    """Manifest of the newest *complete* archive (the manifest is written last)."""
    for run_dir in _verified_archives(older_sport_dir):
        return json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    return None


def _check_free_space(
    target: Path, needed: int, min_free_gb: float, disk_usage: DiskUsageFn
) -> None:
    free = disk_usage(target).free
    if free - needed <= min_free_gb * _GB:
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
    retain_runs: int = DEFAULT_RETAIN_RUNS,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
) -> ArchiveResult:
    """Archive Fresh_Data/<sport> to Older_Data when it changed, then ensure fresh subdirs exist."""
    del slate_date  # accepted for CLI compatibility; archive naming uses wall-clock time
    sport = sport.upper()
    if sport not in SPORTS:
        raise OrganizerError(f"Unsupported sport: {sport}")
    if retain_runs < 1 or min_free_gb < 0 or max_archive_bytes < 0:
        raise OrganizerError("Invalid archive limits")
    today_root = today_root.resolve()
    disk_usage = disk_usage or shutil.disk_usage
    fresh_sport_dir = today_root / "Fresh_Data" / sport
    older_sport_dir = today_root / "Older_Data" / sport

    _check_tree_path(fresh_sport_dir, today_root)
    _check_tree_path(older_sport_dir, today_root)
    manifest = build_manifest(fresh_sport_dir)
    if not manifest:
        result = ArchiveResult(sport, "empty")
    elif manifest == _latest_manifest(older_sport_dir):
        result = ArchiveResult(sport, "unchanged")
    else:
        manifest_text = json.dumps(manifest, sort_keys=True)
        needed = sum(size for size, _mtime in manifest.values()) + len(manifest_text.encode("utf-8"))
        if needed > max_archive_bytes:
            raise OrganizerError(
                f"Archive needs {needed} bytes; remaining invocation budget is {max_archive_bytes} bytes"
            )
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
            # Refuse a source changed during copying rather than certify a partial/stale run.
            if build_manifest(archive_dest) != manifest or build_manifest(fresh_sport_dir) != manifest:
                raise OrganizerError("Archive input changed during copying")
            # Written last (atomically): a run without a manifest is incomplete and never the baseline.
            tmp_manifest = archive_dest / f"{MANIFEST_NAME}.tmp"
            tmp_manifest.write_bytes(manifest_text.encode("utf-8"))
            tmp_manifest.replace(archive_dest / MANIFEST_NAME)
        except BaseException:
            # Remove only the partial copy this call created, so retries cannot stack them.
            shutil.rmtree(archive_dest, ignore_errors=True)
            raise
        result = ArchiveResult(sport, "archived", archive_dest, needed)

    rotate_archives(older_sport_dir, retain_runs)
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
    parser.add_argument("--retain-runs", type=int, default=DEFAULT_RETAIN_RUNS)
    args = parser.parse_args(argv)

    target_sports = SPORTS if args.sport == "ALL" else [args.sport]
    failures = 0
    remaining_bytes = MAX_ARCHIVE_BYTES
    for sp in target_sports:
        try:
            result = archive_and_refresh_sport(
                sp, args.date, today_root=args.today_root, min_free_gb=args.min_free_gb,
                retain_runs=args.retain_runs, max_archive_bytes=remaining_bytes,
            )
        except (OrganizerError, OSError) as exc:  # shutil.Error subclasses OSError
            failures += 1
            print(f"[FAIL] {sp}: archiving aborted: {exc}", file=sys.stderr)
            break
        remaining_bytes -= result.bytes_copied
        if result.status == "archived":
            print(
                f"[OK] {sp}: archived {result.bytes_copied / 1024**2:.1f} MB to {result.archive_dir}"
            )
        else:
            print(f"[OK] {sp}: no archive needed ({result.status})")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
