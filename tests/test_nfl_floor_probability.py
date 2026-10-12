"""Regression coverage for exact price benchmarks and cold-start publication."""
from __future__ import annotations

import pytest

from outlier_nfl.floor_probability import (
    describe_floor, is_half_point_line, odds_probability, paired_market_probability,
)


def floor(**changes):
    return {
        "event_id": "e", "player_id": "p", "player_name": "Runner", "team": "BAL",
        "market": "RUSH_YDS", "scope": "full_game", "position": "OVER", "line": 49.5,
        "target_book": "HARDROCK", "target_odds": -575, "confidence_score": 0.8829,
        **changes,
    }


def quotes(**under_changes):
    return [
        {**floor(), "books": [{"book": "Hard Rock", "odds": -575}]},
        {**floor(position="UNDER"), "books": [{"book": "Hard Rock", "odds": 380}],
         **under_changes},
    ]


@pytest.mark.parametrize("value", [None, "bad", 0, 99, -99, True, float("nan"), float("inf")])
def test_invalid_prices_remain_missing(value):
    assert odds_probability(value) is None


@pytest.mark.parametrize("line", [49, 49.0, "49.5", None, True, 49.25, float("nan")])
def test_push_or_unsupported_lines_withhold_unconditional_benchmark(line):
    result = describe_floor(floor(line=line), quotes())
    assert not is_half_point_line(line)
    assert result["break_even_probability"] is None
    assert result["break_even_probability_status"] == "push_probability_unmodeled"
    assert result["market_probability"] is None


def test_exact_pair_benchmark_and_break_even_are_distinct_from_rank():
    result = describe_floor(floor(), quotes())
    expected_over = 575 / 675
    expected_under = 100 / 480
    assert result["market_probability"] == pytest.approx(expected_over / (expected_over + expected_under))
    assert result["break_even_probability"] == pytest.approx(expected_over)
    assert result["ranking_score"] == 0.8829
    assert result["ranking_score_version"] == "nfl_alt_floor_rank_v1"
    assert result["calibrated_probability"] is None
    assert result["fair_decimal_odds"] is None and result["ev_per_unit"] is None
    assert result["probability_status"] == "insufficient_calibration_history"
    assert not result["actionable"] and not result["bet_actionable"]
    assert result["stake_fraction"] == 0


@pytest.mark.parametrize("field,value", [
    ("event_id", "other"), ("player_id", "other"), ("team", "ATL"),
    ("market", "REC_YDS"), ("scope", "first_half"), ("line", 50.5),
])
def test_opposite_quote_never_crosses_identity(field, value):
    probability, status = paired_market_probability(floor(), quotes(**{field: value}))
    assert probability is None and status == "no_exact_same_book_opposite_quote"


def test_book_mismatch_never_builds_synthetic_market():
    probability, status = paired_market_probability(
        floor(), quotes(books=[{"book": "DRAFTKINGS", "odds": 380}]))
    assert probability is None and status == "no_exact_same_book_opposite_quote"


@pytest.mark.parametrize("side,status", [
    ("OVER", "selected_quote_mismatch_or_conflict"),
    ("UNDER", "conflicting_opposite_quotes"),
])
def test_conflicting_quotes_are_rejected(side, status):
    rows = quotes() + [{**floor(position=side), "books": [{"book": "HARDROCK", "odds": -110}]}]
    assert paired_market_probability(floor(), rows) == (None, status)


def test_duplicate_identical_quotes_are_not_conflicts():
    rows = quotes()
    assert paired_market_probability(floor(), rows + rows) == paired_market_probability(floor(), rows)


def test_missing_price_does_not_become_even_money():
    result = describe_floor(floor(target_odds=None), quotes())
    assert result["break_even_probability"] is None
    assert result["break_even_probability_status"] == "invalid_quote"


def test_missing_stable_player_identity_withholds_market_benchmark():
    assert paired_market_probability(floor(player_id=""), quotes()) == (None, "missing_identity")
