"""Apply calibration, uncertainty, correlation, and drawdown once (Track C4).

Produces pre-cap continuous units with full provenance. Hard-cap allocation
remains the responsibility of ``portfolio.allocate_portfolio_risk``. Learned
multipliers stay neutral when ``shadow_multipliers_neutral`` is true or when
artifacts fail validation (cold-start shadow behavior).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping

from outlier_scrapers.drawdown import (
    DrawdownState,
    resolve_drawdown_multiplier,
)
from outlier_scrapers.sizing import compute_full_kelly
from outlier_scrapers.stake_calibration import (
    CalibrationResult,
    UncertaintyResult,
    resolve_probability_uncertainty,
    resolve_stake_calibration,
)


def _float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _probability(value: Any) -> float | None:
    number = _float(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class LearnedStakeResult:
    source_probability: float | None
    calibrated_probability: float | None
    conservative_probability: float | None
    raw_kelly_units: float
    calibration_multiplier: float
    uncertainty_multiplier: float
    correlation_multiplier: float
    drawdown_multiplier: float
    pre_cap_units: float
    calibration_source: str
    uncertainty_source: str
    drawdown_source: str
    calibration_artifact_version: str
    uncertainty_artifact_version: str
    drawdown_tier: str
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _raw_kelly_units(
    *,
    decimal_price: float,
    win_prob: float,
    push_prob: float,
    kelly_fraction: float,
    unit_bankroll: float,
) -> float:
    p_lose = 1.0 - win_prob - push_prob
    if decimal_price <= 1.0 or push_prob < 0 or p_lose < 0 or (win_prob + p_lose) <= 0:
        return 0.0
    b = decimal_price - 1.0
    full = compute_full_kelly(b, win_prob, p_lose)
    if full <= 0:
        return 0.0
    return max(0.0, kelly_fraction * full * unit_bankroll)


def apply_learned_multipliers(
    row: Mapping[str, Any],
    *,
    calibration_artifact: Mapping[str, Any] | None = None,
    drawdown_state: DrawdownState | None = None,
    correlation_multiplier: float = 1.0,
    shadow_multipliers_neutral: bool = True,
    as_of: datetime | None = None,
    kelly_fraction: float = 0.25,
    unit_bankroll: float = 100.0,
    max_wager_units: float | None = None,
    source_probability_column: str | None = None,
) -> LearnedStakeResult:
    """Apply C1–C3 multipliers exactly once before hard caps.

    When ``shadow_multipliers_neutral`` is True, all learned multipliers are
    forced to 1.0 while still computing diagnostic calibrated/conservative
    probabilities when artifacts are valid (for shadow reporting).
    """
    price = _float(row.get("decimal_price"))
    push = _probability(row.get("push_prob"))
    if push is None:
        push = 0.0
    corr = _clamp01(float(correlation_multiplier))

    if price is None or price <= 1.0:
        return LearnedStakeResult(
            source_probability=None,
            calibrated_probability=None,
            conservative_probability=None,
            raw_kelly_units=0.0,
            calibration_multiplier=1.0,
            uncertainty_multiplier=1.0,
            correlation_multiplier=corr,
            drawdown_multiplier=1.0,
            pre_cap_units=0.0,
            calibration_source="missing_price",
            uncertainty_source="missing_price",
            drawdown_source="missing_price",
            calibration_artifact_version="",
            uncertainty_artifact_version="",
            drawdown_tier="neutral",
            status="ineligible",
            reason="missing_or_invalid_price",
        )

    cal: CalibrationResult = resolve_stake_calibration(
        calibration_artifact,
        row,
        decimal_price=price,
        as_of=as_of,
        source_probability_column=source_probability_column,
        neutral_on_failure=True,
    )
    unc: UncertaintyResult = resolve_probability_uncertainty(
        calibration_artifact,
        row,
        cal,
        decimal_price=price,
        neutral_on_failure=True,
    )
    dd_mult, dd_source = resolve_drawdown_multiplier(
        drawdown_state, as_of=as_of, neutral_on_failure=True
    )
    dd_tier = drawdown_state.tier if drawdown_state is not None else "neutral"

    source_p = cal.source_probability
    if source_p is None:
        # Fall back to common column names for raw Kelly baseline.
        for col in (
            source_probability_column,
            "final_blended_prob",
            "model_prob",
            "market_consensus_prob",
        ):
            if not col:
                continue
            source_p = _probability(row.get(col))
            if source_p is not None:
                break

    raw = 0.0
    if source_p is not None:
        raw = _raw_kelly_units(
            decimal_price=price,
            win_prob=source_p,
            push_prob=push,
            kelly_fraction=kelly_fraction,
            unit_bankroll=unit_bankroll,
        )

    cal_m = cal.calibration_multiplier
    unc_m = unc.uncertainty_multiplier
    if shadow_multipliers_neutral:
        cal_m = 1.0
        unc_m = 1.0
        dd_mult = 1.0
        status = "shadow_neutral"
        reason = "shadow_multipliers_neutral"
    else:
        status = "active"
        reason = None
        # Fail closed only when enforce and artifacts are required-but-missing:
        # cold-start still allows neutral multipliers via resolve_* flags.
        if cal.status != "active" and cal.fallback_source not in {
            "missing_artifact",
            "insufficient_history",
            "inactive",
        }:
            # Keep neutral cold-start; non-cold failures already set multiplier.
            pass

    pre = raw * cal_m * unc_m * corr * dd_mult
    pre = max(0.0, pre)
    if max_wager_units is not None:
        pre = min(pre, float(max_wager_units))

    if dd_mult <= 0.0 and not shadow_multipliers_neutral:
        reason = "drawdown_stop"
        status = "stand_down"

    return LearnedStakeResult(
        source_probability=source_p,
        calibrated_probability=cal.calibrated_probability,
        conservative_probability=unc.conservative_probability,
        raw_kelly_units=round(raw, 10),
        calibration_multiplier=round(cal_m, 10),
        uncertainty_multiplier=round(unc_m, 10),
        correlation_multiplier=round(corr, 10),
        drawdown_multiplier=round(dd_mult, 10),
        pre_cap_units=round(pre, 10),
        calibration_source=cal.fallback_source,
        uncertainty_source=unc.fallback_source,
        drawdown_source=dd_source,
        calibration_artifact_version=cal.artifact_version,
        uncertainty_artifact_version=unc.artifact_version,
        drawdown_tier=dd_tier,
        status=status,
        reason=reason,
    )
