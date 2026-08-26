"""verdict_records migration, persistence, and C_verdict reduction (step 14)."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from outlier_scrapers import (
    desk_snapshot,
    feedback,
    pack,
    pack_index,
    runner_common,
    verdict_report,
    verdict_store,
)


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


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()


def _authoritative_pack(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-25"
    pack_dir.mkdir(parents=True)
    candidate = {field: "" for field in pack.CANDIDATES_HEADER}
    candidate.update(
        {
            "sport": "MLB",
            "event_id": "event-1",
            "_event_starts_at": _future(),
            "as_of": "2026-08-25T12:00:00+00:00",
            "market_id": "market-1",
            "outcome_id": "outcome-1",
            "market_type": "PLAYER_PROP",
            "player_id": "player-1",
            "selection": "Player One OVER 5.5",
            "line": "5.5",
            "price": "-110",
            "book": "Book",
            "board": "A",
            "actionable": "true",
            "max_units": "2.0",
            "recommended_units_pre_news": "1.0",
        }
    )
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        writer.writerow(candidate)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    index = pack_index.build_pack_index(
        pack_dir, now=datetime(2026, 8, 25, 13, tzinfo=timezone.utc)
    )
    publications = {}
    for pass_name in ("A", "D", "B"):
        envelope = {
            "schema_version": "1.0",
            "pass": pass_name,
            "pack_date": pack_dir.name,
            "candidates_sha256": index.candidates_sha256,
            "game_totals_sha256": index.game_totals_sha256,
            "team_totals_sha256": index.team_totals_sha256,
            "verdicts": [
                {
                    "market_id": "market-1",
                    "outcome_id": "outcome-1",
                    "stream": "candidates",
                    "selection": "Player One OVER 5.5",
                    "line": "5.5",
                    "price": "-110",
                    "book": "Book",
                    "verdict": "BET",
                    "confidence": 0.75,
                    "recommended_units": 1.0,
                    "evidence": [],
                    "contradictions": [],
                    "kill_triggers": [],
                    "rejection_reasons": [],
                }
            ],
            "slate_notes": [],
            "needs": [],
        }
        result = runner_common.publish_verdict_pass(
            pack_dir,
            json.dumps(envelope),
            pass_=pass_name,
            request_sha256=pass_name * 64,
            candidates_sha256=index.candidates_sha256,
            game_totals_sha256=index.game_totals_sha256,
            team_totals_sha256=index.team_totals_sha256,
            model="test",
        )
        publications[pass_name] = result.publication_id
    snapshot = {
        "pack_fingerprint": {
            "candidates_sha256": index.candidates_sha256,
            "game_totals_sha256": index.game_totals_sha256,
            "team_totals_sha256": index.team_totals_sha256,
        },
        "publications": {**publications, "C": None, "E": None},
        "synthesis_source": verdict_report.SYNTHESIS_FALLBACK,
        "published_at": "2026-08-25T13:05:00+00:00",
    }
    desk_snapshot.publish_desk_snapshot(pack_dir, snapshot, append_history=False)
    return pack_dir, db_path


def test_persist_desk_snapshot_scores_models_and_is_idempotent(tmp_path):
    pack_dir, db_path = _authoritative_pack(tmp_path)

    first = verdict_store.persist_desk_snapshot(pack_dir, db_path)
    second = verdict_store.persist_desk_snapshot(pack_dir, db_path)

    assert first.publications == 3
    assert first.records == 3
    assert first.decisions_updated == 1
    assert second.records == 3
    assert second.decisions_updated == 0
    with sqlite3.connect(db_path) as conn:
        decision = conn.execute(
            "SELECT decision_id, snapshot_id, A_verdict, B_verdict, C_verdict, "
            "D_verdict, final_verdict FROM decisions"
        ).fetchone()
        assert decision[2:] == ("BET", "BET", "", "BET", "BET")
        assert conn.execute("SELECT COUNT(*) FROM verdict_records").fetchone()[0] == 3
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_id, decision_id, snapshot_id, outcome_id, event_id,
                market_id, win_loss_push, closing_line, closing_price, pnl, settled_at
            ) VALUES ('settlement-1', ?, ?, 'outcome-1', 'event-1', 'market-1',
                      'W', '6.5', -120, 0.909, '2026-08-26T02:00:00+00:00')
            """,
            (decision[0], decision[1]),
        )
        conn.commit()

    report_dir = tmp_path / "report"
    feedback.generate_report(db_path, report_dir)
    with (report_dir / "model_performance.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {(row["model"], row["verdict"], row["n"]) for row in rows} == {
        ("A", "BET", "1"),
        ("B", "BET", "1"),
        ("D", "BET", "1"),
    }


def test_persist_desk_snapshot_fails_closed_on_ambiguous_membership(tmp_path):
    pack_dir, db_path = _authoritative_pack(tmp_path)
    # A second exact-looking membership with a distinct snapshot must not be
    # guessed at. The authoritative publication has no capture-id field.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                selection, line, price, book, created_at
            ) VALUES ('ambiguous-snapshot', '2026-08-25T12:00:00+00:00', 'MLB',
                      'event-1', 'market-1', 'outcome-1', 'Player One OVER 5.5',
                      '5.5', -110, 'Book', '2026-08-25T12:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO decisions (
                decision_id, snapshot_id, created_at, updated_at
            ) VALUES ('ambiguous-decision', 'ambiguous-snapshot',
                      '2026-08-25T12:00:00+00:00', '2026-08-25T12:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO pack_snapshot_memberships (
                pack_capture_id, snapshot_id, pack_path, pack_timestamp,
                source, created_at
            ) VALUES ('ambiguous-capture', 'ambiguous-snapshot', ?,
                      '2026-08-25T12:00:00+00:00', 'candidates',
                      '2026-08-25T12:00:00+00:00')
            """,
            (str(pack_dir.resolve()),),
        )
        conn.commit()

    with pytest.raises(verdict_store.VerdictPersistenceError, match="found 2"):
        verdict_store.persist_desk_snapshot(pack_dir, db_path)
