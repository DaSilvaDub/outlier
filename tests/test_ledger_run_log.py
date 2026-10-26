"""Regression tests for ledger run provenance (``run_id`` columns and ``run_write_log``).

Every test works on a ledger built under ``tmp_path``.  None of them open the
operator's real ``feedback.sqlite3``.
"""

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from outlier_scrapers import feedback, feedback_capture, feedback_db
from outlier_scrapers.feedback_db import RUN_LOG_HASH_FIELDS, run_log_content_hash
from tests.test_feedback import _candidate, _pack

LEDGER_TABLES = ("market_snapshots", "decisions", "settlements", "pack_snapshot_memberships")


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _log(db_path: Path) -> list[dict]:
    return [
        dict(row)
        for row in _rows(db_path, "SELECT * FROM run_write_log ORDER BY log_id")
    ]


def _downgrade_to_pre_run_id(db_path: Path) -> None:
    """Turn a freshly built ledger back into the shape master wrote before this change."""
    conn = sqlite3.connect(db_path)
    try:
        for trigger in ("run_write_log_no_update", "run_write_log_no_delete"):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DROP TABLE run_write_log")
        for table in LEDGER_TABLES:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN run_id")
        conn.commit()
    finally:
        conn.close()


def test_capture_stamps_run_id_and_logs_every_snapshot(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate(), _candidate(market_id="m2", outcome_id="o2")])
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path, run_id="run-A")

    for table in ("market_snapshots", "decisions", "pack_snapshot_memberships"):
        assert {r[0] for r in _rows(db_path, f"SELECT run_id FROM {table}")} == {"run-A"}
    log = _log(db_path)
    assert len(log) == 2
    assert {row["run_id"] for row in log} == {"run-A"}
    assert {row["source"] for row in log} == {"capture_pack"}
    assert all(row["pack_capture_id"] for row in log)
    assert all(row["decision_id"] for row in log)


def test_log_hash_matches_logged_fields(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path, run_id="run-A")

    (row,) = _log(db_path)
    assert row["content_hash"] == run_log_content_hash(
        {field: row[field] for field in RUN_LOG_HASH_FIELDS}
    )
    assert row["final_blended_prob"] == pytest.approx(0.60)
    assert row["units"] == pytest.approx(2.0)


