"""Predictor-foundation gates: stop sizing market EV as if it were a model."""

from outlier_scrapers.slate_quality import (
    MARKET_DEVIG_UNIT_CAP,
    apply_market_devig_unit_cap,
    apply_predictor_gates,
    classify_injuries,
    has_predictive_signal,
)
from outlier_scrapers.t30_reprice import _stream_for_row


def test_classify_injuries_ignores_il_listings_that_are_not_out():
    flags = (
        "NYM: Justin Hagenman (60-Day IL; Ribs Fracture; ret 2026-09-04) | "
        "NYM: Juan Soto (10-Day IL; Left Calf Strain; ret 2026-09-01) | "
        "SD: Joe Musgrove (Out; Right Elbow Inflammation; ret 2026-08-21)"
    )
    view = classify_injuries(flags, "NYM")
    assert view.own_star_out is False
    assert view.opponent_star_out is True
    assert any("Musgrove" in item for item in view.opponent_outs)
    assert not any("Soto" in item for item in view.own_outs)
    assert not any("Hagenman" in item for item in view.own_outs)


def test_classify_injuries_still_counts_explicit_out_inside_il_status():
    flags = "NYY: Aaron Judge (10-Day IL; Out; Right Wrist)"
    view = classify_injuries(flags, "NYY")
    assert view.own_star_out is True
    assert any("Judge" in item for item in view.own_outs)


def test_market_devig_unit_cap_covers_outlier_and_local():
    outlier = {
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 3.0,
        "sizing_flags": "",
    }
    apply_market_devig_unit_cap(outlier)
    assert outlier["recommended_units_pre_news"] == MARKET_DEVIG_UNIT_CAP
    assert "market_devig_unit_cap" in outlier["sizing_flags"]

    local = {
        "model_prob_source": "local_devig",
        "recommended_units_pre_news": 2.5,
        "sizing_flags": "local_devig_unit_cap",
    }
    apply_market_devig_unit_cap(local)
    assert local["recommended_units_pre_news"] == MARKET_DEVIG_UNIT_CAP


def test_has_predictive_signal_requires_insight_movement_or_orf_not_il_flags():
    assert has_predictive_signal({"signal_flags": "own_star_out;opponent_star_out"}) is False
    assert has_predictive_signal({"signal_flags": "insight_support;own_star_out"}) is True
    assert has_predictive_signal({"signal_flags": "movement_support"}) is True
    assert has_predictive_signal({"signal_flags": "orf_support"}) is True
    assert has_predictive_signal({"signal_flags": "insight_support;movement_against"}) is False


