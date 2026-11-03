"""Walk-forward backtest: no leakage, nested pooling, honest market reporting."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from outlier_nba.ratings.model import TeamParams
from outlier_nba.ratings.possessions import build_dataset
from outlier_nba.track import backtest as bt
from outlier_nba.track import tune
from tests.nba_synth import make_league, make_season

SEASONS = [
    ("2016-17", datetime(2016, 10, 25, tzinfo=UTC)),
    ("2017-18", datetime(2017, 10, 17, tzinfo=UTC)),
    ("2018-19", datetime(2018, 10, 16, tzinfo=UTC)),
]
PARAMS = TeamParams(lam=20.0, tau=1e6, carry=0.5)


def _games(gpt: int = 30):
    league = make_league(seed=21, spread=3.5)
    out = []
    for k, (label, start) in enumerate(SEASONS):
        out += make_season(league, label, start, games_per_team=gpt, seed=30 + k)
    return out


@pytest.fixture(scope="module")
def ds():
    return build_dataset(_games())


@pytest.fixture(scope="module")
def ds_shift():
    """The third season's scoring jumps 6% halfway through; ratings fitted on all of it lag."""
    from dataclasses import replace

    base = build_dataset(_games())
    last = np.array([s == "2018-19" for s in base.season])
    cut = int(np.median(base.day[last]))
    up = last & (base.day >= cut)
    return replace(
        base, hp=np.where(up, base.hp * 1.06, base.hp), ap=np.where(up, base.ap * 1.06, base.ap)
    )


@pytest.fixture(scope="module")
def preds(ds):
    return bt.walk_forward(ds, PARAMS)


def test_predictions_never_see_the_day_they_predict(ds, preds) -> None:
    cut = int(np.median(ds.day))
    poisoned = replace(
        ds, hp=np.where(ds.day >= cut, ds.hp + 400, ds.hp), ap=np.where(ds.day >= cut, 3.0, ds.ap)
    )
    again = bt.walk_forward(poisoned, PARAMS)
    before = ds.day <= cut  # the cut day itself is predicted before its own results exist
    for key in ("exp_margin", "exp_total", "elo_p", "e_home", "log_poss"):
        assert np.allclose(preds[key][before], again[key][before], equal_nan=True), key


def test_noise_tracker_recovers_the_generating_noise(preds) -> None:
    t = preds.noise
    assert t.ready
    assert t.sigma_e == pytest.approx(11.0, rel=0.12)  # synthetic per-100 noise
    assert t.sigma_p == pytest.approx(0.04, rel=0.2)
    assert abs(t.rho) < 0.1


def test_model_beats_the_constant_home_court_baseline(ds, preds) -> None:
    sim = bt.run_sim(preds, np.array([s != "2016-17" for s in ds.season]), n_draws=800, seed=3)
    rep = bt.score(ds, preds, sim, scored_seasons=["2018-19"], first_pool_season="2017-18")
    g = rep["groups"]["all_scored"]
    assert g["n"] > 400
    m = g["models"]
    assert m["sim"]["brier"] < m["const_home"]["brier"]
    assert m["elo"]["brier"] < m["const_home"]["brier"]
    # margin error: the rating model beats guessing the average margin
    assert g["mae"]["margin"]["sim"] < g["mae"]["margin"]["const"]
    assert g["mae"]["margin"]["n"] == g["n"]


def test_reliability_in_the_report_is_closed(ds, preds) -> None:
    sim = bt.run_sim(preds, np.array([s != "2016-17" for s in ds.season]), n_draws=500, seed=4)
    rep = bt.score(ds, preds, sim, scored_seasons=["2018-19"], first_pool_season="2017-18")
    g = rep["groups"]["all_scored"]
    rel = g["reliability_pool"]
    assert sum(r["n"] for r in rel) == g["n_common"]
    assert rel[0]["lo"] == 0.0 and rel[-1]["hi"] == 1.0


