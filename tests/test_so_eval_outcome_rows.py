"""The SO promotion gate scores each settled outcome once.

``capture_pack`` writes a new market_snapshot whenever capture time, line, price
or book changes, so one SO bet re-captured at T-30 or quoted by a second book is
several settled rows. ``evaluate_so_probs`` used to count every one of them, so
``n`` (and ``gamelog_v2_rows``, which the promotion gate compares to its minimum
sample) was inflated by re-captures that share a single result.

It now keeps one row per outcome -- the decision-time capture -- *before* it
applies the hash and probability filters, so an outcome is judged on the
probabilities that drove the decision, not on a later re-score.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from outlier_scrapers import feedback, so_eval

V1 = "so-starter-gamelog-v1"
V2 = "so-starter-gamelog-v2"
SELECTION = "Ace Pitcher - Strikeouts OVER 5.5"


def _seed(
    db_path: Path,
    captures: list[dict],
    *,
    event: str = "event-1",
    result: str = "W",
) -> None:
    """Seed one SO outcome captured several times; each capture is one snapshot."""

    feedback.initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for capture in captures:
            suffix = f"{event}-{capture['suffix']}"
            captured_at = capture["captured_at"]
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    selection, line, price, book, decimal_price, board, market_type,
                    market_consensus_prob, independent_model_prob,
                    projection_feature_hash, selected, created_at
                ) VALUES (?, ?, 'MLB', ?, 'market-1', 'outcome-1', ?, '5.5',
                          -110, ?, 1.909, 'B', 'PLAYER_PROP', ?, ?, ?, ?, ?)
                """,
                (
                    f"snap-{suffix}",
                    captured_at,
                    event,
                    SELECTION,
                    capture.get("book", "BookA"),
                    capture.get("market", 0.55),
                    capture.get("independent", 0.60),
                    capture.get("hash", V2),
                    capture.get("selected", 0),
                    captured_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict, units, created_at, updated_at
                ) VALUES (?, ?, 'STAND_DOWN', 0.0, ?, ?)
                """,
                (f"dec-{suffix}", f"snap-{suffix}", captured_at, captured_at),
            )
            conn.execute(
                """
                INSERT INTO settlements (
                    settlement_id, decision_id, snapshot_id, outcome_id, event_id,
                    market_id, actual_result, win_loss_push, pnl, would_have_result,
                    settled_at
                ) VALUES (?, ?, ?, 'outcome-1', ?, 'market-1', '7', ?, 0.0, ?,
                          '2026-07-02T05:00:00+00:00')
                """,
                (f"set-{suffix}", f"dec-{suffix}", f"snap-{suffix}", event, result, result),
            )
        conn.commit()
    finally:
        conn.close()


RECAPTURES = [
    {"suffix": "open", "captured_at": "2026-07-01T14:00:00-04:00"},
    {"suffix": "t30", "captured_at": "2026-07-01T18:35:00-04:00"},
    {"suffix": "book2", "captured_at": "2026-07-01T14:00:00-04:00", "book": "BookB"},
]


def test_a_recaptured_outcome_counts_once(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed(db_path, RECAPTURES)
    _seed(db_path, [{"suffix": "only", "captured_at": "2026-07-02T14:00:00-04:00"}], event="event-2")

    report = so_eval.evaluate_so_probs(db_path, require_v2_hash=True)

    assert report["n"] == 2
    assert report["gamelog_v2_rows"] == 2


def test_decision_time_capture_supplies_the_probabilities(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    # An early scraper sighting the pack never published (independent 0.30), then the
    # capture the pack actually published (independent 0.70). The bet won.
    _seed(
        db_path,
        [
            {"suffix": "early", "captured_at": "2026-07-01T12:00:00-04:00", "independent": 0.30},
            {
                "suffix": "published",
                "captured_at": "2026-07-01T15:00:00-04:00",
                "independent": 0.70,
                "selected": 1,
            },
        ],
    )

    report = so_eval.evaluate_so_probs(db_path)

    assert report["n"] == 1
    assert report["independent_brier"] == pytest.approx((0.70 - 1.0) ** 2, abs=1e-6)


def test_the_hash_filter_judges_the_decision_time_capture(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    # Sized from a v1 projection; a later, unpublished re-score carries the v2 hash.
    # That re-score is not what drove the bet, so it must not unlock the v2 gate.
    _seed(
        db_path,
        [
            {
                "suffix": "published",
                "captured_at": "2026-07-01T15:00:00-04:00",
                "hash": V1,
                "selected": 1,
            },
            {"suffix": "rescore", "captured_at": "2026-07-01T19:00:00-04:00", "hash": V2},
        ],
    )

    v2_only = so_eval.evaluate_so_probs(db_path, require_v2_hash=True)
    assert v2_only["status"] == "insufficient_settled_so"
    assert v2_only["n"] == 0

    pooled = so_eval.evaluate_so_probs(db_path, require_gamelog_hash=True)
    assert pooled["n"] == 1
    assert pooled["gamelog_v2_rows"] == 0


def test_the_kept_capture_is_chosen_without_looking_at_the_result(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed(db_path, RECAPTURES, result="L")

    report = so_eval.evaluate_so_probs(db_path)

    assert report["n"] == 1
    # One loss scored once: independent 0.60 and market 0.55 both predicted a win.
    assert report["independent_brier"] == pytest.approx(0.60**2, abs=1e-6)
    assert report["market_brier"] == pytest.approx(0.55**2, abs=1e-6)
