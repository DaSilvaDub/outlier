"""Promotion evidence from settled shadow outcomes (plan M14).

Reads ``nba_model_eval`` (first pre-tip model forecast joined to the first pre-tip market
capture, its settlement and its verified CLV) and turns it into the :class:`Evidence` the
promotion evaluator decides on, one per market family.

Conventions, all chosen to be conservative:

* a push or void is not an observation; probabilities are the two-way, push-excluded ones
  (the same basis as a de-vigged two-way price);
* the two sides of one quoted line are one observation (they sum to 1 and settle oppositely),
  so only one side of each ``(event, market, line)`` is counted;
* anything that cannot be computed is ``None``, which the evaluator treats as a failure;
* CLV is judged only on the side the model would back (model probability above the market's)
  and only against a verified close.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from outlier_nba.run_context import try_parse_utc
from outlier_nba.track import metrics
from outlier_nba.track.promotion import MAX_MODEL_WEIGHT, Evidence

MIN_BUCKET_N = 100  # plan M14: min_bucket_n_for_gap_test
MIN_PAIRED_N = 30
ET_OFFSET = timedelta(hours=-5)  # distinct-date bucketing only; a fixed offset is enough
WEIGHT_GRID = np.linspace(0.0, MAX_MODEL_WEIGHT, 26)


@dataclass(frozen=True)
class Obs:
    key: tuple[str, str, str]
    day: str
    model: float
    market: float
    won: float
    clv_prob: float | None


def _load(conn: sqlite3.Connection) -> dict[str, list[Obs]]:
    conn.row_factory = sqlite3.Row
    have = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'nba_model_eval'").fetchone()
    if not have:
        return {}
    rows = conn.execute(
        """
        SELECT family, event_id, market_id, line, selection, event_starts_at, model_prob,
               market_consensus_prob, win_loss_push, close_status, clv_prob
        FROM nba_model_eval
        WHERE win_loss_push IN ('W', 'L') AND model_prob IS NOT NULL
          AND market_consensus_prob IS NOT NULL AND market_consensus_prob != ''
        ORDER BY event_id, market_id, line, selection
        """
    ).fetchall()
    seen: set[tuple[str, str, str]] = set()
    out: dict[str, list[Obs]] = defaultdict(list)
    for r in rows:
        key = (r["event_id"], r["market_id"], str(r["line"]))
        if key in seen:  # the other side of a line you already counted
            continue
        seen.add(key)
        tip = try_parse_utc(r["event_starts_at"])
        day = (tip + ET_OFFSET).date().isoformat() if tip else ""
        verified = r["close_status"] == "verified" and r["clv_prob"] not in (None, "")
        out[r["family"]].append(
            Obs(
                key=key,
                day=day,
                model=float(r["model_prob"]),
                market=float(r["market_consensus_prob"]),
                won=1.0 if r["win_loss_push"] == "W" else 0.0,
                clv_prob=float(r["clv_prob"]) if verified else None,
            )
        )
    return out


def calibration_gap(model: np.ndarray, won: np.ndarray) -> float | None:
    """Largest |predicted - observed| over closed buckets holding at least MIN_BUCKET_N."""
    gaps = [
        abs(row["mean_pred"] - row["observed"])
        for row in metrics.reliability(model, won, buckets=10)
        if row["n"] >= MIN_BUCKET_N
    ]
    return max(gaps) if gaps else None


def beats_market(model: np.ndarray, market: np.ndarray, won: np.ndarray) -> bool | None:
    """True only if the model's log loss is lower with a 95% interval clear of zero."""
    if len(won) < MIN_PAIRED_N:
        return None
    diff = metrics.paired_log_loss_diff(market, model, won)
    return bool(diff["ci"][0] > 0.0)


def fit_weight(model: np.ndarray, market: np.ndarray, won: np.ndarray) -> float:
    """Blend weight on the model (capped) that minimises log loss on this evidence."""
    if len(won) < MIN_PAIRED_N:
        return 0.0

    def logit(p: np.ndarray) -> np.ndarray:
        q = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(q / (1 - q))

    best_w, best = 0.0, float("inf")
    for w in WEIGHT_GRID:
        p = 1.0 / (1.0 + np.exp(-(w * logit(model) + (1 - w) * logit(market))))
        loss = metrics.log_loss(p, won)
        if loss < best - 1e-12:
            best_w, best = float(w), loss
    return best_w


def family_evidence(obs: Sequence[Obs]) -> Evidence:
    model = np.array([o.model for o in obs])
    market = np.array([o.market for o in obs])
    won = np.array([o.won for o in obs])
    backed = [o.clv_prob for o in obs if o.clv_prob is not None and o.model > o.market]
    return Evidence(
        n_settled=len(obs),
        distinct_dates=len({o.day for o in obs if o.day}),
        calibration_gap=calibration_gap(model, won),
        oos_logloss_beats_market=beats_market(model, market, won),
        median_clv=float(statistics.median(backed)) if backed else None,
        fitted_weight=fit_weight(model, market, won),
    )


def build_evidence(db_path: Path) -> dict[str, Evidence]:
    """One :class:`Evidence` per family that has any settled model forecast."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        by_family = _load(conn)
    finally:
        conn.close()
    return {family: family_evidence(obs) for family, obs in by_family.items() if obs}


def describe(evidence: dict[str, Evidence]) -> dict[str, Any]:
    return {family: ev.__dict__ for family, ev in sorted(evidence.items())}
