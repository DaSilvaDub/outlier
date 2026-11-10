"""Promotion evidence from settled shadow outcomes (plan M14).

Reads ``nba_model_eval`` (the first pre-tip model forecast of each outcome, joined to the
market snapshot that was current when the forecast was made, its settlement and its verified
CLV) and turns it into the :class:`Evidence` the promotion evaluator decides on, per family.

Conventions, all chosen to be conservative:

* a push or void is not an observation; probabilities are the two-way, push-excluded ones;
* the two sides of one quoted line are one observation for calibration and log loss (they
  sum to 1 and settle oppositely);
* CLV is judged on the side the model would *back* (model probability above the market's),
  chosen before any de-duplication, and only against a verified close;
* observations from one game are not independent, so confidence intervals resample whole
  games (and, separately, whole dates) and promotion needs a minimum number of games;
* the blend weight is fitted on the earlier part of the record and must beat the market on
  the later part before it is reported;
* anything that cannot be computed is ``None``, which the evaluator treats as a failure.
"""

from __future__ import annotations

import shutil
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from outlier_nba.run_context import try_parse_utc
from outlier_nba.track import metrics, model_ledger
from outlier_nba.track.clv import VERIFIED, VERIFIED_WINDOW
from outlier_nba.track.promotion import MAX_MODEL_WEIGHT, Evidence

MIN_BUCKET_N = 100  # plan M14: min_bucket_n_for_gap_test
MIN_PAIRED_N = 30
MIN_CLUSTERS = 10  # a resampling interval over fewer dates (or games) is not an interval
TRAIN_FRACTION = 0.6  # earlier share of the record (by date) used to fit the blend weight
ET_OFFSET = timedelta(hours=-5)  # distinct-date bucketing only; a fixed offset is enough
WEIGHT_GRID = np.linspace(0.0, MAX_MODEL_WEIGHT, 26)


@dataclass(frozen=True)
class Obs:
    """One quoted line. ``model``/``market``/``won`` describe one side (for calibration and
    log loss); ``backed`` and ``clv_prob`` describe the side the model favours."""

    event: str
    key: tuple[str, str, str]
    day: str
    model: float
    market: float
    won: float
    backed: bool = False
    clv_prob: float | None = None


EXCLUSION_REASONS = (
    "no_model_prob",
    "no_market_prob",
    "key_unverifiable",
    "key_mismatch",
    "unsettled",
    "push_or_void",
    "clv_no_verified_close",
    "clv_outside_window",
    "clv_value_missing",
)
ROW_REASONS = EXCLUSION_REASONS[:6]  # drop the whole row; the rest only drop its CLV


@dataclass
class Exclusions:
    """Rows (or CLV observations) left out of the evidence, by reason. Never imputed."""

    counts: dict[str, int]

    @classmethod
    def empty(cls) -> Exclusions:
        return cls({r: 0 for r in EXCLUSION_REASONS})

    def add(self, reason: str) -> None:
        self.counts[reason] += 1

    @property
    def rows_dropped(self) -> int:
        return sum(self.counts[r] for r in ROW_REASONS)


@dataclass(frozen=True)
class FamilyReport:
    evidence: Evidence
    exclusions: Exclusions
    logloss_model: float
    logloss_market: float


def _same_line(a: Any, b: Any, moneyline: bool = False) -> bool:
    """Lines match numerically (``221.5`` == ``221.50``); both blank matches. A moneyline has
    no line (the snapshot stores a ``0`` placeholder), so only there is blank equal to ``0``."""
    if moneyline:
        a, b = (None if x in (None, "", 0, "0", "0.0") else x for x in (a, b))
    blank_a, blank_b = a in (None, ""), b in (None, "")
    if blank_a or blank_b:
        return blank_a and blank_b
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def _row_reason(r: sqlite3.Row) -> str | None:
    """Why a joined row may not enter the evidence, or None. Checked in a fixed order."""
    if r["model_prob"] is None:
        return "no_model_prob"
    if r["model_market_id"] in (None, ""):
        return "key_unverifiable"  # recorded before keys were stored: cannot prove the match
    if str(r["model_market_id"]) != str(r["market_id"]) or not _same_line(
        r["model_line"], r["line"], moneyline=r["family"] == "team.ML"
    ):
        return "key_mismatch"
    if r["model_market_prob"] is None:
        return "no_market_prob"
    if r["win_loss_push"] in (None, ""):
        return "unsettled"
    if r["win_loss_push"] not in ("W", "L"):
        return "push_or_void"
    return None


