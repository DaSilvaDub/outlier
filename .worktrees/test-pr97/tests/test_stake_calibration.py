"""Offline tests for Track C1/C2 stake calibration and uncertainty haircuts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from outlier_scrapers import stake_calibration as sc


def _row(
    *,
    predicted: float = 0.70,
    result: str = "W",
    push: float = 0.0,
    market_type: str = "PLAYER_PROP",
    price: float = -110,
    captured_at: str = "2026-07-10T16:00:00+00:00",
    event_starts_at: str = "2026-07-10T20:00:00+00:00",
    sport: str = "WNBA",
    decimal_price: float = 1.9090909,
) -> dict:
    return {
        "captured_at": captured_at,
        "event_starts_at": event_starts_at,
        "settled_at": "2026-07-10T23:00:00+00:00",
        "sport": sport,
        "market_type": market_type,
        "price": price,
        "decimal_price": decimal_price,
        "data_quality_tier": "HIGH",
        "final_blended_prob": predicted,
        "push_prob": push,
        "win_loss_push": result,
    }


def _rows_70_predict_63_observe(*, n: int = 100, market_type: str = "PLAYER_PROP") -> list[dict]:
    """Nominal 70% predictions that win ~63% of non-push outcomes."""
    wins = int(round(0.63 * n))
    losses = n - wins
    rows = [_row(predicted=0.70, result="W", market_type=market_type) for _ in range(wins)]
    rows += [_row(predicted=0.70, result="L", market_type=market_type) for _ in range(losses)]
    return rows


AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_conditional_non_push_round_trip():
    cond = sc.conditional_non_push_prob(0.45, 0.10)
    assert cond == pytest.approx(0.5)
    back = sc.unconditional_from_conditional(cond, 0.10)
    assert back == pytest.approx(0.45)


def test_fit_70_predict_63_observe_reduces_kelly():
    rows = _rows_70_predict_63_observe(n=100)
    artifact = sc.fit_stake_calibration(
        rows,
        as_of=AS_OF,
        min_samples=30,
        prior_strength=0.0,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    assert artifact["status"] == "active"
    assert artifact["source_probability_column"] == "final_blended_prob"
    assert artifact["global"]["observed_mean"] == pytest.approx(0.63, abs=0.02)
    assert artifact["global"]["predicted_mean"] == pytest.approx(0.70, abs=0.01)

    live = {
        **_row(predicted=0.70),
        "captured_at": "2026-07-21T12:00:00+00:00",
    }
    resolved = sc.resolve_stake_calibration(artifact, live, as_of=datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert resolved.status == "active"
    assert resolved.calibrated_conditional is not None
    assert resolved.calibrated_conditional < 0.70
    assert resolved.calibrated_conditional == pytest.approx(0.63, abs=0.03)
    # Never raise above source
    assert resolved.calibrated_probability <= resolved.source_probability + 1e-12
    assert 0.0 <= resolved.calibration_multiplier < 1.0

    source_k = sc.kelly_fraction_for_probs(1.9090909, 0.70, 0.0)
    cal_k = sc.kelly_fraction_for_probs(
        1.9090909, resolved.calibrated_probability or 0.0, 0.0
    )
    assert cal_k < source_k
    assert resolved.calibration_multiplier == pytest.approx(cal_k / source_k, abs=1e-6)


def test_pushes_excluded_from_binary_fit_but_push_retained_for_kelly():
    rows = _rows_70_predict_63_observe(n=40)
    rows += [_row(predicted=0.70, result="PUSH") for _ in range(20)]
    artifact = sc.fit_stake_calibration(rows, as_of=AS_OF, min_samples=10, prior_strength=0.0)
    assert artifact["eligible_samples"] == 40
    assert artifact["global"]["n"] == 40

    live = {**_row(predicted=0.63, push=0.10), "captured_at": "2026-07-21T12:00:00+00:00"}
    # Force calibrated ≈ source for multiplier path with push mass present
    resolved = sc.resolve_stake_calibration(artifact, live)
    assert resolved.source_probability == pytest.approx(0.63)
    assert resolved.calibrated_probability is not None
    # Kelly path must accept push mass without inflating
    mult = sc.calibration_multiplier(
        decimal_price=1.9090909,
        source_win_prob=0.55,
        calibrated_win_prob=0.50,
        push_prob=0.10,
    )
    assert 0.0 <= mult <= 1.0


def test_future_dated_and_wrong_source_column_rejected():
    rows = _rows_70_predict_63_observe(n=50)
    artifact = sc.fit_stake_calibration(
        rows,
        as_of=AS_OF,
        min_samples=10,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    ok, reason = sc.validate_stake_calibration_artifact(
        artifact, as_of=datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    assert not ok
    assert reason == "future_dated_artifact"

    ok2, reason2 = sc.validate_stake_calibration_artifact(
        artifact,
        as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
        expected_source_column="market_consensus_prob",
    )
    assert not ok2
    assert reason2 == "source_probability_column_mismatch"

    # Resolve stays neutral (shadow cold-start)
    live = {**_row(), "captured_at": "2026-07-01T12:00:00+00:00"}
    resolved = sc.resolve_stake_calibration(
        artifact, live, as_of=datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    assert resolved.calibration_multiplier == 1.0
    assert resolved.status == "neutral"


def test_insufficient_history_is_neutral():
    rows = _rows_70_predict_63_observe(n=5)
    artifact = sc.fit_stake_calibration(rows, as_of=AS_OF, min_samples=30)
    assert artifact["status"] == "insufficient_history"
    resolved = sc.resolve_stake_calibration(artifact, _row())
    assert resolved.calibration_multiplier == 1.0
    assert resolved.fallback_source == "insufficient_history"


def test_never_raise_calibrated_above_source():
    # Observed better than predicted → raw factor > 1, but staking caps at source.
    wins, losses = 90, 10  # 90% observe vs 70% predict
    rows = [_row(predicted=0.70, result="W") for _ in range(wins)]
    rows += [_row(predicted=0.70, result="L") for _ in range(losses)]
    artifact = sc.fit_stake_calibration(
        rows, as_of=AS_OF, min_samples=30, prior_strength=0.0
    )
    live = {**_row(predicted=0.70), "captured_at": "2026-07-21T12:00:00+00:00"}
    resolved = sc.resolve_stake_calibration(artifact, live)
    assert resolved.calibrated_probability is not None
    assert resolved.calibrated_probability <= 0.70 + 1e-12
    assert resolved.calibration_multiplier == pytest.approx(1.0)


def test_segment_shrinkage_and_parent_fallback():
    # Large global worse-than-predicted; one segment sparse.
    rows = _rows_70_predict_63_observe(n=80, market_type="PLAYER_PROP")
    rows += _rows_70_predict_63_observe(n=80, market_type="GAMELINE")
    # Sparse segment with only 5 samples of different type shouldn't form its own estimate
    sparse = _rows_70_predict_63_observe(n=5, market_type="TEAM_TOTAL")
    artifact = sc.fit_stake_calibration(
        rows + sparse, as_of=AS_OF, min_samples=30, prior_strength=30.0
    )
    assert "PLAYER_PROP" in artifact["dimensions"]["market_type"]
    assert "TEAM_TOTAL" not in artifact["dimensions"].get("market_type", {})
    live = {
        **_row(predicted=0.70, market_type="TEAM_TOTAL"),
        "captured_at": "2026-07-21T12:00:00+00:00",
    }
    resolved = sc.resolve_stake_calibration(artifact, live)
    # Sparse TEAM_TOTAL has no market_type estimate; falls back to parent/global.
    assert not resolved.fallback_source.startswith("segment:market_type=TEAM_TOTAL")
    assert resolved.calibration_multiplier < 1.0
    assert resolved.sample_count >= 30


def test_artifact_round_trip(tmp_path):
    rows = _rows_70_predict_63_observe(n=40)
    artifact = sc.fit_stake_calibration(
        rows, as_of=AS_OF, min_samples=10, generated_at="2026-07-20T00:00:00+00:00"
    )
    path = sc.write_stake_calibration_artifact(artifact, tmp_path / "stake_cal.json")
    loaded = sc.load_stake_calibration_artifact(path)
    assert loaded is not None
    assert loaded["artifact_version"] == artifact["artifact_version"]
    assert loaded["global"]["n"] == artifact["global"]["n"]


def test_uncertainty_small_sample_stronger_haircut():
    """Small-sample segment receives a stronger uncertainty haircut than large-sample."""
    large = _rows_70_predict_63_observe(n=200, market_type="PLAYER_PROP")
    small = _rows_70_predict_63_observe(n=40, market_type="GAMELINE")
    artifact = sc.fit_stake_calibration(
        large + small,
        as_of=AS_OF,
        min_samples=30,
        prior_strength=0.0,
        confidence_level=0.80,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    live_large = {
        **_row(predicted=0.70, market_type="PLAYER_PROP"),
        "captured_at": "2026-07-21T12:00:00+00:00",
    }
    live_small = {
        **_row(predicted=0.70, market_type="GAMELINE"),
        "captured_at": "2026-07-21T12:00:00+00:00",
    }
    cal_l = sc.resolve_stake_calibration(artifact, live_large)
    cal_s = sc.resolve_stake_calibration(artifact, live_small)
    unc_l = sc.resolve_probability_uncertainty(artifact, live_large, cal_l)
    unc_s = sc.resolve_probability_uncertainty(artifact, live_small, cal_s)
    assert unc_l.status == "active"
    assert unc_s.status == "active"
    assert unc_s.uncertainty_multiplier < unc_l.uncertainty_multiplier
    assert unc_l.lower_bound is not None and unc_s.lower_bound is not None
    assert unc_s.lower_bound < unc_l.lower_bound


def test_uncertainty_zero_when_calibrated_kelly_non_positive():
    # Calibrated probability below break-even → calibrated Kelly 0 → uncertainty mult 0
    rows = [_row(predicted=0.40, result="L") for _ in range(40)]
    rows += [_row(predicted=0.40, result="W") for _ in range(5)]
    artifact = sc.fit_stake_calibration(
        rows, as_of=AS_OF, min_samples=10, prior_strength=0.0
    )
    live = {
        **_row(predicted=0.40, decimal_price=2.0),
        "captured_at": "2026-07-21T12:00:00+00:00",
        "decimal_price": 2.0,
    }
    cal = sc.resolve_stake_calibration(artifact, live)
    unc = sc.resolve_probability_uncertainty(artifact, live, cal)
    # Even money needs >0.5 to have positive Kelly; 0.40 is non-positive.
    if sc.kelly_fraction_for_probs(2.0, cal.calibrated_probability or 0.0, 0.0) <= 0:
        assert unc.uncertainty_multiplier == 0.0


def test_wilson_lower_bound_monotonic_in_n():
    # Same rate, larger n → higher lower bound
    lb_small = sc.wilson_lower_bound(6.3, 10, confidence_level=0.80)
    lb_large = sc.wilson_lower_bound(63.0, 100, confidence_level=0.80)
    assert lb_large > lb_small
    assert 0.0 <= lb_small <= 1.0
