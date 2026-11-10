"""Read-only per-family promotion-gate status (plan M14).

Builds the evidence from the ledger exactly as ``promote`` does, then reports, for every team
family, how far it is from each gate key. It never writes the ledger or the promotion file and
cannot change a blend weight: only :func:`outlier_nba.track.promotion.evaluate` does that.

A family's verdict is ``PASS`` (no key fails), ``insufficient evidence`` (a sample-size key
fails, so quality cannot yet be judged) or ``FAIL`` (the sample is large enough and a quality
key still fails). An unknown (``None``) key counts as failed, as in the evaluator.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from outlier_nba.track import evidence as ev_mod
from outlier_nba.track import promotion

PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT = "insufficient evidence"
# sample-size keys; every other key judges quality
SAMPLE_KEYS = frozenset({"n_settled", "distinct_dates", "independent_games", "clv_sample"})


@dataclass
class FamilyGate:
    family: str
    verdict: str
    n_settled: int
    n_games: int
    distinct_dates: int
    logloss_model: float | None
    logloss_market: float | None
    oos_logloss_beats_market: bool | None
    calibration_gap: float | None
    median_clv: float | None
    clv_n: int
    failed_keys: list[str]
    blocking_quality_keys: list[str]
    exclusions: dict[str, int] = field(default_factory=dict)


def load_thresholds(config: Path | None = None) -> Mapping[str, Any]:
    """Thresholds from a valid promotion file, else the coded defaults. Never raises: an
    unreadable or tampered file must not change what the gate demands."""
    path = Path(config) if config else promotion.DEFAULT_PATH
    try:
        loaded = promotion._load(path)
    except promotion.PromotionTamperedError:
        return promotion.THRESHOLDS
    return {**promotion.THRESHOLDS, **loaded.get("thresholds", {})}


def _num(x: float) -> float | None:
    return None if math.isnan(x) else x


def _gate(
    family: str, report: ev_mod.FamilyReport | None, thresholds: Mapping[str, Any]
) -> FamilyGate:
    rep = report or ev_mod.FamilyReport(
        ev_mod.Evidence(n_settled=0), ev_mod.Exclusions.empty(), math.nan, math.nan
    )
    e = rep.evidence
    failed = promotion.failed_keys(e, thresholds)
    quality = [k for k in failed if k not in SAMPLE_KEYS]
    if not failed:
        verdict = PASS
    elif set(failed) & SAMPLE_KEYS:
        verdict = INSUFFICIENT
    else:
        verdict = FAIL
    return FamilyGate(
        family=family,
        verdict=verdict,
        n_settled=e.n_settled or 0,
        n_games=e.n_games or 0,
        distinct_dates=e.distinct_dates or 0,
        logloss_model=_num(rep.logloss_model),
        logloss_market=_num(rep.logloss_market),
        oos_logloss_beats_market=e.oos_logloss_beats_market,
        calibration_gap=e.calibration_gap,
        median_clv=e.median_clv,
        clv_n=e.clv_n or 0,
        failed_keys=failed,
        blocking_quality_keys=quality,
        exclusions=dict(rep.exclusions.counts),
    )


def build_gates(db_path: Path, config: Path | None = None) -> list[FamilyGate]:
    """One :class:`FamilyGate` per team family (families with no data report n_settled 0)."""
    thresholds = load_thresholds(config)
    reports = ev_mod.build_report(db_path)
    return [_gate(fam, reports.get(fam), thresholds) for fam in promotion.TEAM_FAMILIES]


def _fmt(x: float | None, spec: str = ".4f") -> str:
    return "n/a" if x is None else format(x, spec)


def format_gates(gates: Sequence[FamilyGate]) -> str:
    lines = [
        "NBA promotion gate (read-only; blend weights untouched)",
        "family              settled games dates  ll_model ll_market  calgap  med_clv clv_n  verdict",
    ]
    for g in gates:
        lines.append(
            f"{g.family:<19} {g.n_settled:>7} {g.n_games:>5} {g.distinct_dates:>5} "
            f"{_fmt(g.logloss_model):>9} {_fmt(g.logloss_market):>9} "
            f"{_fmt(g.calibration_gap, '.3f'):>7} {_fmt(g.median_clv, '+.4f'):>8} "
            f"{g.clv_n:>5}  {g.verdict}"
        )
        if g.failed_keys:
            lines.append(f"    blocked by: {', '.join(g.failed_keys)}")
        dropped = {k: v for k, v in g.exclusions.items() if v}
        if dropped:
            lines.append("    excluded:   " + ", ".join(f"{k}={v}" for k, v in dropped.items()))
    return "\n".join(lines)


def to_payload(gates: Sequence[FamilyGate]) -> dict[str, Any]:
    return {"write_enabled": False, "families": [asdict(g) for g in gates]}
