"""CLV from a *verified* close only (plan M14, U11).

A close counts when it is a genuinely later snapshot of the same outcome, stamped
within 60 minutes before tip (and not after it). Anything else is
``close_unverified`` and excluded from CLV statistics. This avoids NFL's
snapshot-as-close bug (the take standing in as its own close, CLV == 0 by
construction) and a stale "close" captured hours before tip.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from outlier_nba.run_context import try_parse_utc

VERIFIED_WINDOW = timedelta(minutes=60)
VERIFIED = "verified"
UNVERIFIED = "close_unverified"

SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS nba_clv (
        decision_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        close_snapshot_id TEXT,
        close_captured_at TEXT,
        minutes_before_tip REAL,
        taken_consensus_prob REAL,
        close_consensus_prob REAL,
        clv_prob REAL
    )
    """,
)


@dataclass(frozen=True)
class VerifiedClose:
    snapshot_id: str
    line: Any
    price: Any
    consensus_prob: float | None
    captured_at: str
    book: str
    minutes_before_tip: float


def verified_close(
    conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]
) -> VerifiedClose | None:
    """Latest later snapshot of this outcome inside the 60-minute pre-tip window, or None."""
    tip = try_parse_utc(row["event_starts_at"])
    taken = try_parse_utc(row["captured_at"])
    if tip is None or taken is None:
        return None
    best: tuple[Any, ...] | None = None
    best_at = None
    candidates = conn.execute(
        """
        SELECT snapshot_id, line, price, market_consensus_prob, captured_at, book
        FROM market_snapshots
        WHERE sport = 'NBA' AND event_id = ? AND outcome_id = ? AND snapshot_id != ?
        """,
        (row["event_id"], row["outcome_id"], row["snapshot_id"]),
    ).fetchall()
    for cand in candidates:
        at = try_parse_utc(cand[4])
        if at is None or at <= taken or at > tip or tip - at > VERIFIED_WINDOW:
            continue
        if best_at is None or at > best_at:
            best, best_at = tuple(cand), at
    if best is None or best_at is None:
        return None
    prob = best[3]
    return VerifiedClose(
        snapshot_id=best[0],
        line=best[1],
        price=best[2],
        consensus_prob=None if prob in (None, "") else float(prob),
        captured_at=best[4],
        book=str(best[5] or ""),
        minutes_before_tip=round((tip - best_at).total_seconds() / 60.0, 2),
    )


def clv_record(
    conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]
) -> tuple[dict[str, Any], VerifiedClose | None]:
    """The ``nba_clv`` row for a settled decision plus the verified close used (if any)."""
    close = verified_close(conn, row)
    taken_row = conn.execute(
        "SELECT market_consensus_prob, book FROM market_snapshots WHERE snapshot_id = ?",
        (row["snapshot_id"],),
    ).fetchone()
    taken = None if taken_row is None or taken_row[0] in (None, "") else float(taken_row[0])
    taken_book = "" if taken_row is None else str(taken_row[1] or "")
    record: dict[str, Any] = {
        "decision_id": row["decision_id"],
        "status": VERIFIED if close else UNVERIFIED,
        "same_book": bool(close and close.book and close.book == taken_book),
        "close_snapshot_id": close.snapshot_id if close else None,
        "close_captured_at": close.captured_at if close else None,
        "minutes_before_tip": close.minutes_before_tip if close else None,
        "taken_consensus_prob": taken,
        "close_consensus_prob": close.consensus_prob if close else None,
        "clv_prob": (
            close.consensus_prob - taken
            if close and close.consensus_prob is not None and taken is not None
            else None
        ),
    }
    return record, close
