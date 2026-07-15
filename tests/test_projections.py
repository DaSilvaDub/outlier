import math

import pytest

from outlier_scrapers.projections import (
    mlb_first_inning_run_distribution,
    mlb_hits_allowed_distribution,
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
