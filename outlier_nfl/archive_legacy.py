"""Legacy slates in the archive (#231 part c follow-up, epic #222).

The slates from before the phase 1 run bundles (:data:`LEGACY_SLATES`) are never
rebuilt from source. ``record_legacy`` records each file they still have exactly
as it sits (sha256, size, modified time) and copies it once under
``archive/legacy/<sha256>/<name>``, the same way as the nflverse files: read once,
hash that buffer, temp file plus ``os.replace``, refuse on different bytes. The
original is only read. Nothing is fetched again.

``legacy_status`` (read-only) gives each legacy slate one status:

- ``legacy_regradable``: original prediction rows with a line, a price and a
  probability (finite ``model_p`` / ``p_model``, or a ``*_hit_rate`` field) exist
  (``nfl_high_prob_props_`` / ``nfl_calibrated_props_`` / ``nfl_matchup_props_``)
  for at least one game.
- ``unrecoverable``: they don't.

Original vs ``post_slate`` is decided per game, not per day. A file counts as an
original for a game only when its modified time is before that game's kickoff,
or when its bytes hash the same as a recorded copy of the same slate and role
whose modified time is.
Kickoffs come from the saved ``nfl_games_<date>.json`` (``records[].event_id`` /
``event_starts_at``), falling back to the rows' own ``event_starts_at``. Snapshot
rows (``nfl_prop_snapshots_<week>.jsonl`` keeps being appended to) are judged one
by one by their ``taken_at`` and ``run_id``; the file's modified time is ignored.
``cache/espn_injuries_filter_*`` and ``exports/nfl_only_filter_summary_*`` come
from the untracked helper scripts (report section 12) and get
``untracked_helper_output``: never pipeline output, never an original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from outlier_nfl import archive as ar
from outlier_nfl.snapshots import snapshot_path

LEGACY_SLATES = ("2026-09-27", "2026-10-04", "2026-10-05")
LEGACY_ARCHIVE_DIR = "archive/legacy"
PREDICTION_PREFIXES = ("nfl_high_prob_props_", "nfl_calibrated_props_", "nfl_matchup_props_")
CARD_PREFIX = "nfl_best_bets_"
HELPER_GLOBS = ("cache/espn_injuries_filter_{d}*", "exports/nfl_only_filter_summary_{d}*")
SLATE_GLOBS = ("normalized/*_{d}*", "exports/*_{d}*", "scorecard/*_{d}*")
_SCRIPT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_([A-Za-z]+)_([A-Za-z]+)_Game_Script\.md$")


def _season(slate: str) -> int:
    y, m = int(slate[:4]), int(slate[5:7])
    return y if m >= 3 else y - 1


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def default_reports_dir(nfl_dir: Path | str) -> Path:
    """``<repo>/reports/NFL`` for the usual ``<repo>/data/NFL``."""
    return Path(nfl_dir).resolve().parent.parent / "reports" / "NFL"


def legacy_files(nfl_dir: Path | str, slate: str,
                 reports_dir: Path | str | None = None) -> list[tuple[Path, str]]:
    """``(path, role)`` for every file the legacy slate still has. Only lists."""
    root = Path(nfl_dir)
    helpers = {p for g in HELPER_GLOBS for p in root.glob(g.format(d=slate)) if p.is_file()}
    files = {p for g in SLATE_GLOBS for p in root.glob(g.format(d=slate)) if p.is_file()}
    rdir = Path(reports_dir) if reports_dir else default_reports_dir(root)
    files |= {p for p in rdir.glob(f"{slate}_*_Game_Script.md") if p.is_file()}
    snap = snapshot_path(root, slate)
    if snap.is_file():
        files.add(snap)
    files = {p for p in files if not p.name.endswith((".lock", ".tmp"))}
    return ([(p, ar.HELPER_ROLE) for p in sorted(helpers)]
            + [(p, ar.LEGACY_ROLE) for p in sorted(files - helpers)])


def record_legacy(nfl_dir: Path | str, slates: tuple[str, ...] = LEGACY_SLATES,
                  reports_dir: Path | str | None = None) -> ar.RecordResult:
    """Copy each legacy file once under its sha256 and append its entry. Idempotent."""
    root = Path(nfl_dir)
    result = ar.RecordResult([], [], [], [])
    for slate in slates:
        found = legacy_files(root, slate, reports_dir)
        if not found:
            result.missing.append(f"{slate}: no legacy files found")
        for src, role in found:
            mtime = _mtime(src)  # before the read: a later rewrite can only look newer
            data = src.read_bytes()  # read once; hash, check and copy this buffer
            sha = hashlib.sha256(data).hexdigest()
            dest = root / LEGACY_ARCHIVE_DIR / sha / src.name
            try:
                ar._write_once(dest, data)
                entry, new = ar.record_file(
                    root, dest, role=role, slate=slate, source="legacy_disk",
                    season=_season(slate), captured_at=mtime,
                    original_path=ar._rel(root, src), mtime=mtime)
            except ar.ArchiveConflict as exc:
                result.conflicts.append(str(exc))
                continue
            (result.recorded if new else result.already).append(entry)
    return result


# ---------------------------------------------------------------------------
# Status (read-only)
# ---------------------------------------------------------------------------

@dataclass
class FileVerdict:
    original_path: str
    role: str
    mtime: str
    sha256: str
    games: int                     # games the file covers
    original_for: list[str]        # event_ids it is an original for
    post_slate_for: list[str]      # event_ids that had kicked off before it was written
    via_pre_kickoff_copy: bool = False

    @property
    def verdict(self) -> str:
        if self.role == ar.HELPER_ROLE:
            return "untracked_helper_output"
        if self.original_path.startswith("snapshots/"):
            return "per_row"
        if not self.original_for:
            return "post_slate"
        return "partial" if self.post_slate_for else "original"


@dataclass
class LegacyStatus:
    slate: str
    status: str                    # legacy_regradable | unrecoverable
    first_kickoff: str | None
    kickoff_source: str            # nfl_games | prediction_rows | none
    regradable_events: list[str]
    card: dict[str, str]           # {"json"/"md": original | post_slate | partial}
    files: list[FileVerdict] = field(default_factory=list)
    snapshot_rows: dict[str, int] = field(default_factory=dict)
    # regradable rows by probability source: original_model_p (a saved model_p,
    # original pre-repair) | recomputed_from_hit_rates (today's code would compute it)
    probability_sources: dict[str, int] = field(default_factory=dict)


def _load(nfl_dir: Path, e: ar.ArchiveEntry) -> Any:
    try:
        return json.loads(ar.resolve_path(nfl_dir, e.path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _records(body: Any) -> list[dict[str, Any]]:
    rows = body.get("records") if isinstance(body, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def _kickoffs(nfl_dir: Path, have: list[ar.ArchiveEntry]
              ) -> tuple[dict[str, datetime], dict[tuple[str, str], str], str]:
    """``{event_id: kickoff}``, ``{(away, home): event_id}`` and the source used."""
    kick: dict[str, datetime] = {}
    teams: dict[tuple[str, str], str] = {}
    games = [e for e in have if Path(e.original_path or "").name.startswith("nfl_games_")]
    for e in games:
        for r in _records(_load(nfl_dir, e)):
            t, eid = _ts(r.get("event_starts_at")), str(r.get("event_id") or "")
            if t and eid:
                kick[eid] = min(t, kick.get(eid, t))
                if r.get("away_team") and r.get("home_team"):
                    teams[(str(r["away_team"]).upper(), str(r["home_team"]).upper())] = eid
    if kick:
        return kick, teams, "nfl_games"
    for e in have:
        if Path(e.original_path or "").name.startswith(PREDICTION_PREFIXES):
            for r in _records(_load(nfl_dir, e)):
                t, eid = _ts(r.get("event_starts_at")), str(r.get("event_id") or "")
                if t and eid:
                    kick[eid] = min(t, kick.get(eid, t))
    return kick, teams, "prediction_rows" if kick else "none"


def _covered(nfl_dir: Path, e: ar.ArchiveEntry, kick: dict[str, datetime],
             teams: dict[tuple[str, str], str]) -> set[str]:
    """Games a file covers: its rows' event_ids, its game-script matchup, else all."""
    name = Path(e.original_path or "").name
    m = _SCRIPT_RE.match(name)
    if m and (m.group(1).upper(), m.group(2).upper()) in teams:
        return {teams[(m.group(1).upper(), m.group(2).upper())]}
    if name.endswith(".json"):
        ids = {str(r.get("event_id")) for r in _records(_load(nfl_dir, e)) if r.get("event_id")}
        if ids & set(kick):
            return ids & set(kick)
    return set(kick)


