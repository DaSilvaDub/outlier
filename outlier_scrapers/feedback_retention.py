"""Retention policy for settled, never-played feedback ledger rows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from outlier_scrapers.feedback_db import DEFAULT_DB_PATH, FeedbackError, _connect

@dataclass(frozen=True)
class RetentionStats:
    eligible: int
    slimmed: int
    bytes_before: int
    bytes_after: int

_RETENTION_FULL_FIDELITY_VERDICTS = {"PLAY", "BET"}
# Only columns _joined_rows() never selects are safe to clear: that query
# backs fit_blend_weights(), fit_stake_calibration_from_db(), and
# generate_report() alike, so anything it reads is live training/calibration
# input, not disposable detail -- clearing it would silently shrink the
# eligible sample for every fitter and report metric derived from these
# rows, not just trim bytes. What's left is portfolio-sizing-at-capture-time
# and pack-provenance metadata that none of them consume.
_RETENTION_SLIMMED_COLUMNS = [
    "projection_feature_hash",
    "projection_quality_flags",
    "pack_path",
    "policy_fingerprint",
    "portfolio_mode",
    "pre_cap_units",
    "portfolio_units",
    "cap_reasons",
]


def apply_retention_policy(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    cutoff_days: int = 90,
    dry_run: bool = False,
) -> RetentionStats:
    """Slim disposable snapshot columns for settled, never-played history.

    A row keeps its portfolio-sizing-at-capture-time and pack-provenance
    columns (``pre_cap_units``, ``portfolio_units``, ``cap_reasons``,
    ``policy_fingerprint``, ``portfolio_mode``, ``pack_path``,
    ``projection_feature_hash``, ``projection_quality_flags``) only if it
    reached ``PLAY``/``BET`` or the desk flagged it (``board =
    'A_FLAGGED'``); those columns are cleared once the row is already
    settled and older than ``cutoff_days``. Every other column -- including
    the probability/edge/signal-component fields ``fit_blend_weights()``,
    ``fit_stake_calibration_from_db()``, and ``generate_report()`` all read
    via the shared settled-row query -- is left alone regardless of age, so
    retention never shrinks the eligible sample those consume. Unsettled,
    recent, played, or flagged rows are never touched. Vacuums afterward to
    actually reclaim the freed pages on disk.
    """
    if cutoff_days < 0:
        # A negative value pushes the cutoff into the future, matching every
        # settled row (including today's) instead of only old ones -- a
        # typo'd sign here would otherwise silently slim recent history.
        raise FeedbackError(f"cutoff_days must be non-negative, got {cutoff_days}")
    db_path = Path(db_path)
    bytes_before = db_path.stat().st_size if db_path.exists() else 0
    conn = _connect(db_path)
    slimmed = 0
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()
        candidates = conn.execute(
            """
            SELECT DISTINCT s.snapshot_id
            FROM market_snapshots s
            JOIN decisions d ON d.snapshot_id = s.snapshot_id
            JOIN settlements t ON t.snapshot_id = s.snapshot_id
            WHERE COALESCE(s.board, '') != 'A_FLAGGED'
              AND UPPER(COALESCE(NULLIF(d.final_verdict, ''), d.pipeline_verdict, ''))
                  NOT IN ('PLAY', 'BET')
              AND t.settled_at < ?
              AND s.captured_at < ?
              AND (
                  s.projection_feature_hash IS NOT NULL OR s.projection_quality_flags IS NOT NULL
                  OR s.pack_path IS NOT NULL OR s.policy_fingerprint IS NOT NULL
                  OR s.portfolio_mode IS NOT NULL OR s.pre_cap_units IS NOT NULL
                  OR s.portfolio_units IS NOT NULL OR s.cap_reasons IS NOT NULL
              )
            """,
            (cutoff, cutoff),
        ).fetchall()
        snapshot_ids = [row["snapshot_id"] for row in candidates]
        if snapshot_ids and not dry_run:
            clear_assignments = ", ".join(
                f"{column} = NULL" for column in _RETENTION_SLIMMED_COLUMNS
            )
            for start in range(0, len(snapshot_ids), 500):
                chunk = snapshot_ids[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"""
                    UPDATE market_snapshots
                    SET {clear_assignments}
                    WHERE snapshot_id IN ({placeholders})
                    """,
                    chunk,
                )
                slimmed += len(chunk)
        conn.commit()
    finally:
        conn.close()

    if slimmed and not dry_run:
        vacuum_conn = sqlite3.connect(db_path)
        try:
            vacuum_conn.execute("VACUUM")
        finally:
            vacuum_conn.close()

    bytes_after = db_path.stat().st_size if db_path.exists() else bytes_before
    return RetentionStats(
        eligible=len(snapshot_ids),
        slimmed=slimmed,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )
