"""Offline tests for Track C3 drawdown equity and multipliers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from outlier_scrapers import drawdown as dd


AS_OF = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _exec(
    *,
    pnl: float,
    settled_at: str,
    decision_id: str = "d1",
    status: str = "SETTLED",
    placed_units: float = 1.0,
) -> dict:
    return {
        "decision_id": decision_id,
        "execution_status": status,
        "settled_at": settled_at,
        "pnl": pnl,
        "placed_units": placed_units,
        "win_loss_push": "W" if pnl > 0 else ("PUSH" if pnl == 0 else "L"),
    }


def test_chronological_equity_and_hwm():
    rows = [
        _exec(pnl=2.0, settled_at="2026-07-01T00:00:00+00:00", decision_id="a"),
        _exec(pnl=1.0, settled_at="2026-07-02T00:00:00+00:00", decision_id="b"),
        _exec(pnl=-1.5, settled_at="2026-07-03T00:00:00+00:00", decision_id="c"),
    ]
    state = dd.compute_drawdown_state(rows, as_of=AS_OF, starting_equity=0.0)
    assert state.status == "active"
    assert state.equity == pytest.approx(1.5)
    assert state.high_water_mark == pytest.approx(3.0)
    assert state.drawdown_pct == pytest.approx(0.5)
    assert state.sample_count == 3


def test_drawdown_tiers_monotone_and_hard_stop():
    # Force deep drawdown
    rows = [
        _exec(pnl=10.0, settled_at="2026-07-01T00:00:00+00:00", decision_id="a"),
        _exec(pnl=-9.0, settled_at="2026-07-02T00:00:00+00:00", decision_id="b"),
    ]
    state = dd.compute_drawdown_state(rows, as_of=AS_OF)
    assert state.drawdown_pct == pytest.approx(0.9)
    assert state.tier == "stand_down"
    assert state.drawdown_multiplier == 0.0
    assert state.reason == "drawdown_stop"

    # Mild drawdown stays neutral under default 5% band
    mild = [
        _exec(pnl=10.0, settled_at="2026-07-01T00:00:00+00:00", decision_id="a"),
        _exec(pnl=-0.2, settled_at="2026-07-02T00:00:00+00:00", decision_id="b"),
    ]
    mild_state = dd.compute_drawdown_state(mild, as_of=AS_OF)
    assert mild_state.drawdown_pct == pytest.approx(0.02)
    assert mild_state.tier == "neutral"
    assert mild_state.drawdown_multiplier == 1.0


def test_rejects_unsettled_and_future_dated():
    rows = [
        _exec(pnl=5.0, settled_at="2026-07-01T00:00:00+00:00", decision_id="ok"),
        _exec(
            pnl=-100.0,
            settled_at="2026-08-01T00:00:00+00:00",
            decision_id="future",
        ),
        {
            "decision_id": "proposed",
            "execution_status": "PROPOSED",
            "settled_at": "2026-07-02T00:00:00+00:00",
            "pnl": -50.0,
            "placed_units": 0.0,
        },
        {
            "decision_id": "placed_open",
            "execution_status": "PLACED",
            "settled_at": "2026-07-02T00:00:00+00:00",
            "pnl": -50.0,
            "placed_units": 2.0,
        },
    ]
    state = dd.compute_drawdown_state(rows, as_of=AS_OF)
    assert state.sample_count == 1
    assert state.equity == pytest.approx(5.0)
    assert state.drawdown_pct == 0.0


def test_missing_state_neutral_in_shadow():
    mult, reason = dd.resolve_drawdown_multiplier(None, neutral_on_failure=True)
    assert mult == 1.0
    assert reason == "missing_drawdown_state"

    mult2, _ = dd.resolve_drawdown_multiplier(None, neutral_on_failure=False)
    assert mult2 == 0.0


def test_future_dated_state_rejected():
    state = dd.DrawdownState(
        equity=1.0,
        high_water_mark=2.0,
        drawdown_pct=0.5,
        as_of="2026-07-25T00:00:00+00:00",
        included_decision_cutoff="x",
        tier="reduced",
        drawdown_multiplier=0.7,
        sample_count=3,
        status="active",
    )
    mult, reason = dd.resolve_drawdown_multiplier(
        state, as_of=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    assert mult == 1.0
    assert reason == "future_dated_drawdown_state"


def test_tier_validation_rejects_non_monotone():
    with pytest.raises(ValueError, match="non-increasing"):
        dd.validate_tiers(
            [
                {"name": "a", "max_drawdown_pct": 0.05, "multiplier": 0.5},
                {"name": "b", "max_drawdown_pct": 0.10, "multiplier": 0.8},
            ]
        )


def test_state_round_trip(tmp_path):
    rows = [
        _exec(pnl=1.0, settled_at="2026-07-01T00:00:00+00:00", decision_id="a"),
        _exec(pnl=-0.5, settled_at="2026-07-02T00:00:00+00:00", decision_id="b"),
    ]
    state = dd.compute_drawdown_state(rows, as_of=AS_OF)
    path = dd.write_drawdown_state(state, tmp_path / "dd.json")
    loaded = dd.load_drawdown_state(path)
    assert loaded is not None
    assert loaded.equity == state.equity
    assert loaded.drawdown_multiplier == state.drawdown_multiplier
    assert loaded.tier == state.tier
