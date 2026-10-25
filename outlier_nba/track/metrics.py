"""Backtest metrics (plan section 6): Brier, log loss, reliability, ECE, Wilson, MAE.

Reliability buckets are CLOSED: ``[0, 0.1), ..., [0.9, 1.0]`` -- every prediction in
``[0, 1]`` lands in exactly one bucket. The NCAA harness used half-open intervals that
dropped values sitting just under an interior edge (e.g. 0.8499 vs a 0.85 edge).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
EPS = 1e-12
Z95 = 1.959963984540054


def _f(a: Any) -> Array:
    return np.asarray(a, dtype=np.float64)


def brier(p: Any, y: Any) -> float:
    p, y = _f(p), _f(y)
    return float(np.mean((p - y) ** 2))


def _loss_terms(p: Array, y: Array) -> Array:
    p = np.clip(p, EPS, 1.0 - EPS)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def log_loss(p: Any, y: Any) -> float:
    return float(np.mean(_loss_terms(_f(p), _f(y))))


def mae(pred: Any, actual: Any) -> float:
    return float(np.mean(np.abs(_f(pred) - _f(actual))))


def wilson(successes: float, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def bucket_edges(buckets: int) -> Array:
    return np.asarray(np.linspace(0.0, 1.0, buckets + 1), dtype=np.float64)


def _bucket_index(p: Array, buckets: int) -> NDArray[np.int64]:
    # floor into [0, buckets - 1]; p == 1.0 belongs to the last bucket (closed on the right)
    idx = np.floor(np.clip(p, 0.0, 1.0) * buckets + 1e-12).astype(np.int64)
    return np.minimum(idx, buckets - 1)


def reliability(p: Any, y: Any, buckets: int = 10) -> list[dict[str, Any]]:
    p, y = _f(p), _f(y)
    idx = _bucket_index(p, buckets)
    edges = bucket_edges(buckets)
    rows: list[dict[str, Any]] = []
    for b in range(buckets):
        mask = idx == b
        n = int(mask.sum())
        wins = float(y[mask].sum())
        lo, hi = wilson(wins, n)
        rows.append(
            {
                "lo": float(edges[b]),
                "hi": float(edges[b + 1]),
                "n": n,
                "mean_pred": float(p[mask].mean()) if n else None,
                "observed": wins / n if n else None,
                "wilson": [lo, hi] if n else None,
            }
        )
    return rows


def ece(p: Any, y: Any, buckets: int = 10) -> float:
    p, y = _f(p), _f(y)
    total = len(p)
    if total == 0:
        return float("nan")
    out = 0.0
    for row in reliability(p, y, buckets):
        if row["n"]:
            out += row["n"] / total * abs(row["mean_pred"] - row["observed"])
    return float(out)


def _bootstrap_ci(terms: Array, reps: int = 400, seed: int = 7) -> tuple[float, float]:
    """Percentile CI of the mean of per-game terms (iid resampling of games)."""
    if len(terms) == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.array([terms[rng.integers(0, len(terms), len(terms))].mean() for _ in range(reps)])
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def summarize(p: Any, y: Any, buckets: int = 10) -> dict[str, Any]:
    p, y = _f(p), _f(y)
    n = len(p)
    brier_terms = (p - y) ** 2
    ll_terms = _loss_terms(p, y)
    return {
        "n": n,
        "brier": float(brier_terms.mean()) if n else float("nan"),
        "brier_ci": _bootstrap_ci(brier_terms),
        "log_loss": float(ll_terms.mean()) if n else float("nan"),
        "log_loss_ci": _bootstrap_ci(ll_terms),
        "ece": ece(p, y, buckets),
        "accuracy": float(np.mean((p > 0.5) == (y > 0.5))) if n else float("nan"),
        "home_win_rate": float(y.mean()) if n else float("nan"),
        "home_win_rate_wilson": wilson(float(y.sum()), n),
        "reliability": reliability(p, y, buckets),
    }


def paired_log_loss_diff(
    baseline: Any, candidate: Any, y: Any, *, reps: int = 400, seed: int = 11
) -> dict[str, Any]:
    """Per-game ``loss(baseline) - loss(candidate)``: positive means the candidate is better."""
    y = _f(y)
    diff = _loss_terms(_f(baseline), y) - _loss_terms(_f(candidate), y)
    return {"n": len(diff), "mean": float(diff.mean()), "ci": _bootstrap_ci(diff, reps, seed)}
