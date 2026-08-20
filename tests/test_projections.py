import math

import pytest

from outlier_scrapers.projections import (
    mlb_first_inning_run_distribution,
    mlb_hits_allowed_distribution,
    mlb_so_projection_record,
    mlb_strikeout_distribution,
    mlb_total_bases_distribution,
    negative_binomial_distribution,
    parse_args,
    project_rows,
)


def assert_valid_distribution(distribution):
    assert all(probability >= 0 for probability in distribution.pmf.values())
    assert math.isclose(sum(distribution.pmf.values()) + distribution.tail_mass, 1.0, abs_tol=1e-9)
    assert distribution.mean >= 0
    assert distribution.variance >= 0


def test_negative_binomial_distribution_is_bounded_and_calibrated():
    distribution = negative_binomial_distribution(4.0, 8.0)
    assert_valid_distribution(distribution)
    assert distribution.mean == pytest.approx(4.0, abs=0.01)


def test_over_partition_includes_omitted_upper_tail_mass():
    distribution = negative_binomial_distribution(4.0, 8.0, maximum=4)
    partition = distribution.partition(3.5, "OVER")
    expected = distribution.tail_mass + distribution.pmf[4]
    assert partition["win_prob"] == pytest.approx(expected)
    assert sum(partition.values()) == pytest.approx(1.0)


def test_partition_rejects_line_beyond_truncated_support():
    distribution = negative_binomial_distribution(4.0, 8.0, maximum=4)
    with pytest.raises(ValueError, match="bounded PMF support"):
        distribution.partition(5.5, "OVER")


def test_strikeouts_mix_stochastic_workload():
    distribution = mlb_strikeout_distribution(18.0, 0.27)
    assert_valid_distribution(distribution)
    partition = distribution.partition(5.5, "OVER")
    assert partition["win_prob"] + partition["push_prob"] + partition["loss_prob"] == pytest.approx(1.0)


def test_hits_allowed_and_first_inning_models_emit_full_partitions():
    assert_valid_distribution(mlb_hits_allowed_distribution(20.0, 0.12))
    assert_valid_distribution(mlb_first_inning_run_distribution(0.42, 0.38))


def test_total_bases_has_zero_hurdle_and_compound_outcomes():
    distribution = mlb_total_bases_distribution(4, 0.65, {1: 0.65, 2: 0.25, 3: 0.05, 4: 0.05})
    assert_valid_distribution(distribution)
    assert distribution.pmf[0] == pytest.approx(0.65**4)
    assert distribution.pmf.get(1, 0.0) > 0


def test_projection_cli_contract():
    args = parse_args(["project", "--sport", "MLB", "--date", "2026-07-14"])
    assert args.command == "project"
    assert args.sport == "MLB"
    assert args.date == "2026-07-14"


def test_mlb_so_projection_only_emits_for_confirmed_starters():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Jackson Jobe - Strikeouts OVER 4.5",
        "player": "Jackson Jobe",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 4.5,
        "headline_side": "OVER",
    }
    confirmed = {"DET": {"pitcher": "Jackson Jobe", "confirmed": True}}
    record = mlb_so_projection_record(row, confirmed)
    assert record is not None
    assert record["distribution"]["win_prob"] > 0
    assert record["distribution"]["side"] == "OVER"
    assert mlb_so_projection_record(row, {"DET": {"pitcher": "Jackson Jobe", "confirmed": False}}) is None
    assert mlb_so_projection_record(row, {"DET": {"pitcher": "Tarik Skubal", "confirmed": True}}) is None
    reliever = {**row, "player": "Robert Stock", "selection": "Robert Stock - Strikeouts OVER 3.5"}
    assert mlb_so_projection_record(reliever, confirmed) is None


def test_project_rows_returns_auditable_probability_partition():
    projections = project_rows(
        [
            {
                "outcome_id": "k-1",
                "event_id": "game-1",
                "proposition": "PITCHER_STRIKEOUTS",
                "line": 5.5,
                "position": "OVER",
                "projection_features": {"projected_bf": 19, "strikeout_rate": 0.28},
            }
        ],
        "MLB",
    )
    assert projections[0]["status"] == "eligible"
    distribution = projections[0]["distribution"]
    assert distribution["win_prob"] + distribution["push_prob"] + distribution["loss_prob"] == pytest.approx(1.0)


def test_first_inning_yes_no_sides_map_to_over_under_partition():
    projection = project_rows(
        [
            {
                "outcome_id": "yrfi-1",
                "event_id": "game-1",
                "market_id": "first-inning",
                "proposition": "FIRST_INNING_RUN",
                "line": 0.5,
                "position": "YES",
                "projection_features": {"home_run_mean": 0.4, "away_run_mean": 0.35},
            }
        ],
        "MLB",
    )[0]
    assert projection["side"] == "YES"
    assert projection["distribution"]["side"] == "OVER"
    assert projection["distribution"]["push_prob"] == 0.0


def test_non_mlb_shadow_rows_preserve_input_cardinality_and_identity():
    rows = [
        {"outcome_id": "w1", "event_id": "g1", "market_id": "m1"},
        {"outcome_id": "w2", "event_id": "g1", "market_id": "m2"},
    ]
    projections = project_rows(rows, "WNBA")
    assert len(projections) == len(rows)
    assert [projection["row_id"] for projection in projections] == ["w1", "w2"]
    assert {projection["status"] for projection in projections} == {"shadow_only"}