def test_predictor_gates_kill_naked_player_prop_ev():
    row = {
        "market_type": "SO",
        "selection": "Robert Stock - Strikeouts OVER 3.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 3.0,
        "edge_pct": 0.108,
        "independent_model_prob": "",
        "signal_flags": "own_star_out;opponent_star_out",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["recommended_units_pre_news"] == ""
    assert row["actionable"] == "false"
    assert "missing_predictive_signal" in row["sizing_flags"]


def test_predictor_gates_keep_signaled_player_prop_but_cap_devig_units():
    row = {
        "market_type": "SO",
        "selection": "Olivia Miles - Assists OVER 6.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 3.0,
        "edge_pct": 0.105,
        "independent_model_prob": "",
        "signal_flags": "insight_support;movement_support",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["recommended_units_pre_news"] == MARKET_DEVIG_UNIT_CAP
    assert row["actionable"] == "true"
    assert "edge_suspect_no_independent_model" in row["sizing_flags"]


def test_predictor_gates_promote_gamelog_independent_so_sizing(monkeypatch):
    import outlier_scrapers.slate_quality as sq

    monkeypatch.setattr(sq, "ENABLE_INDEPENDENT_SO_SIZING", True)

    row = {
        "market_type": "SO",
        "selection": "Cam Schlittler - Strikeouts OVER 5.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 1.0,
        "edge_pct": 0.05,
        "decimal_price": 2.1,
        "push_prob": 0.0,
        "independent_model_prob": 0.52,
        "independent_push_prob": 0.0,
        "projection_feature_hash": sq.GAMELOG_FEATURE_HASH,
        "signal_flags": "insight_support;movement_support",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["model_prob_source"] == sq.INDEPENDENT_SO_SOURCE
    assert row["model_prob"] == 0.52
    assert float(row["edge_pct"]) > 0.05
    assert 0 < float(row["recommended_units_pre_news"]) <= sq.INDEPENDENT_SO_UNIT_CAP
    assert "independent_gamelog_so_sizing" in row["sizing_flags"]
    assert row["actionable"] == "true"


def test_predictor_gates_skip_independent_so_sizing_when_disabled():
    from outlier_scrapers.slate_quality import (
        ENABLE_INDEPENDENT_SO_SIZING,
        GAMELOG_FEATURE_HASH,
        MARKET_DEVIG_UNIT_CAP,
    )

    assert ENABLE_INDEPENDENT_SO_SIZING is False
    row = {
        "market_type": "SO",
        "selection": "Cam Schlittler - Strikeouts OVER 5.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 3.0,
        "edge_pct": 0.05,
        "decimal_price": 2.1,
        "push_prob": 0.0,
        "independent_model_prob": 0.78,
        "projection_feature_hash": GAMELOG_FEATURE_HASH,
        "signal_flags": "insight_support;movement_support",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["model_prob_source"] == "outlier_devig"
    assert row["recommended_units_pre_news"] == MARKET_DEVIG_UNIT_CAP
    assert "independent_gamelog_so_sizing" not in row["sizing_flags"]


def test_predictor_gates_do_not_promote_independent_without_signal():
    from outlier_scrapers.slate_quality import GAMELOG_FEATURE_HASH

    row = {
        "market_type": "SO",
        "selection": "Sean Burke - Strikeouts UNDER 5.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 1.0,
        "edge_pct": 0.08,
        "decimal_price": 1.91,
        "push_prob": 0.0,
        "independent_model_prob": 0.70,
        "projection_feature_hash": GAMELOG_FEATURE_HASH,
        "signal_flags": "hit_rate_support",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["model_prob_source"] == "outlier_devig"
    assert row["recommended_units_pre_news"] == ""
    assert row["actionable"] == "false"
    assert "missing_predictive_signal" in row["sizing_flags"]


def test_predictor_gates_leave_signaled_gameline_actionable():
    row = {
        "market_type": "GAMELINE",
        "selection": "SD @ NYM Spread HOME +1.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 1.0,
        "edge_pct": 0.04,
        "independent_model_prob": "",
        "signal_flags": "opponent_star_out",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["recommended_units_pre_news"] == 1.0
    assert row["actionable"] == "true"


def test_league_average_so_hash_is_not_independent_eligible():
    from outlier_scrapers.projections import (
        LEAGUE_AVG_SO_HASH,
        WNBA_MINUTES_HASH,
        independent_projection_eligible,
    )

    assert independent_projection_eligible({"feature_snapshot_hash": LEAGUE_AVG_SO_HASH}) is False
    assert independent_projection_eligible({"feature_snapshot_hash": WNBA_MINUTES_HASH}) is False
    assert independent_projection_eligible({"feature_snapshot_hash": "features-123"}) is True
    assert independent_projection_eligible({}) is True
    assert independent_projection_eligible({"feature_snapshot_hash": ""}) is True


def test_t30_routes_canonical_so_market_to_props_stream():
    assert _stream_for_row({"market_type": "SO", "selection": "A - Strikeouts OVER 5.5"}) == "props"
    assert _stream_for_row({"market_type": "PLAYER_PROP"}) == "props"
    assert _stream_for_row({"market_type": "GAMELINE"}) == "games"
