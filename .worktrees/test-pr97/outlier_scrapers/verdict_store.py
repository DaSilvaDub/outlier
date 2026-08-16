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
from datetime import datetime, timezone
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
        INSERT OR REPLACE INTO verdict_records (
            decision_id, pass, publication_id, record_id,
            outcome_id, stream, verdict, confidence,
            recommended_units_model, recommended_units_validated,
            violation_codes, mode, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
