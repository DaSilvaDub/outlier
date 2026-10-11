"""Archive manifest for historical NFL slates (#231 part c).

``<nfl_dir>/archive/manifest.jsonl`` holds one JSON object per line, one line per
archived file. Each line records a file where it already sits. Archiving never
copies, moves or rewrites the file itself.

Fields (every one is validated by :func:`parse_entry`):

- ``slate``: slate key (``YYYY-MM-DD``, or the pipeline's ``YYYY-MM-DD_<window>``
  suffix). ``null`` only for ``nflverse_boxscore``, which is per season.
- ``role``: one of :data:`ROLES`.
- ``path``: POSIX path relative to ``nfl_dir``, or absolute when the file lives
  outside it (the nflverse cache is ``~/.cache/outlier_nflverse`` by default).
- ``sha256`` / ``size``: the bytes on disk when the file was recorded.
- ``captured_at``: ISO-8601 UTC with an offset. This is when the data was
  captured: the run's ``run_started_utc`` for pregame feeds, and the sidecar's
  ``fetched_at`` for nflverse files.
- ``source``: where the bytes came from (``outlier_api``, ``run_writer``,
  ``nflverse``).
- ``run_id``: the pipeline run (pregame roles); ``null`` for nflverse.
- ``season``: NFL season (int).
- ``fetched_at``: ``nflverse_boxscore`` only, and required there. It comes from the
  ``<file>.meta.json`` sidecar that #252 writes (``url, fetched_at, sha256,
  size, rows``).

Shapes checked against real code: run bundles ``runs/<run_id>/raw/{schedule,
event_markets,player_props}.json`` plus ``manifest.json`` with
``context.{slate_date,season,run_started_utc}`` (``run_writer.py``,
``pipeline.py``). Dated pointers ``normalized/nfl_run_pointer_<suffix>.json``
carry ``run_id``. The nflverse cache files ``stats_player_week_<season>.csv`` and
``games.csv`` come from ``boxscore_nflverse.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

MANIFEST_RELPATH = "archive/manifest.jsonl"

PREGAME_ROLES = {
    "pregame_schedule": "raw/schedule.json",
    "pregame_event_markets": "raw/event_markets.json",
    "pregame_player_props": "raw/player_props.json",
    "run_manifest": "manifest.json",
}
NFLVERSE_ROLE = "nflverse_boxscore"
ROLES = (*PREGAME_ROLES, NFLVERSE_ROLE)
REQUIRED_SLATE_ROLES = tuple(PREGAME_ROLES)
SOURCES = ("outlier_api", "run_writer", "nflverse")

_SLATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(_[A-Za-z0-9_-]+)?$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ArchiveError(ValueError):
    """A manifest line or a record request is invalid."""


class ArchiveConflict(ArchiveError):
    """Recording would overwrite an existing manifest entry."""


@dataclass(frozen=True)
class ArchiveEntry:
    slate: str | None
    role: str
    path: str
    sha256: str
    size: int
    captured_at: str
    source: str
    season: int
    run_id: str | None = None
    fetched_at: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.role, self.path)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


def _aware(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ArchiveError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ArchiveError(f"{field} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ArchiveError(f"{field} has no UTC offset: {value!r}")
    return value


def parse_entry(raw: Any) -> ArchiveEntry:
    """Validate one manifest object; raise :class:`ArchiveError` naming the field."""
    if not isinstance(raw, dict):
        raise ArchiveError("entry is not a JSON object")
    allowed = set(ArchiveEntry.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ArchiveError(f"unknown fields: {sorted(unknown)}")
    role = raw.get("role")
    if role not in ROLES:
        raise ArchiveError(f"role must be one of {list(ROLES)}, got {role!r}")
    slate = raw.get("slate")
    if role == NFLVERSE_ROLE:
        if slate is not None and not (isinstance(slate, str) and _SLATE_RE.match(slate)):
            raise ArchiveError(f"slate is not a slate key: {slate!r}")
    elif not (isinstance(slate, str) and _SLATE_RE.match(slate)):
        raise ArchiveError(f"slate is required for {role}: {slate!r}")
    path = raw.get("path")
    if not isinstance(path, str) or not path or "\\" in path:
        raise ArchiveError(f"path must be a non-empty POSIX path: {path!r}")
    sha = raw.get("sha256")
    if not (isinstance(sha, str) and _SHA_RE.match(sha)):
        raise ArchiveError(f"sha256 must be 64 lowercase hex: {sha!r}")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ArchiveError(f"size must be a non-negative int: {size!r}")
    season = raw.get("season")
    if not isinstance(season, int) or isinstance(season, bool) or not 1999 <= season <= 2100:
        raise ArchiveError(f"season must be an int year: {season!r}")
    source = raw.get("source")
    if source not in SOURCES:
        raise ArchiveError(f"source must be one of {list(SOURCES)}, got {source!r}")
    run_id = raw.get("run_id")
    if role != NFLVERSE_ROLE and not (isinstance(run_id, str) and run_id):
        raise ArchiveError(f"run_id is required for {role}")
    if run_id is not None and not isinstance(run_id, str):
        raise ArchiveError("run_id must be a string")
    fetched_at = raw.get("fetched_at")
    if role == NFLVERSE_ROLE:
        fetched_at = _aware(fetched_at, "fetched_at")
    elif fetched_at is not None:
        raise ArchiveError(f"fetched_at is only for {NFLVERSE_ROLE}")
    return ArchiveEntry(
        slate=slate, role=role, path=path, sha256=sha, size=size,
        captured_at=_aware(raw.get("captured_at"), "captured_at"), source=source,
        season=season, run_id=run_id, fetched_at=fetched_at,
    )


def manifest_path(nfl_dir: Path | str) -> Path:
    return Path(nfl_dir) / MANIFEST_RELPATH


@dataclass(frozen=True)
class ManifestProblem:
    line: int
    reason: str


def read_manifest(path: Path | str) -> tuple[list[ArchiveEntry], list[ManifestProblem]]:
    """Every valid entry, plus one problem per unreadable or invalid line. Never writes."""
    p = Path(path)
    if not p.exists():
        return [], []
    entries: list[ArchiveEntry] = []
    problems: list[ManifestProblem] = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(parse_entry(json.loads(line)))
        except (ValueError, ArchiveError) as exc:
            problems.append(ManifestProblem(n, str(exc)))
    return entries, problems


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_path(nfl_dir: Path | str, entry_path: str) -> Path:
    p = Path(entry_path)
    return p if p.is_absolute() else Path(nfl_dir) / p


# ---------------------------------------------------------------------------
# Writer: records files where they sit, appends to the manifest, never rewrites
# ---------------------------------------------------------------------------

def _rel(nfl_dir: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path(nfl_dir).resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def record_file(
    nfl_dir: Path | str, path: Path | str, *, role: str, slate: str | None, source: str,
    season: Any, captured_at: Any, run_id: str | None = None, fetched_at: str | None = None,
) -> tuple[ArchiveEntry, bool]:
    """Append one entry for ``path``. Returns ``(entry, newly_recorded)``.

    ``season`` and ``captured_at`` may come straight from a run manifest; they are
    validated by :func:`parse_entry` like every other field. The file is only read. If ``(role, path)`` is already recorded with the same
    bytes, nothing is written. If it is recorded with different bytes,
    :class:`ArchiveConflict` is raised and the manifest stays as it was.
    """
    from outlier_nfl.utils import file_lock

    root = Path(nfl_dir)
    src = Path(path)
    if not src.is_file():
        raise ArchiveError(f"not a file: {src}")
    entry = parse_entry({
        "slate": slate, "role": role, "path": _rel(root, src), "sha256": sha256_file(src),
        "size": src.stat().st_size, "captured_at": captured_at, "source": source,
        "season": season, "run_id": run_id, "fetched_at": fetched_at,
    })
    mpath = manifest_path(root)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(mpath):
        existing, problems = read_manifest(mpath)
        if problems:
            raise ArchiveError(
                f"{mpath} has {len(problems)} invalid line(s) (first: line {problems[0].line}: "
                f"{problems[0].reason}); fix it before recording")
        for old in existing:
            if old.key() == entry.key():
                if (old.sha256, old.size) == (entry.sha256, entry.size):
                    return old, False
                raise ArchiveConflict(
                    f"{entry.path} ({role}) is already archived with sha256 {old.sha256}; "
                    f"the file on disk now has {entry.sha256}. The archived entry is kept; "
                    "archive a copy under a new path instead")
        with mpath.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(entry.to_json() + "\n")
    return entry, True


@dataclass
class RecordResult:
    recorded: list[ArchiveEntry]
    already: list[ArchiveEntry]
    conflicts: list[str]
    missing: list[str]


def _empty_result() -> RecordResult:
    return RecordResult([], [], [], [])


def record_run(nfl_dir: Path | str, run_id: str, slate: str | None = None) -> RecordResult:
    """Record a committed run bundle's pregame feeds and its manifest.

    Each raw file must still match the sha256 the run manifest sealed, so a file
    edited after the run is never archived as that run's capture.
    """
    root = Path(nfl_dir)
    run_dir = root / "runs" / run_id
    result = _empty_result()
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result.missing.append(f"{run_dir / 'manifest.json'}: {exc}")
        return result
    ctx = manifest.get("context") or {}
    # Key by the pointer's slate name (e.g. 2026-10-04_early), the same key check uses.
    pointer_slates = [k for k, v in pointed_runs(root).items() if v == run_id]
    slate_key = slate or (pointer_slates[0] if pointer_slates else ctx.get("slate_date"))
    captured = ctx.get("run_started_utc") or manifest.get("created_utc")
    season = ctx.get("season")
    sealed = {a.get("name"): a.get("sha256") for a in manifest.get("artifacts") or []}
    for role, name in PREGAME_ROLES.items():
        f = run_dir / name
        if not f.is_file():
            result.missing.append(f"{run_id}/{name}")
            continue
        if name != "manifest.json" and sealed.get(name) != sha256_file(f):
            result.conflicts.append(f"{run_id}/{name}: bytes differ from the run manifest")
            continue
        try:
            entry, new = record_file(
                root, f, role=role, slate=slate_key, season=season, captured_at=captured,
                run_id=run_id, source="run_writer" if role == "run_manifest" else "outlier_api")
        except ArchiveConflict as exc:
            result.conflicts.append(str(exc))
            continue
        except ArchiveError as exc:
            result.missing.append(f"{run_id}/{name}: {exc}")
            continue
        (result.recorded if new else result.already).append(entry)
    return result


def pointed_runs(nfl_dir: Path | str) -> dict[str, str]:
    """``{slate suffix: run_id}`` from the dated run pointers (``latest`` excluded)."""
    out: dict[str, str] = {}
    for p in sorted((Path(nfl_dir) / "normalized").glob("nfl_run_pointer_*.json")):
        suffix = p.stem[len("nfl_run_pointer_"):]
        if suffix == "latest" or not _SLATE_RE.match(suffix):
            continue
        try:
            run_id = json.loads(p.read_text(encoding="utf-8"))["run_id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(run_id, str) and run_id:
            out[suffix] = run_id
    return out


def record_pointed_runs(nfl_dir: Path | str) -> RecordResult:
    total = _empty_result()
    for slate, run_id in pointed_runs(nfl_dir).items():
        r = record_run(nfl_dir, run_id, slate)
        total.recorded += r.recorded
        total.already += r.already
        total.conflicts += r.conflicts
        total.missing += r.missing
    return total


def nflverse_files(cache_dir: Path | str, season: int) -> list[Path]:
    cache = Path(cache_dir)
    files = [cache / f"stats_player_week_{season}.csv", cache / "games.csv"]
    return [p for f in files for p in (f, f.with_name(f.name + ".meta.json"))]


NFLVERSE_ARCHIVE_DIR = "archive/nflverse"


def _write_once(dest: Path, data: bytes) -> bool:
    """Create ``dest`` from the in-memory ``data`` once. True if written, False if identical.

    Raises :class:`ArchiveConflict` when ``dest`` already exists with other bytes.
    The buffer goes to a unique temp file and is renamed into place with
    ``os.replace``, so a crash never leaves a half-written copy. The rename only
    happens when ``dest`` didn't exist, and the folder name is the content's
    sha256, so any other writer racing for the same path is writing the same bytes.
    """
    from outlier_nfl.utils import unique_temp_path

    if dest.exists():
        if dest.read_bytes() == data:
            return False
        raise ArchiveConflict(f"{dest} already exists with different bytes; it is kept")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = unique_temp_path(dest)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def record_nflverse(nfl_dir: Path | str, cache_dir: Path | str, seasons: list[int]) -> RecordResult:
    """Copy the nflverse box-score inputs and their #252 sidecars into the archive.

    The cache swaps in new bytes at the same path on every refresh, so each
    validated CSV and its sidecar are copied to
    ``<nfl_dir>/archive/nflverse/<csv sha256>/<name>``, written once and never
    replaced. The entry records that copy. A CSV is archived only when its bytes
    match its sidecar's ``sha256`` exactly. A match on ``previous_sha256`` alone means
    a refresh is in progress, and that CSV is refused. ``fetched_at`` and ``captured_at`` come from the
    sidecar.
    """
    root = Path(nfl_dir)
    result = _empty_result()
    for season in seasons:
        files = nflverse_files(cache_dir, season)
        for csv_path, side in ((files[0], files[1]), (files[2], files[3])):
            # Read each file exactly once; hash, check and copy those same buffers.
            try:
                side_data = side.read_bytes()
                data = csv_path.read_bytes()
            except OSError:
                result.missing.append(f"{csv_path} (and its .meta.json sidecar)")
                continue
            try:
                meta = json.loads(side_data)
                want, fetched_at = str(meta["sha256"]), str(meta["fetched_at"])
            except (ValueError, KeyError, TypeError):
                result.conflicts.append(f"{side}: unreadable sidecar; not archived")
                continue
            got = hashlib.sha256(data).hexdigest()
            if got != want:
                # Since #236 a refresh writes the sidecar first, so a CSV matching only
                # previous_sha256 is an in-progress refresh: never archive it.
                mid = got == meta.get("previous_sha256")
                result.conflicts.append(
                    f"{csv_path}: bytes do not match its sidecar sha256"
                    + (" (a refresh is in progress: the sidecar is new, the CSV is the "
                       "previous copy)" if mid else "")
                    + "; not archived, retry after the refresh finishes")
                continue
            meta_sha = want
            folder = root / NFLVERSE_ARCHIVE_DIR / meta_sha
            for name, payload in ((csv_path.name, data), (side.name, side_data)):
                dest = folder / name
                try:
                    _write_once(dest, payload)
                    entry, new = record_file(
                        root, dest, role=NFLVERSE_ROLE, slate=None, source="nflverse",
                        season=season, captured_at=fetched_at, fetched_at=fetched_at)
                except ArchiveConflict as exc:
                    result.conflicts.append(str(exc))
                    continue
                (result.recorded if new else result.already).append(entry)
    return result


# ---------------------------------------------------------------------------
# Completeness checker (read-only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Gap:
    slate: str | None
    role: str | None
    kind: str  # missing_role | pointer_run_not_archived | file_missing | sha_mismatch
    #           | size_mismatch | invalid_line | missing_nflverse
    detail: str


@dataclass
class CheckReport:
    slates: list[str]
    seasons: list[int]
    entries: int
    gaps: list[Gap]

    @property
    def complete_slates(self) -> list[str]:
        bad = {g.slate for g in self.gaps}
        return [s for s in self.slates if s not in bad]


def check_archive(nfl_dir: Path | str, slates: list[str] | None = None,
                  seasons: list[int] | None = None) -> CheckReport:
    """Compare the manifest with the expected slates and the files on disk.

    The expected slates are every dated run pointer plus ``slates``. This only
    reads files.
    """
    root = Path(nfl_dir)
    entries, problems = read_manifest(manifest_path(root))
    pointers = pointed_runs(root)
    expected = sorted(set(pointers) | set(slates or []))
    gaps = [Gap(None, None, "invalid_line", f"manifest line {p.line}: {p.reason}")
            for p in problems]

    by_slate: dict[str, list[ArchiveEntry]] = {}
    for e in entries:
        if e.slate is not None:
            by_slate.setdefault(e.slate, []).append(e)
    for slate in expected:
        have = by_slate.get(slate, [])
        roles = {e.role for e in have}
        for role in REQUIRED_SLATE_ROLES:
            if role not in roles:
                gaps.append(Gap(slate, role, "missing_role", f"no {role} archived"))
        run_id = pointers.get(slate)
        if run_id and have and run_id not in {e.run_id for e in have}:
            gaps.append(Gap(slate, None, "pointer_run_not_archived",
                            f"pointer names run {run_id}; archived runs: "
                            f"{sorted({e.run_id or '' for e in have})}"))

    for season in seasons or []:
        nv = [e for e in entries if e.role == NFLVERSE_ROLE]
        names_this_season = {Path(e.path).name for e in nv if e.season == season}
        names_any = {Path(e.path).name for e in nv}
        need = {f"stats_player_week_{season}.csv": names_this_season,
                f"stats_player_week_{season}.csv.meta.json": names_this_season,
                "games.csv": names_any, "games.csv.meta.json": names_any}
        for name, pool in need.items():
            if name not in pool:
                gaps.append(Gap(None, NFLVERSE_ROLE, "missing_nflverse",
                                f"season {season}: {name} not archived"))

    for e in entries:
        f = resolve_path(root, e.path)
        if not f.is_file():
            gaps.append(Gap(e.slate, e.role, "file_missing", e.path))
        elif f.stat().st_size != e.size:
            gaps.append(Gap(e.slate, e.role, "size_mismatch",
                            f"{e.path}: {f.stat().st_size} bytes, archived {e.size}"))
        elif sha256_file(f) != e.sha256:
            gaps.append(Gap(e.slate, e.role, "sha_mismatch", e.path))
    return CheckReport(expected, sorted(seasons or []), len(entries), gaps)
