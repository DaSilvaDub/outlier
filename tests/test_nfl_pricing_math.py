"""Phase 3a (#225): projection provenance, push-aware probabilities, TD scope and EV approval."""

from __future__ import annotations

from typing import Any

from outlier_nfl.projection import attach_model_p_hierarchy_record


def _row(**kw: Any) -> dict[str, Any]:
    base = {"player_name": "Lamar Jackson", "market": "RUSH_YDS", "position": "OVER",
            "line": 52.5, "l10_hit_rate": 60.0, "l10_sample": 10, "l20_hit_rate": 55.0,
            "l20_sample": 20}
    return {**base, **kw}


# F06a ----------------------------------------------------------------------

def test_hierarchy_fallback_clears_stale_projection_method_and_count() -> None:
    rec = _row(model_p=0.61, model_p_source="projection_nflverse_gaussian",
               model_p_method="gamelog_gaussian", model_p_n_games=3)
    attach_model_p_hierarchy_record(rec, week_index={})  # no weeks: projection unavailable
    assert rec["model_p_source"].startswith("empirical_hit_rate")
    assert rec.get("model_p_method") is None and rec.get("model_p_n_games") is None


def test_hierarchy_projection_keeps_its_own_method_and_count() -> None:
    weeks = [{"rushing_yards": y} for y in (40, 60, 80)]
    rec = _row(model_p_method="stale")
    attach_model_p_hierarchy_record(rec, week_index={"LAMARJACKSON": weeks})
    assert rec["model_p_source"] == "projection_nflverse_gaussian"
    assert (rec["model_p_method"], rec["model_p_n_games"]) == ("gamelog_gaussian", 3)