def test_pool_weight_for_a_season_ignores_that_season_and_later(ds, preds) -> None:
    sel = np.array([s != "2016-17" for s in ds.season])
    sim = bt.run_sim(preds, sel, n_draws=500, seed=5)
    y = (ds.hp > ds.ap).astype(float)
    season_arr = np.array(ds.season)
    base = bt.pool_weights(
        sorted(set(ds.season)), season_arr, sim["p_home"], preds["elo_p"], y,
        first_pool="2017-18", scored=["2018-19"], min_n=100,
    )  # fmt: skip
    flipped = np.where(season_arr == "2018-19", 1.0 - y, y)  # sabotage the scored season
    again = bt.pool_weights(
        sorted(set(ds.season)), season_arr, sim["p_home"], preds["elo_p"], flipped,
        first_pool="2017-18", scored=["2018-19"], min_n=100,
    )  # fmt: skip
    assert base == again


def test_market_comparison_is_explicit_when_absent_and_n_labelled_when_present(ds, preds) -> None:
    y = (ds.hp > ds.ap).astype(float)
    p = np.full(len(ds), 0.55)
    absent = bt.market_comparison(ds, p, {}, y)
    assert absent == {
        "available": False, "n": 0, "note": "no closing lines supplied; nothing compared"
    }  # fmt: skip
    ids = [g.game_id for g in ds.games][:50]
    present = bt.market_comparison(ds, p, {i: 0.5 for i in ids}, y)
    assert present["available"] and present["n"] == 50
    assert "paired_log_loss_gain_vs_market" in present


def test_not_ready_games_are_never_simulated(ds, preds) -> None:
    sim = bt.run_sim(preds, np.ones(len(ds), dtype=bool), n_draws=200, seed=6)
    assert np.isnan(sim["p_home"][~preds.ready]).all()


def test_tuning_refuses_data_from_the_scoring_window(ds) -> None:
    with pytest.raises(ValueError, match="tuning data must end"):
        tune.tune_all(ds, PARAMS, tune_season="2017-18")


def test_coordinate_descent_picks_the_better_grid_value(ds) -> None:
    tune_ds = build_dataset([g for g in _games() if g.season <= "2017-18"])
    best, trail = tune.tune_component(
        "elo", tune_ds, TeamParams(elo_k=2.0), tune_season="2017-18", passes=1,
        grids={"elo_k": (2.0, 16.0)},
    )  # fmt: skip
    assert best.elo_k == 16.0  # a tiny K barely learns; the larger step must win
    assert any(row["param"] == "elo_k" for row in trail)


def test_tune_all_runs_every_component_on_a_small_grid(monkeypatch) -> None:
    small = {
        "ratings": {"lam": (20.0, 200.0)},
        "pace": {"p_lam": (10.0, 100.0)},
        "noise": {"noise_halflife": (500.0, 1e12)},
        "level": {"bias_halflife": (100.0, 1e12)},
        "elo": {"elo_k": (4.0, 20.0)},
    }
    monkeypatch.setattr(tune, "GRIDS", small)
    tune_ds = build_dataset([g for g in _games() if g.season <= "2017-18"])
    best, trails = tune.tune_all(tune_ds, TeamParams(), tune_season="2017-18")
    assert set(trails) == {"ratings", "pace", "noise", "level", "elo"}
    assert best.lam in (20.0, 200.0) and best.p_lam in (10.0, 100.0) and best.elo_k in (4.0, 20.0)
    assert all(row["score"] > 0 for t in trails.values() for row in t)


def test_run_backtest_end_to_end_reports_params_data_and_no_market() -> None:
    report = bt.run_backtest(
        _games(),
        first_scored="2018-19",
        last_scored="2018-19",
        tune_season="2017-18",
        n_draws=400,
        start_params=PARAMS,
        tune_params=False,
    )
    assert report["scored_seasons"] == ["2018-19"] and report["tuned"] is False
    assert report["market"]["available"] is False
    assert report["data"]["games"] == len(_games())
    assert report["groups"]["all_scored"]["n"] > 300


def test_run_backtest_refuses_a_window_that_overlaps_tuning() -> None:
    with pytest.raises(ValueError, match="after the tuning season"):
        bt.run_backtest(
            _games(), first_scored="2017-18", last_scored="2018-19", tune_season="2017-18"
        )


