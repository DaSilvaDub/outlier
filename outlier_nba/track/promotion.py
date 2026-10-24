"""Promotion evaluator (plan M14), team families only.

``config/nba_promotion.json`` holds, per market family, a status (``shadow`` or
``promoted``) and the model blend weight. Everything that prices reads it through
:func:`status` / :func:`model_weight`, and only :func:`evaluate` writes it. The file
carries a hash of its own content, so a hand edit makes every reader raise
:class:`PromotionTamperedError`: there is no way to switch a family on by editing JSON.

The hash is *tamper-evident, not tamper-proof*: anyone who can write the file can also
recompute it. It catches accidental and casual edits (and makes a hand edit show up in review
as a mismatched hash); the real controls are code review of this module and of any change to
the config, and the fail-closed default. Do not treat it as authentication.

A family is promoted only when *every* key passes on supplied evidence, and a key whose
evidence is unknown (``None``) counts as a failure. In particular, with no closing-line
comparison ``oos_logloss_beats_market`` is unknown, so nothing can be promoted on model
skill alone. A later evaluation that fails demotes the family back to shadow.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from outlier_scrapers import paths

logger = structlog.get_logger("outlier_nba.promotion")

DEFAULT_PATH = paths.PROJECT_ROOT / "config" / "nba_promotion.json"
TEAM_FAMILIES = (
    "team.ML",
    "team.SPREAD",
    "team.TOTAL",
    "team.TEAM_TOTAL",
    "team.ALT_SPREAD",
    "team.ALT_TOTAL",
    "team.ALT_TEAM_TOTAL",
)
MAX_MODEL_WEIGHT = 0.25
THRESHOLDS = {
    "min_settled_outcomes": 1500,
    "min_distinct_dates": 45,
    "max_abs_calibration_gap": 0.03,
    "require_oos_logloss_beat_market": True,
    "require_median_clv_non_negative": True,
}


class PromotionTamperedError(RuntimeError):
    """The promotion file is missing, unreadable or does not match its own hash."""


@dataclass(frozen=True)
class Evidence:
    n_settled: int | None = None
    distinct_dates: int | None = None
    calibration_gap: float | None = None
    oos_logloss_beats_market: bool | None = None
    median_clv: float | None = None
    fitted_weight: float | None = None


def _hash(cfg: Mapping[str, Any]) -> str:
    body = {k: v for k, v in cfg.items() if k != "config_hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write(cfg: dict[str, Any], path: Path) -> None:
    cfg = {**cfg, "config_hash": _hash(cfg)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path) -> dict[str, Any]:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PromotionTamperedError(f"cannot read promotion config {path}: {exc}") from exc
    if not isinstance(cfg, dict) or cfg.get("config_hash") != _hash(cfg):
        raise PromotionTamperedError(
            f"{path} does not match its hash; only the evaluator writes it"
        )
    return cfg


def initialize(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    """Write the all-shadow starting file (the evaluator is the only author)."""
    cfg: dict[str, Any] = {
        "schema": 1,
        "thresholds": dict(THRESHOLDS),
        "max_model_weight": MAX_MODEL_WEIGHT,
        "families": {
            fam: {"status": "shadow", "model_weight": 0.0, "last_evidence": None}
            for fam in TEAM_FAMILIES
        },
    }
    _write(cfg, Path(path))
    return _load(Path(path))


def _family(cfg: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    fams = cfg["families"]
    if family not in fams:
        raise KeyError(f"unknown market family {family!r}")
    return fams[family]  # type: ignore[no-any-return]


def status(family: str, path: Path = DEFAULT_PATH) -> str:
    return str(_family(_load(Path(path)), family)["status"])


def model_weight(family: str, path: Path = DEFAULT_PATH) -> float:
    """Blend weight on the model; 0.0 unless the family is promoted."""
    fam = _family(_load(Path(path)), family)
    return float(fam["model_weight"]) if fam["status"] == "promoted" else 0.0


def _failures(ev: Evidence, th: Mapping[str, Any]) -> list[str]:
    bad: list[str] = []
    if ev.n_settled is None or ev.n_settled < th["min_settled_outcomes"]:
        bad.append("n_settled")
    if ev.distinct_dates is None or ev.distinct_dates < th["min_distinct_dates"]:
        bad.append("distinct_dates")
    if ev.calibration_gap is None or abs(ev.calibration_gap) > th["max_abs_calibration_gap"]:
        bad.append("calibration_gap")
    if th["require_oos_logloss_beat_market"] and ev.oos_logloss_beats_market is not True:
        bad.append("oos_logloss_vs_market")
    if th["require_median_clv_non_negative"] and (ev.median_clv is None or ev.median_clv < 0):
        bad.append("median_clv")
    return bad


def evaluate(
    evidence: Mapping[str, Evidence], path: Path = DEFAULT_PATH
) -> dict[str, dict[str, Any]]:
    """Re-decide each family named in ``evidence``; returns the new decision per family."""
    cfg = _load(Path(path))
    cap = float(cfg["max_model_weight"])
    decisions: dict[str, dict[str, Any]] = {}
    for family, ev in evidence.items():
        fam = _family(cfg, family)
        failed = _failures(ev, cfg["thresholds"])
        promoted = not failed
        weight = min(max(ev.fitted_weight or 0.0, 0.0), cap) if promoted else 0.0
        cfg["families"][family] = {
            "status": "promoted" if promoted else "shadow",
            "model_weight": weight,
            "last_evidence": {k: v for k, v in ev.__dict__.items()},
            "failed_keys": failed,
        }
        decisions[family] = {"from": fam["status"], **cfg["families"][family]}
        logger.info("promotion_decision", family=family, status=cfg["families"][family]["status"],
                    failed=failed)  # fmt: skip
    _write(cfg, Path(path))
    return decisions
