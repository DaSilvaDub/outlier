"""Nightly NBA settlement through the shared results collector (plan M14).

Grading, matching and import are ``outlier_scrapers.results`` / ``feedback``; this
module only replaces the collector's close with a *verified* close and records the
verification status beside each settlement.

Every unsettled NBA decision whose tip has already started is graded. The shared
collector rejects a lookback over 30 days, and other sports keep their own window,
so NBA results are fetched in pieces of at most 30 days.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from outlier_nba.track import clv
from outlier_nba.track.views import ensure_nba_schema
from outlier_scrapers import feedback, results

LEAGUE = "NBA"
_MAX_RESULT_WINDOW_DAYS = 30
_SUMMARY_KEYS = (
    "pending_count",
    "settlement_rows",
    "unmatched_event_count",
    "unsupported_count",
    "invalid_time_count",
    "provider_event_count",
)


def _merge_summaries(parts: list[dict[str, Any]]) -> dict[str, int]:
    merged = {key: 0 for key in _SUMMARY_KEYS}
    for part in parts:
        for key in _SUMMARY_KEYS:
            merged[key] += int(part.get(key, 0))
    return merged


def _oldest_unsettled_start(conn: sqlite3.Connection, current: datetime) -> datetime | None:
    row = conn.execute(
        """
        SELECT MIN(s.event_starts_at)
        FROM decisions d
        JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
        LEFT JOIN settlements x ON x.decision_id = d.decision_id
        WHERE x.settlement_id IS NULL
          AND s.sport = ?
          AND s.event_starts_at < ?
        """,
        (LEAGUE, current.isoformat()),
    ).fetchone()
    started = None if row is None else row[0]
    return results._parse_aware(started)


def _days_covering(start: datetime, end: datetime) -> int:
    span = end - start
    whole = span.days
    return whole if span == timedelta(days=whole) else whole + 1


def _result_windows(
    oldest: datetime, current: datetime, lookback_days: int
) -> list[tuple[datetime, int]]:
    """``(window_end, lookback_days)`` covering ``[oldest, current]``, each <= 30 days.

    A legal ``lookback_days`` that already reaches ``oldest`` stays one collector call,
    the same call a short nightly run made before older tips were included.
    """
    needed = max(1, _days_covering(oldest, current))
    if needed <= lookback_days <= _MAX_RESULT_WINDOW_DAYS:
        return [(current, lookback_days)]
    windows: list[tuple[datetime, int]] = []
    end = current
    while end > oldest:
        days = max(1, min(_MAX_RESULT_WINDOW_DAYS, _days_covering(oldest, end)))
        windows.append((end, days))
        nxt = end - timedelta(days=days)
        if nxt <= oldest:
            break
        # Inclusive bounds would otherwise grade the cutoff instant twice.
        end = nxt - timedelta(microseconds=1)
    return windows


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
        oldest = _oldest_unsettled_start(conn, current)
        if oldest is None or oldest >= current:
            windows = [(current, lookback_days)]
        else:
            windows = _result_windows(oldest, current, lookback_days)
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        parts: list[dict[str, Any]] = []
        for end, days in windows:
            batch, part = results.collect_settlement_rows(
                conn, [LEAGUE], lookback_days=days, now=end, fetch_json=fetch_json
            )
            parts.append(part)
            for row in batch:
                decision_id = str(row["decision_id"])
                if decision_id in seen:
                    continue
                seen.add(decision_id)
                found.append(row)
        summary = _merge_summaries(parts)
        summary["settlement_rows"] = len(found)
        rows = found
        taken = {
            r["decision_id"]: r
            for r in conn.execute(
                """
                SELECT d.decision_id, d.snapshot_id, s.event_id, s.outcome_id,
                       s.event_starts_at, s.captured_at
                FROM decisions d JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
                WHERE s.sport = 'NBA'
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