def test_later_run_overwrites_live_row_but_log_keeps_each_runs_values(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    first = _candidate()
    pack_dir = _pack(tmp_path, [first])
    feedback.capture_pack(pack_dir, db_path, run_id="run-A")

    second = dict(first, final_blended_prob=0.70, model_prob=0.70, edge_pct=0.20)
    pack_dir = _pack(tmp_path, [second])
    feedback.capture_pack(pack_dir, db_path, run_id="run-B")

    live = _rows(db_path, "SELECT final_blended_prob, run_id FROM market_snapshots")
    assert [(r[0], r[1]) for r in live] == [(0.70, "run-A")]  # first writer, informational
    log = _log(db_path)
    assert [(r["run_id"], r["final_blended_prob"]) for r in log] == [
        ("run-A", 0.60),
        ("run-B", 0.70),
    ]
    assert log[0]["content_hash"] != log[1]["content_hash"]


def test_retry_in_same_run_appends_instead_of_replacing(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    pack_dir = _pack(tmp_path, [_candidate()])

    feedback.capture_pack(pack_dir, db_path, run_id="run-A")
    feedback.capture_pack(pack_dir, db_path, run_id="run-A")

    log = _log(db_path)
    assert [r["run_id"] for r in log] == ["run-A", "run-A"]
    assert log[0]["log_id"] != log[1]["log_id"]
    assert log[0]["content_hash"] == log[1]["content_hash"]


def test_capture_without_run_id_logs_null_run(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(_pack(tmp_path, [_candidate()]), db_path)

    (row,) = _log(db_path)
    assert row["run_id"] is None
    assert _rows(db_path, "SELECT run_id FROM market_snapshots")[0][0] is None


def test_run_write_log_rejects_update_and_delete(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(_pack(tmp_path, [_candidate()]), db_path, run_id="run-A")

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
            conn.execute("UPDATE run_write_log SET run_id = 'forged'")
        with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
            conn.execute("DELETE FROM run_write_log")
    finally:
        conn.close()
    assert [r["run_id"] for r in _log(db_path)] == ["run-A"]


def test_log_failure_fails_capture_and_leaves_no_partial_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "feedback.sqlite3"
    pack_dir = _pack(tmp_path, [_candidate()])
    feedback.initialize_database(db_path)

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(feedback_capture, "log_run_writes", boom)
    with pytest.raises(sqlite3.OperationalError):
        feedback.capture_pack(pack_dir, db_path, run_id="run-A")

    for table in ("market_snapshots", "decisions", "pack_snapshot_memberships", "run_write_log"):
        assert _rows(db_path, f"SELECT COUNT(*) FROM {table}")[0][0] == 0


def test_unknown_snapshot_in_log_request_raises(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    conn = feedback.open_database(db_path)
    try:
        with pytest.raises(feedback_db.FeedbackError):
            feedback_db.log_run_writes(
                conn,
                run_id="run-A",
                source="test",
                pack_capture_id="p",
                snapshot_ids=["missing"],
            )
    finally:
        conn.close()


def test_upgrade_adds_columns_and_keeps_existing_rows_byte_identical(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    pack_dir = _pack(tmp_path, [_candidate(), _candidate(market_id="m2", outcome_id="o2")])
    feedback.capture_pack(pack_dir, db_path, run_id="seed")
    _downgrade_to_pre_run_id(db_path)

    def dump() -> dict[str, list[tuple]]:
        conn = sqlite3.connect(db_path)
        try:
            out = {}
            for table in LEDGER_TABLES:
                cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})") if c[1] != "run_id"]
                col_sql = ", ".join(cols)
                out[table] = conn.execute(f"SELECT {col_sql} FROM {table} ORDER BY 1, 2").fetchall()
            return out
        finally:
            conn.close()

    before = dump()
    assert before["market_snapshots"] and before["pack_snapshot_memberships"]

    feedback.initialize_database(db_path)
    feedback.initialize_database(db_path)  # idempotent

    assert dump() == before
    for table in LEDGER_TABLES:
        cols = [c[1] for c in _rows(db_path, f"PRAGMA table_info({table})")]
        assert "run_id" in cols
        assert _rows(db_path, f"SELECT COUNT(*) FROM {table} WHERE run_id IS NOT NULL")[0][0] == 0
    assert _rows(db_path, "SELECT COUNT(*) FROM run_write_log")[0][0] == 0
    membership_pk = [
        c[1] for c in _rows(db_path, "PRAGMA table_info(pack_snapshot_memberships)") if c[5]
    ]
    assert membership_pk == ["pack_capture_id", "snapshot_id"]


def test_upgraded_ledger_accepts_new_captures(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(_pack(tmp_path, [_candidate()]), db_path, run_id="seed")
    _downgrade_to_pre_run_id(db_path)

    feedback.capture_pack(_pack(tmp_path, [_candidate()]), db_path, run_id="run-A")

    assert [r["run_id"] for r in _log(db_path)] == ["run-A"]
    # The row written before the upgrade keeps no run id; the log records the new run.
    assert _rows(db_path, "SELECT run_id FROM market_snapshots")[0][0] is None


def test_settlement_insert_records_first_writer_run_id(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(_pack(tmp_path, [_candidate()]), db_path, run_id="run-A")
    snapshot = _rows(db_path, "SELECT snapshot_id FROM market_snapshots")[0][0]
    decision = _rows(db_path, "SELECT decision_id FROM decisions")[0][0]

    stats = feedback.import_settlements(
        db_path,
        [
            {
                "decision_id": decision,
                "snapshot_id": snapshot,
                "win_loss_push": "WIN",
                "actual_result": "12",
            }
        ],
        run_id="run-S",
    )

    assert stats["unmatched_count"] == 0 and stats["ambiguous_count"] == 0
    assert _rows(db_path, "SELECT run_id FROM settlements")[0][0] == "run-S"


def test_import_decisions_logs_the_run(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    pack_dir = _pack(tmp_path, [_candidate()])
    feedback.capture_pack(pack_dir, db_path, run_id="run-A")

    feedback.import_decisions(pack_dir / "decisions.csv", db_path, run_id="run-I")

    assert [(r["run_id"], r["source"]) for r in _log(db_path)] == [
        ("run-A", "capture_pack"),
        ("run-I", "import_decisions"),
    ]


def test_daily_job_builds_one_run_id_for_pack_and_manifest():
    from outlier_scrapers import daily_job

    run_id = daily_job._new_run_id("2026-10-25")
    assert re.fullmatch(r"2026-10-25-\d+", run_id)


# --- structural guard -------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
WRITE_RE = re.compile(
    r"(INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(market_snapshots|decisions|settlements|pack_snapshot_memberships)\b",
    re.IGNORECASE,
)

# Files whose ledger writes record ``run_id`` and append to ``run_write_log`` (or, for
# settlements, stamp ``run_id`` on insert).
COVERED_WRITERS = {
    "outlier_scrapers/feedback_capture.py",
    "outlier_scrapers/feedback_settlement.py",
}
# Writers that change ledger rows without a run id or log row.  Each one is a known gap:
# 10a/10b must not treat the fields they touch as run-attributed.  Do not add a file here
# to silence this test; cover the writer or record why it cannot be covered.
KNOWN_UNCOVERED_WRITERS = {
    "outlier_scrapers/feedback_db.py",  # schema migrations and backfills
    "outlier_scrapers/feedback_retention.py",  # retention compaction
    "outlier_scrapers/verdict_store.py",  # persist_desk_snapshot updates final_verdict/units
}


def _writer_files() -> set[str]:
    found = set()
    for package in REPO.glob("outlier_*"):
        for path in package.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if WRITE_RE.search(text):
                found.add(path.relative_to(REPO).as_posix())
    return found


def test_every_ledger_writer_is_classified():
    unclassified = _writer_files() - COVERED_WRITERS - KNOWN_UNCOVERED_WRITERS
    assert not unclassified, (
        "These files write ledger tables but are neither run-logged nor listed as known "
        f"gaps: {sorted(unclassified)}"
    )


def test_covered_writers_still_write_and_call_the_log():
    capture_source = (REPO / "outlier_scrapers/feedback_capture.py").read_text(encoding="utf-8")
    tree = ast.parse(capture_source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "log_run_writes"
    ]
    assert len(calls) == 2  # capture_pack and import_decisions
    assert "run_id" in (REPO / "outlier_scrapers/feedback_settlement.py").read_text(
        encoding="utf-8"
    )
