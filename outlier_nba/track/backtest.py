"""Walk-forward backtest of the team model (plan section 6, Phase 2 step 10).

Every game is predicted from a daily refit that sees only games on earlier Eastern
calendar days. Hyperparameters come from seasons *before* the scoring window; the
simulator's noise estimates come from the model's own earlier out-of-sample predictions;
the ensemble weight for a season is fit on earlier seasons' out-of-sample predictions only.

Baselines: Elo alone, ridge alone (Gaussian on the ridge margin), and a constant
home-court rate. "Market" appears in the report only where closing lines are supplied,
and its sample size is printed beside it; with none supplied the report says so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog
from numpy.typing import NDArray

from outlier_nba.ratings.elo import EloParams, EloTable
from outlier_nba.ratings.model import NoiseTracker, TeamModel, TeamParams, predict
from outlier_nba.ratings.possessions import Dataset
from outlier_nba.sim.engine import SimParams, simulate
from outlier_nba.track import metrics

logger = structlog.get_logger("outlier_nba.backtest")

FArr = NDArray[np.float64]
FIELDS = (
    "e_home", "e_away", "log_poss", "sigma_p", "sigma_e", "rho", "ot_ratio",
    "sigma_margin", "sigma_total", "exp_margin", "exp_total", "elo_p",
)  # fmt: skip
MIN_FIT_GAMES = 150  # a first-ever season has no prior, so its earliest fits are not trusted
TOTAL_BASELINE_WINDOW = 1000
POOL_GRID = np.linspace(0.0, 1.0, 21)
_erf = np.frompyfunc(math.erf, 1, 1)


def norm_cdf(x: FArr) -> FArr:
    scaled = np.asarray(x, dtype=np.float64) / math.sqrt(2.0)
    return 0.5 * (1.0 + np.asarray(_erf(scaled), dtype=np.float64))


def logit(p: FArr) -> FArr:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1.0 - p))


def expit(z: FArr) -> FArr:
    return 1.0 / (1.0 + np.exp(-z))


@dataclass
class Predictions:
    f: dict[str, FArr]
    ready: NDArray[np.bool_]  # simulator inputs are out-of-sample and complete
    noise: NoiseTracker  # state after the last game
    elo: EloTable | None = None  # state after the last game

    def __getitem__(self, key: str) -> FArr:
        return self.f[key]


def walk_forward(
    ds: Dataset, params: TeamParams, *, components: Sequence[str] = ("ratings", "pace", "elo")
) -> Predictions:
    n = len(ds)
    out: dict[str, FArr] = {k: np.full(n, np.nan) for k in FIELDS}
    ready = np.zeros(n, dtype=bool)
    use_r, use_p, use_e = "ratings" in components, "pace" in components, "elo" in components
    model = TeamModel(ds, params, use_ratings=use_r, use_pace=use_p)
    elo = EloTable(EloParams(params.elo_k, params.elo_hfa, params.elo_carry, params.elo_mov_exp))
    tracker = NoiseTracker(halflife=params.noise_halflife)
    track_noise = use_r and use_p
    starts = np.flatnonzero(np.r_[True, ds.day[1:] != ds.day[:-1]])
    ends = np.r_[starts[1:], n]
    for s, e in zip(starts, ends):
        day, season = int(ds.day[s]), ds.season[s]
        asof = model.fit_asof(season, day) if (use_r or use_p) else None
        trusted = asof is not None and (
            model.has_prior(season) or asof.rating.n_games >= MIN_FIT_GAMES
        )
        sig_p = tracker.sigma_p if tracker.ready else 0.0
        day_preds = {}
        for i in range(s, e):
            h, a = int(ds.home[i]), int(ds.away[i])
            neutral, playoff = bool(ds.neutral[i]), bool(ds.stype[i] == 3)
            if asof is not None:
                pr = predict(asof, h, a, neutral=neutral, playoff=playoff, sigma_p=sig_p)
                day_preds[i] = pr
                out["e_home"][i], out["e_away"][i] = pr.e_home, pr.e_away
                out["log_poss"][i], out["exp_margin"][i] = pr.log_poss, pr.exp_margin
                out["exp_total"][i] = pr.exp_total
            if use_e:
                out["elo_p"][i] = elo.prob_home(h, a, neutral=neutral, season=season)
            if track_noise and trusted and tracker.ready:
                out["sigma_p"][i], out["sigma_e"][i] = tracker.sigma_p, tracker.sigma_e
                out["rho"][i], out["ot_ratio"][i] = tracker.rho, tracker.ot_ratio
                out["sigma_margin"][i], out["sigma_total"][i] = (
                    tracker.sigma_margin,
                    tracker.sigma_total,
                )
                ready[i] = bool(np.isfinite(tracker.ot_ratio))
        for i in range(s, e):  # results become known only after the whole day is predicted
            if use_e:
                elo.update(
                    int(ds.home[i]), int(ds.away[i]), ds.hp[i], ds.ap[i],
                    neutral=bool(ds.neutral[i]), season=season,
                )  # fmt: skip
            if track_noise and trusted and i in day_preds and not np.isnan(ds.poss[i]):
                pr = day_preds[i]
                tracker.update(
                    e_h=ds.hp[i] / ds.poss[i] * 100.0 - pr.e_home,
                    e_a=ds.ap[i] / ds.poss[i] * 100.0 - pr.e_away,
                    pace_resid=math.log(ds.poss48[i]) - pr.log_poss,
                    exp_margin=pr.exp_margin,
                    m_res=(ds.hp[i] - ds.ap[i]) - pr.exp_margin,
                    t_res=(ds.hp[i] + ds.ap[i]) - pr.exp_total,
                    ot=bool(ds.ot[i] > 0),
                )
    return Predictions(out, ready, tracker, elo if use_e else None)


# ----------------------------------------------------------------------------- simulation


def run_sim(
    preds: Predictions, select: NDArray[np.bool_], *, n_draws: int, seed: int, chunk: int = 150
) -> dict[str, FArr]:
    """Simulate the selected games; unselected / not-ready games stay NaN."""
    n = len(select)
    out: dict[str, FArr] = {k: np.full(n, np.nan) for k in ("p_home", "margin", "total", "p_ot")}
    idx = np.flatnonzero(select & preds.ready)
    rng = np.random.default_rng(seed)
    for lo in range(0, len(idx), chunk):
        sel = idx[lo : lo + chunk]
        params = SimParams(
            log_poss=preds["log_poss"][sel], sigma_p=preds["sigma_p"][sel],
            e_home=preds["e_home"][sel], e_away=preds["e_away"][sel],
            sigma_e=preds["sigma_e"][sel], rho=preds["rho"][sel],
            ot_ratio=preds["ot_ratio"][sel],
        )  # fmt: skip
        d = simulate(params, n_draws, rng)
        out["p_home"][sel] = (d.margin > 0).mean(axis=1)
        out["margin"][sel] = d.margin.mean(axis=1)
        out["total"][sel] = d.total.mean(axis=1)
        out["p_ot"][sel] = (d.ot > 0).mean(axis=1)
    return out


# ------------------------------------------------------------------------------- scoring


def fit_pool_weight(p_a: FArr, p_b: FArr, y: FArr) -> float:
    """Weight on ``p_a`` in a logit pool, by log loss on a 0.05 grid."""
    best, best_w = math.inf, 0.5
    for w in POOL_GRID:
        loss = metrics.log_loss(expit(w * logit(p_a) + (1 - w) * logit(p_b)), y)
        if loss < best:
            best, best_w = loss, float(w)
    return best_w


def _prior_means(ds: Dataset, values: FArr, include: NDArray[np.bool_], window: int | None):
    """Mean of ``values[include]`` over games on strictly earlier days (NaN when none)."""
    order_val = np.where(include, values, 0.0)
    cum = np.r_[0.0, np.cumsum(order_val)]
    cnt = np.r_[0, np.cumsum(include.astype(np.int64))]
    starts = np.searchsorted(ds.day, ds.day, side="left")  # first index of each game's day
    total = cum[starts]
    count = cnt[starts].astype(np.float64)
    if window is not None:
        lo = np.maximum(starts - window, 0)
        total = cum[starts] - cum[lo]
        count = (cnt[starts] - cnt[lo]).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def pool_weights(
    seasons: Sequence[str], season_of: NDArray[Any], p_sim: FArr, p_elo: FArr, y: FArr,
    *, first_pool: str, scored: Sequence[str], min_n: int = 300,
) -> dict[str, float]:  # fmt: skip
    """Per-scored-season weight on the simulation, fit on strictly earlier seasons only."""
    out: dict[str, float] = {}
    for s in scored:
        earlier = [t for t in seasons if first_pool <= t < s]
        m = np.isin(season_of, earlier) & np.isfinite(p_sim) & np.isfinite(p_elo)
        out[s] = fit_pool_weight(p_sim[m], p_elo[m], y[m]) if m.sum() >= min_n else 0.5
    return out


def score(
    ds: Dataset,
    preds: Predictions,
    sim: Mapping[str, FArr],
    *,
    scored_seasons: Sequence[str],
    first_pool_season: str,
    market: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    season_arr = np.array(ds.season)
    y = (ds.hp > ds.ap).astype(np.float64)
    nonstress = ~ds.stress
    const_p = _prior_means(ds, y, nonstress, None)
    const_margin = _prior_means(ds, ds.hp - ds.ap, nonstress, None)
    const_total = _prior_means(ds, ds.hp + ds.ap, np.ones(len(ds), bool), TOTAL_BASELINE_WINDOW)
    p_sim, p_elo = sim["p_home"], preds["elo_p"]
    with np.errstate(invalid="ignore", divide="ignore"):
        p_ridge = norm_cdf(preds["exp_margin"] / preds["sigma_margin"])
    seasons = sorted(set(ds.season))
    weights = pool_weights(
        seasons, season_arr, p_sim, p_elo, y, first_pool=first_pool_season, scored=scored_seasons
    )
    w_vec = np.array([weights.get(s, np.nan) for s in ds.season])
    p_pool = expit(w_vec * logit(p_sim) + (1 - w_vec) * logit(p_elo))

    models = {"sim": p_sim, "elo": p_elo, "ridge": p_ridge, "const_home": const_p, "pool": p_pool}
    maes = {
        "margin": {"sim": sim["margin"], "ridge": preds["exp_margin"], "const": const_margin},
        "total": {"sim": sim["total"], "ridge": preds["exp_total"], "const": const_total},
    }
    actual = {"margin": ds.hp - ds.ap, "total": ds.hp + ds.ap}
    scored_mask = np.isin(season_arr, list(scored_seasons)) & preds.ready & np.isfinite(p_sim)
    groups = _groups(ds, season_arr, scored_mask, scored_seasons)
    report: dict[str, Any] = {
        "pool_weight_on_sim": weights,
        "groups": {name: _score_group(m, models, maes, actual, y, preds, sim, ds)
                   for name, m in groups.items()},
    }  # fmt: skip
    report["ot"] = _ot_report(ds, preds, sim, scored_mask, season_arr, scored_seasons)
    report["market"] = market_comparison(ds, p_pool, market or {}, y)
    return report


def _groups(
    ds: Dataset, season_arr: NDArray[Any], base: NDArray[np.bool_], scored: Sequence[str]
) -> dict[str, NDArray[np.bool_]]:
    out = {"all_scored": base, "excluding_stress_slices": base & ~np.isin(
        season_arr, ["2019-20", "2020-21"]
    )}  # fmt: skip
    for s in scored:
        out[f"season:{s}"] = base & (season_arr == s)
    out["regular_season"] = base & (ds.stype == 2)
    out["postseason"] = base & (ds.stype == 3)
    out["play_in"] = base & (ds.stype == 5)
    out["stress:2019-20_bubble_restart"] = base & (season_arr == "2019-20") & ds.stress
    out["stress:2020-21"] = base & (season_arr == "2020-21")
    return {k: v for k, v in out.items() if v.any()}


def _score_group(m, models, maes, actual, y, preds, sim, ds) -> dict[str, Any]:
    sel_all = m & np.all([np.isfinite(p) for p in models.values()], axis=0)
    res: dict[str, Any] = {"n": int(m.sum()), "n_common": int(sel_all.sum()), "models": {}}
    for name, p in models.items():
        s = metrics.summarize(p[sel_all], y[sel_all])
        s.pop("reliability")
        res["models"][name] = s
    res["reliability_pool"] = metrics.reliability(models["pool"][sel_all], y[sel_all])
    res["reliability_sim"] = metrics.reliability(models["sim"][sel_all], y[sel_all])
    res["paired_log_loss_gain"] = {
        f"{base}->pool": metrics.paired_log_loss_diff(
            models[base][sel_all], models["pool"][sel_all], y[sel_all]
        )
        for base in ("elo", "ridge", "const_home", "sim")
    }
    res["mae"] = {}
    for kind, preds_by in maes.items():
        ok = m & np.all([np.isfinite(v) for v in preds_by.values()], axis=0)
        res["mae"][kind] = {
            "n": int(ok.sum()),
            **{k: metrics.mae(v[ok], actual[kind][ok]) for k, v in preds_by.items()},
            "sim_bias": float(np.mean(preds_by["sim"][ok] - actual[kind][ok]))
            if ok.any()
            else None,
        }
    res["mean_sigma_margin"] = float(np.nanmean(preds["sigma_margin"][m]))
    res["realised_margin_sd"] = float(np.std((ds.hp - ds.ap)[m] - preds["exp_margin"][m]))
    return res


def _ot_report(ds, preds, sim, mask, season_arr, scored) -> dict[str, Any]:
    out = {}
    for s in scored:
        m = mask & (season_arr == s)
        if m.any():
            out[s] = {
                "n": int(m.sum()),
                "actual_rate": float((ds.ot[m] > 0).mean()),
                "predicted_rate": float(np.nanmean(sim["p_ot"][m])),
            }
    return out


def market_comparison(
    ds: Dataset, p_model: FArr, closes: Mapping[str, float], y: FArr
) -> dict[str, Any]:
    """Model vs a vig-free closing home-win probability, on games that have one."""
    if not closes:
        return {"available": False, "n": 0, "note": "no closing lines supplied; nothing compared"}
    ids = np.array([g.game_id for g in ds.games])
    have = np.array([i in closes for i in ids]) & np.isfinite(p_model)
    if not have.any():
        return {"available": False, "n": 0, "note": "closing lines matched no scored game"}
    p_mkt = np.array([closes[i] for i in ids[have]])
    return {
        "available": True,
        "n": int(have.sum()),
        "market": {
            k: v for k, v in metrics.summarize(p_mkt, y[have]).items() if k != "reliability"
        },
        "model": {
            k: v for k, v in metrics.summarize(p_model[have], y[have]).items() if k != "reliability"
        },
        "paired_log_loss_gain_vs_market": metrics.paired_log_loss_diff(
            p_mkt, p_model[have], y[have]
        ),
    }


def run_backtest(
    games: Sequence[Any],
    *,
    first_scored: str,
    last_scored: str,
    tune_season: str,
    n_draws: int = 4000,
    seed: int = 17,
    start_params: TeamParams | None = None,
    tune_params: bool = True,
    market: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Tune on seasons up to ``tune_season``, walk forward over everything, score the window."""
    from outlier_nba.ratings.possessions import build_dataset, describe
    from outlier_nba.track.tune import tune_all

    seasons = sorted({g.season for g in games})
    scored = [s for s in seasons if first_scored <= s <= last_scored]
    if not scored or first_scored <= tune_season:
        raise ValueError("the scoring window must start after the tuning season")
    params = start_params or TeamParams()
    trails: dict[str, Any] = {}
    if tune_params:
        tune_ds = build_dataset([g for g in games if g.season <= tune_season])
        params, trails = tune_all(tune_ds, params, tune_season=tune_season)
    ds = build_dataset(list(games))
    preds = walk_forward(ds, params)
    season_arr = np.array(ds.season)
    sim = run_sim(preds, season_arr >= tune_season, n_draws=n_draws, seed=seed)
    report = score(
        ds, preds, sim, scored_seasons=scored, first_pool_season=tune_season, market=market
    )
    report.update(
        {
            "params": params.to_dict(),
            "tuned": tune_params,
            "tune_season": tune_season,
            "tuning_candidates": {k: len(v) for k, v in trails.items()},
            "scored_seasons": scored,
            "n_draws": n_draws,
            "data": describe(ds),
            "noise_final": preds.noise.to_dict(),
        }
    )
    return report
