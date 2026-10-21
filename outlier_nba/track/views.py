"""Ledger schema additions and the one-row-per-outcome evaluation view (plan M14).

The ledger stores a snapshot per capture, so one outcome appears many times as
its line and price drift. Training or scoring on all of them is pseudo-replication.
``nba_outcome_eval`` keeps exactly the first decision-time (pre-tip) capture of
each ``(event_id, outcome_id)``.
"""

from __future__ import annotations

import sqlite3

from outlier_nba.track.capture import METHOD_TABLE_SQL
from outlier_nba.track.clv import SCHEMA_SQL as CLV_SCHEMA_SQL

EVAL_VIEW_NAME = "nba_outcome_eval"
EVAL_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS nba_outcome_eval AS
SELECT * FROM (
    SELECT s.snapshot_id, s.event_id, s.outcome_id, s.market_id, s.market_type, s.selection,
           s.line, s.price, s.book, s.market_consensus_prob, s.push_prob, s.captured_at,
           s.event_starts_at, s.data_quality_flags, d.units AS units,
           x.win_loss_push, x.actual_result, x.closing_price, x.clv_price,
           c.status AS close_status, c.clv_prob,
           ROW_NUMBER() OVER (
               PARTITION BY s.event_id, s.outcome_id
               ORDER BY s.captured_at ASC, s.snapshot_id ASC
           ) AS rn
    FROM market_snapshots s
    LEFT JOIN decisions d ON d.snapshot_id = s.snapshot_id
    LEFT JOIN settlements x ON x.decision_id = d.decision_id
    LEFT JOIN nba_clv c ON c.decision_id = d.decision_id
    WHERE s.sport = 'NBA' AND s.captured_at < s.event_starts_at
) WHERE rn = 1
"""


def ensure_nba_schema(conn: sqlite3.Connection) -> None:
    for statement in (*CLV_SCHEMA_SQL, METHOD_TABLE_SQL):
        conn.execute(statement)
    conn.execute(EVAL_VIEW_SQL)
    conn.commit()
