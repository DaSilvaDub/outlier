"""Phase 3a (#225): projection provenance, push-aware probabilities, TD scope and EV approval."""

from __future__ import annotations

from typing import Any

import pytest

from outlier_nfl.projection import attach_model_p_hierarchy_record, project_hit_probability_v2


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


# F06b ----------------------------------------------------------------------

def test_enrich_close_hierarchy_honours_overwrite_model_p_false() -> None:
    from outlier_nfl.enrich_close import attach_close_fields

    weeks = {"LAMARJACKSON": [{"rushing_yards": y} for y in (40, 60, 80)]}
    keep = _row(model_p=0.7, model_p_source="empirical_hit_rate_market_prior")
    attach_close_fields(keep, attach_model_p="hierarchy", week_index=weeks, overwrite_model_p=False)
    assert (keep["model_p"], keep["model_p_source"]) == (0.7, "empirical_hit_rate_market_prior")

    replace = _row(model_p=0.7, model_p_source="empirical_hit_rate_market_prior")
    attach_close_fields(replace, attach_model_p="hierarchy", week_index=weeks, overwrite_model_p=True)
    assert replace["model_p_source"] == "projection_nflverse_gaussian"


def test_hierarchy_fills_a_missing_model_p_when_not_overwriting() -> None:
    weeks = {"LAMARJACKSON": [{"rushing_yards": y} for y in (40, 60, 80)]}
    rec = _row()
    attach_model_p_hierarchy_record(rec, week_index=weeks, overwrite=False)
    assert rec["model_p_source"] == "projection_nflverse_gaussian"


# F07 -----------------------------------------------------------------------

def _v2(market: str, line: float, position: str, values: list[float], col: str):
    return project_hit_probability_v2(player_name="x", market=market, line=line,
                                      position=position, week_rows=[{col: v} for v in values])


def test_poisson_integer_under_excludes_the_push() -> None:
    r = _v2("PASS_TD", 2.0, "UNDER", [1, 2, 3], "passing_tds")  # mean 2
    assert r is not None
    assert r.model_p == pytest.approx(0.406006, abs=1e-6)
    assert r.p_push == pytest.approx(0.270671, abs=1e-6)
    over = _v2("PASS_TD", 2.0, "OVER", [1, 2, 3], "passing_tds")
    assert over is not None
    assert over.model_p + r.model_p + r.p_push == pytest.approx(1.0, abs=3e-6)
    assert r.p_loss == pytest.approx(over.model_p, abs=1e-6)


def test_poisson_half_line_has_no_push_and_zero_rate_is_coherent() -> None:
    r = _v2("PASS_TD", 1.5, "UNDER", [1, 2, 3], "passing_tds")
    assert r is not None and r.p_push == 0.0
    z = _v2("PASS_TD", 0.0, "UNDER", [0, 0, 0], "passing_tds")
    assert z is not None and (z.model_p, z.p_push, z.p_loss) == (0.0, 1.0, 0.0)


def test_gaussian_integer_line_marks_push_unmodeled() -> None:
    r = _v2("PASS_YDS", 268.0, "OVER", [281, 255, 300], "passing_yards")
    assert r is not None and r.p_push is None and r.p_loss is None
    h = _v2("PASS_YDS", 268.5, "OVER", [281, 255, 300], "passing_yards")
    assert h is not None and h.p_push == 0.0 and h.p_loss == pytest.approx(1 - h.model_p)


def test_hierarchy_records_win_push_loss() -> None:
    rec = {"player_name": "Kelce", "market": "REC", "position": "UNDER", "line": 5.0}
    attach_model_p_hierarchy_record(rec, week_index={"KELCE": [{"receptions": v} for v in (6, 4, 5)]})
    assert rec["model_p"] == rec["model_p_win"] == pytest.approx(0.440493, abs=1e-6)
    assert rec["model_p_push"] == pytest.approx(0.175467, abs=1e-6)
    assert rec["model_p_win"] + rec["model_p_push"] + rec["model_p_loss"] == pytest.approx(1.0, abs=1e-5)
