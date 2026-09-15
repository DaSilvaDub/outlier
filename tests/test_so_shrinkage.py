"""Tests for gamelog SO shrinkage / overconfidence dampening."""

from __future__ import annotations

import pytest

from outlier_scrapers.projections import (
    LEAGUE_STRIKEOUT_RATE,
    STARTER_PROJECTED_BF,
    mlb_so_projection_record,
    shrink_starter_so_features,
    soften_so_win_probability,
)


def test_shrink_starter_so_features_pulls_extreme_rate_toward_league():
    raw_bf = 24.0
    raw_rate = 0.40  # extreme
    shrunk = shrink_starter_so_features(
        raw_bf,
        raw_rate,
        starts=3,
        total_bf=72.0,
        total_k=28.8,
    )
    assert LEAGUE_STRIKEOUT_RATE < shrunk["strikeout_rate"] < raw_rate
    assert (
        STARTER_PROJECTED_BF < shrunk["projected_bf"] < raw_bf or shrunk["projected_bf"] != raw_bf
    )
    assert shrunk["workload_dispersion"] > 12.0
    assert 0.0 < shrunk["reliability"] < 1.0


def test_soften_so_win_probability_pulls_toward_coin_flip():
    record = {"win_prob": 0.80, "push_prob": 0.0, "loss_prob": 0.20, "side": "OVER"}
    soft = soften_so_win_probability(record, reliability=0.4)
    assert soft["win_prob"] == pytest.approx(0.4 * 0.80 + 0.6 * 0.5)
    assert soft["loss_prob"] == pytest.approx(1.0 - soft["win_prob"])
    assert soft["win_prob"] + soft["loss_prob"] + soft["push_prob"] == pytest.approx(1.0)


def test_mlb_so_gamelog_path_is_less_extreme_after_shrinkage():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Cam Schlittler - Strikeouts OVER 5.5",
        "player": "Cam Schlittler",
        "team": "NYY",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 5.5,
        "headline_side": "OVER",
    }
    probable = {
        "NYY": {
            "pitcher": "Cam Schlittler",
            "confirmed": True,
            "projected_bf": 24.0,
            "strikeout_rate": 0.34,
            "starts": 3,
            "total_bf": 72.0,
            "total_k": 24.5,
            "feature_source": "mlb_stats_gamelog",
        }
    }
    # Raw distribution without going through shrink helper for comparison:
    from outlier_scrapers.projections import mlb_strikeout_distribution

    raw = mlb_strikeout_distribution(24.0, 0.34).to_record(line=5.5, side="OVER")
    shrunk_record = mlb_so_projection_record(row, probable)
    assert shrunk_record is not None
    assert shrunk_record["distribution"]["win_prob"] < raw["win_prob"]
    assert shrunk_record["distribution"]["win_prob"] > 0.5  # still OVER-leaning
