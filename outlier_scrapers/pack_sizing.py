"""Stake-sizing helpers for the pack writer.

Calls into ``portfolio.py`` / ``sizing.py`` / ``learned_multipliers.py`` for the
actual policy; this layer only wires row-level state to that policy and never
duplicates it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from outlier_scrapers import paths
from outlier_scrapers.pack_market import _to_float
from outlier_scrapers.schema import ValidationError
from outlier_scrapers.sizing import compute_sizing


def _apply_blended_sizing(row: dict[str, Any], blended_probability: Any) -> None:
    """Resize one row from the promoted blend, leaving every gate downstream."""

    probability = _to_float(blended_probability)
    decimal_price = _to_float(row.get("decimal_price"))
    push_prob = _to_float(row.get("push_prob"))
    if probability is None or decimal_price is None or push_prob is None:
        row["blend_sizing_source"] = "audit_only"
        return
    if row.get("recommended_units_pre_news") in ("", None):
        # Sizing was already withheld upstream (push-capable, proxy-only, EV
        # mismatch); a promoted blend must not revive a suppressed stake.
        row["blend_sizing_source"] = "audit_only"
        return
    sizing = compute_sizing(
        decimal_price=decimal_price, model_prob=probability, push_prob=push_prob
    )
    row["model_prob"] = probability
    row["model_prob_source"] = "blended_market_model"
    row["implied_prob"] = sizing.implied_prob
    row["edge_pct"] = sizing.edge_pct
    row["kelly_025_units"] = sizing.kelly_025_units
    row["max_units"] = sizing.max_units
    row["recommended_units_pre_news"] = sizing.recommended_units_pre_news
    row["blend_sizing_source"] = "promoted_blend"


def _apply_enforced_portfolio_units(row: dict[str, Any], allocated_units: Any) -> None:
    """Apply enforce-mode units without reviving a non-actionable recommendation."""

    units = _to_float(allocated_units)
    is_actionable = str(row.get("actionable") or "").lower() == "true"
    is_board_a = str(row.get("board") or "").upper() == "A"
    if is_actionable and is_board_a and units is not None and units > 0:
        row["recommended_units_pre_news"] = allocated_units
        return

    row["recommended_units_pre_news"] = ""
    if is_actionable:
        row["actionable"] = "false"
        if is_board_a:
            row["board"] = "A_FLAGGED"
        if row.get("_board") == "board_a":
            row["_board"] = "flagged"


def _load_learned_stake_runtime(policy: Any) -> dict[str, Any]:
    """Load policy-controlled calibration and drawdown inputs once per pack."""

    policy_path = paths.PROJECT_ROOT / "config" / "portfolio_risk.json"
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    calibration_policy = payload.get("calibration") or {}
    uncertainty_policy = payload.get("uncertainty") or {}
    drawdown_policy = payload.get("drawdown") or {}
    calibration_uncertainty_enabled = bool(calibration_policy.get("enabled")) and bool(
        uncertainty_policy.get("enabled")
    )
    drawdown_enabled = bool(drawdown_policy.get("enabled"))
    source_column = str(calibration_policy.get("source_probability_column") or "").strip()
    if calibration_uncertainty_enabled and source_column != "market_consensus_prob":
        raise ValidationError(
            "Active learned stake multipliers require policy calibration source "
            "market_consensus_prob."
        )

    def policy_path_value(raw: Any, default: Path) -> Path:
        if raw in (None, ""):
            return default
        configured = Path(str(raw))
        return configured if configured.is_absolute() else paths.PROJECT_ROOT / configured

    from outlier_scrapers import drawdown, stake_calibration

    calibration_path = policy_path_value(
        calibration_policy.get("artifact_path"), stake_calibration.DEFAULT_ARTIFACT_PATH
    )
    drawdown_path = policy_path_value(
        drawdown_policy.get("state_path"), drawdown.DEFAULT_STATE_PATH
    )
    return {
        "enabled": calibration_uncertainty_enabled or drawdown_enabled,
        "source_probability_column": source_column or "market_consensus_prob",
        "calibration_artifact": (
            stake_calibration.load_stake_calibration_artifact(calibration_path)
            if calibration_uncertainty_enabled
            else None
        ),
        "drawdown_state": (
            drawdown.load_drawdown_state(drawdown_path) if drawdown_enabled else None
        ),
        "shadow_multipliers_neutral": bool(policy.shadow_multipliers_neutral),
    }


def _apply_learned_stake_before_caps(
    projected: dict[str, Any],
    original: dict[str, Any],
    *,
    stream: str,
    policy: Any,
    runtime: Mapping[str, Any],
) -> None:
    """Apply one learned haircut without reviving or increasing a legacy stake."""

    legacy_units = _to_float(projected.get("units"))
    if legacy_units is None or legacy_units <= 0 or stream not in policy.streams_in_scope:
        projected["pre_cap_units"] = 0.0
        return

    from outlier_scrapers.learned_multipliers import apply_learned_multipliers

    result = apply_learned_multipliers(
        projected,
        calibration_artifact=runtime.get("calibration_artifact"),
        drawdown_state=runtime.get("drawdown_state"),
        shadow_multipliers_neutral=bool(runtime.get("shadow_multipliers_neutral", True)),
        as_of=datetime.now().astimezone(),
        max_wager_units=float(policy.max_wager_units),
        source_probability_column=str(
            runtime.get("source_probability_column") or "market_consensus_prob"
        ),
    )
    diagnostics = {
        "learned_source_probability": result.source_probability,
        "learned_calibrated_probability": result.calibrated_probability,
        "learned_conservative_probability": result.conservative_probability,
        "learned_raw_kelly_units": result.raw_kelly_units,
        "calibration_multiplier": result.calibration_multiplier,
        "uncertainty_multiplier": result.uncertainty_multiplier,
        "correlation_multiplier": result.correlation_multiplier,
        "drawdown_multiplier": result.drawdown_multiplier,
        "learned_pre_cap_units": result.pre_cap_units,
        "calibration_source": result.calibration_source,
        "uncertainty_source": result.uncertainty_source,
        "drawdown_source": result.drawdown_source,
        "calibration_artifact_version": result.calibration_artifact_version,
        "uncertainty_artifact_version": result.uncertainty_artifact_version,
        "drawdown_tier": result.drawdown_tier,
        "learned_multiplier_status": result.status,
        "learned_multiplier_reason": result.reason or "",
    }
    projected.update(diagnostics)
    original.update(diagnostics)

    active = bool(runtime.get("enabled")) and not bool(
        runtime.get("shadow_multipliers_neutral", True)
    )
    learned_legacy_ceiling = legacy_units * (
        result.calibration_multiplier
        * result.uncertainty_multiplier
        * result.correlation_multiplier
        * result.drawdown_multiplier
    )
    projected["pre_cap_units"] = (
        min(legacy_units, learned_legacy_ceiling, result.pre_cap_units)
        if active
        else legacy_units
    )
