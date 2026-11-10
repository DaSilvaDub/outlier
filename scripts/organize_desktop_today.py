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

Fresh_Data staging (only when --today-root is the real Desktop Today):
- Copies artifacts the refresh scripts already generated; never generates reports and
  never imports code from outside this repo (no Gemini skills, no nba-props-pipeline).
- Only undated files or files stamped with the active slate date are staged.
- Google Drive is staged only without --skip-drive and while no publication hold is
  active (outlier_scrapers.publication_hold). A held Drive stage still stages locally,
  then exits 3 so schedulers see the run was not published.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outlier_scrapers.publication_hold import (  # noqa: E402
    PublicationHoldError,
    publication_hold_active,
)

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


# --- Fresh_Data deliverable staging -------------------------------------------------------
#
# Staging only copies artifacts the refresh scripts already generated. It never generates
# reports (that belongs to the refresh scripts) and never imports code from outside this
# repo: the retired NBA Gemini generator and nba-props-pipeline are not reachable from here.

REPO_ROOT = Path(__file__).resolve().parent.parent
CFB_ROOT = Path(r"C:\Users\dasil\Dev\GitHub\cfb-analytics")
DATE_STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DRIVE_HELD_EXIT = 3

Source = tuple[Path, str]  # (file to copy, Fresh_Data subdir)


def _slate_date(value: str) -> str:
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid slate date {value!r}; use YYYY-MM-DD") from exc


def resolve_eastern_today() -> str:
    try:
        return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except ZoneInfoNotFoundError:  # Windows without tzdata
        return datetime.date.today().isoformat()


