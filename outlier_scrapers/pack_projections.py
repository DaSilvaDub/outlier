"""Independent-projection and probability-blend transformations for the pack.

Keeps quantitative/model transformations together: indexing shadow-projection
artifacts, applying them to a candidate row without touching consensus/sizing
fields, and the market/model probability blend (which drives sizing only once
promoted — see ``probability_blend.py``).
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from outlier_scrapers import paths, probability_blend
from outlier_scrapers.pack_market import _to_float
from outlier_scrapers.pack_sizing import _apply_blended_sizing
from outlier_scrapers.schema import ValidationError


def _validate_projection_artifact_date(
    payload: dict[str, Any] | None, expected_date: str | None
) -> None:
    """Reject a present projection artifact that is not for the pack slate."""

    if payload is None or expected_date is None:
        return
    artifact_date = str(payload.get("date") or "").strip()
    if not artifact_date:
        raise ValidationError(
            f"Projection artifact is missing date; expected slate {expected_date}."
        )
    if artifact_date != expected_date:
        raise ValidationError(
            f"Projection artifact date {artifact_date} does not match pack slate {expected_date}."
        )


def index_projections(
    payload: dict[str, Any] | None, expected_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """Index eligible shadow projections by normalized outcome id."""

    _validate_projection_artifact_date(payload, expected_date)
    indexed: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for projection in (payload or {}).get("projections") or []:
        if not isinstance(projection, dict) or projection.get("status") != "eligible":
            continue
        outcome_id = projection.get("row_id") or projection.get("outcome_id")
        if outcome_id not in (None, ""):
            key = str(outcome_id)
            if key in indexed:
                ambiguous.add(key)
            else:
                indexed[key] = projection
    for key in ambiguous:
        indexed.pop(key, None)
    return indexed


def apply_shadow_projection(
    row: dict[str, Any], projection: dict[str, Any] | None, expected_side: Any
) -> list[str]:
    """Populate independent fields without touching consensus or sizing fields."""

    if not projection:
        return []
    distribution = projection.get("distribution")
    if not isinstance(distribution, dict):
        return ["projection_invalid_distribution"]
    if projection.get("event_id") not in (None, "", row.get("event_id")):
        return ["projection_event_mismatch"]
    if projection.get("market_id") not in (None, "", row.get("market_id")):
        return ["projection_market_mismatch"]
    if str(projection.get("sport") or "").upper() not in (
        "",
        str(row.get("sport") or "").upper(),
    ):
        return ["projection_sport_mismatch"]
    projection_line = _to_float(distribution.get("line", projection.get("line")))
    row_line = _to_float(row.get("line"))
    if projection_line is not None and row_line is not None and projection_line != row_line:
        return ["projection_line_mismatch"]
    projection_side = str(distribution.get("side") or projection.get("side") or "").upper()
    side_aliases = {"YES": "OVER", "NO": "UNDER"}
    normalized_side = str(expected_side or "").upper()
    if side_aliases.get(normalized_side, normalized_side) not in ("", projection_side):
        return ["projection_side_mismatch"]
    win_prob = _to_float(distribution.get("win_prob"))
    push_prob = _to_float(distribution.get("push_prob"))
    if win_prob is None or not 0.0 <= win_prob <= 1.0:
        return ["projection_invalid_probability"]
    from outlier_scrapers.projections import independent_projection_eligible

    if independent_projection_eligible(projection):
        row["independent_model_prob"] = win_prob
        row["independent_push_prob"] = push_prob if push_prob is not None else ""
        implied_prob = _to_float(row.get("implied_prob"))
        if implied_prob is None:
            decimal_price = _to_float(row.get("decimal_price"))
            implied_prob = 1.0 / decimal_price if decimal_price and decimal_price > 0 else None
        if implied_prob is not None:
            row["independent_edge_pct"] = (win_prob - implied_prob) * 100.0
    row["projection_distribution"] = "discrete_pmf"
    row["projection_mean"] = distribution.get("mean", "")
    row["projection_variance"] = distribution.get("variance", "")
    quantiles = distribution.get("quantiles")
    row["projection_quantiles"] = json.dumps(quantiles, sort_keys=True) if quantiles else ""
    row["projection_model_version"] = distribution.get("model_version", "")
    row["projection_feature_hash"] = projection.get("feature_snapshot_hash", "")
    return []


def _projection_side_conflicts(row: dict[str, Any], expected_side: Any) -> bool:
    """Return True when an available projection mean opposes the wager side."""

    mean = _to_float(row.get("projection_mean"))
    line = _to_float(row.get("line"))
    side = str(expected_side or "").strip().upper()
    if mean is None or line is None:
        return False
    if side in {"OVER", "YES"}:
        return mean <= line
    if side in {"UNDER", "NO"}:
        return mean >= line
    return False


def apply_learned_probability_blend(
    row: dict[str, Any],
    artifact: dict[str, Any] | None,
    promotion: dict[str, Any] | None = None,
) -> None:
    """Record the blend, and let it drive sizing only once it is promoted.

    Shadow (the default) keeps the historical contract: the blend is audit-only
    and never touches model_prob or Kelly units. Promotion is a manual policy
    change in ``config/blend_promotion.json`` gated on eligible sample count —
    the fitted artifact stayed at n=72 (market vs recency L10) for months, which
    is far too thin to size from.
    """

    if row.get("projection_quality_flags"):
        return
    blended = probability_blend.blend_probabilities(
        row.get("market_consensus_prob"),
        row.get("independent_model_prob"),
        artifact,
        row,
    )
    if blended is None:
        return
    row["blend_market_weight"] = blended["market_weight"]
    row["blend_model_weight"] = blended["model_weight"]
    row["blend_weight_source"] = blended["source"]
    row["blend_model_version"] = blended["model_version"]
    row["blend_segment"] = json.dumps(blended.get("segment") or {}, sort_keys=True)
    row["final_blended_prob"] = blended["final_probability"]
    status = promotion if promotion is not None else probability_blend.promotion_status(artifact)
    row["blend_promotion_mode"] = status.get("mode", "shadow")
    if not status.get("drives_sizing"):
        row["blend_sizing_source"] = "audit_only"
        return
    _apply_blended_sizing(row, blended["final_probability"])


def load_projection_records(
    leagues: Sequence[str], expected_date: str | None = None
) -> list[dict[str, Any]]:
    """Load the league projection artifacts for pack-local audit freezing."""

    from outlier_scrapers.pack_market import load_json

    records: list[dict[str, Any]] = []
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        league_paths = paths.league_paths(league)
        payload = load_json(league_paths.normalized / f"{league.lower()}_projections_latest.json")
        _validate_projection_artifact_date(payload, expected_date)
        records.extend((payload or {}).get("projections") or [])
    return records
