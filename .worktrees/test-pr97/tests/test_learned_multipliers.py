"""Offline tests for Track C4 learned-multiplier integration."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from outlier_scrapers import drawdown as dd
from outlier_scrapers import learned_multipliers as lm
from outlier_scrapers import stake_calibration as sc


AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _train_rows(n: int = 100) -> list[dict]:
    wins = int(round(0.63 * n))
    losses = n - wins
    base = {
        "captured_at": "2026-07-10T16:00:00+00:00",
        "event_starts_at": "2026-07-10T20:00:00+00:00",
        "settled_at": "2026-07-10T23:00:00+00:00",
        "sport": "WNBA",
        "market_type": "PLAYER_PROP",
        "price": -110,
        "decimal_price": 1.9090909,
        "data_quality_tier": "HIGH",
        "final_blended_prob": 0.70,
        "push_prob": 0.0,
    }
    return (
        [{**base, "win_loss_push": "W"} for _ in range(wins)]
        + [{**base, "win_loss_push": "L"} for _ in range(losses)]
    )


def _live_row() -> dict:
    return {
        "captured_at": "2026-07-21T12:00:00+00:00",
        "event_starts_at": "2026-07-21T20:00:00+00:00",
        "sport": "WNBA",
        "market_type": "PLAYER_PROP",
        "price": -110,
        "decimal_price": 1.9090909,
        "data_quality_tier": "HIGH",
        "final_blended_prob": 0.70,
        "push_prob": 0.0,
    }


def test_shadow_neutral_forces_multipliers_to_one():
    artifact = sc.fit_stake_calibration(
        _train_rows(), as_of=AS_OF, min_samples=30, prior_strength=0.0
    )
    state = dd.compute_drawdown_state(
        [
            {
                "decision_id": "a",
                "execution_status": "SETTLED",
                "settled_at": "2026-07-01T00:00:00+00:00",
                "pnl": 10.0,
                "placed_units": 1.0,
                "win_loss_push": "W",
            },
            {
                "decision_id": "b",
                "execution_status": "SETTLED",
                "settled_at": "2026-07-02T00:00:00+00:00",
                "pnl": -8.0,
                "placed_units": 1.0,
                "win_loss_push": "L",
            },
        ],
        as_of=AS_OF,
    )
    assert state.drawdown_multiplier < 1.0

    result = lm.apply_learned_multipliers(
        _live_row(),
        calibration_artifact=artifact,
        drawdown_state=state,
        shadow_multipliers_neutral=True,
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert result.status == "shadow_neutral"
    assert result.calibration_multiplier == 1.0
    assert result.uncertainty_multiplier == 1.0
    assert result.drawdown_multiplier == 1.0
    assert result.raw_kelly_units > 0
    assert result.pre_cap_units == pytest.approx(result.raw_kelly_units)
    # Diagnostics still populated when artifacts valid
    assert result.calibrated_probability is not None
    assert result.calibrated_probability < 0.70


def test_active_multipliers_reduce_pre_cap():
    artifact = sc.fit_stake_calibration(
        _train_rows(), as_of=AS_OF, min_samples=30, prior_strength=0.0
    )
    result = lm.apply_learned_multipliers(
        _live_row(),
        calibration_artifact=artifact,
        drawdown_state=None,
        correlation_multiplier=1.0,
        shadow_multipliers_neutral=False,
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert result.status == "active"
    assert result.calibration_multiplier < 1.0
    assert result.uncertainty_multiplier <= 1.0
    assert result.pre_cap_units < result.raw_kelly_units
    assert result.pre_cap_units == pytest.approx(
        result.raw_kelly_units
        * result.calibration_multiplier
        * result.uncertainty_multiplier
        * result.correlation_multiplier
        * result.drawdown_multiplier,
        abs=1e-8,
    )


def test_drawdown_stop_zeros_pre_cap_in_active_mode():
    artifact = sc.fit_stake_calibration(
        _train_rows(), as_of=AS_OF, min_samples=30, prior_strength=0.0
    )
    state = dd.compute_drawdown_state(
        [
            {
                "decision_id": "a",
                "execution_status": "SETTLED",
                "settled_at": "2026-07-01T00:00:00+00:00",
                "pnl": 10.0,
                "placed_units": 1.0,
                "win_loss_push": "W",
            },
            {
                "decision_id": "b",
                "execution_status": "SETTLED",
                "settled_at": "2026-07-02T00:00:00+00:00",
                "pnl": -9.5,
                "placed_units": 1.0,
                "win_loss_push": "L",
            },
        ],
        as_of=AS_OF,
    )
    assert state.drawdown_multiplier == 0.0
    result = lm.apply_learned_multipliers(
        _live_row(),
        calibration_artifact=artifact,
        drawdown_state=state,
        shadow_multipliers_neutral=False,
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert result.pre_cap_units == 0.0
    assert result.reason == "drawdown_stop"
    assert result.status == "stand_down"


def test_correlation_multiplier_never_increases_stake():
    artifact = sc.fit_stake_calibration(
        _train_rows(), as_of=AS_OF, min_samples=30, prior_strength=0.0
    )
    base = lm.apply_learned_multipliers(
        _live_row(),
        calibration_artifact=artifact,
        shadow_multipliers_neutral=False,
        correlation_multiplier=1.0,
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    haircut = lm.apply_learned_multipliers(
        _live_row(),
        calibration_artifact=artifact,
        shadow_multipliers_neutral=False,
        correlation_multiplier=0.6,
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert haircut.pre_cap_units <= base.pre_cap_units
    assert haircut.correlation_multiplier == 0.6


def test_cold_start_missing_artifact_is_neutral():
    result = lm.apply_learned_multipliers(
        _live_row(),
        calibration_artifact=None,
        shadow_multipliers_neutral=False,
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert result.calibration_multiplier == 1.0
    assert result.uncertainty_multiplier == 1.0
    assert result.raw_kelly_units > 0
    assert result.pre_cap_units == pytest.approx(result.raw_kelly_units)
