"""SO eval harness + context/WNBA scaffold tests (feature branch)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from outlier_scrapers.projections import (
    apply_so_context_adjustments,
    compute_wnba_minutes_features,
    mlb_so_projection_record,
    wnba_points_projection_record,
)
from outlier_scrapers.results import _latest_local_close
from outlier_scrapers.so_eval import evaluate_so_probs
from outlier_scrapers.line_movement import STALE_PROPS_MAX_AGE_HOURS
from outlier_scrapers.feed_health import MAX_SOURCE_AGE_HOURS


def test_stale_windows_are_aligned():
    assert STALE_PROPS_MAX_AGE_HOURS == MAX_SOURCE_AGE_HOURS == 6.0


def test_so_context_adjustments_blend_opponent_and_park():
    bf, rate = apply_so_context_adjustments(22.0, 0.20, opponent_k_rate=0.30, park_k_factor=1.05)
    assert bf == 22.0
    expected = (0.7 * 0.20 + 0.3 * 0.30) * 1.05
    assert rate == expected
    assert rate != 0.20
    # park alone
    bf2, rate2 = apply_so_context_adjustments(22.0, 0.20, park_k_factor=1.10)
    assert rate2 == 0.20 * 1.10


def test_mlb_so_uses_context_adjustments_when_present():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Michael McGreevy - Strikeouts OVER 5.5",
        "player": "Michael McGreevy",
        "team": "STL",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 5.5,
        "headline_side": "OVER",
    }
    base = {
        "STL": {
            "pitcher": "Michael McGreevy",
            "confirmed": True,
            "projected_bf": 22.0,
            "strikeout_rate": 0.20,
            "feature_source": "mlb_stats_gamelog",
        }
    }
    with_ctx = {
        "STL": {
            **base["STL"],
            "opponent_k_rate": 0.30,
            "park_k_factor": 1.0,
        }
    }
    plain = mlb_so_projection_record(row, base)
    adjusted = mlb_so_projection_record(row, with_ctx)
    assert plain is not None and adjusted is not None
    assert adjusted["distribution"]["win_prob"] != plain["distribution"]["win_prob"]


def test_wnba_minutes_features_fail_closed_and_project():
    assert compute_wnba_minutes_features([20.0, 22.0]) is None
    features = compute_wnba_minutes_features([20.0, 22.0, 24.0, 18.0])
    assert features is not None
    assert features["games"] == 4
    row = {
        "sport": "WNBA",
        "market_type": "PTS",
        "selection": "A'ja Wilson - Points OVER 22.5",
        "line": 22.5,
        "headline_side": "OVER",
        "outcome_id": "o1",
        "event_id": "e1",
        "market_id": "m1",
    }
    record = wnba_points_projection_record(row, features={**features, "points_per_minute": 0.95})
    assert record is not None
    assert record["feature_snapshot_hash"] == "wnba-minutes-ppm-v1"
    assert record["distribution"]["win_prob"] > 0
    assert wnba_points_projection_record(row, features=features) is None


def test_latest_local_close_falls_back_without_same_book(tmp_path: Path):
    from outlier_scrapers import feedback

    db = tmp_path / "fb.sqlite3"
    feedback.initialize_database(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # Only a DK snapshot was ever captured for this outcome; the row being
    # settled was taken at FD (a snapshot_id that never made it into
    # market_snapshots here, which is fine -- it only needs to be excluded).
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, event_starts_at, created_at
        ) VALUES (
            'dk-snap', '2026-08-20T18:00:00+00:00', 'MLB', 'e1', 'm1', 'o1',
            'Player SO OVER 5.5', 5.5, -110, 'DK',
            '2026-08-20T23:00:00+00:00', '2026-08-20T18:00:00+00:00'
        )
        """
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT 'e1' AS event_id, 'o1' AS outcome_id, 'm1' AS market_id,
               'Player SO OVER 5.5' AS selection, 'FD' AS book,
               'taken-snap' AS snapshot_id
        """
    ).fetchone()
    line, price = _latest_local_close(conn, row)
    assert line == "5.5"
    assert price == -110
    conn.close()


def test_evaluate_so_probs_reports_insufficient_without_pairs(tmp_path: Path):
    db = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE market_snapshots (
            snapshot_id TEXT, selection TEXT, market_type TEXT,
            market_consensus_prob REAL, independent_model_prob REAL,
            projection_feature_hash TEXT, model_prob_source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlements (
            snapshot_id TEXT, win_loss_push TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    report = evaluate_so_probs(db)
    assert report["status"] == "insufficient_settled_so"
    assert report["n"] == 0
