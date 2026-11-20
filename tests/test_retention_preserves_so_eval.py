"""Retention must not erode the SO promotion sample.

``so_eval.evaluate_so_probs(require_v2_hash=True)`` admits a settled row only
when its ``projection_feature_hash`` names the current gamelog generation, so
clearing that hash on old never-played rows silently shrank the promotion
sample every night. The hash is now kept; the other disposable columns still go.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from outlier_scrapers import feedback, so_eval


def _seed_so_row(db_path: Path, *, suffix: str, settled_days_ago: int) -> None:
    feedback.initialize_database(db_path)
    now = datetime.now(timezone.utc)
    captured_at = (now - timedelta(days=settled_days_ago + 1)).isoformat()
    settled_at = (now - timedelta(days=settled_days_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                selection, line, price, book, decimal_price, board, market_type,
                market_consensus_prob, independent_model_prob,
                projection_feature_hash, pack_path, cap_reasons, created_at
            ) VALUES (?, ?, 'MLB', ?, ?, ?, 'Ace Pitcher - Strikeouts OVER 5.5', '5.5',
                      -110, 'Book', 1.909, 'B', 'PLAYER_PROP', 0.52, 0.58,
                      'so-starter-gamelog-v2', '/packs/2026-01-01', 'none', ?)
            """,
            (
                f"snapshot-{suffix}",
                captured_at,
                f"event-{suffix}",
                f"market-{suffix}",
                f"outcome-{suffix}",
                captured_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO decisions (
                decision_id, snapshot_id, pipeline_verdict, units, created_at, updated_at
            ) VALUES (?, ?, 'STAND_DOWN', 0.0, ?, ?)
            """,
            (f"decision-{suffix}", f"snapshot-{suffix}", captured_at, captured_at),
        )
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_id, decision_id, snapshot_id, outcome_id, event_id, market_id,
                actual_result, win_loss_push, pnl, would_have_result, settled_at
            ) VALUES (?, ?, ?, ?, ?, ?, '7', 'W', 0.0, 'W', ?)
            """,
            (
                f"settlement-{suffix}",
                f"decision-{suffix}",
                f"snapshot-{suffix}",
                f"outcome-{suffix}",
                f"event-{suffix}",
                f"market-{suffix}",
                settled_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_retention_keeps_the_so_promotion_sample_intact(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed_so_row(db_path, suffix="old", settled_days_ago=200)
    _seed_so_row(db_path, suffix="recent", settled_days_ago=1)
    before = so_eval.evaluate_so_probs(db_path, require_v2_hash=True)
    assert before["n"] == 2

    stats = feedback.apply_retention_policy(db_path, cutoff_days=90)

    assert stats.slimmed == 1
    after = so_eval.evaluate_so_probs(db_path, require_v2_hash=True)
    assert after["n"] == before["n"]
    conn = sqlite3.connect(db_path)
    try:
        hash_, pack_path, cap_reasons = conn.execute(
            "SELECT projection_feature_hash, pack_path, cap_reasons "
            "FROM market_snapshots WHERE snapshot_id = 'snapshot-old'"
        ).fetchone()
    finally:
        conn.close()
    assert hash_ == "so-starter-gamelog-v2"
    # The genuinely disposable provenance columns are still reclaimed.
    assert pack_path is None
    assert cap_reasons is None


def test_a_kept_hash_alone_does_not_re_select_a_slimmed_row(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed_so_row(db_path, suffix="old", settled_days_ago=200)

    first = feedback.apply_retention_policy(db_path, cutoff_days=90)
    rerun = feedback.apply_retention_policy(db_path, cutoff_days=90)

    assert first.eligible == 1
    assert rerun.eligible == 0
    assert rerun.slimmed == 0