# What best_bets reads as a probability: ``best_bets._empirical`` (the
# ``{l5,l10,l20,season}_hit_rate`` fields it shrinks into model_p), plus a stored
# model_p / p_model (the name settle reads).
HIT_RATE_FIELDS = ("l5_hit_rate", "l10_hit_rate", "l20_hit_rate", "season_hit_rate")


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def probability_source(r: dict[str, Any]) -> str:
    """``original_model_p`` when a finite model_p was saved, else ``recomputed_from_hit_rates``."""
    if _finite(r.get("model_p")) or _finite(r.get("p_model")):
        return "original_model_p"
    return "recomputed_from_hit_rates"


def _regradable_row(r: dict[str, Any]) -> bool:
    """A line, a price and a probability: without all three a row can't be regraded."""
    price = r.get("sportsbook_best_odds", r.get("best_odds"))
    has_price = price is not None or bool(r.get("books"))
    has_p = _finite(r.get("model_p")) or _finite(r.get("p_model")) \
        or any(_finite(r.get(k)) for k in HIT_RATE_FIELDS)
    return _finite(r.get("line")) and has_price and has_p


def legacy_status(nfl_dir: Path | str, slate: str) -> LegacyStatus:
    """Status of one legacy slate from its archived copies. Only reads."""
    root = Path(nfl_dir)
    entries, _ = ar.read_manifest(ar.manifest_path(root))
    legacy = [e for e in entries if e.role in ar.LEGACY_ROLES]
    have = [e for e in legacy if e.slate == slate]
    kick, teams, source = _kickoffs(root, have)
    # Earliest recorded modified time of each content hash, within this slate and
    # role only: a copy from another slate or role never vouches for this one.
    first_seen: dict[tuple[str, str], datetime] = {}
    for e in have:
        t, k = _ts(e.mtime), (e.role, e.sha256)
        if t and (k not in first_seen or t < first_seen[k]):
            first_seen[k] = t
    files: list[FileVerdict] = []
    regradable: set[str] = set()
    card: dict[str, str] = {}
    snap_rows: dict[str, int] = {}
    seen_rows: dict[tuple[str, ...], str] = {}
    for e in have:
        name = Path(e.original_path or "").name
        mt = _ts(e.mtime)
        if mt is None:
            continue
        if (e.original_path or "").startswith("snapshots/"):
            continue  # judged row by row below
        covered = set() if e.role == ar.HELPER_ROLE else _covered(root, e, kick, teams)
        best = min(mt, first_seen.get((e.role, e.sha256), mt))
        orig = sorted(g for g in covered if best < kick[g])
        late = sorted(g for g in covered if best >= kick[g])
        v = FileVerdict(e.original_path or e.path, e.role, e.mtime or "", e.sha256,
                        len(covered), orig, late,
                        via_pre_kickoff_copy=any(mt >= kick[g] for g in orig))
        files.append(v)
        if e.role == ar.LEGACY_ROLE and name.startswith(PREDICTION_PREFIXES) and orig:
            ok = set(orig)
            for r in _records(_load(root, e)):
                if str(r.get("event_id")) in ok and _regradable_row(r):
                    regradable.add(str(r.get("event_id")))
                    # one prediction appears in several files: count it once
                    seen_rows.setdefault((str(r.get("event_id")), str(r.get("player_name")),
                                          str(r.get("market")), str(r.get("position")),
                                          str(r.get("line"))), probability_source(r))
        if name.startswith(CARD_PREFIX) and e.role == ar.LEGACY_ROLE:
            card[name.rsplit(".", 1)[-1]] = v.verdict
    # The weekly snapshot file is shared by every slate in its week and only grows,
    # so take its largest recorded copy (from any slate) and keep this slate's rows.
    snap_name = snapshot_path(root, slate).name
    snaps = [e for e in legacy if Path(e.original_path or "").name == snap_name
             and (e.original_path or "").startswith("snapshots/")]
    if snaps:
        biggest = max(snaps, key=lambda e: e.size)
        snap_rows.update(pre_kickoff=0, post_slate=0, with_run_id=0, no_kickoff=0)
        _judge_snapshot_rows(root, biggest, slate, kick, snap_rows)
        files.append(FileVerdict(biggest.original_path or biggest.path, biggest.role,
                                 biggest.mtime or "", biggest.sha256, 0, [], []))
    prob_sources = {k: sum(1 for v in seen_rows.values() if v == k)
                    for k in ("original_model_p", "recomputed_from_hit_rates")}
    first = min(kick.values()).isoformat() if kick else None
    return LegacyStatus(slate, "legacy_regradable" if regradable else "unrecoverable",
                        first, source, sorted(regradable), card, files, snap_rows,
                        prob_sources)


def _judge_snapshot_rows(root: Path, e: ar.ArchiveEntry, slate: str,
                         kick: dict[str, datetime], counts: dict[str, int]) -> None:
    """Count this slate's snapshot rows by their own ``taken_at`` and ``run_id``."""
    try:
        lines = ar.resolve_path(root, e.path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # torn line
        if not isinstance(row, dict) or row.get("slate_date") != slate:
            continue
        if row.get("run_id"):
            counts["with_run_id"] += 1  # belongs to a run bundle, not to the legacy slate
            continue
        k = kick.get(str(row.get("event_id") or "")) or _ts(row.get("event_starts_at"))
        taken = _ts(row.get("taken_at"))
        if k is None or taken is None:
            counts["no_kickoff"] += 1
        elif taken < k:
            counts["pre_kickoff"] += 1
        else:
            counts["post_slate"] += 1
