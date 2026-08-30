"""Desk-level snapshot, writer lock, readiness, and retention (step 12).

`desk_snapshot.json` is the one coherent pointer set every non-publish
reader resolves. It advances only when `_desk_publication_ready` says A/D/B
are fingerprint-fresh against the live pack CSVs. E is used only when its
pack hashes match *and* its `upstream_publication_ids` equal the selected
A/D/B(/C) publications; otherwise the no-E fallback is computed inline.

See docs/plans/2026-08-12-structured-ai-verdicts.md
'The desk-level snapshot', 'Concurrency', 'Stale-lock recovery', 'Retention'.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from outlier_scrapers import pack_index, runner_common as rc, verdict_report
from outlier_scrapers.stage_result import ArtifactState, FinalReportResolution

SNAPSHOT_NAME = "desk_snapshot.json"
HISTORY_NAME = "desk_snapshot_history.jsonl"
WRITER_LOCK_NAME = ".writer_lock"
DAILY_LOCK_NAME = ".daily_job_lock"
HISTORY_KEEP = 20
STALE_LOCK_AFTER = timedelta(minutes=30)
DEFAULT_RETENTION_AGE = timedelta(days=7)
PREVIEW_BANNER = (
    "NON-AUTHORITATIVE latest_preview — desk_snapshot.json is the source of truth."
)
HASH_KEYS = ("candidates_sha256", "game_totals_sha256", "team_totals_sha256")
REQUIRED_PASSES = ("A", "D", "B")
_IN_PROCESS_PUBLICATION_LOCK = threading.RLock()


class LockBusy(RuntimeError):
    """Another desk write is in progress for this pack."""


class LockNotBreakable(RuntimeError):
    """Stale-lock recovery refused (live PID or other host)."""


@dataclass(frozen=True)
class DeskReadiness:
    ready: bool
    fingerprint: dict[str, str]
    selected: dict[str, str | None]
    e_publication_id: str | None
    e_usable: bool
    reason: str
    upstream_state: ArtifactState = ArtifactState.MISSING
    e_state: ArtifactState = ArtifactState.MISSING


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat()


def daily_lock_dir(pack_dir: Path) -> Path:
    return pack_dir.parent / DAILY_LOCK_NAME


def writer_lock_dir(pack_dir: Path) -> Path:
    return pack_dir / "verdicts" / WRITER_LOCK_NAME


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query = 0x1000
        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(process_query, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _parse_acquired_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _dir_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def read_lock_owner(lock_dir: Path) -> dict[str, Any] | None:
    owner_path = lock_dir / "owner.json"
    if not owner_path.exists():
        return None
    try:
        data = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def lock_breakable(
    lock_dir: Path,
    *,
    now: datetime | None = None,
    stale_after: timedelta = STALE_LOCK_AFTER,
    hostname: str | None = None,
    pid_is_running: Callable[[int], bool] = _pid_is_running,
) -> tuple[bool, str]:
    """Return (breakable, reason) for a held writer lock."""
    if not lock_dir.exists():
        return True, "no_lock"
    when = now or _now()
    host = hostname if hostname is not None else socket.gethostname()
    owner = read_lock_owner(lock_dir)
    if owner is None:
        age = when - _dir_mtime(lock_dir)
        if age >= stale_after:
            return True, "no_owner_expired"
        return False, "no_owner_fresh"
    acquired = _parse_acquired_at(str(owner.get("acquired_at") or ""))
    age = (when - acquired) if acquired is not None else (when - _dir_mtime(lock_dir))
    owner_host = str(owner.get("hostname") or "")
    try:
        owner_pid = int(owner.get("pid") or 0)
    except (TypeError, ValueError):
        owner_pid = 0
    if owner_host and owner_host != host:
        return False, (
            f"held by other host {owner_host} pid={owner_pid} age={age}; "
            "use --break-stale-lock only after confirming that host is done"
        )
    if pid_is_running(owner_pid):
        return False, f"pid {owner_pid} still running on {owner_host or host}"
    if age < stale_after:
        return False, f"dead pid {owner_pid} but age {age} < stale_lock_after"
    return True, "same_host_dead_expired"


def _rmdir_lock(lock_dir: Path) -> None:
    owner = lock_dir / "owner.json"
    if owner.exists():
        try:
            owner.unlink()
        except OSError:
            pass
    try:
        lock_dir.rmdir()
    except OSError:
        pass


def acquire_writer_lock(
    pack_dir: Path,
    *,
    operation: str,
    now: datetime | None = None,
    stale_after: timedelta = STALE_LOCK_AFTER,
    retries: int = 4,
    hostname: str | None = None,
    pid_is_running: Callable[[int], bool] = _pid_is_running,
) -> Path:
    """mkdir(exist_ok=False) acquire of packs/<date>/verdicts/.writer_lock."""
    lock_dir = writer_lock_dir(pack_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    when = now or _now()
    host = hostname if hostname is not None else socket.gethostname()
    last_reason = "busy"
    for attempt in range(max(1, retries)):
        try:
            lock_dir.mkdir(exist_ok=False)
        except FileExistsError:
            breakable, reason = lock_breakable(
                lock_dir,
                now=when,
                stale_after=stale_after,
                hostname=host,
                pid_is_running=pid_is_running,
            )
            last_reason = reason
            if breakable:
                _rmdir_lock(lock_dir)
                continue
            if "other host" in reason:
                raise LockNotBreakable(reason) from None
            time.sleep(0.05 * (attempt + 1))
            continue
        owner = {
            "pid": os.getpid(),
            "hostname": host,
            "acquired_at": _iso(when),
            "operation": operation,
        }
        (lock_dir / "owner.json").write_text(
            json.dumps(owner, sort_keys=True) + "\n", encoding="utf-8"
        )
        return lock_dir
    raise LockBusy(f"another desk write is in progress for this pack ({last_reason})")


def release_writer_lock(lock_dir: Path | None) -> None:
    if lock_dir is not None:
        _rmdir_lock(lock_dir)


def _daily_owner_path(lock_dir: Path) -> Path:
    return lock_dir / "owner.json"


def _read_daily_owner(lock_dir: Path) -> dict[str, Any] | None:
    path = _daily_owner_path(lock_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_daily_owner(lock_dir: Path, *, pid: int, depth: int) -> None:
    _daily_owner_path(lock_dir).write_text(
        json.dumps({"pid": pid, "depth": depth}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def acquire_daily_lock(pack_dir: Path, *, retries: int = 4) -> Path:
    lock_dir = daily_lock_dir(pack_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    for attempt in range(max(1, retries)):
        try:
            lock_dir.mkdir(exist_ok=False)
            _write_daily_owner(lock_dir, pid=pid, depth=1)
            return lock_dir
        except FileExistsError:
            owner = _read_daily_owner(lock_dir)
            if owner is not None and owner.get("pid") == pid:
                depth = int(owner.get("depth") or 1) + 1
                _write_daily_owner(lock_dir, pid=pid, depth=depth)
                return lock_dir
            time.sleep(0.05 * (attempt + 1))
    raise LockBusy("another daily job is in progress (packs/.daily_job_lock)")


def release_daily_lock(lock_dir: Path | None) -> None:
    if lock_dir is None:
        return
    owner = _read_daily_owner(lock_dir)
    if owner is not None and owner.get("pid") == os.getpid():
        depth = int(owner.get("depth") or 1) - 1
        if depth > 0:
            _write_daily_owner(lock_dir, pid=os.getpid(), depth=depth)
            return
    _rmdir_lock(lock_dir)


@contextmanager
def fingerprint_locks(
    pack_dir: Path,
    *,
    operation: str,
    now: datetime | None = None,
    stale_after: timedelta = STALE_LOCK_AFTER,
) -> Iterator[None]:
    """Acquire .daily_job_lock then .writer_lock; release in reverse."""
    with _IN_PROCESS_PUBLICATION_LOCK:
        daily = acquire_daily_lock(pack_dir)
        try:
            writer = acquire_writer_lock(
                pack_dir, operation=operation, now=now, stale_after=stale_after
            )
            try:
                yield
            finally:
                release_writer_lock(writer)
        finally:
            release_daily_lock(daily)


@contextmanager
def writer_lock_only(
    pack_dir: Path,
    *,
    operation: str,
    now: datetime | None = None,
    stale_after: timedelta = STALE_LOCK_AFTER,
) -> Iterator[None]:
    """Acquire only .writer_lock (projection / retention — no live CSV reads)."""
    with _IN_PROCESS_PUBLICATION_LOCK:
        writer = acquire_writer_lock(
            pack_dir, operation=operation, now=now, stale_after=stale_after
        )
        try:
            yield
        finally:
            release_writer_lock(writer)


def break_stale_lock(
    pack_dir: Path,
    *,
    now: datetime | None = None,
    stale_after: timedelta = STALE_LOCK_AFTER,
    hostname: str | None = None,
    pid_is_running: Callable[[int], bool] = _pid_is_running,
    force: bool = False,
) -> dict[str, Any]:
    """Operator path: break the writer lock only when the recovery rule allows."""
    lock_dir = writer_lock_dir(pack_dir)
    if not lock_dir.exists():
        return {"broken": False, "reason": "no_lock"}
    owner = read_lock_owner(lock_dir)
    breakable, reason = lock_breakable(
        lock_dir,
        now=now,
        stale_after=stale_after,
        hostname=hostname,
        pid_is_running=pid_is_running,
    )
    if not breakable and not force:
        return {"broken": False, "reason": reason, "owner": owner}
    _rmdir_lock(lock_dir)
    return {"broken": True, "reason": reason if breakable else "forced", "owner": owner}


def live_fingerprint(pack_dir: Path, *, policy_path: Path | str | None = None) -> dict[str, str]:
    index = pack_index.build_pack_index(pack_dir, policy_path=policy_path)
    return {
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
    }


def _current_publication_id(pack_dir: Path, pass_name: str) -> str | None:
    pointer = pack_dir / "verdicts" / pass_name / "current.json"
    if not pointer.exists():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pub_id = str(data.get("publication_id") or "")
    return pub_id or None


def _publication_dir(pack_dir: Path, pass_name: str, pub_id: str) -> Path:
    return pack_dir / "verdicts" / pass_name / pub_id


def _read_verdicts_json(pack_dir: Path, pass_name: str, pub_id: str) -> dict[str, Any] | None:
    path = _publication_dir(pack_dir, pass_name, pub_id) / "verdicts.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _fingerprint_matches(data: dict[str, Any], fingerprint: dict[str, str]) -> bool:
    return all(str(data.get(key) or "") == fingerprint[key] for key in HASH_KEYS)


def _aggregate_artifact_state(states: Iterator[ArtifactState]) -> ArtifactState:
    precedence = {
        ArtifactState.CURRENT: 0,
        ArtifactState.MISSING: 1,
        ArtifactState.STALE: 2,
        ArtifactState.INVALID: 3,
    }
    values = list(states)
    if not values:
        return ArtifactState.MISSING
    return max(values, key=precedence.__getitem__)


def _fresh_selected(
    pack_dir: Path, identity: rc.PackIdentity
) -> tuple[dict[str, str | None], dict[str, ArtifactState]]:
    selected: dict[str, str | None] = {}
    states: dict[str, ArtifactState] = {}
    for name in (*REQUIRED_PASSES, "C"):
        result = rc.validate_current_publication(pack_dir, name, identity)
        states[name] = result.state
        selected[name] = result.publication_id if result.current else None
    return selected, states


def _e_upstream_matches(upstream: dict[str, Any], selected: dict[str, str | None]) -> bool:
    for name in REQUIRED_PASSES:
        if str(upstream.get(name) or "") != (selected.get(name) or ""):
            return False
    e_c = str(upstream.get("C") or "")
    selected_c = selected.get("C") or ""
    if e_c and e_c != selected_c:
        return False
    return True


def desk_publication_ready(
    pack_dir: Path,
    *,
    policy_path: Path | str | None = None,
) -> DeskReadiness:
    """Purpose-built gate. Never consults chatgpt_a.md / gemini_b.md flat files."""
    identity = rc.load_pack_identity(pack_dir)
    fingerprint = {
        "candidates_sha256": identity.candidates_sha256,
        "game_totals_sha256": identity.game_totals_sha256,
        "team_totals_sha256": identity.team_totals_sha256,
    }
    selected, states = _fresh_selected(pack_dir, identity)
    upstream_state = _aggregate_artifact_state(
        iter(states[name] for name in REQUIRED_PASSES)
    )
    if any(not selected.get(name) for name in REQUIRED_PASSES):
        missing = [name for name in REQUIRED_PASSES if not selected.get(name)]
        return DeskReadiness(
            ready=False,
            fingerprint=fingerprint,
            selected=selected,
            e_publication_id=None,
            e_usable=False,
            reason=f"missing_or_stale:{','.join(missing)}",
            upstream_state=upstream_state,
            e_state=ArtifactState.MISSING,
        )
    e_validation = rc.validate_current_publication(pack_dir, "E", identity)
    e_id = e_validation.publication_id
    e_state = e_validation.state
    e_usable = False
    if e_validation.current and e_id:
        data = _read_verdicts_json(pack_dir, "E", e_id)
        if data is not None:
            upstream = data.get("upstream_publication_ids") or {}
            if isinstance(upstream, dict) and _e_upstream_matches(upstream, selected):
                e_usable = True
            else:
                e_state = ArtifactState.STALE
    return DeskReadiness(
        ready=True,
        fingerprint=fingerprint,
        selected=selected,
        e_publication_id=e_id if e_usable else None,
        e_usable=e_usable,
        reason="e_usable" if e_usable else "fallback_no_e",
        upstream_state=upstream_state,
        e_state=e_state,
    )


def upstream_ready_for_e(
    pack_dir: Path,
    results: dict[str, Any] | None = None,
    *,
    policy_path: Path | str | None = None,
) -> bool:
    """Thin adapter over :func:`desk_publication_ready` for PR2's parallel runner.

    E may run once A/D/B validate CURRENT against the live pack fingerprint --
    purely a function of on-disk publication state, never of this run's own
    :class:`~outlier_scrapers.stage_result.StageResult`\\ s. That means a
    failed A/B/D execution *this run* still unlocks E as long as a prior,
    still-valid CURRENT publication remains on disk (its failure stays visible
    in status/components regardless), and a failed/absent C never blocks E at
    all since C is not one of the required passes. ``results`` is accepted
    (and otherwise unused) so callers can pass this run's StageResult map
    without the adapter needing to inspect it.
    """
    del results  # gating is disk-state-based only; see docstring.
    return desk_publication_ready(pack_dir, policy_path=policy_path).ready


def validate_snapshot_for_final_report(pack_dir: Path) -> FinalReportResolution:
    """Snapshot-first authority check for the final report (PR2).

    ``desk_snapshot.json`` is validated against the *live* pack fingerprint
    and against each pinned publication's own manifest/hash integrity via
    :func:`runner_common.validate_publication` -- never against a pass-level
    ``current.json`` pointer, so a coherent, still-valid snapshot is never
    invalidated merely because a *newer* current-pointer exists elsewhere
    (e.g. a phase re-ran after the snapshot advanced). Callers (run_desk)
    layer the ``file``/``compatibility_artifact`` fields (claude_e.md,
    manual_betting_report.md -- both non-authoritative rendered outputs of
    the pinned publication) on top of this.
    """
    snapshot = read_desk_snapshot(pack_dir)
    if snapshot is None:
        return FinalReportResolution(state=ArtifactState.MISSING, authoritative=False, reason="no_snapshot")

    identity = rc.load_pack_identity(pack_dir)
    live_fp = identity.as_dict()
    snapshot_fp = snapshot.get("pack_fingerprint") or {}
    fp_ok = all(str(snapshot_fp.get(key) or "") == live_fp[key] for key in HASH_KEYS)
    if not fp_ok:
        return FinalReportResolution(
            state=ArtifactState.STALE,
            authoritative=False,
            reason="snapshot_pack_fingerprint_mismatch",
        )

    publications = snapshot.get("publications") or {}
    source = str(snapshot.get("synthesis_source") or "")

    if source == verdict_report.SYNTHESIS_CLAUDE_E and publications.get("E"):
        expected_upstream = {name: publications.get(name) for name in REQUIRED_PASSES}
        validation = rc.validate_publication(
            pack_dir, "E", str(publications["E"]), identity, expected_upstream=expected_upstream
        )
        if validation.state is ArtifactState.CURRENT:
            return FinalReportResolution(
                state=ArtifactState.CURRENT, authoritative=True, source=source, reason="snapshot_e_valid"
            )
        return FinalReportResolution(
            state=validation.state,
            authoritative=False,
            source=source,
            reason=f"snapshot_e_publication_invalid:{validation.reason}",
        )

    if source:
        for name in REQUIRED_PASSES:
            pub_id = publications.get(name)
            if not pub_id:
                return FinalReportResolution(
                    state=ArtifactState.STALE,
                    authoritative=False,
                    source=source,
                    reason=f"snapshot_pinned_publication_missing:{name}",
                )
            validation = rc.validate_publication(pack_dir, name, str(pub_id), identity)
            if validation.state is not ArtifactState.CURRENT:
                return FinalReportResolution(
                    state=validation.state,
                    authoritative=False,
                    source=source,
                    reason=f"snapshot_pinned_publication_invalid:{name}:{validation.reason}",
                )
        return FinalReportResolution(
            state=ArtifactState.CURRENT, authoritative=True, source=source, reason="snapshot_fallback_valid"
        )

    return FinalReportResolution(
        state=ArtifactState.INVALID, authoritative=False, reason="snapshot_missing_synthesis_source"
    )


def read_desk_snapshot(pack_dir: Path) -> dict[str, Any] | None:
    path = pack_dir / "verdicts" / SNAPSHOT_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def build_snapshot_payload(
    pack_dir: Path,
    readiness: DeskReadiness,
    *,
    now: datetime | None = None,
    policy_path: Path | str | None = None,
) -> dict[str, Any]:
    when = now or _now()
    if readiness.e_usable and readiness.e_publication_id:
        data = _read_verdicts_json(pack_dir, "E", readiness.e_publication_id) or {}
        upstream = dict(data.get("upstream_publication_ids") or {})
        publications = {
            "A": upstream.get("A"),
            "D": upstream.get("D"),
            "B": upstream.get("B"),
            "C": upstream.get("C"),
            "E": readiness.e_publication_id,
        }
        source = verdict_report.SYNTHESIS_CLAUDE_E
    else:
        result = verdict_report.compute_no_e_fallback(pack_dir, policy_path=policy_path)
        publications = {
            "A": result.publications.get("A"),
            "D": result.publications.get("D"),
            "B": result.publications.get("B"),
            "C": result.publications.get("C"),
            "E": None,
        }
        source = verdict_report.SYNTHESIS_FALLBACK
    return {
        "pack_fingerprint": dict(readiness.fingerprint),
        "publications": publications,
        "synthesis_source": source,
        "published_at": _iso(when),
    }


def publish_desk_snapshot(
    pack_dir: Path,
    payload: dict[str, Any],
    *,
    append_history: bool = True,
) -> Path:
    """Commit desk_snapshot.json first, then append history. Never the reverse."""
    dest = pack_dir / "verdicts" / SNAPSHOT_NAME
    _atomic_write_json(dest, payload)
    if append_history:
        history = pack_dir / "verdicts" / HISTORY_NAME
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return dest


def advance_desk_snapshot(
    pack_dir: Path,
    *,
    now: datetime | None = None,
    policy_path: Path | str | None = None,
    hold_locks: bool = True,
    append_history: bool = True,
) -> dict[str, Any] | None:
    """If ready, publish a new snapshot. Returns the payload or None."""

    def _run() -> dict[str, Any] | None:
        readiness = desk_publication_ready(pack_dir, policy_path=policy_path)
        if not readiness.ready:
            return None
        payload = build_snapshot_payload(
            pack_dir, readiness, now=now, policy_path=policy_path
        )
        publish_desk_snapshot(pack_dir, payload, append_history=append_history)
        return payload

    if hold_locks:
        with fingerprint_locks(pack_dir, operation="desk_publish", now=now):
            return _run()
    with writer_lock_only(pack_dir, operation="desk_publish", now=now):
        return _run()


def write_latest_preview(pack_dir: Path, pass_name: str) -> Path | None:
    """Non-authoritative preview from the pass's own current.json."""
    pub_id = _current_publication_id(pack_dir, pass_name)
    if not pub_id:
        return None
    parent = pack_dir / "verdicts" / pass_name
    fragment = parent / pub_id / "report_fragment.md"
    body = fragment.read_text(encoding="utf-8") if fragment.exists() else ""
    preview_md = parent / "latest_preview.md"
    preview_md.write_text(f"{PREVIEW_BANNER}\n\n{body}", encoding="utf-8")
    preview_json = parent / "latest_preview.json"
    preview_json.write_text(
        json.dumps(
            {"authoritative": False, "publication_id": pub_id, "pass": pass_name},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return preview_md


def project_legacy_from_snapshot(pack_dir: Path) -> None:
    """Rebuild chatgpt_a.md + status JSON from desk_snapshot.json only."""
    snapshot = read_desk_snapshot(pack_dir)
    if snapshot is None:
        return
    pubs = snapshot.get("publications") or {}
    a_id = pubs.get("A")
    if a_id:
        fragment = pack_dir / "verdicts" / "A" / str(a_id) / "report_fragment.md"
        if fragment.exists():
            (pack_dir / "chatgpt_a.md").write_text(
                fragment.read_text(encoding="utf-8"), encoding="utf-8"
            )
    status_name = "reason" + "ing_status.json"
    status = {
        "final_report": {
            "source": snapshot.get("synthesis_source"),
            "file": SNAPSHOT_NAME,
        },
        "publications": pubs,
        "pack_fingerprint": snapshot.get("pack_fingerprint"),
    }
    (pack_dir / status_name).write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _history_publications(pack_dir: Path, *, keep: int = HISTORY_KEEP) -> set[str]:
    path = pack_dir / "verdicts" / HISTORY_NAME
    if not path.exists():
        return set()
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    retained: set[str] = set()
    for line in lines[-keep:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for value in (row.get("publications") or {}).values():
            if value:
                retained.add(str(value))
    return retained


def _cited_publications(pack_dir: Path, pass_name: str, pub_id: str) -> set[str]:
    found: set[str] = set()
    data = _read_verdicts_json(pack_dir, pass_name, pub_id)
    if not data:
        return found
    for value in (data.get("upstream_publication_ids") or {}).values():
        if value:
            found.add(str(value))
    for rec in data.get("reconciliations") or data.get("verdicts") or data.get("findings") or []:
        for cite in rec.get("cites") or []:
            cited = cite.get("publication_id")
            if cited:
                found.add(str(cited))
    return found


def _pass_for_publication(pack_dir: Path, pub_id: str) -> str | None:
    root = pack_dir / "verdicts"
    if not root.exists():
        return None
    for pass_dir in root.iterdir():
        if not pass_dir.is_dir() or pass_dir.name.startswith("."):
            continue
        if (pass_dir / pub_id).is_dir():
            return pass_dir.name
    return None


def retained_publication_ids(pack_dir: Path, *, history_keep: int = HISTORY_KEEP) -> set[str]:
    retained: set[str] = set()
    snapshot = read_desk_snapshot(pack_dir)
    if snapshot:
        for value in (snapshot.get("publications") or {}).values():
            if value:
                retained.add(str(value))
    for name in ("A", "B", "C", "D", "E"):
        pub_id = _current_publication_id(pack_dir, name)
        if pub_id:
            retained.add(pub_id)
    retained |= _history_publications(pack_dir, keep=history_keep)
    growing = True
    while growing:
        growing = False
        for pub_id in list(retained):
            pass_name = _pass_for_publication(pack_dir, pub_id)
            if not pass_name:
                continue
            extras = _cited_publications(pack_dir, pass_name, pub_id) - retained
            if extras:
                retained |= extras
                growing = True
    return retained


def prune_publications(
    pack_dir: Path,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_RETENTION_AGE,
    history_keep: int = HISTORY_KEEP,
    hold_lock: bool = True,
) -> list[str]:
    """Delete versioned dirs that are unreachable AND older than max_age."""

    def _run() -> list[str]:
        when = now or _now()
        keep = retained_publication_ids(pack_dir, history_keep=history_keep)
        deleted: list[str] = []
        root = pack_dir / "verdicts"
        if not root.exists():
            return deleted
        for pass_dir in root.iterdir():
            if not pass_dir.is_dir() or pass_dir.name.startswith("."):
                continue
            for child in pass_dir.iterdir():
                if not child.is_dir() or child.name.endswith(".tmp") or ".tmp-" in child.name:
                    continue
                if child.name in keep:
                    continue
                age = when - _dir_mtime(child)
                if age < max_age:
                    continue
                import shutil

                shutil.rmtree(child, ignore_errors=True)
                deleted.append(child.name)
        return deleted

    if hold_lock:
        with writer_lock_only(pack_dir, operation="retention", now=now):
            return _run()
    return _run()


def maybe_advance_desk(
    pack_dir: Path,
    *,
    now: datetime | None = None,
    policy_path: Path | str | None = None,
    hold_locks: bool = True,
) -> dict[str, Any] | None:
    """Lock, maybe publish snapshot, project legacy + latest_preview.

    ``hold_locks=False`` is for callers that already hold ``.daily_job_lock``
    (the daily job). Taking the same directory lock again deadlocks the snapshot.
    """
    payload = advance_desk_snapshot(
        pack_dir, now=now, policy_path=policy_path, hold_locks=hold_locks
    )
    if payload is None:
        return None
    with writer_lock_only(pack_dir, operation="project_legacy", now=now):
        project_legacy_from_snapshot(pack_dir)
        for name in ("A", "B", "C", "D", "E"):
            write_latest_preview(pack_dir, name)
    return payload
