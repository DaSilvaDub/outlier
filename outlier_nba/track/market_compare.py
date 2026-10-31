"""Model versus historical closing lines, on the games that have one (plan section 6.6).

Only games present in the supplied closing lines are compared, and every figure carries its n.
The market probability is the de-vigged moneyline; margin and total comparisons use the closing
spread and total as the market's point forecasts. Where a blend of model and market is tested,
its weight is fitted on strictly earlier seasons and evaluated on the next one.

What this can and cannot say: the "close" is a single source of unknown book and time, so
beating it is evidence about the model, not a claim that a bet at a bookmaker would have won.
Nothing here accounts for the bookmaker's margin on a real bet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from outlier_nba.market.closing import MarketLine, market_probability
from outlier_nba.ratings.possessions import Dataset
from outlier_nba.track import metrics

FArr = NDArray[np.float64]
GRID = np.linspace(0.0, 1.0, 21)
MIN_FIT_N = 300


def _logit(p: FArr) -> FArr:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def _blend(w: float, model: FArr, market: FArr) -> FArr:
    return 1.0 / (1.0 + np.exp(-(w * _logit(model) + (1 - w) * _logit(market))))


def _summ(p: FArr, y: FArr) -> dict[str, float]:
    s = metrics.summarize(p, y)
    return {k: s[k] for k in ("n", "brier", "log_loss", "ece")}


def _gain(market: FArr, model: FArr, y: FArr, days: Sequence[Any]) -> dict[str, Any]:
    by_game = metrics.paired_log_loss_diff(market, model, y)
    by_day = metrics.clustered_log_loss_diff(market, model, y, list(days))
    return {"mean": by_game["mean"], "ci_game": by_game["ci"], "ci_day": by_day["ci"]}


def market_report(
    ds: Dataset,
    lines: Mapping[str, MarketLine],
    models: Mapping[str, FArr],
    sim_margin: FArr,
    sim_total: FArr,
    scored: Sequence[str],
) -> dict[str, Any]:
    ids = np.array([g.game_id for g in ds.games])
    season = np.array(ds.season)
    have = np.array([i in lines for i in ids])
    ok = have & np.isin(season, list(scored))
    for p in models.values():
        ok &= np.isfinite(p)
    ok &= np.isfinite(sim_margin) & np.isfinite(sim_total)
    if not ok.any():
        return {"available": False, "n": 0, "note": "no scored game has a closing line"}
    idx = np.flatnonzero(ok)
    y = (ds.hp > ds.ap).astype(np.float64)[idx]
    ln = [lines[str(ids[i])] for i in idx]
    mkt = np.array([m.p_home_ml for m in ln])
    mult = np.array(
        [market_probability(m.ml_home, m.ml_away, "multiplicative") or np.nan for m in ln]
    )
    days = [m.day.isoformat() for m in ln]
    sea = season[idx]
    out: dict[str, Any] = {"available": True, "n": int(len(idx)), "games_by_season": {}}
    for s in scored:
        out["games_by_season"][s] = int((sea == s).sum())

    out["win_probability"] = {
        "market_shin": _summ(mkt, y),
        "market_multiplicative": _summ(mult, y),
        **{name: _summ(p[idx], y) for name, p in models.items()},
    }
    neutral = np.array([m.neutral for m in ln])
    out["by_venue"] = {
        name: {
            "n": int(mask.sum()),
            "market": metrics.log_loss(mkt[mask], y[mask]),
            **{k: metrics.log_loss(models[k][idx][mask], y[mask]) for k in models},
        }
        for name, mask in (("ordinary", ~neutral), ("neutral", neutral))
        if mask.any()
    }
    out["gain_over_market"] = {name: _gain(mkt, p[idx], y, days) for name, p in models.items()}
    out["by_season"] = {
        s: {
            "n": int((sea == s).sum()),
            "market": metrics.log_loss(mkt[sea == s], y[sea == s]),
            **{name: metrics.log_loss(p[idx][sea == s], y[sea == s]) for name, p in models.items()},
        }
        for s in scored
        if (sea == s).any()
    }

    actual_margin = (ds.hp - ds.ap)[idx]
    actual_total = (ds.hp + ds.ap)[idx]
    has_spread = np.array([m.spread_home is not None for m in ln])
    has_total = np.array([m.total_close is not None for m in ln])
    mk_margin = np.array([-m.spread_home if m.spread_home is not None else np.nan for m in ln])
    mk_total = np.array([m.total_close if m.total_close is not None else np.nan for m in ln])
    out["margin"] = {
        "n": int(has_spread.sum()),
        "market_mae": metrics.mae(mk_margin[has_spread], actual_margin[has_spread]),
        "model_mae": metrics.mae(sim_margin[idx][has_spread], actual_margin[has_spread]),
        "market_bias": float(np.mean(mk_margin[has_spread] - actual_margin[has_spread])),
        "model_bias": float(np.mean(sim_margin[idx][has_spread] - actual_margin[has_spread])),
    }
    out["total"] = {
        "n": int(has_total.sum()),
        "market_mae": metrics.mae(mk_total[has_total], actual_total[has_total]),
        "model_mae": metrics.mae(sim_total[idx][has_total], actual_total[has_total]),
        "market_bias": float(np.mean(mk_total[has_total] - actual_total[has_total])),
        "model_bias": float(np.mean(sim_total[idx][has_total] - actual_total[has_total])),
    }

    # blend of the simulation with the market, weight fitted on earlier seasons only
    sim_p = models["sim"][idx]
    blended = np.full(len(idx), np.nan)
    weights: dict[str, float | None] = {}
    for s in scored:
        earlier = np.isin(sea, [t for t in scored if t < s])
        if earlier.sum() < MIN_FIT_N:
            weights[s] = None
            continue
        losses = [
            metrics.log_loss(_blend(w, sim_p[earlier], mkt[earlier]), y[earlier]) for w in GRID
        ]
        w_best = float(GRID[int(np.argmin(losses))])
        weights[s] = w_best
        cur = sea == s
        blended[cur] = _blend(w_best, sim_p[cur], mkt[cur])
    judged = np.isfinite(blended)
    out["blend_with_market"] = {"weights": weights, "n": int(judged.sum())}
    if judged.any():
        out["blend_with_market"].update(
            {
                "market_log_loss": metrics.log_loss(mkt[judged], y[judged]),
                "blend_log_loss": metrics.log_loss(blended[judged], y[judged]),
                "gain": _gain(
                    mkt[judged], blended[judged], y[judged], [d for d, j in zip(days, judged) if j]
                ),
            }
        )
    return out
