"""One settled outcome is one observation for fitting and evaluation.

``capture_pack`` creates a new market_snapshot whenever the capture time, line,
price or book changes, so a single bet that is re-captured (T-30 recaptures,
second book, a later pack run) shows up several times in ``_joined_rows``. The
fitters and the outcome-level report sections now read ``_outcome_rows``: the
decision-time capture of each outcome (earliest published capture, else earliest).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from outlier_scrapers import calibration, feedback, feedback_db, probability_blend
from outlier_scrapers import stake_calibration
from outlier_scrapers.feedback_db import one_row_per_outcome


def _row(**overrides: Any) -> dict[str, Any]:
    base = {
        "sport": "MLB",
        "event_id": "event-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
        "selection": "STL Team Total OVER 3.5",
        "line": "3.5",
        "captured_at": "2026-07-01T16:00:00-04:00",
        "snapshot_id": "snap-a",
        "decision_id": "dec-a",
    }
    base.update(overrides)
    return base


def test_keeps_the_earliest_capture_by_instant_not_by_text():
    # Text order puts "...T17:00-04:00" first; it is 21:00Z, after 20:00Z.
    later = _row(captured_at="2026-07-01T17:00:00-04:00", snapshot_id="snap-late")
    earlier = _row(captured_at="2026-07-01T20:00:00+00:00", snapshot_id="snap-early")

    kept = one_row_per_outcome([later, earlier])

    assert [row["snapshot_id"] for row in kept] == ["snap-early"]


def test_a_published_capture_beats_an_earlier_unselected_one():
    # The decision-time capture is the one the pack published, not merely the
    # first time a scraper saw the line (797 such groups in the live ledger).
    early_unpublished = _row(captured_at="2026-07-01T12:00:00-04:00", snapshot_id="a", selected=0)
    published = _row(captured_at="2026-07-01T15:00:00-04:00", snapshot_id="b", selected=1)
    later_published = _row(captured_at="2026-07-01T18:00:00-04:00", snapshot_id="c", selected=1)

    kept = one_row_per_outcome([early_unpublished, later_published, published])

    assert [row["snapshot_id"] for row in kept] == ["b"]


def test_distinct_lines_selections_and_events_stay_separate():
    rows = [
        _row(),
        _row(snapshot_id="snap-b", line="4.5", selection="STL Team Total OVER 4.5"),
        _row(snapshot_id="snap-c", event_id="event-2"),
        _row(snapshot_id="snap-d", sport="WNBA"),
        _row(snapshot_id="snap-e", captured_at="2026-07-01T18:00:00-04:00"),
    ]

    kept = {row["snapshot_id"] for row in one_row_per_outcome(rows)}

    assert kept == {"snap-a", "snap-b", "snap-c", "snap-d"}


def test_unparseable_timestamp_sorts_after_parseable_ones_and_ties_are_stable():
    rows = [
        _row(captured_at="", snapshot_id="snap-blank"),
        _row(snapshot_id="snap-z"),
        _row(snapshot_id="snap-y"),
    ]

    assert [row["snapshot_id"] for row in one_row_per_outcome(rows)] == ["snap-y"]


def test_rows_without_identity_are_never_merged():
    rows = [{"win_loss_push": "W"}, {"win_loss_push": "L"}, _row(event_id="")]

    assert len(one_row_per_outcome(rows)) == 3


def _seed(db_path: Path, captures: list[tuple[str, str, str]]) -> None:
    """Seed one outcome captured several times: (suffix, captured_at, book)."""

    feedback.initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for suffix, captured_at, book in captures:
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    selection, line, price, book, decimal_price, implied_prob,
                    market_consensus_prob, independent_model_prob, push_prob,
                    board, market_type, event_starts_at, hours_before_game,
                    odds_range, time_before_game, data_quality_tier, created_at
                ) VALUES (?, ?, 'MLB', 'event-1', 'market-1', 'outcome-1',
                          'STL Team Total OVER 3.5', '3.5', -110, ?, 1.909, 0.5238,
                          0.55, 0.60, 0.0, 'B', 'TEAM_PROP',
                          '2026-07-01T23:05:00+00:00', 5.0, 'MINUS_110_TO_PLUS_110',
                          'H3_TO_12', 'HIGH', ?)
                """,
                (f"snap-{suffix}", captured_at, book, captured_at),
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
                ) VALUES (?, ?, ?, 'outcome-1', 'event-1', 'market-1', '5', 'W', 0.0,
                          'W', '2026-07-02T05:00:00+00:00')
                """,
                (f"set-{suffix}", f"dec-{suffix}", f"snap-{suffix}"),
            )
        conn.commit()
    finally:
        conn.close()


CAPTURES = [
    ("t30", "2026-07-01T18:35:00-04:00", "BookA"),
    ("open", "2026-07-01T14:00:00-04:00", "BookA"),
    ("book2", "2026-07-01T14:00:00-04:00", "BookB"),
]


def test_outcome_rows_returns_the_first_decision_time_capture(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed(db_path, CAPTURES)

    conn = feedback_db.open_database(db_path)
    try:
        joined = feedback_db._joined_rows(conn)
        outcomes = feedback_db._outcome_rows(conn)
    finally:
        conn.close()

    assert len(joined) == 3
    assert [row["snapshot_id"] for row in outcomes] == ["snap-book2"]


def test_blend_and_stake_calibration_fit_one_row_per_outcome(tmp_path, monkeypatch):
    db_path = tmp_path / "feedback.sqlite3"
    _seed(db_path, CAPTURES)
    seen: dict[str, int] = {}

    def fake_blend(rows: Any, **_kwargs: Any) -> dict[str, Any]:
        seen["blend"] = len(list(rows))
        return {}

    def fake_stake(rows: Any, **_kwargs: Any) -> dict[str, Any]:
        seen["stake"] = len(list(rows))
        return {}

    monkeypatch.setattr(probability_blend, "fit_weight_artifact", fake_blend)
    monkeypatch.setattr(probability_blend, "write_weight_artifact", lambda *_a: None)
    monkeypatch.setattr(stake_calibration, "fit_stake_calibration", fake_stake)
    monkeypatch.setattr(stake_calibration, "write_stake_calibration_artifact", lambda *_a: None)

    calibration.fit_blend_weights(db_path, tmp_path / "blend.json")
    calibration.fit_stake_calibration_from_db(db_path, tmp_path / "stake.json")

    assert seen == {"blend": 1, "stake": 1}


def test_report_scores_outcomes_once_but_keeps_decision_level_counts(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed(db_path, CAPTURES)

    out = feedback.generate_report(db_path, tmp_path / "report")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))

    assert summary["coverage"]["graded_and_linked"] == 3
    assert summary["coverage"]["graded_outcomes"] == 1
    consensus = next(
        m for m in summary["probability_metrics"] if m["probability_source"] == "market_consensus"
    )
    assert consensus["n"] == 1
