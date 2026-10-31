"""Team simulation: one set of draws prices margin, total and team totals."""

from __future__ import annotations

import numpy as np
import pytest

from outlier_nba.sim.engine import SimInputError, SimParams, simulate


def _params(**kw) -> SimParams:
    base = {
        "log_poss": np.log(99.0), "sigma_p": 0.04, "e_home": 115.0, "e_away": 113.0,
        "sigma_e": 11.0, "rho": 0.0, "ot_ratio": 1.0,
    }  # fmt: skip
    base.update(kw)
    return SimParams.single(**base)


def test_margin_and_total_come_from_the_same_draws() -> None:
    d = simulate(_params(), 4000, np.random.default_rng(0))
    assert np.array_equal(d.margin, d.home - d.away)
    assert np.array_equal(d.total, d.home + d.away)
    assert d.home.shape == (1, 4000)


def test_means_match_the_analytic_expectation() -> None:
    d = simulate(_params(ot_ratio=1.0), 60000, np.random.default_rng(1))
    # whole-game accounting: each overtime adds 5/48 of the regulation possessions
    poss = np.exp(np.log(99.0) + 0.5 * 0.04**2) * (1 + 5 / 48 * d.ot.mean())
    assert d.total.mean() == pytest.approx(poss * (115 + 113) / 100, abs=0.5)
    assert d.margin.mean() == pytest.approx(poss * 2 / 100, abs=0.4)


def test_possessions_are_positive_with_no_floor_applied() -> None:
    d = simulate(_params(sigma_p=0.2), 20000, np.random.default_rng(2))
    assert (d.poss > 0).all()
    assert d.poss.min() < 99.0 * 0.7  # the lower tail is allowed to be long (no 90/95 clamp)


def test_overtime_frequency_scales_with_the_ratio() -> None:
    rng = np.random.default_rng(3)
    base = simulate(_params(e_home=114.0, e_away=114.0, ot_ratio=1.0), 80000, rng)
    boosted = simulate(_params(e_home=114.0, e_away=114.0, ot_ratio=2.0), 80000, rng)
    r1, r2 = (base.ot > 0).mean(), (boosted.ot > 0).mean()
    assert r2 == pytest.approx(2 * r1, rel=0.2)
    assert 0.02 < r1 < 0.045  # a Gaussian margin with sd ~ 14 ties about 3% of the time


def test_overtime_games_end_decided() -> None:
    d = simulate(_params(e_home=114.0, e_away=114.0, ot_ratio=2.0), 30000, np.random.default_rng(4))
    assert (d.margin != 0).all()
    assert (d.ot >= 0).all() and d.ot.max() <= 5


def test_seeded_runs_are_reproducible() -> None:
    a = simulate(_params(), 500, np.random.default_rng(9))
    b = simulate(_params(), 500, np.random.default_rng(9))
    assert np.array_equal(a.home, b.home)


def test_correlated_noise_shrinks_margin_spread_and_widens_total_spread() -> None:
    rng = np.random.default_rng(5)
    low = simulate(_params(rho=0.0), 50000, rng)
    high = simulate(_params(rho=0.5), 50000, rng)
    assert high.margin.std() < low.margin.std()
    assert high.total.std() > low.total.std()


def test_mirrored_game_flips_the_margin() -> None:
    a = simulate(_params(e_home=117.0, e_away=111.0), 60000, np.random.default_rng(6))
    b = simulate(_params(e_home=111.0, e_away=117.0), 60000, np.random.default_rng(7))
    assert a.margin.mean() == pytest.approx(-b.margin.mean(), abs=0.5)


def test_many_games_in_one_call() -> None:
    p = SimParams(
        log_poss=np.full(3, np.log(99.0)), sigma_p=np.full(3, 0.04),
        e_home=np.array([110.0, 115.0, 120.0]), e_away=np.full(3, 113.0),
        sigma_e=np.full(3, 11.0), rho=np.zeros(3), ot_ratio=np.ones(3),
    )  # fmt: skip
    d = simulate(p, 3000, np.random.default_rng(8))
    assert d.home.shape == (3, 3000)
    means = d.margin.mean(axis=1)
    assert means[0] < means[1] < means[2]


@pytest.mark.parametrize(
    "bad",
    [{"sigma_e": 0.0}, {"sigma_e": float("nan")}, {"rho": 1.0}, {"e_home": float("inf")},
     {"sigma_p": -0.1}, {"ot_ratio": float("nan")}],
)  # fmt: skip
def test_invalid_inputs_fail_closed(bad) -> None:
    with pytest.raises(SimInputError):
        simulate(_params(**bad), 100, np.random.default_rng(0))


def test_tiny_draw_counts_are_refused() -> None:
    with pytest.raises(SimInputError):
        simulate(_params(), 10, np.random.default_rng(0))


def test_pace_efficiency_beta_narrows_the_total_without_moving_the_mean() -> None:
    base = SimParams.single(
        log_poss=np.log(99.0), sigma_p=0.05, e_home=114.0, e_away=112.0, sigma_e=11.0, rho=0.0,
        ot_ratio=1.0, beta_pe=0.0,
    )  # fmt: skip
    neg = SimParams.single(
        log_poss=np.log(99.0), sigma_p=0.05, e_home=114.0, e_away=112.0, sigma_e=11.0, rho=0.0,
        ot_ratio=1.0, beta_pe=-40.0,
    )  # fmt: skip
    rng = np.random.default_rng(12)
    a, b = simulate(base, 80000, rng), simulate(neg, 80000, rng)
    assert b.total.std() < a.total.std() - 0.5  # faster games are less efficient: less spread
    assert b.total.mean() == pytest.approx(a.total.mean(), abs=0.6)
    assert b.margin.std() == pytest.approx(a.margin.std(), rel=0.05)


def test_beta_defaults_to_none_and_must_be_finite() -> None:
    assert _params().beta_pe is None  # existing callers are unchanged
    with pytest.raises(SimInputError):
        simulate(_params(beta_pe=float("nan")), 100, np.random.default_rng(0))
