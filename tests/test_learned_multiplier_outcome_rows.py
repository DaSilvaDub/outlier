"""The learned-multiplier promotion signal counts each recommended bet once.

A bet that is re-published by later pack runs is one snapshot with one
``pack_snapshot_memberships`` row per run, and the positive-unit population query
returns every membership. Counting them separately inflated
``min_settled_recommendations``, observation days, sport depth, and the
calibration-pair count that gate promoting the stake multipliers, while ROI and
hit rate were weighted by how often a pack was refreshed. The signal now keeps
one row per outcome (the first publication); the raw membership count stays
visible as ``pack_recommendation_rows``.
"""

from __future__ import annotations

from typing import Any

from outlier_scrapers import feedback

POLICY = {
    **feedback.LEARNED_MULTIPLIER_PROMOTION_DEFAULTS,
    "config_valid": True,
    "config_reason": "ok",
}


def _membership(pack: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "pack_capture_id": f"pack-{pack}",
        "pack_timestamp": f"2026-07-01T{10 + pack:02d}:00:00+00:00",
        "snapshot_id": "snapshot-1",
        "captured_at": "2026-07-01T09:00:00+00:00",
        "sport": "MLB",
        "event_id": "event-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
        "selection": "STL Team Total OVER 3.5",
        "line": "3.5",
        "selected": 1,
        "units": 1.0,
        "decimal_price": 2.0,
        "market_consensus_prob": 0.55,
        "push_prob": 0.0,
        "clv_price": 0.01,
        "event_starts_at": "2026-07-01T23:05:00+00:00",
        "win_loss_push": "W",
    }
    row.update(overrides)
    return row


def _signal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return feedback.learned_multiplier_promotion_signal(
        rows, POLICY, active_source_probability_column="market_consensus_prob"
    )


def test_a_bet_published_by_several_packs_counts_once():
    rows = [_membership(pack) for pack in range(3)]

    population = _signal(rows)["population"]

    assert population["total_recommendations"] == 1
    assert population["settled_recommendations"] == 1
    assert population["wins"] == 1
    assert population["settled_units"] == 1.0
    assert population["pack_recommendation_rows"] == 3


def test_distinct_bets_stay_separate():
    rows = [
        _membership(0),
        _membership(1),
        _membership(0, snapshot_id="snapshot-2", outcome_id="outcome-2"),
        _membership(0, snapshot_id="snapshot-3", event_id="event-2", sport="WNBA"),
    ]

    population = _signal(rows)["population"]

    assert population["total_recommendations"] == 3
    assert population["pack_recommendation_rows"] == 4
    assert population["settled_by_sport"] == {"MLB": 2, "WNBA": 1}


def test_first_publication_is_kept_whatever_the_later_results_say():
    # Rows of one outcome share a result in the ledger; if they ever disagreed,
    # the choice must still not depend on which result is more flattering.
    rows = [
        _membership(
            1,
            win_loss_push="W",
            snapshot_id="snapshot-b",
            captured_at="2026-07-01T11:00:00+00:00",
        ),
        _membership(
            0,
            win_loss_push="L",
            snapshot_id="snapshot-a",
            captured_at="2026-07-01T09:00:00+00:00",
        ),
    ]

    population = _signal(rows)["population"]

    assert population["total_recommendations"] == 1
    assert population["losses"] == 1
    assert population["wins"] == 0


def test_rows_without_identity_are_never_merged():
    rows = [
        {"units": 1.0, "win_loss_push": "W", "sport": "MLB"},
        {"units": 1.0, "win_loss_push": "L", "sport": "MLB"},
    ]

    population = _signal(rows)["population"]

    assert population["total_recommendations"] == 2
    assert population["pack_recommendation_rows"] == 2


def test_republishing_cannot_clear_the_minimum_sample_gate():
    # 120 distinct bets, each published by three pack runs: 360 membership rows.
    rows = [
        _membership(
            pack,
            snapshot_id=f"snapshot-{index}",
            outcome_id=f"outcome-{index}",
            event_starts_at=f"2026-07-{index % 30 + 1:02d}T23:05:00+00:00",
            sport="MLB" if index % 2 == 0 else "WNBA",
        )
        for index in range(120)
        for pack in range(3)
    ]

    signal = _signal(rows)

    assert signal["population"]["settled_recommendations"] == 120
    assert signal["gates"]["minimum_settled_recommendations"] is False
