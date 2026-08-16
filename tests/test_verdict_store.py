"""verdict_records migration, persistence, and C_verdict reduction (step 14)."""

from __future__ import annotations

import json
import sqlite3

from outlier_scrapers import verdict_store


def _legacy_db(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            A_verdict TEXT,
            B_verdict TEXT,
            C_verdict TEXT,
            D_verdict TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO decisions VALUES ('d1', 'BET', 'BET', '', 'PASS')"
    )
    conn.commit()
    return conn


def test_migration_creates_four_column_pk_on_legacy_db(tmp_path):
    conn = _legacy_db(tmp_path)
    verdict_store.ensure_verdict_records_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(verdict_records)")}
    assert {"decision_id", "pass", "publication_id", "record_id"} <= cols
    ordered = [
        row[1]
        for row in sorted(conn.execute("PRAGMA table_info(verdict_records)"), key=lambda r: r[5])
        if row[5]
    ]
    assert ordered == ["decision_id", "pass", "publication_id", "record_id"]
    legacy = conn.execute("SELECT A_verdict, B_verdict, C_verdict, D_verdict FROM decisions").fetchone()
    assert legacy == ("BET", "BET", "", "PASS")


def test_two_record_ids_same_publication_both_persist(tmp_path):
    conn = _legacy_db(tmp_path)
    verdict_store.persist_verdict_record(
        conn, decision_id="d1", pass_name="C", publication_id="pubC",
        record_id="C:out1:aaa", outcome_id="out1", verdict="CONFIRMS",
    )
    verdict_store.persist_verdict_record(
        conn, decision_id="d1", pass_name="C", publication_id="pubC",
        record_id="C:out1:bbb", outcome_id="out1", verdict="NEUTRAL",
    )
    count = conn.execute("SELECT COUNT(*) FROM verdict_records").fetchone()[0]
    assert count == 2


def test_two_publication_ids_same_request_do_not_collide(tmp_path):
    conn = _legacy_db(tmp_path)
    verdict_store.persist_verdict_record(
        conn, decision_id="d1", pass_name="A", publication_id="pubA1",
        record_id="A:out1:same", outcome_id="out1", verdict="BET",
    )
    verdict_store.persist_verdict_record(
        conn, decision_id="d1", pass_name="A", publication_id="pubA2",
        record_id="A:out1:same", outcome_id="out1", verdict="PASS",
    )
    rows = conn.execute(
        "SELECT publication_id, verdict FROM verdict_records ORDER BY publication_id"
    ).fetchall()
    assert rows == [("pubA1", "BET"), ("pubA2", "PASS")]


def test_c_verdict_confirms_over_neutral():
    assert verdict_store.reduce_c_verdict(["CONFIRMS", "NEUTRAL"]) == "CONFIRMS"


def test_c_verdict_contradicts_wins():
    assert verdict_store.reduce_c_verdict(["CONFIRMS", "CONTRADICTS", "CONFIRMS"]) == "CONTRADICTS"


def test_c_verdict_empty_when_no_findings():
    assert verdict_store.reduce_c_verdict([]) == ""
    assert verdict_store.reduce_c_verdict(["", None]) == ""  # type: ignore[list-item]


def test_project_legacy_c_verdict_from_store(tmp_path):
    conn = _legacy_db(tmp_path)
    verdict_store.persist_verdict_record(
        conn, decision_id="d1", pass_name="C", publication_id="pubC",
        record_id="C:out1:a", outcome_id="out1", verdict="NEUTRAL",
    )
    verdict_store.persist_verdict_record(
        conn, decision_id="d1", pass_name="C", publication_id="pubC",
        record_id="C:out1:b", outcome_id="out1", verdict="CONFIRMS",
    )
    assert verdict_store.project_legacy_c_verdict(conn, decision_id="d1", outcome_id="out1") == "CONFIRMS"


def test_rejected_count_from_snapshot(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-15"
    pub = pack_dir / "verdicts" / "A" / "pubA"
    pub.mkdir(parents=True)
    (pub / "violations.json").write_text(
        json.dumps(
            [
                {"code": "line_tampered", "severity": "reject"},
                {"code": "unbound_name_in_prose", "severity": "warn"},
            ]
        ),
        encoding="utf-8",
    )
    (pack_dir / "verdicts" / "desk_snapshot.json").write_text(
        json.dumps(
            {
                "publications": {"A": "pubA", "D": None, "B": None, "E": None},
                "synthesis_source": "fallback_no_e",
            }
        ),
        encoding="utf-8",
    )
    assert verdict_store.rejected_count_from_snapshot(pack_dir) == 1
