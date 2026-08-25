"""Learn and apply versioned market/model probability blend weights."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from outlier_scrapers import paths

SCHEMA_VERSION = 1
DEFAULT_WEIGHTS_PATH = paths.PROJECT_ROOT / "calibration" / "blend_weights.json"
DIMENSIONS = (
    "league",
    "market_type",
    "odds_range",
    "time_before_game",
    "data_quality_tier",
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


def hours_before_game(captured_at: Any, event_starts_at: Any) -> float | None:
    captured = _parse_timestamp(captured_at)
    starts = _parse_timestamp(event_starts_at)
    if captured is None or starts is None:
        return None
    return (starts - captured).total_seconds() / 3600.0


def odds_range(price: Any) -> str:
    american = _float(price)
    if american is None:
        return "UNKNOWN"
    if american <= -200:
        return "<=-200"
    if american <= -121:
        return "-199_TO_-121"
    if american <= -101:
        return "-120_TO_-101"
    if american <= 100:
        return "-100_TO_+100"
    if american <= 149:
        return "+101_TO_+149"
    return "+150+"


def time_before_game_bucket(hours: Any) -> str:
    value = _float(hours)
    if value is None:
        return "UNKNOWN"
    if value < 0:
        return "POST_START"
    if value <= 1:
        return "0_TO_1H"
    if value <= 6:
        return "1_TO_6H"
    if value <= 24:
        return "6_TO_24H"
    return "24H_PLUS"


def data_quality_tier(
    data_quality_flags: Any,
    projection_quality_flags: Any = "",
    *,
    disqualifying: bool = False,
) -> str:
    if disqualifying or str(projection_quality_flags or "").strip():
        return "LOW"
    return "MEDIUM" if str(data_quality_flags or "").strip() else "HIGH"


def segment_context(row: Mapping[str, Any]) -> dict[str, str]:
    hours = _float(row.get("hours_before_game"))
    if hours is None:
        hours = hours_before_game(
            row.get("captured_at") or row.get("as_of"),
            row.get("event_starts_at") or row.get("_event_starts_at"),
        )
    tier = row.get("data_quality_tier")
    if not tier:
        tier = data_quality_tier(
            row.get("data_quality_flags"),
            row.get("projection_quality_flags"),
            disqualifying=bool(row.get("disqualifying")),
        )
    return {
        "league": str(row.get("sport") or row.get("league") or "UNKNOWN").strip().upper(),
        "market_type": str(row.get("market_type") or "UNKNOWN").strip().upper(),
        "odds_range": str(row.get("odds_range") or odds_range(row.get("price"))).strip().upper(),
        "time_before_game": str(
            row.get("time_before_game") or time_before_game_bucket(hours)
        ).strip().upper(),
        "data_quality_tier": str(tier or "UNKNOWN").strip().upper(),
    }


def _training_pair(row: Mapping[str, Any]) -> tuple[float, float, float] | None:
    result = str(row.get("win_loss_push") or "").strip().upper()
    market = _probability(row.get("market_consensus_prob"))
    independent = _probability(row.get("independent_model_prob"))
    push_val = row.get("push_prob")
    push = _probability(push_val) if push_val not in (None, "") else 0.0
    if result not in {"W", "L"} or market is None or independent is None:
        return None
    if push is None or push >= 1.0:
        return None
    market /= 1.0 - push
    independent /= 1.0 - push
    if not (0.0 <= market <= 1.0 and 0.0 <= independent <= 1.0):
        return None
    return market, independent, 1.0 if result == "W" else 0.0


def fit_market_weight(pairs: Iterable[tuple[float, float, float]]) -> dict[str, Any]:
    kept = list(pairs)
    denominator = sum((market - independent) ** 2 for market, independent, _ in kept)
    if not kept:
        return {"market_weight": 1.0, "model_weight": 0.0, "n": 0, "brier_score": None}
    if denominator <= 1e-15:
        market_weight = 1.0
    else:
        numerator = sum(
            (market - independent) * (actual - independent)
            for market, independent, actual in kept
        )
        market_weight = min(1.0, max(0.0, numerator / denominator))
    brier = sum(
        (market_weight * market + (1.0 - market_weight) * independent - actual) ** 2
        for market, independent, actual in kept
    ) / len(kept)
    return {
        "market_weight": round(market_weight, 8),
        "model_weight": round(1.0 - market_weight, 8),
        "n": len(kept),
        "brier_score": round(brier, 10),
    }


def fit_weight_artifact(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_samples: int = 30,
    prior_strength: float = 30.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")
    if prior_strength < 0:
        raise ValueError("prior_strength cannot be negative")
    eligible: list[tuple[Mapping[str, Any], tuple[float, float, float]]] = []
    for row in rows:
        pair = _training_pair(row)
        context = segment_context(row)
        hours = _float(row.get("hours_before_game"))
        if hours is None:
            hours = hours_before_game(row.get("captured_at"), row.get("event_starts_at"))
        if pair is None or hours is None or hours < 0 or "UNKNOWN" in context.values():
            continue
        eligible.append((row, pair))
    global_fit = fit_market_weight(pair for _, pair in eligible)
    active = global_fit["n"] >= min_samples
    if not active:
        global_fit = {**global_fit, "market_weight": 1.0, "model_weight": 0.0}
    dimensions: dict[str, dict[str, dict[str, Any]]] = {}
    if active:
        global_weight = float(global_fit["market_weight"])
        for dimension in DIMENSIONS:
            groups: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
            for row, pair in eligible:
                groups[segment_context(row)[dimension]].append(pair)
            fitted: dict[str, dict[str, Any]] = {}
            for value, pairs in sorted(groups.items()):
                if len(pairs) < min_samples:
                    continue
                raw = fit_market_weight(pairs)
                shrunk = (
                    len(pairs) * float(raw["market_weight"]) + prior_strength * global_weight
                ) / (len(pairs) + prior_strength)
                fitted[value] = {
                    **raw,
                    "raw_market_weight": raw["market_weight"],
                    "market_weight": round(shrunk, 8),
                    "model_weight": round(1.0 - shrunk, 8),
                }
            dimensions[dimension] = fitted
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "active" if active else "insufficient_history",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "trained_through": max(
            (str(row.get("captured_at") or "") for row, _ in eligible), default=""
        ),
        "objective": "brier_score",
        "min_samples": min_samples,
        "prior_strength": prior_strength,
        "eligible_samples": len(eligible),
        "global": global_fit,
        "dimensions": dimensions,
    }
    fingerprint_payload = {key: value for key, value in artifact.items() if key != "generated_at"}
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    artifact["model_version"] = f"blend-brier-v1-{fingerprint}"
    return artifact


def write_weight_artifact(artifact: Mapping[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(json.dumps(dict(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return output_path


def load_weight_artifact(path: Path = DEFAULT_WEIGHTS_PATH) -> dict[str, Any] | None:
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


def resolve_market_weight(
    artifact: Mapping[str, Any] | None, row: Mapping[str, Any]
) -> dict[str, Any]:
    if not artifact or artifact.get("status") != "active":
        return {
            "market_weight": 1.0,
            "model_weight": 0.0,
            "source": "market_only_insufficient_history",
            "matched_dimensions": [],
            "model_version": str((artifact or {}).get("model_version") or ""),
        }
    generated = _parse_timestamp(artifact.get("generated_at"))
    row_time = _parse_timestamp(row.get("captured_at") or row.get("as_of"))
    if generated is None or row_time is None or generated > row_time:
        return {
            "market_weight": 1.0,
            "model_weight": 0.0,
            "source": "market_only_artifact_cutoff",
            "matched_dimensions": [],
            "model_version": str(artifact.get("model_version") or ""),
        }
    global_fit = artifact.get("global")
    if not isinstance(global_fit, Mapping):
        return {
            "market_weight": 1.0,
            "model_weight": 0.0,
            "source": "market_only_invalid_artifact",
            "matched_dimensions": [],
            "model_version": str(artifact.get("model_version") or ""),
        }
    global_weight = _probability(global_fit.get("market_weight"))
    global_weight = global_weight if global_weight is not None else 1.0
    prior_value = _float(artifact.get("prior_strength"))
    prior = max(0.0, prior_value if prior_value is not None else 1.0)
    weighted_sum, total = global_weight * prior, prior
    matched: list[str] = []
    context = segment_context(row)
    tables = artifact.get("dimensions")
    if isinstance(tables, Mapping):
        for dimension in DIMENSIONS:
            table = tables.get(dimension)
            entry = table.get(context[dimension]) if isinstance(table, Mapping) else None
            if not isinstance(entry, Mapping):
                continue
            weight, n = _probability(entry.get("market_weight")), _float(entry.get("n"))
            if weight is None or n is None or n <= 0:
                continue
            evidence = n if prior == 0 else min(n, prior * 4.0)
            weighted_sum += weight * evidence
            total += evidence
            matched.append(dimension)
    market_weight = weighted_sum / total if total > 0 else global_weight
    market_weight = min(1.0, max(0.0, market_weight))
    return {
        "market_weight": round(market_weight, 8),
        "model_weight": round(1.0 - market_weight, 8),
        "source": "learned:" + ",".join(matched) if matched else "learned:global",
        "matched_dimensions": matched,
        "model_version": str(artifact.get("model_version") or ""),
        "segment": context,
    }


def blend_probabilities(
    market_probability: Any,
    independent_probability: Any,
    artifact: Mapping[str, Any] | None,
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    market = _probability(market_probability)
    independent = _probability(independent_probability)
    if market is None or independent is None:
        return None
    resolved = resolve_market_weight(artifact, row)
    market_weight = float(resolved["market_weight"])
    final = market_weight * market + (1.0 - market_weight) * independent
    return {**resolved, "final_probability": round(final, 10)}


DEFAULT_PROMOTION_PATH = paths.PROJECT_ROOT / "config" / "blend_promotion.json"
DEFAULT_PROMOTION_MIN_SAMPLES = 1000
# Cache keyed by policy path -> (mtime, policy); a rewritten file reloads itself.
_PROMOTION_POLICY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def load_promotion_policy(path: Path = DEFAULT_PROMOTION_PATH) -> dict[str, Any]:
    """Load the manual promotion policy for the market/model blend.

    Shadow is the default and the fail-safe: an unreadable, malformed, or
    missing policy never lets the blend drive sizing.
    """

    policy: dict[str, Any] = {
        "mode": "shadow",
        "min_eligible_samples": DEFAULT_PROMOTION_MIN_SAMPLES,
        "model_version": "",
        "promoted_by": "",
        "promoted_at": "",
    }
    path = Path(path)
    try:
        stamp = path.stat().st_mtime
    except OSError:
        _PROMOTION_POLICY_CACHE.pop(str(path), None)
        return policy
    cached = _PROMOTION_POLICY_CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        return dict(cached[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _PROMOTION_POLICY_CACHE[str(path)] = (stamp, dict(policy))
        return policy
    if not isinstance(payload, Mapping):
        return policy
    mode = str(payload.get("mode") or "shadow").strip().lower()
    policy["mode"] = mode if mode in {"shadow", "live"} else "shadow"
    minimum = _float(payload.get("min_eligible_samples"))
    if minimum is not None and minimum >= 0:
        policy["min_eligible_samples"] = int(minimum)
    for key in ("model_version", "promoted_by", "promoted_at"):
        policy[key] = str(payload.get(key) or "")
    _PROMOTION_POLICY_CACHE[str(path)] = (stamp, dict(policy))
    return policy


def promotion_status(
    artifact: Mapping[str, Any] | None, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Whether the fitted blend may drive live sizing, and why not when it may not."""

    resolved = dict(policy) if policy is not None else load_promotion_policy()
    minimum = int(resolved.get("min_eligible_samples") or DEFAULT_PROMOTION_MIN_SAMPLES)
    samples = int(_float((artifact or {}).get("eligible_samples")) or 0)
    version = str((artifact or {}).get("model_version") or "")
    pinned = str(resolved.get("model_version") or "")
    reasons: list[str] = []
    if not artifact or artifact.get("status") != "active":
        reasons.append("artifact_inactive")
    if str(resolved.get("mode")) != "live":
        reasons.append("policy_shadow")
    if samples < minimum:
        reasons.append(f"insufficient_samples:{samples}<{minimum}")
    if pinned and pinned != version:
        reasons.append("model_version_mismatch")
    return {
        "drives_sizing": not reasons,
        "mode": str(resolved.get("mode")),
        "eligible_samples": samples,
        "min_eligible_samples": minimum,
        "model_version": version,
        "reasons": reasons,
    }
