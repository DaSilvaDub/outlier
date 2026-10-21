"""Backtest metrics: Brier, log loss, closed reliability buckets, ECE, Wilson, MAE."""

from __future__ import annotations

import math

import numpy as np
import pytest

from outlier_nba.track import metrics


def test_brier_and_log_loss_known_values() -> None:
    p = np.array([0.8, 0.2])
    y = np.array([1, 0])
    assert metrics.brier(p, y) == pytest.approx(0.04)
    assert metrics.log_loss(p, y) == pytest.approx(-math.log(0.8))


def test_log_loss_clips_certainty_instead_of_returning_inf() -> None:
    assert math.isfinite(metrics.log_loss(np.array([1.0, 0.0]), np.array([0, 1])))


def test_wilson_interval_contains_the_rate_and_widens_for_small_n() -> None:
    lo, hi = metrics.wilson(60, 100)
    assert lo < 0.6 < hi
    lo2, hi2 = metrics.wilson(6, 10)
    assert (hi2 - lo2) > (hi - lo)
    assert metrics.wilson(0, 0) == (0.0, 1.0)


def test_reliability_buckets_are_closed_with_no_gaps() -> None:
    edges = metrics.bucket_edges(10)
    assert edges[0] == 0.0 and edges[-1] == 1.0
    # the NCAA bug dropped values sitting exactly on or just under an interior edge
    probs = np.array([0.0, 0.05, 0.1, 0.849, 0.85, 0.8999999, 0.9, 0.9999, 1.0])
    ys = np.zeros_like(probs)
    rows = metrics.reliability(probs, ys, buckets=10)
    assert sum(r["n"] for r in rows) == len(probs)  # every prediction lands in one bucket
    assert rows[0]["lo"] == 0.0 and rows[-1]["hi"] == 1.0
    for a, b in zip(rows, rows[1:]):
        assert a["hi"] == b["lo"]  # contiguous


def test_ece_is_zero_for_perfectly_calibrated_buckets() -> None:
    probs = np.array([0.25] * 4 + [0.75] * 4)
    ys = np.array([1, 0, 0, 0, 1, 1, 1, 0])
    assert metrics.ece(probs, ys, buckets=10) == pytest.approx(0.0)


def test_ece_detects_miscalibration() -> None:
    probs = np.full(100, 0.9)
    ys = np.zeros(100)
    assert metrics.ece(probs, ys, buckets=10) == pytest.approx(0.9)


def test_summary_reports_n_and_confidence_intervals() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.2, 0.8, 500)
    y = (rng.uniform(size=500) < p).astype(float)
    s = metrics.summarize(p, y)
    assert s["n"] == 500
    assert s["brier_ci"][0] < s["brier"] < s["brier_ci"][1]
    assert s["log_loss_ci"][0] < s["log_loss"] < s["log_loss_ci"][1]


def test_paired_difference_ci_excludes_zero_for_a_clearly_better_model() -> None:
    rng = np.random.default_rng(1)
    p_true = rng.uniform(0.1, 0.9, 2000)
    y = (rng.uniform(size=2000) < p_true).astype(float)
    good, bad = p_true, np.full(2000, 0.5)
    d = metrics.paired_log_loss_diff(bad, good, y)  # positive = second is better
    assert d["mean"] > 0 and d["ci"][0] > 0


def test_mae() -> None:
    assert metrics.mae(np.array([1.0, 3.0]), np.array([2.0, 1.0])) == pytest.approx(1.5)
