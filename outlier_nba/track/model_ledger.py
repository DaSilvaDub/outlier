"""Persist the team model's probabilities beside the market snapshots (plan M14).

The shadow run writes ``team_shadow.csv`` into its bundle; that is not queryable next to
the settled outcomes. ``nba_model_probs`` stores one row per ``(event, outcome, run)`` and
``nba_model_eval`` joins the *first pre-tip* model row of each outcome to the matching
``nba_outcome_eval`` row (the first pre-tip market capture, with its settlement and CLV).
Using the first row on both sides keeps one observation per outcome, as the rest of the
evaluation does, and means a later re-run on a drifted line cannot replace the decision-time
forecast.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

MODEL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nba_model_probs (
    event_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    family TEXT NOT NULL,
    market TEXT NOT NULL,
    line TEXT,
    model_prob REAL NOT NULL,
    model_win REAL,
    model_push REAL,
    model_loss REAL,
    market_prob REAL,
    model_weight REAL NOT NULL,
    final_prob REAL,
    flags TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (event_id, outcome_id, run_id)
)
"""

MODEL_VIEW_NAME = "nba_model_eval"
MODEL_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS nba_model_eval AS
SELECT e.*, m.run_id AS model_run_id, m.as_of AS model_as_of, m.family AS family,
       m.market AS model_market, m.model_prob AS model_prob, m.model_win AS model_win,
       m.model_push AS model_push, m.model_loss AS model_loss,
       m.market_prob AS model_market_prob, m.model_weight AS model_weight,
       m.final_prob AS final_prob, m.flags AS model_flags
FROM nba_outcome_eval e
JOIN (
    SELECT p.*, ROW_NUMBER() OVER (
               PARTITION BY p.event_id, p.outcome_id ORDER BY p.as_of ASC, p.run_id ASC
           ) AS mrn
    FROM nba_model_probs p
) m ON m.event_id = e.event_id AND m.outcome_id = e.outcome_id
WHERE m.mrn = 1 AND m.as_of < e.event_starts_at
"""


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def ensure_model_schema(conn: sqlite3.Connection) -> None:
    """Table now; the view needs ``nba_outcome_eval``, so it is created only once that exists."""
    conn.execute(MODEL_TABLE_SQL)
    have = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'nba_outcome_eval'").fetchone()
    if have:
        conn.execute(MODEL_VIEW_SQL)
    conn.commit()


def model_rows(rows: Sequence[Mapping[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """Shadow rows that carry a model probability, shaped for ``nba_model_probs``."""
    out: list[dict[str, Any]] = []
    for r in rows:
        prob = _num(r.get("model_prob"))
        if prob is None:
            continue
        out.append(
            {
                "event_id": str(r["event_id"]),
                "outcome_id": str(r["outcome_id"]),
                "run_id": run_id,
                "as_of": str(r["as_of"]),
                "family": str(r["family"]),
                "market": str(r["market"]),
                "line": None if r.get("line") in (None, "") else str(r["line"]),
                "model_prob": prob,
                "model_win": _num(r.get("model_win")),
                "model_push": _num(r.get("model_push")),
                "model_loss": _num(r.get("model_loss")),
                "market_prob": _num(r.get("market_prob")),
                "model_weight": float(r.get("model_weight") or 0.0),
                "final_prob": _num(r.get("final_prob")),
                "flags": str(r.get("flags") or ""),
            }
        )
    return out


def record_model_probs(
    db_path: Path, rows: Sequence[Mapping[str, Any]], run_id: str
) -> dict[str, int]:
    """Insert the run's model probabilities; a repeat of the same run is a no-op."""
    from outlier_nba.track.views import ensure_nba_schema
    from outlier_scrapers import feedback

    feedback.initialize_database(db_path)
    recs = model_rows(rows, run_id)
    with sqlite3.connect(db_path) as conn:
        ensure_nba_schema(conn)
        conn.executemany(
            """
            INSERT OR IGNORE INTO nba_model_probs
                (event_id, outcome_id, run_id, as_of, family, market, line, model_prob,
                 model_win, model_push, model_loss, market_prob, model_weight, final_prob, flags)
            VALUES (:event_id, :outcome_id, :run_id, :as_of, :family, :market, :line,
                    :model_prob, :model_win, :model_push, :model_loss, :market_prob,
                    :model_weight, :final_prob, :flags)
            """,
            recs,
        )
        conn.commit()
    return {"model_rows": len(recs)}
