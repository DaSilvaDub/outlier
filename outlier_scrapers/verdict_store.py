"""Child-table persistence for per-pass envelope records (step 14).

`verdict_records` is keyed by (decision_id, pass, publication_id, record_id)
so C can store several findings per outcome and a forced rerun under the same
request hash still persists as a distinct publication. Legacy A/D/B columns
stay one verdict per outcome; C_verdict is a lossy precedence reduction.

See docs/plans/2026-08-12-structured-ai-verdicts.md 'Verdict persistence'.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

CREATE_VERDICT_RECORDS = """
CREATE TABLE IF NOT EXISTS verdict_records (
    decision_id TEXT NOT NULL,
    pass TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    outcome_id TEXT,
    stream TEXT,
    verdict TEXT,
    confidence REAL,
    recommended_units_model REAL,
    recommended_units_validated REAL,
    violation_codes TEXT,
    mode TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (decision_id, pass, publication_id, record_id)
)
"""

C_PRECEDENCE = ("CONTRADICTS", "CONFIRMS", "NEUTRAL")


def ensure_verdict_records_schema(conn: sqlite3.Connection) -> None:
    """Additive CREATE TABLE IF NOT EXISTS. Safe on a legacy decisions-only DB."""
    conn.execute(CREATE_VERDICT_RECORDS)


def reduce_c_verdict(findings: Sequence[str]) -> str:
    """CONTRADICTS > CONFIRMS > NEUTRAL; empty if C produced nothing usable."""
    present = {str(item) for item in findings if item}
    for value in C_PRECEDENCE:
        if value in present:
            return value
    return ""


def persist_verdict_record(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    pass_name: str,
    publication_id: str,
    record_id: str,
    outcome_id: str = "",
    stream: str = "",
    verdict: str = "",
    confidence: float | None = None,
    recommended_units_model: float | None = None,
    recommended_units_validated: float | None = None,
    violation_codes: Iterable[str] = (),
    mode: str = "shadow",
    created_at: str | None = None,
) -> None:
    ensure_verdict_records_schema(conn)
    when = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO verdict_records (
            decision_id, pass, publication_id, record_id,
            outcome_id, stream, verdict, confidence,
            recommended_units_model, recommended_units_validated,
            violation_codes, mode, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id, pass, publication_id, record_id) DO UPDATE SET
            outcome_id=excluded.outcome_id, stream=excluded.stream,
            verdict=excluded.verdict, confidence=excluded.confidence,
            recommended_units_model=excluded.recommended_units_model,
            recommended_units_validated=excluded.recommended_units_validated,
            violation_codes=excluded.violation_codes, mode=excluded.mode
        """,
        (
            decision_id,
            pass_name,
            publication_id,
            record_id,
            outcome_id,
            stream,
            verdict,
            confidence,
            recommended_units_model,
            recommended_units_validated,
            json.dumps(list(violation_codes), sort_keys=True),
            mode,
            when,
        ),
    )


