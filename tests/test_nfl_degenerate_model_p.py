"""Offline enrich_close never emits a model_p of exactly 0 or 1 (#229, deferred a)."""

from __future__ import annotations

import pytest

from outlier_nfl.enrich_close import enrich_prediction_payload

BASE = {"player_name": "Joe Wr", "team": "KC", "market": "REC", "position": "OVER",
        "line": 4.5, "event_id": "e1", "best_odds": -110, "implied_probability": 52.38}


def _enrich(rows, mode="pass"):
    return enrich_prediction_payload({"records": rows}, mode="explicit", attach_model_p=mode)


@pytest.mark.parametrize("value", [0.0, 1.0, 100.0, 0, 1, "nan", float("inf")])
def test_supplied_degenerate_model_p_is_cleared(value):
    out = _enrich([{**BASE, "model_p": value, "model_p_source": "external"}])
    row = out["records"][0]
    assert row["model_p"] is None and row["model_p_source"] is None
    assert row["model_p_guard"] == "degenerate_model_p"
    assert row["model_p_rejected"] == value and row["model_p_rejected_source"] == "external"
    assert out["model_p_enrichment"]["n_degenerate_model_p"] == 1
    assert out["model_p_enrichment"]["n_with_model_p"] == 0


def test_raw_empirical_rate_of_one_is_cleared():
    out = _enrich([{**BASE, "l10_hit_rate": 1.0}], mode="empirical_hit_rate")
    assert out["records"][0]["model_p"] is None
    assert out["records"][0]["sportsbook_edge_pts"] is None


def test_ordinary_model_p_untouched():
    out = _enrich([{**BASE, "model_p": 0.61, "model_p_source": "external"}])
    assert out["records"][0]["model_p"] == 0.61
    assert "model_p_guard" not in out["records"][0]
    assert out["model_p_enrichment"]["n_degenerate_model_p"] == 0
