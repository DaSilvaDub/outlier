"""Rows stored as sport='nba' match the same NBA queries as results._pending_rows."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from outlier_nba.track.clv import verified_close
from outlier_nba.track.due import capture_due
from outlier_nba.track.views import ensure_nba_schema
from outlier_scrapers import feedback

TIP = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)
TAKEN = TIP - timedelta(minutes=90)
CLOSE = TIP - timedelta(minutes=30)


def _insert(conn, snapshot_id, sport, captured, tip, event_id, outcome_id):
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, market_type, event_starts_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'sel', '1.5', -110, 'Book', 'GAMELINE', ?, ?)
        """,
        (
            snapshot_id,
            captured.isoformat(),
            sport,
            event_id,
            f"market-{snapshot_id}",
            outcome_id,
            tip.isoformat(),
            captured.isoformat(),
        ),
    )


def _ledger(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    feedback.initialize_database(db)
    with feedback.open_database(db) as conn:
        _insert(conn, "upper", "NBA", TAKEN, TIP, "e-upper", "o-upper")
        _insert(conn, "lower", "nba", TAKEN, TIP, "e-lower", "o-lower")
        _insert(conn, "lower-close", "nba", CLOSE, TIP, "e-lower", "o-lower")
        _insert(conn, "mlb", "MLB", TAKEN, TIP, "e-mlb", "o-mlb")
        ensure_nba_schema(conn)
        conn.execute(
            """
            INSERT INTO nba_model_probs (
                event_id, outcome_id, run_id, as_of, family, market, model_prob, model_weight
            ) VALUES ('e-lower', 'o-lower', 'r1', ?, 'team.ML', 'ML', 0.6, 0.0)
            """,
            ((TAKEN + timedelta(minutes=5)).isoformat(),),
        )
    return db


def test_outcome_eval_counts_a_lowercase_nba_row(tmp_path):
    db = _ledger(tmp_path)
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM nba_outcome_eval").fetchone()[0]
    assert n == 2


def test_model_eval_joins_a_lowercase_nba_row(tmp_path):
    db = _ledger(tmp_path)
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM nba_model_eval").fetchone()[0]
    assert n == 1


def test_verified_close_finds_a_lowercase_nba_row(tmp_path):
    db = _ledger(tmp_path)
    with sqlite3.connect(db) as conn:
        close = verified_close(
            conn,
            {
                "snapshot_id": "lower",
                "event_id": "e-lower",
                "outcome_id": "o-lower",
                "captured_at": TAKEN.isoformat(),
                "event_starts_at": TIP.isoformat(),
            },
        )
    assert close is not None and close.snapshot_id == "lower-close"


def test_capture_due_reads_a_lowercase_nba_row(tmp_path):
    now = datetime(2026, 10, 21, 22, 10, tzinfo=UTC)
    tip = now + timedelta(minutes=25)
    db = tmp_path / "due.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE market_snapshots (sport TEXT, captured_at TEXT, event_starts_at TEXT)"
        )
        conn.execute(
            "INSERT INTO market_snapshots VALUES (?, ?, ?)",
            ("nba", (now - timedelta(hours=3)).isoformat(), tip.isoformat()),
        )
    result = capture_due(db, now)
    assert result.due and result.next_tip == tip
