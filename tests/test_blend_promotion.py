"""The learned market/model blend stays audit-only until it is promoted."""

from __future__ import annotations

import json

from outlier_scrapers import pack, probability_blend


def _artifact(samples: int) -> dict:
    return {
        "schema_version": probability_blend.SCHEMA_VERSION,
        "status": "active",
        "model_version": "blend-test-v1",
        "eligible_samples": samples,
        "generated_at": "2026-08-01T00:00:00+00:00",
        "prior_strength": 0.0,
        "global": {"market_weight": 0.5, "model_weight": 0.5, "n": samples},
        "dimensions": {},
    }


def _row() -> dict:
    return {
        "market_consensus_prob": 0.50,
        "independent_model_prob": 0.60,
        "decimal_price": 2.10,
        "push_prob": 0.0,
        "recommended_units_pre_news": 1.0,
        "model_prob": 0.50,
        "model_prob_source": "outlier_devig",
        "edge_pct": 0.05,
        "as_of": "2026-08-20T18:00:00+00:00",
    }


def _policy(path, **overrides) -> dict:
    payload = {"mode": "shadow", "min_eligible_samples": 1000, "model_version": ""}
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return probability_blend.load_promotion_policy(path)


def test_missing_or_malformed_policy_falls_back_to_shadow(tmp_path):
    assert probability_blend.load_promotion_policy(tmp_path / "absent.json")["mode"] == "shadow"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert probability_blend.load_promotion_policy(broken)["mode"] == "shadow"
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"mode": "YOLO"}), encoding="utf-8")
    assert probability_blend.load_promotion_policy(weird)["mode"] == "shadow"


def test_checked_in_policy_ships_in_shadow_mode():
    policy = probability_blend.load_promotion_policy()
    assert policy["mode"] == "shadow"
    status = probability_blend.promotion_status(_artifact(72), policy)
    assert status["drives_sizing"] is False
    assert "policy_shadow" in status["reasons"]


def test_thin_history_cannot_drive_sizing_even_in_live_mode(tmp_path):
    policy = _policy(tmp_path / "promotion.json", mode="live")
    status = probability_blend.promotion_status(_artifact(72), policy)
    assert status["drives_sizing"] is False
    assert any(reason.startswith("insufficient_samples") for reason in status["reasons"])


def test_pinned_model_version_must_match(tmp_path):
    policy = _policy(
        tmp_path / "promotion.json", mode="live", model_version="a-different-version"
    )
    status = probability_blend.promotion_status(_artifact(5000), policy)
    assert status["drives_sizing"] is False
    assert "model_version_mismatch" in status["reasons"]


def test_shadow_blend_never_touches_sizing(tmp_path):
    policy = _policy(tmp_path / "promotion.json")
    artifact = _artifact(72)
    row = _row()
    pack.apply_learned_probability_blend(
        row, artifact, probability_blend.promotion_status(artifact, policy)
    )
    assert row["final_blended_prob"] == 0.55
    assert row["blend_sizing_source"] == "audit_only"
    assert row["model_prob"] == 0.50
    assert row["model_prob_source"] == "outlier_devig"
    assert row["recommended_units_pre_news"] == 1.0


def test_promoted_blend_resizes_from_the_blended_probability(tmp_path):
    policy = _policy(tmp_path / "promotion.json", mode="live", min_eligible_samples=1000)
    artifact = _artifact(5000)
    status = probability_blend.promotion_status(artifact, policy)
    assert status["drives_sizing"] is True
    row = _row()
    pack.apply_learned_probability_blend(row, artifact, status)
    assert row["blend_sizing_source"] == "promoted_blend"
    assert row["model_prob"] == row["final_blended_prob"] == 0.55
    assert row["model_prob_source"] == "blended_market_model"
    assert row["edge_pct"] is not None and row["edge_pct"] > 0


def test_promoted_blend_never_revives_a_withheld_stake(tmp_path):
    policy = _policy(tmp_path / "promotion.json", mode="live", min_eligible_samples=1000)
    artifact = _artifact(5000)
    status = probability_blend.promotion_status(artifact, policy)
    row = {**_row(), "recommended_units_pre_news": ""}
    pack.apply_learned_probability_blend(row, artifact, status)
    assert row["blend_sizing_source"] == "audit_only"
    assert row["recommended_units_pre_news"] == ""
    assert row["model_prob"] == 0.50


def test_projection_flagged_rows_are_never_blended(tmp_path):
    policy = _policy(tmp_path / "promotion.json", mode="live", min_eligible_samples=1)
    row = {**_row(), "projection_quality_flags": "projection_audit_only"}
    pack.apply_learned_probability_blend(
        row, _artifact(5000), probability_blend.promotion_status(_artifact(5000), policy)
    )
    assert "final_blended_prob" not in row