def test_noise_tracker_level_bias_needs_history_and_follows_recent_residuals() -> None:
    from outlier_nba.ratings.model import MIN_BIAS_N, NoiseTracker

    t = NoiseTracker(bias_halflife=100.0)

    def feed(raw: float, n: int) -> None:
        for _ in range(n):
            t.update(1.0, 1.0, 0.01, 0.0, 1.0, 1.0, False, raw_eff_resid=raw)

    feed(1.0, MIN_BIAS_N - 1)
    assert t.level_bias == 0.0  # not enough history to trust
    feed(1.0, 50)
    assert t.level_bias == pytest.approx(1.0, abs=0.01)
    feed(-0.5, 600)  # the level moves: a short half-life follows it, an expanding mean would not
    assert t.level_bias == pytest.approx(-0.5, abs=0.05)


def test_level_correction_cuts_the_lag_behind_a_scoring_level_shift(ds_shift) -> None:
    import dataclasses

    params = dataclasses.replace(PARAMS, bias_halflife=150.0)
    off = bt.walk_forward(ds_shift, dataclasses.replace(params, bias_weight=0.0))
    on = bt.walk_forward(ds_shift, params)
    season = np.array([s == "2018-19" for s in ds_shift.season]) & on.ready
    late = season & (ds_shift.day >= np.percentile(ds_shift.day[season], 70))
    actual = (ds_shift.hp + ds_shift.ap)[late]
    bias_off = np.mean(off["exp_total"][late] - actual)
    bias_on = np.mean(on["exp_total"][late] - actual)
    assert bias_off < -3.0  # ratings fitted on pre- and post-shift games run well behind
    assert abs(bias_on) < 0.7 * abs(bias_off)
    assert (on["bias"][late] > 0).all()
    # the same amount is added to both teams, so who is favoured cannot change
    assert np.allclose(on["exp_margin"][season], off["exp_margin"][season], atol=1e-9)


def test_pace_component_is_not_counted_twice_in_the_simulated_total() -> None:
    """sigma_e/rho must describe the efficiency noise left after the pace term is removed."""
    from outlier_nba.ratings.model import NoiseTracker
    from outlier_nba.sim.engine import SimParams, simulate

    rng = np.random.default_rng(3)
    n, sp, beta, se, e_h, e_a = 6000, 0.05, -150.0, 11.0, 114.0, 112.0  # an exaggerated link
    z0, zh, za = (rng.standard_normal(n) for _ in range(3))
    eff_h = e_h + beta * sp * z0 + se * zh
    eff_a = e_a + beta * sp * z0 + se * za
    truth = 99.0 * np.exp(sp * z0) * (eff_h + eff_a) / 100.0

    tracker = NoiseTracker()
    for i in range(n):
        raw_h, raw_a = eff_h[i] - e_h, eff_a[i] - e_a
        tracker.update(
            raw_h, raw_a, sp * z0[i], 0.0, 1.0, 1.0, False, raw_eff_resid=0.5 * (raw_h + raw_a)
        )
    assert tracker.beta_pe == pytest.approx(beta, rel=0.1)
    assert tracker.sigma_e == pytest.approx(se, rel=0.05)  # not sqrt(se^2 + (beta*sp)^2) = 13.3

    def total_sd(sigma_p: float, sigma_e: float, rho: float, beta_pe: float) -> float:
        params = SimParams.single(
            log_poss=np.log(99.0), sigma_p=sigma_p, e_home=e_h, e_away=e_a, sigma_e=sigma_e,
            rho=rho, ot_ratio=1.0, beta_pe=beta_pe,
        )  # fmt: skip
        return float(simulate(params, 60000, np.random.default_rng(4)).total.std())

    # the simulation fed the tracker's estimates must spread like one fed the true parameters
    # (overtime points are in both, so only a double-counted pace term can separate them)
    assert total_sd(
        tracker.sigma_p, tracker.sigma_e, tracker.rho, tracker.beta_pe
    ) == pytest.approx(total_sd(sp, se, 0.0, beta), rel=0.03)
    assert truth.std() > 0  # the generating process itself has the spread being matched