def project_legacy_c_verdict(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    outcome_id: str,
    publication_id: str | None = None,
) -> str:
    """Reduce persisted C findings for one decision/outcome into C_verdict."""
    ensure_verdict_records_schema(conn)
    if publication_id:
        rows = conn.execute(
            """
            SELECT verdict FROM verdict_records
            WHERE decision_id = ? AND pass = 'C' AND outcome_id = ? AND publication_id = ?
            """,
            (decision_id, outcome_id, publication_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT verdict FROM verdict_records
            WHERE decision_id = ? AND pass = 'C' AND outcome_id = ?
            """,
            (decision_id, outcome_id),
        ).fetchall()
    return reduce_c_verdict([str(row[0] or "") for row in rows])


def rejected_count_from_snapshot(pack_dir: Any) -> int:
    """Sum reject-severity violations across snapshot-named publications."""
    from outlier_scrapers.desk_snapshot import read_desk_snapshot

    snapshot = read_desk_snapshot(pack_dir)
    if not snapshot:
        return 0
    pubs = snapshot.get("publications") or {}
    total = 0
    for pass_name, pub_id in pubs.items():
        if not pub_id:
            continue
        path = pack_dir / "verdicts" / str(pass_name) / str(pub_id) / "violations.json"
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        total += sum(1 for row in rows if isinstance(row, dict) and row.get("severity") == "reject")
    return total


class VerdictPersistenceError(ValueError):
    """The authoritative desk snapshot cannot be projected unambiguously."""


@dataclass(frozen=True)
class VerdictPersistenceStats:
    publications: int
    records: int
    decisions_updated: int


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerdictPersistenceError(f"cannot read {path}") from exc
    if not isinstance(value, dict):
        raise VerdictPersistenceError(f"expected object in {path}")
    return value


def _same_number(left: Any, right: Any) -> bool:
    if str(left or "").strip() == str(right or "").strip():
        return True
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return False


def _resolve_decision(conn: sqlite3.Connection, pack_dir: Path, record: Any) -> tuple[str, str]:
    rows = conn.execute(
        """
        SELECT d.decision_id, s.snapshot_id, s.selection, s.line, s.price, s.book
        FROM pack_snapshot_memberships m
        JOIN market_snapshots s ON s.snapshot_id=m.snapshot_id
        JOIN decisions d ON d.snapshot_id=s.snapshot_id
        WHERE m.pack_path=? AND s.market_id=? AND s.outcome_id=?
        """,
        (str(pack_dir.resolve()), str(record.market_id), str(record.outcome_id)),
    ).fetchall()
    matches = [
        row for row in rows
        if str(row[2] or "") == str(record.selection or "")
        and _same_number(row[3], record.line)
        and _same_number(row[4], record.price)
        and (not getattr(record, "book", "") or str(row[5] or "") == str(record.book))
    ]
    if len(matches) != 1:
        raise VerdictPersistenceError(
            f"authoritative record {record.outcome_id} found {len(matches)} exact pack decisions"
        )
    return str(matches[0][0]), str(matches[0][1])


def _publication(pack_dir: Path, pass_name: str, publication_id: str, fingerprint: dict[str, str]) -> tuple[Any, list[str], str]:
    from outlier_scrapers import runner_common

    pub_dir = pack_dir / "verdicts" / pass_name / publication_id
    payload = _read_object(pub_dir / "verdicts.json")
    if str(payload.get("pack_date") or "") != pack_dir.name:
        raise VerdictPersistenceError(f"{pass_name} publication pack date mismatch")
    for key, expected in fingerprint.items():
        if str(payload.get(key) or "") != expected:
            raise VerdictPersistenceError(f"{pass_name} publication fingerprint mismatch")
    kind = "finding" if pass_name == "C" else ("reconciliation" if pass_name == "E" else "verdict")
    try:
        parsed = runner_common.parse_envelope(json.dumps(payload), kind).envelope
    except Exception as exc:
        raise VerdictPersistenceError(f"invalid {pass_name} publication") from exc
    violations_path = pub_dir / "violations.json"
    violations: list[str] = []
    if violations_path.exists():
        try:
            raw = json.loads(violations_path.read_text(encoding="utf-8"))
            violations = sorted({str(item.get("code")) for item in raw if isinstance(item, dict) and item.get("code")}) if isinstance(raw, list) else []
        except (OSError, json.JSONDecodeError):
            violations = []
    status_path = pub_dir / "status_fragment.json"
    mode = str(_read_object(status_path).get("mode") or "shadow") if status_path.exists() else "shadow"
    return parsed, violations, mode


def persist_desk_snapshot(pack_dir: Path, db_path: Path) -> VerdictPersistenceStats:
    """Project exactly the immutable publications pinned by desk_snapshot.json."""
    from outlier_scrapers import desk_snapshot, verdict_report

    pack_dir, db_path = Path(pack_dir), Path(db_path)
    snapshot = desk_snapshot.read_desk_snapshot(pack_dir)
    if not snapshot:
        raise VerdictPersistenceError("desk_snapshot.json is missing or invalid")
    fingerprint = snapshot.get("pack_fingerprint")
    if not isinstance(fingerprint, dict) or fingerprint != desk_snapshot.live_fingerprint(pack_dir):
        raise VerdictPersistenceError("desk snapshot fingerprint is stale or invalid")
    pubs = snapshot.get("publications")
    if not isinstance(pubs, dict):
        raise VerdictPersistenceError("desk snapshot publications are invalid")
    source = str(snapshot.get("synthesis_source") or "")
    required = ("A", "D", "B")
    if source == verdict_report.SYNTHESIS_FALLBACK and (any(not pubs.get(p) for p in required) or pubs.get("E")):
        raise VerdictPersistenceError("fallback snapshot requires exactly A, D, and B")
    if source != verdict_report.SYNTHESIS_FALLBACK and not pubs.get("E"):
        raise VerdictPersistenceError("non-fallback snapshot requires E")

    loaded: dict[str, tuple[Any, list[str], str]] = {}
    for name in ("A", "B", "C", "D", "E"):
        pub_id = pubs.get(name)
        if pub_id:
            loaded[name] = _publication(pack_dir, name, str(pub_id), fingerprint)
    pass_records: dict[str, Sequence[Any]] = {}
    for name, (envelope, _codes, _mode) in loaded.items():
        pass_records[name] = tuple(getattr(envelope, "findings", getattr(envelope, "reconciliations", getattr(envelope, "verdicts", ()))))
    if source == verdict_report.SYNTHESIS_FALLBACK:
        fallback = verdict_report.reconcile_no_e(
            {name: pass_records[name] for name in required},
            {name: str(pubs[name]) for name in required},
            pack_date=pack_dir.name,
            candidates_sha256=str(fingerprint["candidates_sha256"]),
            game_totals_sha256=str(fingerprint["game_totals_sha256"]),
            team_totals_sha256=str(fingerprint["team_totals_sha256"]),
        )
        final_records = tuple(fallback.records)
    else:
        final_records = tuple(pass_records.get("E", ()))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_verdict_records_schema(conn)
        resolved: dict[tuple[str, str], tuple[str, str]] = {}
        for name, records in pass_records.items():
            for record in records:
                resolved[(name, record.record_id)] = _resolve_decision(conn, pack_dir, record)
        final_resolved = {record.outcome_id: _resolve_decision(conn, pack_dir, record) for record in final_records}
        before = conn.total_changes
        for name, records in pass_records.items():
            _env, codes, mode = loaded[name]
            for record in records:
                decision_id, _snapshot_id = resolved[(name, record.record_id)]
                units = getattr(record, "recommended_units", None)
                persist_verdict_record(
                    conn, decision_id=decision_id, pass_name=name,
                    publication_id=str(pubs[name]), record_id=record.record_id,
                    outcome_id=record.outcome_id, stream=record.stream,
                    verdict=record.verdict, confidence=getattr(record, "confidence", None),
                    recommended_units_model=units, recommended_units_validated=units,
                    violation_codes=codes, mode=mode,
                    created_at=str(snapshot.get("published_at") or "") or None,
                )
        updates: dict[str, dict[str, Any]] = {}
        for name in ("A", "B", "D"):
            for record in pass_records.get(name, ()):
                decision_id, _ = resolved[(name, record.record_id)]
                updates.setdefault(decision_id, {})[f"{name}_verdict"] = record.verdict
        for record in pass_records.get("C", ()):
            decision_id, _ = resolved[("C", record.record_id)]
            updates.setdefault(decision_id, {})["C_verdict"] = project_legacy_c_verdict(
                conn, decision_id=decision_id, outcome_id=record.outcome_id,
                publication_id=str(pubs["C"]),
            )
        for record in final_records:
            decision_id, _ = final_resolved[record.outcome_id]
            updates.setdefault(decision_id, {}).update(final_verdict=record.verdict, units=getattr(record, "recommended_units", None))
        changed_decisions = 0
        for decision_id, values in updates.items():
            current = conn.execute("SELECT A_verdict,B_verdict,C_verdict,D_verdict,final_verdict,units FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
            desired = {key: values.get(key, current[key]) for key in ("A_verdict", "B_verdict", "C_verdict", "D_verdict", "final_verdict", "units")}
            if any(current[key] != desired[key] for key in desired):
                conn.execute("UPDATE decisions SET A_verdict=?,B_verdict=?,C_verdict=?,D_verdict=?,final_verdict=?,units=?,updated_at=? WHERE decision_id=?", (*[desired[k] for k in ("A_verdict","B_verdict","C_verdict","D_verdict","final_verdict","units")], datetime.now(timezone.utc).isoformat(), decision_id))
                changed_decisions += 1
        conn.commit()
        del before
        return VerdictPersistenceStats(len(loaded), sum(len(v) for v in pass_records.values()), changed_decisions)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
