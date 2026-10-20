"""``negative_learned_edge`` must run where the learned probability exists.

The gate used to sit in ``build_row`` and read ``learned_conservative_probability``,
a column only written later by ``write_pack`` -> ``_apply_learned_stake_before_caps``.
It could never fire. It now lives in the sizing step, and only withholds a stake
when the learned layer actually governs published stakes (multipliers active and
the portfolio policy in enforce mode); a shadow diagnostic never vetoes a bet.
"""

from __future__ import annotations

from typing import Any

from outlier_scrapers.pack import _apply_learned_stake_before_caps, build_row
from outlier_scrapers.portfolio import PortfolioPolicy


def _artifact(observed_mean: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_version": "stake-cal-test",
        "status": "active",
        "source_probability_column": "market_consensus_prob",
        "training_cutoff": "2026-01-01T00:00:00+00:00",
        "min_samples": 30,
        "prior_strength": 0.0,
        "confidence_level": 0.8,
        "global": {
            "n": 100,
            "wins": round(observed_mean * 100),
            "losses": 100 - round(observed_mean * 100),
            "observed_mean": observed_mean,
            "predicted_mean": 0.58,
            "shrunk_observed_mean": observed_mean,
            "shrunk_reliability_factor": 1.0,
        },
        "dimensions": {},
    }


def _rows() -> tuple[dict[str, Any], dict[str, Any]]:
    original = {
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
        "decimal_price": 1.9090909,
        "implied_prob": 0.5238,
        "push_prob": 0.0,
        "market_consensus_prob": 0.58,
        "recommended_units_pre_news": 1.5,
        "data_quality_flags": "thin_liquidity",
        "data_quality_tier": "HIGH",
        "projection_quality_flags": "",
    }
    return {**original, "units": 1.5}, original


def _run(
    *, observed_mean: float, multipliers_neutral: bool, mode: str = "enforce"
) -> tuple[dict[str, Any], dict[str, Any]]:
    projected, original = _rows()
    policy = PortfolioPolicy(
        mode=mode,
        streams_in_scope=["candidates"],
        max_wager_units=10.0,
        shadow_multipliers_neutral=multipliers_neutral,
    )
    _apply_learned_stake_before_caps(
        projected,
        original,
        stream="candidates",
        policy=policy,
        runtime={
            "enabled": True,
            "source_probability_column": "market_consensus_prob",
            "calibration_artifact": _artifact(observed_mean),
            "drawdown_state": None,
            "shadow_multipliers_neutral": multipliers_neutral,
        },
    )
    return projected, original


def test_active_learned_layer_withholds_stake_when_conservative_edge_is_negative():
    projected, original = _run(observed_mean=0.40, multipliers_neutral=False)

    assert original["learned_conservative_probability"] < original["implied_prob"]
    for row in (projected, original):
        flags = row["data_quality_flags"].split(";")
        assert "negative_learned_edge" in flags
        assert "thin_liquidity" in flags
        assert row["recommended_units_pre_news"] == ""
        assert row["actionable"] == "false"
        assert row["board"] == "A_FLAGGED"
        assert row["_board"] == "flagged"
        assert row["data_quality_tier"] == "LOW"
    assert projected["pre_cap_units"] == 0.0


def test_active_learned_layer_keeps_stake_when_conservative_edge_is_positive():
    projected, original = _run(observed_mean=0.70, multipliers_neutral=False)

    assert original["learned_conservative_probability"] > original["implied_prob"]
    assert "negative_learned_edge" not in original["data_quality_flags"]
    assert original["actionable"] == "true"
    assert original["recommended_units_pre_news"] == 1.5
    assert projected["pre_cap_units"] > 0.0


def test_shadow_neutral_learned_diagnostic_never_vetoes_a_stake():
    projected, original = _run(observed_mean=0.40, multipliers_neutral=True)

    assert original["learned_conservative_probability"] < original["implied_prob"]
    assert "negative_learned_edge" not in original["data_quality_flags"]
    assert original["actionable"] == "true"
    assert original["board"] == "A"
    assert original["recommended_units_pre_news"] == 1.5
    assert projected["pre_cap_units"] == 1.5


def test_portfolio_shadow_mode_does_not_touch_the_published_row():
    projected, original = _run(observed_mean=0.40, multipliers_neutral=False, mode="shadow")

    assert "negative_learned_edge" not in original["data_quality_flags"]
    assert original["actionable"] == "true"
    assert original["recommended_units_pre_news"] == 1.5
    # The allocator still sees the learned ceiling of zero.
    assert projected["pre_cap_units"] == 0.0


def test_build_row_gates_no_longer_read_a_column_they_cannot_have():
    """The dead build-time copy is gone; the flag stays disqualifying downstream."""
    import inspect

    from outlier_scrapers import pack_selection

    source = inspect.getsource(pack_selection._apply_quality_and_signal_flags)
    assert "learned_conservative_probability" not in source
    assert "learned_conservative_probability" not in inspect.getsource(build_row)
    assert "negative_learned_edge" in pack_selection.DISQUALIFYING_DQ_FLAGS