def safe_copy(src: Path, dst: Path, retries: int = 5, delay: float = 0.5) -> None:
    """copy2 with retries for transient Drive/OneDrive locks ([WinError 32]); raises on final failure."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        try:
            shutil.copy2(src, dst)
            return
        except PermissionError:
            if attempt == retries:
                raise
            time.sleep(delay * attempt)


def is_current_artifact(name: str, dates: list[str]) -> bool:
    """Undated files (rolling 'latest' outputs) or files stamped with one of the active dates."""
    stamps = DATE_STAMP_RE.findall(name)
    return not stamps or any(stamp in dates for stamp in stamps)


def _files(directory: Path, pattern: str = "*") -> list[Path]:
    return sorted(p for p in directory.glob(pattern) if p.is_file()) if directory.is_dir() else []


def is_stageable_csv(path: Path, dates: list[str]) -> bool:
    """A CSV needs at least one data row; one with a slate_date column must match an active date.

    Undated rolling CSVs can be left over from an earlier run (exporters keep the last good
    file when a fetch fails, and an empty-slate export leaves a header-only file), so the
    filename alone cannot prove they are current.
    """
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        first = next(reader, None)
        if first is None:
            return False
        if "slate_date" in (reader.fieldnames or []):
            return (first.get("slate_date") or "") in dates
    return True


def _current_exports(export_dir: Path, dates: list[str]) -> list[Source]:
    return [
        (f, "01_Packs")
        for f in _files(export_dir)
        if is_current_artifact(f.name, dates)
        and (f.suffix.lower() != ".csv" or is_stageable_csv(f, dates))
    ]


def nba_sources(repo_root: Path, slate_date: str) -> list[Source]:
    return _current_exports(repo_root / "data" / "NBA" / "exports", [slate_date])


def nfl_sources(repo_root: Path, slate_date: str) -> list[Source]:
    sources = _current_exports(repo_root / "data" / "NFL" / "exports", [slate_date])
    norm_dir = repo_root / "data" / "NFL" / "normalized"
    for pattern in (f"*_{slate_date}.*", "nfl_best_bets_latest.json", "summary_latest.json"):
        sources += [(f, "02_Full_Pipeline") for f in _files(norm_dir, pattern)]
    rep_dir = repo_root / "reports" / "NFL"
    for pattern in (f"{slate_date}_*", "Top_*.md", "Alt_Floors_latest.md", "Matchup_Boost_Rankings.csv"):
        sources += [
            (f, "05_Audits" if "audit" in f.name.lower() else "04_Reports")
            for f in _files(rep_dir, pattern)
        ]
    sources += [(f, "05_Audits") for f in _files(rep_dir / "05_Audits")]
    return sources


def ncaa_active_dates(cfb_root: Path, slate_date: str) -> list[str]:
    """The slate date, plus the next simulated slate when today's sim has no games."""
    sim_path = cfb_root / "data" / "reports" / slate_date / f"{slate_date}-master-v2-sim.json"
    if not sim_path.is_file():
        return [slate_date]
    try:
        sim = json.loads(sim_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OrganizerError(f"Corrupt CFB sim file {sim_path}: {exc}") from exc
    if sim.get("games"):
        return [slate_date]
    report_root = cfb_root / "data" / "reports"
    for cand in sorted(p for p in report_root.iterdir() if p.is_dir()):
        if DATE_STAMP_RE.fullmatch(cand.name) and cand.name > slate_date:
            if (cand / f"{cand.name}-master-v2-sim.json").is_file():
                return [slate_date, cand.name]
    return [slate_date]


def _cfb_report_subdir(path: Path) -> str:
    if "audit" in path.name.lower():
        return "05_Audits"
    return "04_Reports" if path.suffix == ".md" else "02_Full_Pipeline"


def ncaa_sources(repo_root: Path, cfb_root: Path, slate_date: str) -> list[Source]:
    dates = ncaa_active_dates(cfb_root, slate_date) if cfb_root.is_dir() else [slate_date]
    sources = _current_exports(repo_root / "data" / "NCAAFB" / "exports", dates)
    for d in dates:
        d_rep = cfb_root / "data" / "reports" / d
        sources += [(f, _cfb_report_subdir(f)) for f in _files(d_rep)]
        sources += [(f, "03_Early_Pipeline") for f in _files(d_rep / "snapshots")]
    sources += [(f, "05_Audits") for f in _files(cfb_root / "docs" / "scorecards", "lessons_learned.md")]
    return sources


def collect_sources(
    sport: str, slate_date: str, *, repo_root: Path = REPO_ROOT, cfb_root: Path = CFB_ROOT
) -> list[Source]:
    sp = sport.upper()
    if sp == "NBA":
        return nba_sources(repo_root, slate_date)
    if sp == "NFL":
        return nfl_sources(repo_root, slate_date)
    if sp == "NCAA":
        return ncaa_sources(repo_root, cfb_root, slate_date)
    raise OrganizerError(f"Unsupported sport for deliverable staging: {sport}")


def populate_sport_deliverables(
    sport: str,
    slate_date: str,
    roots: list[Path],
    *,
    repo_root: Path = REPO_ROOT,
    cfb_root: Path = CFB_ROOT,
) -> int:
    """Copy current artifacts into <root>/Fresh_Data/<SPORT>/<subdir> for every root; returns files copied."""
    sp = sport.upper()
    sources = collect_sources(sp, slate_date, repo_root=repo_root, cfb_root=cfb_root)
    copied = 0
    for root in roots:
        fresh = root / "Fresh_Data" / sp
        for src, subdir in sources:
            safe_copy(src, fresh / subdir / src.name)
            copied += 1
    return copied


def resolve_staging_roots(
    today_root: Path, *, skip_drive: bool, repo_root: Path = REPO_ROOT
) -> tuple[list[Path], bool]:
    """Local root always; Drive only when requested, mounted, and no publication hold is active.

    Returns (roots, drive_held). drive_held is True when Drive was requested and mounted but
    an active (or unreadable) publication hold blocked it.
    """
    roots = [today_root]
    if skip_drive:
        return roots, False
    drive_today = get_drive_today()
    if drive_today is None:
        return roots, False
    try:
        held = publication_hold_active(repo_root)
    except PublicationHoldError as exc:
        print(f"[HELD] unreadable publication hold, treating as active: {exc}", file=sys.stderr)
        held = True
    if held:
        return roots, True
    return [*roots, drive_today], False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--sport", default="ALL", choices=["NBA", "NFL", "NCAA", "ALL"], help="Target sport"
    )
    parser.add_argument("--date", default=None, type=_slate_date, help="Slate date YYYY-MM-DD")
    parser.add_argument(
        "--today-root", type=Path, default=DESKTOP_TODAY, help="Root holding Fresh_Data/Older_Data"
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=DEFAULT_MIN_FREE_GB,
        help="Free-space floor for archiving",
    )
    parser.add_argument(
        "--skip-drive",
        action="store_true",
        help="Stage Fresh_Data locally only; never write to Google Drive",
    )
    parser.add_argument(
        "--no-stage",
        action="store_true",
        help="Archive only; skip copying current artifacts into Fresh_Data",
    )
    args = parser.parse_args(argv)

    slate_date = args.date or resolve_eastern_today()
    stage = not args.no_stage and Path(args.today_root).resolve() == DESKTOP_TODAY.resolve()
    target_sports = SPORTS if args.sport == "ALL" else [args.sport]
    failures = 0
    drive_held = False
    for sp in target_sports:
        try:
            result = archive_and_refresh_sport(
                sp, slate_date, today_root=args.today_root, min_free_gb=args.min_free_gb
            )
            if stage:
                roots, held = resolve_staging_roots(args.today_root, skip_drive=args.skip_drive)
                drive_held = drive_held or held
                copied = populate_sport_deliverables(sp, slate_date, roots)
                print(f"[OK] {sp}: staged {copied} file copies into {len(roots)} root(s)")
        except (OrganizerError, OSError) as exc:  # shutil.Error subclasses OSError
            failures += 1
            print(f"[FAIL] {sp}: organizing aborted: {exc}", file=sys.stderr)
            continue
        if result.status == "archived":
            print(
                f"[OK] {sp}: archived {result.bytes_copied / 1024**2:.1f} MB to {result.archive_dir}"
            )
        else:
            print(f"[OK] {sp}: no archive needed ({result.status})")

    if failures:
        return 1
    if drive_held:
        print(
            "[HELD] Publication hold active: Fresh_Data staged locally only, Drive skipped.",
            file=sys.stderr,
        )
        return DRIVE_HELD_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
