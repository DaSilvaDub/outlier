"""Stake calibration and probability-uncertainty haircuts (Track C1/C2).

Ownership chain (shadow-safe until activation):

    market_consensus_prob + independent_model_prob
    → final_blended_prob                 # existing probability_blend layer
    → calibrated_probability             # C1
    → conservative_probability           # C2
    → continuous Kelly stake

Calibration never double-haircuts the blend layer: it only re-maps the source
probability that already drove the historical recommendation. Multipliers are
Kelly ratios clipped to [0, 1] and never raise stakes above raw Kelly.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from outlier_scrapers import paths, probability_blend
from outlier_scrapers.sizing import compute_full_kelly

SCHEMA_VERSION = 1
DEFAULT_ARTIFACT_PATH = paths.PROJECT_ROOT / "calibration" / "stake_calibration.json"
DEFAULT_SOURCE_PROBABILITY_COLUMN = "final_blended_prob"
DEFAULT_MIN_SAMPLES = 30
DEFAULT_PRIOR_STRENGTH = 30.0
DEFAULT_CONFIDENCE_LEVEL = 0.80
WILSON_METHOD = "wilson_one_sided"

DIMENSIONS = probability_blend.DIMENSIONS


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


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def conditional_non_push_prob(win_prob: float, push_prob: float) -> float | None:
    """Convert unconditional P(win) to P(win | not push)."""
    if not (0.0 <= win_prob <= 1.0) or not (0.0 <= push_prob < 1.0):
        return None
    conditional = win_prob / (1.0 - push_prob)
    if not (0.0 <= conditional <= 1.0):
        return None
    return conditional


def unconditional_from_conditional(conditional: float, push_prob: float) -> float | None:
    """Convert P(win | not push) back to unconditional P(win)."""
    if not (0.0 <= conditional <= 1.0) or not (0.0 <= push_prob < 1.0):
        return None
    return conditional * (1.0 - push_prob)


def kelly_fraction_for_probs(
    decimal_price: float,
    win_prob: float,
    push_prob: float = 0.0,
) -> float:
    """Full-Kelly fraction (0 when non-positive or invalid)."""
    if decimal_price <= 1.0:
        return 0.0
    p_lose = 1.0 - win_prob - push_prob
    if push_prob < 0.0 or win_prob < 0.0 or p_lose < 0.0 or (win_prob + p_lose) <= 0.0:
        return 0.0
    b = decimal_price - 1.0
    full = compute_full_kelly(b, win_prob, p_lose)
    return max(0.0, full)


def calibration_multiplier(
    *,
    decimal_price: float,
    source_win_prob: float,
    calibrated_win_prob: float,
    push_prob: float = 0.0,
) -> float:
    """Kelly(calibrated) / Kelly(source), clipped to [0, 1]."""
    source_k = kelly_fraction_for_probs(decimal_price, source_win_prob, push_prob)
    cal_k = kelly_fraction_for_probs(decimal_price, calibrated_win_prob, push_prob)
    if source_k <= 0.0:
        return 0.0 if cal_k <= 0.0 else 0.0
    return _clamp01(cal_k / source_k)


def uncertainty_multiplier(
    *,
    decimal_price: float,
    calibrated_win_prob: float,
    conservative_win_prob: float,
    push_prob: float = 0.0,
) -> float:
    """Kelly(conservative) / Kelly(calibrated), clipped to [0, 1]."""
    cal_k = kelly_fraction_for_probs(decimal_price, calibrated_win_prob, push_prob)
    if cal_k <= 0.0:
        return 0.0
    cons_k = kelly_fraction_for_probs(decimal_price, conservative_win_prob, push_prob)
    return _clamp01(cons_k / cal_k)


def wilson_lower_bound(
    wins: float,
    n: float,
    *,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> float:
    """One-sided Wilson lower confidence bound for a binomial proportion.

    Uses the standard normal quantile for a one-sided lower bound at the given
    confidence level (e.g. 0.80 → z ≈ 0.8416). Empty samples return 0.0.
    """
    if n <= 0:
        return 0.0
    if wins < 0 or wins > n:
        raise ValueError("wins must be in [0, n]")
    if not (0.5 < confidence_level < 1.0):
        raise ValueError("confidence_level must be in (0.5, 1.0)")
    # One-sided: Phi(z) = confidence_level
    # Approximate inverse normal via Beasley-Springer/Moro-quality rational for common levels,
    # but use erfinv-equivalent from math for exactness when available.
    z = _norm_ppf(confidence_level)
    phat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)
    return _clamp01((centre - margin) / denom)


def _norm_ppf(p: float) -> float:
    """Approximate standard-normal inverse CDF for p in (0, 1)."""
    # Acklam's rational approximation (sufficient for deterministic haircuts).
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        )
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    )


@dataclass(frozen=True)
class CalibrationResult:
    source_probability: float | None
    calibrated_probability: float | None
    calibrated_conditional: float | None
    calibration_multiplier: float
    sample_count: int
    predicted_mean: float | None
    observed_mean: float | None
    shrunk_mean: float | None
    fallback_source: str
    artifact_version: str
    source_probability_column: str
    segment: dict[str, str]
    status: str


@dataclass(frozen=True)
class UncertaintyResult:
    conservative_probability: float | None
    conservative_conditional: float | None
    uncertainty_multiplier: float
    confidence_level: float
    method: str
    sample_count: int
    lower_bound: float | None
    fallback_source: str
    artifact_version: str
    status: str


def _training_observation(
    row: Mapping[str, Any],
    *,
    source_probability_column: str,
    as_of: datetime | None,
) -> tuple[Mapping[str, Any], float, float] | None:
    """Return (row, conditional_predicted, outcome) or None if ineligible."""
    result = str(row.get("win_loss_push") or "").strip().upper()
    if result not in {"W", "L"}:
        return None  # exclude pushes from binary calibration
    source = _probability(row.get(source_probability_column))
    push = _probability(row.get("push_prob"))
    if source is None or push is None:
        return None
    conditional = conditional_non_push_prob(source, push)
    if conditional is None:
        return None
    hours = _float(row.get("hours_before_game"))
    if hours is None:
        hours = probability_blend.hours_before_game(
            row.get("captured_at") or row.get("as_of"),
            row.get("event_starts_at") or row.get("_event_starts_at"),
        )
    if hours is None or hours < 0:
        return None
    context = probability_blend.segment_context(row)
    if "UNKNOWN" in context.values():
        return None
    if as_of is not None:
        captured = _parse_timestamp(row.get("captured_at") or row.get("as_of"))
        if captured is None or captured > as_of:
            return None
        settled = _parse_timestamp(row.get("settled_at"))
        if settled is not None and settled > as_of:
            return None
    return row, conditional, 1.0 if result == "W" else 0.0


def _fit_group(outcomes: list[tuple[float, float]]) -> dict[str, Any]:
    n = len(outcomes)
    if n == 0:
        return {
            "n": 0,
            "wins": 0.0,
            "losses": 0.0,
            "predicted_mean": None,
            "observed_mean": None,
            "reliability_factor": 1.0,
        }
    wins = sum(actual for _, actual in outcomes)
    losses = n - wins
    predicted_mean = sum(pred for pred, _ in outcomes) / n
    observed_mean = wins / n
    if predicted_mean <= 1e-15:
        factor = 1.0
    else:
        factor = observed_mean / predicted_mean
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "predicted_mean": round(predicted_mean, 10),
        "observed_mean": round(observed_mean, 10),
        "reliability_factor": round(factor, 10),
    }


def _shrink_toward_global(
    local: Mapping[str, Any],
    global_fit: Mapping[str, Any],
    *,
    prior_strength: float,
) -> dict[str, Any]:
    n = int(local.get("n") or 0)
    g_obs = _float(global_fit.get("observed_mean"))
    g_pred = _float(global_fit.get("predicted_mean"))
    l_obs = _float(local.get("observed_mean"))
    l_pred = _float(local.get("predicted_mean"))
    if n <= 0 or g_obs is None or g_pred is None or l_obs is None or l_pred is None:
        return {
            **dict(local),
            "shrunk_observed_mean": global_fit.get("observed_mean"),
            "shrunk_predicted_mean": global_fit.get("predicted_mean"),
            "shrunk_reliability_factor": global_fit.get("reliability_factor", 1.0),
        }
    denom = n + prior_strength
    shrunk_obs = (n * l_obs + prior_strength * g_obs) / denom
    shrunk_pred = (n * l_pred + prior_strength * g_pred) / denom
    if shrunk_pred <= 1e-15:
        factor = 1.0
    else:
        factor = shrunk_obs / shrunk_pred
    return {
        **dict(local),
        "shrunk_observed_mean": round(shrunk_obs, 10),
        "shrunk_predicted_mean": round(shrunk_pred, 10),
        "shrunk_reliability_factor": round(factor, 10),
    }


def fit_stake_calibration(
    rows: Iterable[Mapping[str, Any]],
    *,
    as_of: datetime,
    source_probability_column: str = DEFAULT_SOURCE_PROBABILITY_COLUMN,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    policy_fingerprint: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Fit a versioned stake-calibration artifact from settled chronological rows.

    Training uses only W/L outcomes (pushes excluded from the binary fit) with
    known push probability retained for later Kelly recomputation. Segment
    estimates shrink toward the global parent.
    """
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")
    if prior_strength < 0:
        raise ValueError("prior_strength cannot be negative")
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = as_of.astimezone(timezone.utc)

    eligible: list[tuple[Mapping[str, Any], float, float]] = []
    for row in rows:
        obs = _training_observation(
            row, source_probability_column=source_probability_column, as_of=as_of
        )
        if obs is not None:
            eligible.append(obs)

    global_raw = _fit_group([(pred, actual) for _, pred, actual in eligible])
    active = int(global_raw["n"]) >= min_samples
    if not active:
        global_fit = {
            **global_raw,
            "shrunk_observed_mean": global_raw.get("observed_mean"),
            "shrunk_predicted_mean": global_raw.get("predicted_mean"),
            "shrunk_reliability_factor": 1.0,
        }
    else:
        global_fit = _shrink_toward_global(
            global_raw, global_raw, prior_strength=0.0
        )

    dimensions: dict[str, dict[str, dict[str, Any]]] = {}
    if active:
        for dimension in DIMENSIONS:
            groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
            for row, pred, actual in eligible:
                groups[probability_blend.segment_context(row)[dimension]].append(
                    (pred, actual)
                )
            fitted: dict[str, dict[str, Any]] = {}
            for value, pairs in sorted(groups.items()):
                if len(pairs) < min_samples:
                    continue
                local = _fit_group(pairs)
                fitted[value] = _shrink_toward_global(
                    local, global_fit, prior_strength=prior_strength
                )
            dimensions[dimension] = fitted

    trained_through = max(
        (str(row.get("captured_at") or row.get("settled_at") or "") for row, _, _ in eligible),
        default="",
    )
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "active" if active else "insufficient_history",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "training_cutoff": as_of.isoformat(),
        "trained_through": trained_through,
        "source_probability_column": source_probability_column,
        "objective": "reliability_shrinkage_kelly_ratio",
        "min_samples": min_samples,
        "prior_strength": prior_strength,
        "confidence_level": confidence_level,
        "uncertainty_method": WILSON_METHOD,
        "eligible_samples": len(eligible),
        "policy_fingerprint": policy_fingerprint,
        "global": global_fit,
        "dimensions": dimensions,
    }
    fingerprint_payload = {
        key: value for key, value in artifact.items() if key != "generated_at"
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    artifact["artifact_version"] = f"stake-cal-v1-{fingerprint}"
    artifact["artifact_fingerprint"] = fingerprint
    return artifact


def write_stake_calibration_artifact(artifact: Mapping[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(dict(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output_path)
    return output_path


def load_stake_calibration_artifact(
    path: Path = DEFAULT_ARTIFACT_PATH,
) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    return payload


def validate_stake_calibration_artifact(
    artifact: Mapping[str, Any] | None,
    *,
    as_of: datetime | None = None,
    expected_source_column: str | None = None,
) -> tuple[bool, str]:
    """Reject missing, malformed, insufficient-history, or future-dated artifacts."""
    if artifact is None:
        return False, "missing_artifact"
    if not isinstance(artifact, Mapping):
        return False, "malformed_artifact"
    if artifact.get("schema_version") != SCHEMA_VERSION:
        return False, "unsupported_schema_version"
    if artifact.get("status") != "active":
        return False, str(artifact.get("status") or "inactive")
    global_fit = artifact.get("global")
    if not isinstance(global_fit, Mapping) or int(global_fit.get("n") or 0) < 1:
        return False, "insufficient_history"
    source_col = str(artifact.get("source_probability_column") or "")
    if not source_col:
        return False, "missing_source_probability_column"
    if expected_source_column and source_col != expected_source_column:
        return False, "source_probability_column_mismatch"
    cutoff = _parse_timestamp(artifact.get("training_cutoff") or artifact.get("generated_at"))
    if cutoff is None:
        return False, "missing_training_cutoff"
    if as_of is not None:
        as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        as_of_utc = as_of_utc.astimezone(timezone.utc)
        if cutoff > as_of_utc:
            return False, "future_dated_artifact"
    return True, "ok"


def _select_estimate(
    artifact: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[dict[str, Any], str, dict[str, str]]:
    """Pick a sufficiently sampled segment, preferring market_type, else global.

    Sparse segments (below ``min_samples``) fall back to the next preferred
    parent dimension and finally to the global estimate.
    """
    context = probability_blend.segment_context(row)
    global_fit = dict(artifact.get("global") or {})
    min_samples = int(artifact.get("min_samples") or DEFAULT_MIN_SAMPLES)
    tables = artifact.get("dimensions")
    # market_type first (most actionable for stake calibration), then the rest.
    preferred = ("market_type",) + tuple(d for d in DIMENSIONS if d != "market_type")
    if isinstance(tables, Mapping):
        for dimension in preferred:
            table = tables.get(dimension)
            if not isinstance(table, Mapping):
                continue
            entry = table.get(context[dimension])
            if not isinstance(entry, Mapping):
                continue
            n = int(entry.get("n") or 0)
            if n < min_samples:
                continue
            return dict(entry), f"segment:{dimension}={context[dimension]}", context
    return global_fit, "global", context


def resolve_stake_calibration(
    artifact: Mapping[str, Any] | None,
    row: Mapping[str, Any],
    *,
    decimal_price: float | None = None,
    as_of: datetime | None = None,
    source_probability_column: str | None = None,
    neutral_on_failure: bool = True,
) -> CalibrationResult:
    """Resolve calibrated probability and calibration_multiplier for a row.

    Cold-start / invalid artifacts return neutral multiplier 1.0 in shadow
    (``neutral_on_failure=True``). Never raises calibrated probability above
    the source for staking.
    """
    source_col = source_probability_column or str(
        (artifact or {}).get("source_probability_column") or DEFAULT_SOURCE_PROBABILITY_COLUMN
    )
    source_p = _probability(row.get(source_col))
    push = _probability(row.get("push_prob"))
    if push is None:
        push = 0.0
    price = _float(decimal_price if decimal_price is not None else row.get("decimal_price"))
    empty_segment = probability_blend.segment_context(row) if row else {}
    version = str((artifact or {}).get("artifact_version") or "")

    def _neutral(reason: str) -> CalibrationResult:
        mult = 1.0 if neutral_on_failure else 0.0
        return CalibrationResult(
            source_probability=source_p,
            calibrated_probability=source_p if neutral_on_failure else None,
            calibrated_conditional=(
                conditional_non_push_prob(source_p, push) if source_p is not None else None
            ),
            calibration_multiplier=mult,
            sample_count=0,
            predicted_mean=None,
            observed_mean=None,
            shrunk_mean=None,
            fallback_source=reason,
            artifact_version=version,
            source_probability_column=source_col,
            segment=empty_segment,
            status="neutral" if neutral_on_failure else "fail_closed",
        )

    ok, reason = validate_stake_calibration_artifact(
        artifact, as_of=as_of, expected_source_column=source_probability_column
    )
    if not ok:
        return _neutral(reason)
    assert artifact is not None
    if source_p is None or price is None or price <= 1.0:
        return _neutral("missing_source_or_price")

    estimate, source_key, context = _select_estimate(artifact, row)
    factor = _float(estimate.get("shrunk_reliability_factor"))
    if factor is None:
        factor = _float(estimate.get("reliability_factor")) or 1.0
    source_cond = conditional_non_push_prob(source_p, push)
    if source_cond is None:
        return _neutral("invalid_source_partition")

    # Apply reliability; never raise calibrated above source for staking.
    raw_cond = _clamp01(source_cond * factor)
    calibrated_cond = min(source_cond, raw_cond)
    calibrated_p = unconditional_from_conditional(calibrated_cond, push)
    if calibrated_p is None:
        return _neutral("invalid_calibrated_partition")

    mult = calibration_multiplier(
        decimal_price=price,
        source_win_prob=source_p,
        calibrated_win_prob=calibrated_p,
        push_prob=push,
    )
    return CalibrationResult(
        source_probability=round(source_p, 10),
        calibrated_probability=round(calibrated_p, 10),
        calibrated_conditional=round(calibrated_cond, 10),
        calibration_multiplier=round(mult, 10),
        sample_count=int(estimate.get("n") or 0),
        predicted_mean=_float(estimate.get("predicted_mean")),
        observed_mean=_float(estimate.get("observed_mean")),
        shrunk_mean=_float(estimate.get("shrunk_observed_mean")),
        fallback_source=source_key,
        artifact_version=version,
        source_probability_column=source_col,
        segment=context,
        status="active",
    )


def resolve_probability_uncertainty(
    artifact: Mapping[str, Any] | None,
    row: Mapping[str, Any],
    calibration: CalibrationResult,
    *,
    decimal_price: float | None = None,
    confidence_level: float | None = None,
    neutral_on_failure: bool = True,
) -> UncertaintyResult:
    """C2: one-sided Wilson lower bound → conservative Kelly haircut."""
    version = str((artifact or {}).get("artifact_version") or calibration.artifact_version)
    conf = confidence_level
    if conf is None and artifact is not None:
        conf = _float(artifact.get("confidence_level"))
    if conf is None:
        conf = DEFAULT_CONFIDENCE_LEVEL
    price = _float(decimal_price if decimal_price is not None else row.get("decimal_price"))
    push = _probability(row.get("push_prob"))
    if push is None:
        push = 0.0

    def _neutral(reason: str) -> UncertaintyResult:
        mult = 1.0 if neutral_on_failure else 0.0
        return UncertaintyResult(
            conservative_probability=calibration.calibrated_probability,
            conservative_conditional=calibration.calibrated_conditional,
            uncertainty_multiplier=mult,
            confidence_level=float(conf),
            method=WILSON_METHOD,
            sample_count=calibration.sample_count,
            lower_bound=None,
            fallback_source=reason,
            artifact_version=version,
            status="neutral" if neutral_on_failure else "fail_closed",
        )

    if calibration.status != "active" or calibration.calibrated_conditional is None:
        return _neutral(calibration.fallback_source or "inactive_calibration")
    if artifact is None or price is None or price <= 1.0:
        return _neutral("missing_artifact_or_price")

    estimate, source_key, _ = _select_estimate(artifact, row)
    n = float(estimate.get("n") or 0)
    wins = _float(estimate.get("wins"))
    if wins is None:
        obs = _float(estimate.get("observed_mean"))
        wins = (obs * n) if obs is not None else 0.0
    prior = max(0.0, _float(artifact.get("prior_strength")) or 0.0)
    g_obs = _float((artifact.get("global") or {}).get("observed_mean")) or 0.0
    # Shrink before bounding: pseudo-counts from global parent.
    eff_n = n + prior
    eff_wins = wins + prior * g_obs
    if eff_n <= 0:
        return _neutral("zero_effective_sample")

    lower = wilson_lower_bound(eff_wins, eff_n, confidence_level=float(conf))
    # Never claim more precision than the calibrated point.
    conservative_cond = min(calibration.calibrated_conditional, lower)
    conservative_p = unconditional_from_conditional(conservative_cond, push)
    if conservative_p is None or calibration.calibrated_probability is None:
        return _neutral("invalid_conservative_partition")

    mult = uncertainty_multiplier(
        decimal_price=price,
        calibrated_win_prob=calibration.calibrated_probability,
        conservative_win_prob=conservative_p,
        push_prob=push,
    )
    return UncertaintyResult(
        conservative_probability=round(conservative_p, 10),
        conservative_conditional=round(conservative_cond, 10),
        uncertainty_multiplier=round(mult, 10),
        confidence_level=float(conf),
        method=str(artifact.get("uncertainty_method") or WILSON_METHOD),
        sample_count=int(n),
        lower_bound=round(lower, 10),
        fallback_source=source_key,
        artifact_version=version,
        status="active",
    )
