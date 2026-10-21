"""Nightly NBA settlement through the shared results collector (plan M14).

Grading, matching and import are ``outlier_scrapers.results`` / ``feedback``; this
module only replaces the collector's close with a *verified* close and records the
verification status beside each settlement.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from outlier_nba.track import clv
from outlier_nba.track.views import ensure_nba_schema
from outlier_scrapers import feedback, results

LEAGUE = "NBA"


def settle_nba(
    db_path: Path,
    *,
    lookback_days: int = 3,
    now: datetime | None = None,
    fetch_json: Callable[[str], dict[str, Any]] = results._fetch_json,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    feedback.initialize_database(db_path)
    with feedback.open_database(db_path) as conn:
        ensure_nba_schema(conn)
        rows, summary = results.collect_settlement_rows(
            conn, [LEAGUE], lookback_days=lookback_days, now=current, fetch_json=fetch_json
        )
        # UPPER() to match results._pending_rows: a snapshot stored as 'nba' is
        # pending there but would miss this map, and the KeyError below would lose
        # the whole settle run before any settlement or CLV row was written.
        taken = {
            r["decision_id"]: r
            for r in conn.execute(
                """
                SELECT d.decision_id, d.snapshot_id, s.event_id, s.outcome_id,
                       s.event_starts_at, s.captured_at
                FROM decisions d JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
                WHERE UPPER(s.sport) = 'NBA'
                """
            ).fetchall()
        }
        records: list[dict[str, Any]] = []
        for row in rows:
            record, close = clv.clv_record(conn, taken[row["decision_id"]])
            # results.py's own close is "any later snapshot"; only a verified one may stand.
            row["closing_line"] = close.line if close else None
            # The snapshot's price is the best price at that moment; a price CLV is only
            # meaningful against the same book, so a cross-book close leaves it unknown
            # (the consensus-probability CLV in nba_clv still stands).
            row["closing_price"] = close.price if close and record["same_book"] else None
            records.append(record)
        imported = (
            feedback.import_settlements(conn, rows)
            if rows
            else {
                "unmatched_count": 0,
                "ambiguous_count": 0,
                "duplicate_count": 0,
                "updated_count": 0,
            }
        )
        _write_clv(conn, records)
    verified = sum(1 for r in records if r["status"] == clv.VERIFIED)
    return {
        **summary,
        **imported,
        "clv_verified": verified,
        "clv_close_unverified": len(records) - verified,
    }


def _write_clv(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO nba_clv
            (decision_id, status, close_snapshot_id, close_captured_at, minutes_before_tip,
             taken_consensus_prob, close_consensus_prob, clv_prob)
        VALUES (:decision_id, :status, :close_snapshot_id, :close_captured_at,
                :minutes_before_tip, :taken_consensus_prob, :close_consensus_prob, :clv_prob)
        """,
        records,
    )
    conn.commit()
