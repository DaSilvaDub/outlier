"""Blend, stake, drawdown, and learned-multiplier fitting from the feedback ledger."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from outlier_scrapers import drawdown, paths, probability_blend, stake_calibration
from outlier_scrapers.feedback_db import (
    DEFAULT_DB_PATH,
    PROBABILITY_COLUMNS,
    _connect,
    _float,
    _joined_rows,
    _mean,
    _text,
)

DEFAULT_LEARNED_MULTIPLIER_PROMOTION_PATH = (
    paths.PROJECT_ROOT / "config" / "learned_multiplier_promotion.json"
)

LEARNED_MULTIPLIER_POPULATION = "positive_unit_pack_recommendations_v1"
LEARNED_MULTIPLIER_PROMOTION_DEFAULTS: dict[str, Any] = {
    "schema_version": "1.0",
    "source_probability_column": "market_consensus_prob",
    "min_settled_recommendations": 200,
    "min_observation_days": 30,
    "min_settlement_rate": 0.80,
    "max_missing_event_start_rate": 0.0,
    "min_probability_coverage": 0.95,
    "max_absolute_calibration_gap": 0.03,
    "require_positive_roi": True,
    "min_price_coverage": 0.95,
    "min_clv_coverage": 0.80,
    "min_mean_price_clv": 0.0,
    "required_sport_samples": {"MLB": 50, "WNBA": 50},
}


def _positive_unit_recommendation_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the exact published positive-unit population, including unsettled rows.

    Pack membership is the authority here. The broad decisions/settlements join
    intentionally remains available to the fitters and general reports, but it
    must not determine whether learned stake multipliers are safe to promote.
    """

    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                m.pack_capture_id, m.snapshot_id, m.pack_path, m.pack_timestamp,
                m.source, m.pipeline_verdict, m.units, m.selected, m.actionable,
                s.captured_at, s.sport, s.event_id, s.market_id, s.outcome_id,
                s.player_id, s.selection, s.line, s.price, s.book,
                s.market_consensus_prob, s.independent_model_prob,
                s.final_blended_prob, s.push_prob, s.edge,
                s.data_quality_flags, s.data_quality_tier, s.event_starts_at,
                s.hours_before_game, s.odds_range, s.time_before_game,
                s.market_type, s.model_prob_source, s.decimal_price,
                t.win_loss_push, t.settled_at, t.clv_line, t.clv_price
            FROM pack_snapshot_memberships m
            JOIN market_snapshots s ON s.snapshot_id = m.snapshot_id
            LEFT JOIN settlements t ON t.settlement_id = (
                SELECT t2.settlement_id
                FROM settlements t2
                WHERE t2.snapshot_id = m.snapshot_id
                ORDER BY t2.settled_at DESC, t2.settlement_id DESC
                LIMIT 1
            )
            WHERE m.selected = 1
              AND m.actionable = 1
              AND COALESCE(m.units, 0) > 0
              AND UPPER(COALESCE(m.pipeline_verdict, '')) IN ('PLAY', 'BET')
            ORDER BY m.pack_timestamp, m.pack_capture_id, m.snapshot_id
            """
        )
    ]


def fit_blend_weights(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = probability_blend.DEFAULT_WEIGHTS_PATH,
    *,
    min_samples: int = 200,
    prior_strength: float = 30.0,
) -> dict[str, Any]:
    """Fit a versioned blend artifact from pregame, settled ledger snapshots."""

    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
    artifact = probability_blend.fit_weight_artifact(
        rows, min_samples=min_samples, prior_strength=prior_strength
    )
    probability_blend.write_weight_artifact(artifact, Path(output_path))
    return artifact


def fit_stake_calibration_from_db(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = stake_calibration.DEFAULT_ARTIFACT_PATH,
    *,
    as_of: datetime | None = None,
    source_probability_column: str = stake_calibration.DEFAULT_SOURCE_PROBABILITY_COLUMN,
    min_samples: int = stake_calibration.DEFAULT_MIN_SAMPLES,
    prior_strength: float = stake_calibration.DEFAULT_PRIOR_STRENGTH,
    confidence_level: float = stake_calibration.DEFAULT_CONFIDENCE_LEVEL,
    policy_fingerprint: str = "",
) -> dict[str, Any]:
    """Fit a stake-calibration artifact from settled ledger rows (Track C1)."""

    cutoff = as_of or datetime.now(timezone.utc)
    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
    artifact = stake_calibration.fit_stake_calibration(
        rows,
        as_of=cutoff,
        source_probability_column=source_probability_column,
        min_samples=min_samples,
        prior_strength=prior_strength,
        confidence_level=confidence_level,
        policy_fingerprint=policy_fingerprint,
    )
    stake_calibration.write_stake_calibration_artifact(artifact, Path(output_path))
    return artifact


def compute_drawdown_from_db(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = drawdown.DEFAULT_STATE_PATH,
    *,
    as_of: datetime | None = None,
) -> drawdown.DrawdownState:
    """Compute drawdown state from settled placed ledger rows (Track C3)."""

    cutoff = as_of or datetime.now(timezone.utc)
    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
    # Treat decision units + settlement pnl as placed when settlement exists.
    executions: list[dict[str, Any]] = []
    for row in rows:
        result = _text(row.get("win_loss_push")).upper()
        if result not in {"W", "L", "PUSH"}:
            continue
        units = _float(row.get("units"), field="units")
        if units is None or units <= 0:
            continue
        executions.append(
            {
                "decision_id": row.get("decision_id"),
                "snapshot_id": row.get("snapshot_id"),
                "execution_status": "SETTLED",
                "settled_at": row.get("settled_at") or row.get("captured_at"),
                "pnl": row.get("pnl"),
                "placed_units": units,
                "units": units,
                "win_loss_push": result,
            }
        )
    state = drawdown.compute_drawdown_state(executions, as_of=cutoff)
    drawdown.write_drawdown_state(state, Path(output_path))
    return state


def load_learned_multiplier_promotion_policy(
    path: Path = DEFAULT_LEARNED_MULTIPLIER_PROMOTION_PATH,
) -> dict[str, Any]:
    """Load the readiness thresholds; malformed policy is explicitly fail-closed."""

    policy = dict(LEARNED_MULTIPLIER_PROMOTION_DEFAULTS)
    policy["required_sport_samples"] = dict(
        LEARNED_MULTIPLIER_PROMOTION_DEFAULTS["required_sport_samples"]
    )
    policy["config_valid"] = False
    policy["config_reason"] = "missing_config"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return policy
    except json.JSONDecodeError:
        policy["config_reason"] = "malformed_config"
        return policy
    if not isinstance(payload, dict):
        policy["config_reason"] = "malformed_config"
        return policy
    unknown = set(payload) - set(LEARNED_MULTIPLIER_PROMOTION_DEFAULTS)
    if unknown:
        policy["config_reason"] = f"unknown_keys:{','.join(sorted(unknown))}"
        return policy
    merged = {**policy, **payload}
    required_sports = merged.get("required_sport_samples")
    count_keys = ("min_settled_recommendations", "min_observation_days")
    numeric_keys = (
        "min_settlement_rate",
        "max_missing_event_start_rate",
        "min_probability_coverage",
        "max_absolute_calibration_gap",
        "min_price_coverage",
        "min_clv_coverage",
        "min_mean_price_clv",
    )
    if (
        merged.get("schema_version") != "1.0"
        or merged.get("source_probability_column") not in set(PROBABILITY_COLUMNS.values())
        or not all(
            isinstance(merged.get(key), int) and not isinstance(merged.get(key), bool)
            for key in count_keys
        )
        or not all(
            isinstance(merged.get(key), (int, float))
            and not isinstance(merged.get(key), bool)
            and math.isfinite(float(merged[key]))
            for key in numeric_keys
        )
        or not isinstance(merged.get("require_positive_roi"), bool)
        or not isinstance(required_sports, dict)
        or not all(
            isinstance(sport, str)
            and sport.strip()
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and minimum >= 0
            for sport, minimum in required_sports.items()
        )
    ):
        policy["config_reason"] = "invalid_config_values"
        return policy
    if not (
        int(merged["min_settled_recommendations"]) >= 1
        and int(merged["min_observation_days"]) >= 1
        and 0.0 <= float(merged["min_settlement_rate"]) <= 1.0
        and 0.0 <= float(merged["max_missing_event_start_rate"]) <= 1.0
        and 0.0 <= float(merged["min_probability_coverage"]) <= 1.0
        and float(merged["max_absolute_calibration_gap"]) >= 0.0
        and 0.0 <= float(merged["min_price_coverage"]) <= 1.0
        and 0.0 <= float(merged["min_clv_coverage"]) <= 1.0
    ):
        policy["config_reason"] = "invalid_config_values"
        return policy
    merged["config_valid"] = True
    merged["config_reason"] = "ok"
    return merged


def _active_learned_multiplier_source_column() -> str:
    """Resolve the probability source that the shipped multiplier runtime uses."""

    policy_path = paths.PROJECT_ROOT / "config" / "portfolio_risk.json"
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    calibration = payload.get("calibration")
    if not isinstance(calibration, dict):
        return ""
    source = str(calibration.get("source_probability_column") or "").strip()
    return source if source in set(PROBABILITY_COLUMNS.values()) else ""


def learned_multiplier_promotion_signal(
    rows: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
    *,
    active_source_probability_column: str | None = None,
) -> dict[str, Any]:
    """Evaluate automatic readiness using only exact positive-unit recommendations.

    This is a promotion signal, never an activation switch. Every gate must pass
    before the report says the multipliers are ready for manual review.
    """

    resolved = dict(policy or load_learned_multiplier_promotion_policy())
    source_column = str(resolved.get("source_probability_column") or "")
    active_source = (
        active_source_probability_column
        if active_source_probability_column is not None
        else _active_learned_multiplier_source_column()
    )
    settled = [row for row in rows if _text(row.get("win_loss_push")).upper() in {"W", "L", "PUSH"}]
    graded = [row for row in settled if _text(row.get("win_loss_push")).upper() in {"W", "L"}]
    wins = sum(_text(row.get("win_loss_push")).upper() == "W" for row in graded)
    losses = len(graded) - wins
    pushes = len(settled) - len(graded)
    settled_units = sum(_float(row.get("units"), field="units") or 0.0 for row in settled)
    valid_prices = [
        price
        for row in settled
        if (price := _float(row.get("decimal_price"), field="decimal_price")) is not None
        and price > 1.0
    ]
    profit = 0.0
    for row in settled:
        units = _float(row.get("units"), field="units") or 0.0
        result = _text(row.get("win_loss_push")).upper()
        decimal_price = _float(row.get("decimal_price"), field="decimal_price")
        if result == "W" and decimal_price is not None and decimal_price > 1.0:
            profit += units * (decimal_price - 1.0)
        elif result == "L":
            profit -= units

    probability_pairs: list[tuple[float, float]] = []
    for row in graded:
        source = _float(row.get(source_column), field=source_column)
        push = _float(row.get("push_prob"), field="push_prob")
        if source is None or not 0.0 <= source <= 1.0:
            continue
        if push is None:
            push = 0.0
        conditional = stake_calibration.conditional_non_push_prob(source, push)
        if conditional is None:
            continue
        probability_pairs.append(
            (conditional, 1.0 if _text(row.get("win_loss_push")).upper() == "W" else 0.0)
        )

    expected_hit_rate = _mean(probability for probability, _actual in probability_pairs)
    actual_hit_rate = _mean(actual for _probability_value, actual in probability_pairs)
    calibration_gap = (
        actual_hit_rate - expected_hit_rate
        if expected_hit_rate is not None and actual_hit_rate is not None
        else None
    )
    clv_values = [_float(row.get("clv_price"), field="clv_price") for row in settled]
    clv_values = [value for value in clv_values if value is not None]
    event_dates = {
        _text(row.get("event_starts_at"))[:10]
        for row in settled
        if _text(row.get("event_starts_at"))
    }
    missing_event_starts = sum(not _text(row.get("event_starts_at")) for row in rows)
    sport_samples: dict[str, int] = defaultdict(int)
    for row in settled:
        sport_samples[_text(row.get("sport")).upper() or "UNKNOWN"] += 1

    total = len(rows)
    settled_count = len(settled)
    settlement_rate = settled_count / total if total else 0.0
    missing_event_start_rate = missing_event_starts / total if total else 1.0
    probability_coverage = len(probability_pairs) / len(graded) if graded else 0.0
    price_coverage = len(valid_prices) / settled_count if settled_count else 0.0
    clv_coverage = len(clv_values) / settled_count if settled_count else 0.0
    roi = profit / settled_units if settled_units > 0 else None
    required_sports = dict(resolved.get("required_sport_samples") or {})
    sport_depth_passes = all(
        sport_samples.get(str(sport).upper(), 0) >= int(minimum)
        for sport, minimum in required_sports.items()
    )
    gates = {
        "config_valid": bool(resolved.get("config_valid")),
        "source_probability_matches_portfolio_policy": bool(active_source)
        and source_column == active_source,
        "minimum_settled_recommendations": settled_count
        >= int(resolved.get("min_settled_recommendations") or 0),
        "minimum_observation_days": len(event_dates)
        >= int(resolved.get("min_observation_days") or 0),
        "minimum_settlement_rate": settlement_rate
        >= float(resolved.get("min_settlement_rate") or 0.0),
        "maximum_missing_event_start_rate": missing_event_start_rate
        <= float(resolved.get("max_missing_event_start_rate") or 0.0),
        "minimum_probability_coverage": probability_coverage
        >= float(resolved.get("min_probability_coverage") or 0.0),
        "maximum_absolute_calibration_gap": calibration_gap is not None
        and abs(calibration_gap) <= float(resolved.get("max_absolute_calibration_gap") or 0.0),
        "positive_roi": not bool(resolved.get("require_positive_roi"))
        or (roi is not None and roi > 0.0),
        "minimum_price_coverage": price_coverage
        >= float(resolved.get("min_price_coverage") or 0.0),
        "minimum_clv_coverage": clv_coverage >= float(resolved.get("min_clv_coverage") or 0.0),
        "minimum_mean_price_clv": bool(clv_values)
        and (_mean(clv_values) or 0.0) >= float(resolved.get("min_mean_price_clv") or 0.0),
        "required_sport_depth": sport_depth_passes,
    }
    failed_gates = [name for name, passed in gates.items() if not passed]
    ready = not failed_gates
    return {
        "schema_version": "1.0",
        "status": "READY_FOR_MANUAL_REVIEW" if ready else "NOT_READY",
        "population_definition": LEARNED_MULTIPLIER_POPULATION,
        "auto_promotion": False,
        "ready_for_manual_promotion_review": ready,
        "failed_gates": failed_gates,
        "gates": gates,
        "thresholds": {
            key: value
            for key, value in resolved.items()
            if key not in {"config_valid", "config_reason"}
        },
        "config_reason": resolved.get("config_reason", "unspecified"),
        "portfolio_policy_source_probability_column": active_source,
        "population": {
            "total_recommendations": total,
            "settled_recommendations": settled_count,
            "graded_recommendations": len(graded),
            "unsettled_recommendations": total - settled_count,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "observation_days": len(event_dates),
            "missing_event_starts": missing_event_starts,
            "settlement_rate": settlement_rate,
            "settled_units": settled_units,
            "profit": profit,
            "roi": roi,
            "expected_hit_rate": expected_hit_rate,
            "actual_hit_rate": actual_hit_rate,
            "calibration_gap": calibration_gap,
            "probability_coverage": probability_coverage,
            "price_coverage": price_coverage,
            "clv_coverage": clv_coverage,
            "mean_price_clv": _mean(clv_values),
            "settled_by_sport": dict(sorted(sport_samples.items())),
        },
    }
