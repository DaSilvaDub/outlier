"""Hyperparameter tuning on seasons *outside* the scoring window (plan section 6.2).

Coordinate descent over small grids. Each candidate is a full walk-forward over the
tuning history (so the season chain and the daily refit are exercised exactly as in the
backtest) and is judged only on the games of the final tuning season. The scoring seasons
are never loaded here: the caller hands in the tuning games and nothing else.

Three independent objectives, each over only its own parameters:
ratings -> margin MSE, pace -> log-possession MSE, Elo -> home-win log loss.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

import numpy as np
import structlog

from outlier_nba.ratings.model import TeamParams
from outlier_nba.ratings.possessions import Dataset
from outlier_nba.track import metrics
from outlier_nba.track.backtest import norm_cdf, walk_forward

logger = structlog.get_logger("outlier_nba.tune")

GRIDS: dict[str, dict[str, Sequence[float]]] = {
    "ratings": {
        "lam": (1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0),
        "tau": (30.0, 45.0, 60.0, 90.0, 135.0, 180.0, 365.0, 1e6),
        "carry": (0.0, 0.3, 0.5, 0.7, 0.9),
        "lam_h": (10.0, 100.0, 1000.0),
    },
    "pace": {
        "p_lam": (0.5, 1.5, 5.0, 20.0, 80.0, 320.0),
        "p_tau": (15.0, 30.0, 45.0, 90.0, 180.0, 365.0, 1e6),
        "p_carry": (0.0, 0.3, 0.5, 0.7, 0.9),
    },
    "noise": {"noise_halflife": (500.0, 1000.0, 2000.0, 4000.0, 1e12)},
    "level": {"bias_halflife": (100.0, 200.0, 400.0, 800.0, 1600.0, 1e12)},
    "elo": {
        "elo_k": (8.0, 12.0, 16.0, 20.0, 28.0, 36.0),
        "elo_hfa": (40.0, 70.0, 100.0, 130.0),
        "elo_carry": (0.5, 0.65, 0.75, 0.9),
        "elo_mov_exp": (0.6, 0.8, 1.0),
    },
}
COMPONENTS = {
    "ratings": ("ratings",),
    "pace": ("pace",),
    "elo": ("elo",),
    "noise": ("ratings", "pace"),
    "level": ("ratings", "pace"),
}


def _objective(kind: str, ds: Dataset, params: TeamParams, target: np.ndarray) -> float:
    preds = walk_forward(ds, params, components=COMPONENTS[kind])
    ok = target & ~np.isnan(ds.poss)
    if kind == "ratings":
        err = (ds.hp - ds.ap)[ok] - preds["exp_margin"][ok]
        return float(np.mean(err**2))
    if kind == "pace":
        err = np.log(ds.poss48[ok]) - preds["log_poss"][ok]
        return float(np.mean(err**2))
    y = (ds.hp > ds.ap).astype(np.float64)
    if kind == "level":  # one-step-ahead error of the mean per-100 efficiency, overtime-agnostic
        ok = target & preds.ready & ~np.isnan(ds.poss)
        actual = 0.5 * (ds.hp + ds.ap) / ds.poss * 100.0
        predicted = 0.5 * (preds["e_home"] + preds["e_away"])
        return float(np.mean((actual[ok] - predicted[ok]) ** 2))
    if kind == "noise":  # calibration of the Gaussian-margin probability the tracker feeds
        ok = target & preds.ready
        p = norm_cdf(preds["exp_margin"][ok] / preds["sigma_margin"][ok])
        return metrics.log_loss(p, y[ok])
    elo_ok = target
    return metrics.log_loss(preds["elo_p"][elo_ok], y[elo_ok])


def tune_component(
    kind: str,
    ds: Dataset,
    start: TeamParams,
    *,
    tune_season: str,
    passes: int = 2,
    grids: Mapping[str, Sequence[float]] | None = None,
    objective: Callable[[str, Dataset, TeamParams, np.ndarray], float] = _objective,
) -> tuple[TeamParams, list[dict[str, float | str]]]:
    """Return the tuned parameters and the trail of every candidate that was scored."""
    grid = grids or GRIDS[kind]
    target = np.array([s == tune_season for s in ds.season])
    best = start
    best_score = objective(kind, ds, best, target)
    trail: list[dict[str, float | str]] = [
        {"param": "start", "value": math.nan, "score": best_score}
    ]
    for _ in range(passes):
        improved = False
        for name, values in grid.items():
            for v in values:
                if getattr(best, name) == v:
                    continue
                cand = replace(best, **{name: v})
                sc = objective(kind, ds, cand, target)
                trail.append({"param": name, "value": float(v), "score": sc})
                if sc < best_score - 1e-12:
                    best, best_score, improved = cand, sc, True
        if not improved:
            break
    logger.info("tuned", kind=kind, score=best_score, params=best.to_dict())
    return best, trail


def tune_all(
    ds: Dataset, start: TeamParams, *, tune_season: str
) -> tuple[TeamParams, dict[str, list[dict[str, float | str]]]]:
    """Tune the three components in turn on ``ds`` (tuning seasons only)."""
    if any(s > tune_season for s in ds.season):
        raise ValueError(f"tuning data must end at {tune_season}; found later seasons")
    params, trails = start, {}
    for kind in ("ratings", "pace", "noise", "level", "elo"):
        params, trails[kind] = tune_component(kind, ds, params, tune_season=tune_season)
    return params, trails