def _clv_value(r: sqlite3.Row, excl: Exclusions) -> float | None:
    """CLV of a backed row: only a verified close inside the 60-minute policy, else None."""
    if r["close_status"] != VERIFIED:
        excl.add("clv_no_verified_close")
        return None
    minutes = r["clv_minutes_before_tip"]
    limit = VERIFIED_WINDOW.total_seconds() / 60.0
    if minutes in (None, "") or not 0.0 <= float(minutes) <= limit:
        excl.add("clv_outside_window")
        return None
    if r["clv_prob"] in (None, ""):
        excl.add("clv_value_missing")
        return None
    return float(r["clv_prob"])


def _load_report(conn: sqlite3.Connection) -> tuple[dict[str, list[Obs]], dict[str, Exclusions]]:
    conn.row_factory = sqlite3.Row
    have = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'nba_model_eval'").fetchone()
    if not have:
        return {}, {}
    rows = conn.execute(
        """
        SELECT family, event_id, market_id, line, selection, event_starts_at, model_prob,
               model_market_prob, win_loss_push, close_status, clv_prob, model_market_id,
               model_line, clv_minutes_before_tip
        FROM nba_model_eval
        ORDER BY event_id, market_id, line, selection
        """
    ).fetchall()
    excl: dict[str, Exclusions] = defaultdict(Exclusions.empty)
    sides: dict[tuple[str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        reason = _row_reason(r)
        if reason is not None:
            excl[r["family"]].add(reason)
            continue
        sides[(r["family"], r["event_id"], r["market_id"], str(r["line"]))].append(r)
    out: dict[str, list[Obs]] = defaultdict(list)
    for (family, event_id, market_id, line), group in sides.items():
        first = group[0]  # either side serves calibration and log loss
        favoured = next((r for r in group if r["model_prob"] > r["model_market_prob"]), None)
        clv = None if favoured is None else _clv_value(favoured, excl[family])
        tip = try_parse_utc(first["event_starts_at"])
        out[family].append(
            Obs(
                event=event_id,
                key=(event_id, market_id, line),
                day=(tip + ET_OFFSET).date().isoformat() if tip else "",
                model=float(first["model_prob"]),
                market=float(first["model_market_prob"]),
                won=1.0 if first["win_loss_push"] == "W" else 0.0,
                backed=favoured is not None,
                clv_prob=clv,
            )
        )
    return out, excl


def _load(conn: sqlite3.Connection) -> dict[str, list[Obs]]:
    return _load_report(conn)[0]


def calibration_gap(model: np.ndarray, won: np.ndarray) -> float | None:
    """Largest |predicted - observed| over closed buckets holding at least MIN_BUCKET_N."""
    gaps = [
        abs(row["mean_pred"] - row["observed"])
        for row in metrics.reliability(model, won, buckets=10)
        if row["n"] >= MIN_BUCKET_N
    ]
    return max(gaps) if gaps else None


def beats_market(obs: Sequence[Obs]) -> bool | None:
    """True only if the log-loss gain over the market is clear of zero at the 95% level when
    resampling whole games *and* when resampling whole dates (correlated lines stay together).
    """
    if len(obs) < MIN_PAIRED_N:
        return None
    if len({o.event for o in obs}) < MIN_PAIRED_N or len({o.day for o in obs}) < MIN_CLUSTERS:
        return None  # too few independent clusters to resample
    model = np.array([o.model for o in obs])
    market = np.array([o.market for o in obs])
    won = np.array([o.won for o in obs])
    by_game = metrics.clustered_log_loss_diff(market, model, won, [o.event for o in obs])
    by_day = metrics.clustered_log_loss_diff(market, model, won, [o.day for o in obs])
    return bool(by_game["ci"][0] > 0.0 and by_day["ci"][0] > 0.0)


def _blend_loss(w: float, model: np.ndarray, market: np.ndarray, won: np.ndarray) -> float:
    def logit(p: np.ndarray) -> np.ndarray:
        q = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(q / (1 - q))

    p = 1.0 / (1.0 + np.exp(-(w * logit(model) + (1 - w) * logit(market))))
    return metrics.log_loss(p, won)


def fit_weight(obs: Sequence[Obs]) -> tuple[float, bool]:
    """``(weight, validated)``: fit on the earlier dates, confirmed on the later ones.

    The weight is reported only if, on the untouched later dates, blending it in beats the
    market alone; otherwise it is 0.0.
    """
    days = sorted({o.day for o in obs})
    if len(days) < 2:
        return 0.0, False
    cut = days[min(max(1, int(len(days) * TRAIN_FRACTION)), len(days) - 1)]
    train = [o for o in obs if o.day < cut]
    valid = [o for o in obs if o.day >= cut]
    if len(train) < MIN_PAIRED_N or len(valid) < MIN_PAIRED_N:
        return 0.0, False

    def arrays(rows: Sequence[Obs]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.array([o.model for o in rows]),
            np.array([o.market for o in rows]),
            np.array([o.won for o in rows]),
        )

    m_t, k_t, w_t = arrays(train)
    best_w, best = 0.0, float("inf")
    for w in WEIGHT_GRID:
        loss = _blend_loss(float(w), m_t, k_t, w_t)
        if loss < best - 1e-12:
            best_w, best = float(w), loss
    if best_w == 0.0:
        return 0.0, False
    m_v, k_v, w_v = arrays(valid)
    ok = _blend_loss(best_w, m_v, k_v, w_v) < _blend_loss(0.0, m_v, k_v, w_v)
    return (best_w, True) if ok else (0.0, False)


def family_evidence(obs: Sequence[Obs]) -> Evidence:
    model = np.array([o.model for o in obs])
    won = np.array([o.won for o in obs])
    backed = [o for o in obs if o.backed]
    verified = [o for o in backed if o.clv_prob is not None]
    weight, _validated = fit_weight(obs)
    return Evidence(
        n_settled=len(obs),
        distinct_dates=len({o.day for o in obs if o.day}),
        n_games=len({o.event for o in obs}),
        calibration_gap=calibration_gap(model, won),
        oos_logloss_beats_market=beats_market(obs),
        median_clv=(
            float(statistics.median(o.clv_prob for o in verified if o.clv_prob is not None))
            if verified
            else None
        ),
        clv_n=len(verified),
        clv_dates=len({o.day for o in verified if o.day}),
        clv_coverage=len(verified) / len(backed) if backed else None,
        fitted_weight=weight,
    )


def _local_copy(db_path: Path, workdir: Path) -> Path:
    """Copy the ledger (and any WAL sidecars) to local temp: the original is never touched,
    and the view can be refreshed on the copy without writing a synced file."""
    dest = workdir / "ledger.sqlite3"
    shutil.copy2(db_path, dest)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{db_path}{suffix}")
        if side.exists():
            shutil.copy2(side, Path(f"{dest}{suffix}"))
    return dest


def _logloss(rows: Sequence[Obs]) -> tuple[float, float]:
    won = np.array([o.won for o in rows])
    return (
        metrics.log_loss(np.array([o.model for o in rows]), won),
        metrics.log_loss(np.array([o.market for o in rows]), won),
    )


def build_report(db_path: Path) -> dict[str, FamilyReport]:
    """Evidence, exclusion counts and log losses per family, from a throwaway local copy.

    The copy is brought to the current schema first (key columns, current view definition), so
    a ledger written by an older build is read under today's key checks. ``{}`` if the ledger
    has no model/market join at all.
    """
    with tempfile.TemporaryDirectory(prefix="nba_gate_") as tmp:
        local = _local_copy(Path(db_path), Path(tmp))
        conn = sqlite3.connect(local)
        try:
            model_ledger.ensure_model_schema(conn)
            by_family, excl = _load_report(conn)
        finally:
            conn.close()
    out: dict[str, FamilyReport] = {}
    for family in sorted(set(by_family) | set(excl)):
        obs = by_family.get(family, [])
        if obs:
            ll_model, ll_market = _logloss(obs)
        else:
            ll_model = ll_market = float("nan")
        out[family] = FamilyReport(
            evidence=family_evidence(obs) if obs else Evidence(n_settled=0),
            exclusions=excl.get(family, Exclusions.empty()),
            logloss_model=ll_model,
            logloss_market=ll_market,
        )
    return out


def build_evidence(db_path: Path) -> dict[str, Evidence]:
    """One :class:`Evidence` per family that has any settled model forecast."""
    return {f: r.evidence for f, r in build_report(db_path).items() if r.evidence.n_settled}


def describe(evidence: dict[str, Evidence]) -> dict[str, Any]:
    return {family: ev.__dict__ for family, ev in sorted(evidence.items())}
