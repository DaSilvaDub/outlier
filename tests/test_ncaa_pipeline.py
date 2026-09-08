"""Tests for the standalone NCAA college football analytics pipeline.

The invariants exercised here are the ones that make the models *different
from each other*: safety must not see the price, value must, correlation must
raise the joint probability rather than lower it, and every model must refuse
to answer when its inputs are absent.
"""

from __future__ import annotations

import math
import random

from outlier_scrapers.ncaa import backtest, calibrate, odds, stats
from outlier_scrapers.ncaa.config import NcaaConfig, ParlayConfig, TierConfig, load_config
from outlier_scrapers.ncaa.features import (
    CoachingProfile,
    GameContext,
    GameInputs,
    MarketQuote,
    RosterStatus,
    TeamEfficiency,
    TeamSide,
    build_matchup_features,
)
from outlier_scrapers.ncaa.mismatch import score_mismatch
from outlier_scrapers.ncaa.parlay import (
    ParlayLeg,
    build_parlay_ladder,
    correlation_matrix,
    evaluate_parlay,
    fragility,
    joint_probability,
    marginal_leg_analysis,
)
from outlier_scrapers.ncaa.pipeline import analyze_slate
from outlier_scrapers.ncaa.report import (
    CertaintyLanguageError,
    certainty_language_violations,
    render_slate_report,
)
from outlier_scrapers.ncaa.safety import (
    SOURCE_BLENDED,
    SOURCE_SPREAD_ONLY,
    estimate_win_probability,
)
from outlier_scrapers.ncaa.tiers import TIER_AVOID, TIER_CORE, assign_tier
from outlier_scrapers.ncaa.totals import PASS, project_total
from outlier_scrapers.ncaa.value import assess_value

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

STRONG = dict(
    offense_success_rate=0.50,
    offense_explosiveness=0.82,
    offense_points_per_drive=3.0,
    defense_points_per_drive=1.5,
    offensive_line_grade=87.0,
    defensive_front_grade=85.0,
    pressure_rate=0.38,
    sack_rate_allowed=0.03,
    turnover_worthy_play_rate=0.018,
    opponent_adjusted_net=18.0,
    seconds_per_play=26.0,
    games_played=8,
    qb_grade=88.0,
    starting_qb_status="starter",
    recruiting_talent_composite=930.0,
    skill_position_depth=85.0,
    defensive_depth=83.0,
    players_out=0,
    players_doubtful=0,
    players_questionable=1,
    suspensions=0,
    injury_report_complete=True,
    head_coach_grade=85.0,
    offensive_coordinator_grade=83.0,
    defensive_coordinator_grade=82.0,
    lead_protection_rate=0.91,
    returning_production=0.70,
    rush_rate=0.48,
)

WEAK = dict(STRONG)
WEAK.update(
    offense_success_rate=0.39,
    offense_explosiveness=0.58,
    offense_points_per_drive=1.5,
    defense_points_per_drive=2.9,
    offensive_line_grade=56.0,
    defensive_front_grade=54.0,
    qb_grade=58.0,
    recruiting_talent_composite=520.0,
    skill_position_depth=53.0,
    defensive_depth=51.0,
    players_out=2,
    head_coach_grade=53.0,
    lead_protection_rate=0.61,
    opponent_adjusted_net=-14.0,
    turnover_worthy_play_rate=0.043,
    seconds_per_play=28.0,
    rush_rate=0.55,
)


def _side(team: str, **kwargs) -> TeamSide:
    def pick(cls):
        return {
            key: value
            for key, value in kwargs.items()
            if key in cls.__dataclass_fields__ and key != "team"
        }

    return TeamSide(
        TeamEfficiency(team, **pick(TeamEfficiency)),
        RosterStatus(team, **pick(RosterStatus)),
        CoachingProfile(team, **pick(CoachingProfile)),
    )


def _game(
    game_id="g1",
    favorite="FAV",
    underdog="DOG",
    favorite_rating=24.0,
    underdog_rating=-6.0,
    spread=-27.5,
    favorite_ml=-2600,
    underdog_ml=1250,
    total=52.5,
    **context,
) -> GameInputs:
    context.setdefault("dome", True)
    return GameInputs(
        game_id,
        _side(favorite, power_rating=favorite_rating, offense_epa_play=0.28,
              defense_epa_play=-0.13, **STRONG),
        _side(underdog, power_rating=underdog_rating, offense_epa_play=-0.11,
              defense_epa_play=0.09, **WEAK),
        GameContext(favorite, underdog, kickoff_utc="2026-09-12T16:00:00Z",
                    conference_game=True, rest_days_home=7, rest_days_away=7, **context),
        MarketQuote(spread_current=spread, moneyline_home_current=favorite_ml,
                    moneyline_away_current=underdog_ml, total_current=total,
                    book_count=7, hours_since_capture=3.0),
        sources={"ratings": "provider_x"},
    )


def _leg(team, probability, price, **kwargs):
    return ParlayLeg(
        game_id=kwargs.pop("game_id", team),
        team=team,
        opponent=kwargs.pop("opponent", "OPP"),
        model_prob=probability,
        market_prob_fair=kwargs.pop("market_prob_fair", probability - 0.01),
        decimal_price=price,
        confidence=kwargs.pop("confidence", 90.0),
        upset_risk=kwargs.pop("upset_risk", 10.0),
        tier=kwargs.pop("tier", TIER_CORE),
        tags=kwargs.pop("tags", {}),
        primary_upset_path=kwargs.pop("primary_upset_path", None),
    )


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------


def test_normal_quantile_inverts_cdf():
    for p in (1e-6, 0.01, 0.25, 0.5, 0.9, 0.99, 0.999999):
        assert abs(stats.normal_cdf(stats.normal_ppf(p)) - p) < 1e-10


def test_gauss_hermite_reproduces_normal_moments():
    nodes, weights = stats.standard_normal_expectation_nodes(24)
    assert abs(sum(weights) - 1.0) < 1e-12
    assert abs(sum(w * m for m, w in zip(nodes, weights))) < 1e-12
    assert abs(sum(w * m * m for m, w in zip(nodes, weights)) - 1.0) < 1e-10
    assert abs(sum(w * m ** 4 for m, w in zip(nodes, weights)) - 3.0) < 1e-9


def test_rank_one_factor_recovers_uniform_block():
    rho = 0.3
    matrix = [[1.0 if i == j else rho for j in range(4)] for i in range(4)]
    loadings = stats.rank_one_factor(matrix)
    # A zeroed-diagonal power iteration would converge to rho*(n-1)/n here.
    for loading in loadings:
        assert abs(loading - math.sqrt(rho)) < 1e-6


def test_rank_one_factor_is_zero_without_correlation():
    assert stats.rank_one_factor([[1.0, 0.0], [0.0, 1.0]]) == [0.0, 0.0]


# --------------------------------------------------------------------------
# odds
# --------------------------------------------------------------------------


def test_american_decimal_round_trip():
    for price in (-2000, -350, -110, 100, 275, 4000):
        assert abs(odds.decimal_to_american(odds.american_to_decimal(price)) - price) < 1e-6


def test_invalid_american_prices_return_none():
    for price in (None, 0, 50, -99, "x"):
        assert odds.american_to_decimal(price) is None


def test_every_devig_method_normalises():
    raw = [odds.implied_prob(-2000), odds.implied_prob(1000)]
    for method in odds.DEVIG_METHODS:
        result = odds.devig(raw, method=method)
        assert result is not None
        assert abs(sum(result.probabilities) - 1.0) < 1e-9


def test_power_devig_loads_more_margin_on_the_longshot():
    raw = [odds.implied_prob(-2000), odds.implied_prob(1000)]
    proportional = odds.devig(raw, method="multiplicative")
    power = odds.devig(raw, method="power")
    assert proportional is not None and power is not None
    # The favorite keeps more of its raw probability under the power method.
    assert power.probabilities[0] > proportional.probabilities[0]


def test_one_sided_market_cannot_be_devigged():
    assert odds.devig([0.9]) is None


def test_kelly_growth_is_zero_without_edge():
    # 94% at -1800 is a losing bet: it needs 94.7% to break even.
    assert odds.kelly_growth(0.94, odds.american_to_decimal(-1800)) == 0.0
    assert odds.kelly_growth(0.94, odds.american_to_decimal(-1200)) > 0.0


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_unknown_config_keys_raise(tmp_path=None):
    import json
    import tempfile
    from pathlib import Path

    directory = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    bad = directory / "bad.json"
    bad.write_text(json.dumps({"margin": {"margin_sigmaa": 1}}), encoding="utf-8")
    try:
        load_config(bad)
    except ValueError as exc:
        assert "margin_sigmaa" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a misspelled configuration key was silently accepted")


def test_nested_config_sections_are_built(tmp_path=None):
    import json
    import tempfile
    from pathlib import Path

    directory = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    path = directory / "ncaa.json"
    path.write_text(json.dumps({"margin": {"margin_sigma": 14.0}}), encoding="utf-8")
    config = load_config(path)
    assert config.margin.margin_sigma == 14.0
    # Unspecified values keep their defaults.
    assert config.margin.home_field_points == 2.4


def test_parlay_decay_bound_matches_core_threshold():
    """A stricter decay bound than the CORE floor makes CORE legs unusable."""
    config = NcaaConfig()
    implied = 1.0 - config.tiers.core_min_win_prob
    assert abs(config.parlay.max_probability_decay_per_leg - implied) < 1e-9


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------


def test_favorite_resolved_from_spread_sign():
    home_favorite = build_matchup_features(_game(spread=-27.5))
    assert home_favorite.favorite == "FAV"
    assert home_favorite.favorite_is_home is True
    away_favorite = build_matchup_features(_game(spread=7.5))
    assert away_favorite.favorite == "DOG"
    assert away_favorite.favorite_is_home is False


def test_market_spread_is_reported_favorite_relative():
    features = build_matchup_features(_game(spread=-27.5))
    assert features.market_spread_favorite == -27.5
    away = build_matchup_features(_game(spread=7.5))
    assert away.market_spread_favorite == -7.5


def test_missing_inputs_yield_none_not_zero():
    bare = GameInputs("g", _side("A"), _side("B"), GameContext("A", "B"))
    features = build_matchup_features(bare)
    assert features.quality_gap is None
    assert features.quarterback_edge is None
    assert features.injury_edge is None
    assert "favorite_undetermined" in features.flags


def test_composite_features_are_scale_normalised():
    """Composites must not be dominated by whichever metric has bigger units."""
    game = _game()
    features = build_matchup_features(game)
    # Grade gap is 30 points and the rate gap is 0.14; an unnormalised mean
    # would land near 15 rather than in standard-deviation units.
    assert features.trenches_edge is not None
    assert 0.5 < features.trenches_edge < 5.0
    assert features.quarterback_edge is not None
    assert 0.5 < features.quarterback_edge < 5.0


def test_critical_coverage_is_narrower_than_total_coverage():
    features = build_matchup_features(_game())
    assert features.critical_coverage.total < features.coverage.total
    assert features.critical_coverage.ratio == 1.0


# --------------------------------------------------------------------------
# mismatch
# --------------------------------------------------------------------------


def test_mismatch_refuses_to_score_sparse_games():
    bare = GameInputs(
        "g",
        _side("A", power_rating=5.0),
        _side("B", power_rating=1.0),
        GameContext("A", "B"),
    )
    score = score_mismatch(build_matchup_features(bare))
    assert score.score is None
    assert "mismatch_insufficient_coverage" in score.flags


def test_mismatch_identifies_structural_advantages():
    score = score_mismatch(build_matchup_features(_game()))
    assert score.score is not None and score.score > 70.0
    assert "quarterback_advantage" in score.structural_advantages


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------


def test_win_probability_blends_fundamental_and_spread():
    game = _game()
    win = estimate_win_probability(game, build_matchup_features(game))
    assert win.model_source == SOURCE_BLENDED
    assert win.fundamental_prob is not None and win.spread_prob is not None
    assert win.model_prob is not None
    low, high = sorted([win.fundamental_prob, win.spread_prob])
    assert low - 1e-9 <= win.blended_prob <= high + 1e-9


def test_spread_only_estimate_is_flagged_and_blocked_from_core():
    game = GameInputs(
        "g",
        _side("A"),
        _side("B"),
        GameContext("A", "B"),
        MarketQuote(spread_current=-21.0, moneyline_home_current=-1200,
                    moneyline_away_current=800),
    )
    features = build_matchup_features(game)
    win = estimate_win_probability(game, features)
    assert win.model_source == SOURCE_SPREAD_ONLY
    assert "fundamental_unavailable" in win.flags
    tier = assign_tier(win, assess_value(game, features, win), score_mismatch(features))
    assert tier.tier == TIER_AVOID


def test_no_source_means_no_probability():
    game = GameInputs("g", _side("A"), _side("B"), GameContext("A", "B"))
    win = estimate_win_probability(game, build_matchup_features(game))
    assert win.model_prob is None
    assert win.usable is False
    assert "no_win_probability_source" in win.flags


def test_road_status_is_not_penalised_twice():
    """Generic road status is already in expected margin via home field."""
    plain = _game(spread=15.0, favorite_ml=1200, underdog_ml=-2600)
    hostile = _game(spread=15.0, favorite_ml=1200, underdog_ml=-2600, hostile_venue=True)
    plain_win = estimate_win_probability(plain, build_matchup_features(plain))
    hostile_win = estimate_win_probability(hostile, build_matchup_features(hostile))
    assert "hostile_road_environment" not in plain_win.penalties
    assert "hostile_road_environment" in hostile_win.penalties
    assert hostile_win.model_prob < plain_win.model_prob


def test_penalties_shave_more_probability_from_weaker_favorites():
    """A logit penalty must cost an 85% favorite more than a 97% one."""
    config = NcaaConfig()
    penalty = config.penalties.rivalry_volatility
    from outlier_scrapers.ncaa.stats import expit, logit

    weak_drop = 0.85 - expit(logit(0.85) - penalty)
    strong_drop = 0.97 - expit(logit(0.97) - penalty)
    assert weak_drop > strong_drop


def test_confidence_falls_when_critical_inputs_are_missing():
    full = _game()
    thin = GameInputs(
        "g",
        _side("FAV", power_rating=24.0, offense_epa_play=0.28, defense_epa_play=-0.13),
        _side("DOG", power_rating=-6.0, offense_epa_play=-0.11, defense_epa_play=0.09),
        GameContext("FAV", "DOG", dome=True),
        MarketQuote(spread_current=-27.5, moneyline_home_current=-2600,
                    moneyline_away_current=1250),
    )
    full_win = estimate_win_probability(full, build_matchup_features(full))
    thin_win = estimate_win_probability(thin, build_matchup_features(thin))
    assert thin_win.confidence < full_win.confidence
    assert "incomplete_critical_inputs" in thin_win.flags


# --------------------------------------------------------------------------
# value: the same prediction at different prices
# --------------------------------------------------------------------------


def test_identical_probability_different_prices_changes_the_bet():
    cheap = _game(favorite_ml=-1200, underdog_ml=750)
    dear = _game(favorite_ml=-12000, underdog_ml=2500)
    results = []
    for game in (cheap, dear):
        features = build_matchup_features(game)
        win = estimate_win_probability(game, features)
        results.append((win, assess_value(game, features, win)))

    (cheap_win, cheap_value), (dear_win, dear_value) = results
    # The safety model never saw the moneyline, so its answer is identical.
    assert cheap_win.model_prob == dear_win.model_prob
    # The value model did, so its answer is not.
    assert cheap_value.probability_edge > dear_value.probability_edge
    assert cheap_value.kelly_growth > dear_value.kelly_growth
    assert dear_value.ev_per_unit < 0
    assert "negative_expected_value" in dear_value.flags


def test_market_free_edge_is_reported_alongside_blended_edge():
    game = _game()
    features = build_matchup_features(game)
    win = estimate_win_probability(game, features)
    value = assess_value(game, features, win)
    assert value.market_free_edge is not None
    assert value.probability_edge is not None


# --------------------------------------------------------------------------
# tiers
# --------------------------------------------------------------------------


def test_quarterback_uncertainty_blocks_core():
    game = _game()
    uncertain = GameInputs(
        game.game_id,
        TeamSide(
            game.home.efficiency,
            RosterStatus(**{**game.home.roster.__dict__, "starting_qb_status": "questionable"}),
            game.home.coaching,
        ),
        game.away,
        game.context,
        game.market,
        game.sources,
    )
    features = build_matchup_features(uncertain)
    win = estimate_win_probability(uncertain, features)
    tier = assign_tier(win, assess_value(uncertain, features, win), score_mismatch(features))
    assert tier.tier != TIER_CORE
    assert "quarterback_uncertain" in tier.blocking


def test_structural_advantage_requirement_is_configurable():
    game = _game()
    features = build_matchup_features(game)
    win = estimate_win_probability(game, features)
    value = assess_value(game, features, win)
    mismatch = score_mismatch(features)
    strict = assign_tier(win, value, mismatch, TierConfig(core_requires_structural_advantage=True))
    relaxed = assign_tier(win, value, mismatch, TierConfig(core_requires_structural_advantage=False))
    assert strict.tier == relaxed.tier == TIER_CORE


# --------------------------------------------------------------------------
# parlay
# --------------------------------------------------------------------------


def test_zero_correlation_reproduces_the_naive_product():
    config = ParlayConfig(correlation_tags={"model": 0.0, "same_game": 0.9})
    legs = [_leg(f"T{i}", 0.9, 1.15, game_id=f"g{i}") for i in range(3)]
    independent, correlated, loadings = joint_probability(legs, config)
    assert abs(independent - 0.9 ** 3) < 1e-12
    assert abs(correlated - independent) < 1e-9
    assert loadings == [0.0, 0.0, 0.0]


def test_positive_correlation_raises_the_all_win_probability():
    """The sign matters: correlated legs win together more often, not less."""
    config = ParlayConfig(correlation_tags={"model": 0.6, "same_game": 0.9})
    legs = [_leg(f"T{i}", 0.9, 1.15, game_id=f"g{i}") for i in range(3)]
    independent, correlated, _ = joint_probability(legs, config)
    assert correlated > independent
    # ...and never above the weakest leg, which is the perfect-correlation limit.
    assert correlated <= 0.9 + 1e-9


def test_shared_tags_increase_pairwise_correlation():
    config = ParlayConfig()
    tagged = [
        _leg("A", 0.95, 1.1, game_id="g1", tags={"weather_system": "gulf"}),
        _leg("B", 0.95, 1.1, game_id="g2", tags={"weather_system": "gulf"}),
        _leg("C", 0.95, 1.1, game_id="g3", tags={"weather_system": "plains"}),
    ]
    matrix = correlation_matrix(tagged, config)
    assert matrix[0][1] > matrix[0][2]


def test_calibration_stress_is_never_optimistic():
    config = ParlayConfig()
    legs = [_leg(f"T{i}", 0.94, 1.09, game_id=f"g{i}") for i in range(3)]
    parlay = evaluate_parlay(legs, config)
    assert parlay.p_stressed < parlay.p_correlated


def test_weak_leg_is_rejected_even_though_it_lengthens_the_price():
    """The rule the optimizer exists for."""
    config = ParlayConfig()
    core = [_leg(f"T{i}", 0.96, 1.05, game_id=f"g{i}") for i in range(3)]
    parlay = evaluate_parlay(core, config)
    weak = _leg("WEAK", 0.80, 1.28, game_id="gW", upset_risk=30.0)
    accepted, rejected = marginal_leg_analysis(parlay, [weak], config)
    assert accepted == []
    assert len(rejected) == 1
    assert rejected[0].team == "WEAK"
    assert rejected[0].rule == "max_probability_decay_per_leg"
    # The rejected extension really would have paid more.
    extended = evaluate_parlay(list(parlay.legs) + [weak], config)
    assert extended.decimal_price > parlay.decimal_price


def test_ladder_never_contains_a_leg_the_marginal_rule_would_refuse():
    config = ParlayConfig()
    pool = [_leg(f"S{i}", 0.96, 1.05, game_id=f"gs{i}") for i in range(3)]
    pool += [_leg(f"W{i}", 0.78, 1.30, game_id=f"gw{i}", upset_risk=35.0) for i in range(3)]
    ladder = build_parlay_ladder(pool, config)
    for parlay in ladder.values():
        assert all(leg.model_prob >= 0.9 for leg in parlay.legs)


def test_optimizer_returns_nothing_rather_than_forcing_a_play():
    config = ParlayConfig()
    pool = [_leg(f"W{i}", 0.70, 1.45, game_id=f"g{i}", upset_risk=40.0) for i in range(5)]
    assert build_parlay_ladder(pool, config) == {}


def test_high_upset_risk_legs_never_enter_a_parlay():
    config = ParlayConfig()
    pool = [_leg(f"T{i}", 0.97, 1.04, game_id=f"g{i}") for i in range(3)]
    pool.append(_leg("RISKY", 0.97, 1.04, game_id="gr", upset_risk=99.0))
    ladder = build_parlay_ladder(pool, config)
    for parlay in ladder.values():
        assert "RISKY" not in {leg.team for leg in parlay.legs}


def test_one_leg_per_game():
    config = ParlayConfig()
    pool = [
        _leg("A", 0.97, 1.04, game_id="same"),
        _leg("B", 0.96, 1.05, game_id="same"),
        _leg("C", 0.97, 1.04, game_id="g2"),
        _leg("D", 0.96, 1.05, game_id="g3"),
    ]
    ladder = build_parlay_ladder(pool, config)
    for parlay in ladder.values():
        ids = [leg.game_id for leg in parlay.legs]
        assert len(ids) == len(set(ids))


def test_fragility_shares_are_a_partition_of_losing_outcomes():
    config = ParlayConfig()
    legs = [
        _leg("STRONG", 0.98, 1.03, game_id="g1"),
        _leg("MID", 0.95, 1.06, game_id="g2"),
        _leg("WEAKEST", 0.91, 1.10, game_id="g3", primary_upset_path="Turnover variance"),
    ]
    parlay = evaluate_parlay(legs, config)
    result = fragility(parlay, config)
    assert result.most_likely_culprit == "WEAKEST"
    total = sum(result.blame_share.values()) + result.multi_leg_failure_share
    assert abs(total - 1.0) < 1e-6
    assert 0.0 <= result.multi_leg_failure_share <= 1.0


# --------------------------------------------------------------------------
# totals
# --------------------------------------------------------------------------


def test_league_average_matchup_projects_the_league_average_total():
    """The anchor that catches opponent-adjustment drift."""
    config = NcaaConfig()
    average = dict(offense_points_per_drive=config.totals.league_points_per_drive,
                   defense_points_per_drive=config.totals.league_points_per_drive,
                   seconds_per_play=27.0)
    game = GameInputs(
        "g",
        _side("A", power_rating=0.0, **average),
        _side("B", power_rating=0.0, **average),
        GameContext("A", "B", dome=True),
        MarketQuote(total_current=49.5, spread_current=0.0),
    )
    projection = project_total(game, build_matchup_features(game), config)
    expected = (
        2
        * config.totals.base_drives_per_team
        * config.totals.league_points_per_drive
    )
    assert abs(projection.projected_total - expected) < 0.5


def test_opponent_adjustment_is_damped_and_bounded():
    config = NcaaConfig()
    game = GameInputs(
        "g",
        _side("A", offense_points_per_drive=3.5, defense_points_per_drive=3.0,
              seconds_per_play=22.0),
        _side("B", offense_points_per_drive=3.4, defense_points_per_drive=2.9,
              seconds_per_play=22.0),
        GameContext("A", "B", dome=True),
        MarketQuote(total_current=70.5, spread_current=-1.0),
    )
    projection = project_total(game, build_matchup_features(game), config)
    per_team_drive_points = projection.projected_total / (
        2 * projection.expected_drives_per_team
    )
    assert per_team_drive_points <= config.totals.max_points_per_drive


def test_integer_total_derives_a_push_probability():
    config = NcaaConfig()
    game = _game(total=52.0)
    projection = project_total(game, build_matchup_features(game), config)
    assert projection.push_probability is not None and projection.push_probability > 0
    assert "integer_line_push_derived" in projection.flags
    assert abs(
        projection.over_probability + projection.under_probability
        + projection.push_probability - 1.0
    ) < 1e-9


def test_half_point_total_cannot_push():
    config = NcaaConfig()
    game = _game(total=52.5)
    projection = project_total(game, build_matchup_features(game), config)
    assert projection.push_probability == 0.0


def test_totals_pass_without_points_per_drive():
    game = GameInputs(
        "g",
        _side("A", seconds_per_play=26.0),
        _side("B", seconds_per_play=27.0),
        GameContext("A", "B", dome=True),
        MarketQuote(total_current=50.5),
    )
    projection = project_total(game, build_matchup_features(game))
    assert projection.recommendation == PASS
    assert projection.projected_total is None
    assert "points_per_drive_unavailable" in projection.flags


def test_environmental_deductions_are_capped():
    config = NcaaConfig()
    game = _game(dome=False, wind_mph=45.0, temperature_f=5.0, precipitation_chance=0.95)
    projection = project_total(game, build_matchup_features(game), config)
    assert sum(projection.adjustments.values()) >= -(
        config.totals.max_environmental_points + config.totals.max_game_script_points + 1e-9
    )


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------


def test_calibration_recovers_a_known_overconfidence():
    rng = random.Random(7)
    data = []
    for _ in range(3000):
        stated = rng.uniform(0.55, 0.99)
        true = stats.expit(0.7 * stats.logit(stated))
        data.append((stated, 1 if rng.random() < true else 0))
    calibrator = calibrate.fit_calibrator(data)
    assert calibrator.method != "identity"
    assert calibrator.apply(0.95) < 0.95


def test_calibration_declines_when_the_model_is_already_calibrated():
    rng = random.Random(11)
    data = [
        (p, 1 if rng.random() < p else 0)
        for p in (rng.uniform(0.55, 0.99) for _ in range(3000))
    ]
    calibrator = calibrate.fit_calibrator(data)
    assert calibrator.method == "identity"
    assert "no_out_of_sample_improvement" in calibrator.flags


def test_calibration_refuses_small_samples():
    calibrator = calibrate.fit_calibrator([(0.9, 1)] * 40)
    assert calibrator.method == "identity"
    assert "insufficient_samples" in calibrator.flags


def test_isotonic_fit_is_monotone():
    rng = random.Random(3)
    data = [(p, 1 if rng.random() < p else 0)
            for p in (rng.uniform(0.5, 0.99) for _ in range(800))]
    breakpoints = calibrate.fit_isotonic(data)
    values = [y for _, y in breakpoints]
    assert values == sorted(values)


# --------------------------------------------------------------------------
# backtest
# --------------------------------------------------------------------------


def test_calibration_table_recovers_true_rates():
    rng = random.Random(5)
    results = []
    for i in range(2000):
        p = rng.choice([0.82, 0.87, 0.91, 0.935, 0.96])
        results.append(
            backtest.MoneylineResult(f"g{i}", "T", p, p * 1.01, 1 / (p * 1.04),
                                     rng.random() < p)
        )
    report = backtest.backtest_moneylines(results)
    for row in report.calibration:
        if row.sufficient_sample:
            assert abs(row.calibration_gap) < 0.05


def test_small_backtests_are_flagged_not_trusted():
    results = [
        backtest.MoneylineResult(f"g{i}", "T", 0.96, 0.95, 1.05, True) for i in range(3)
    ]
    report = backtest.backtest_moneylines(results)
    assert "no_bucket_meets_sample_floor" in report.flags
    assert all(not row.sufficient_sample for row in report.calibration)


def test_parlay_survival_reports_the_calibration_gap():
    rows = backtest.parlay_survival(
        [backtest.ParlayResult(3, 3, 0.80, 1.55)] * 8
        + [backtest.ParlayResult(3, 2, 0.80, 1.55)] * 2
    )
    assert len(rows) == 1
    assert rows[0].win_rate == 0.8
    assert rows[0].mean_absolute_error == 0.0


def test_totals_backtest_keeps_pushes_out_of_the_win_rate():
    results = [
        backtest.TotalsResult("g1", 55.0, 54.0, 60.0, "OVER", 1.91, True),
        backtest.TotalsResult("g2", 55.0, 54.0, 50.0, "OVER", 1.91, False),
        backtest.TotalsResult("g3", 55.0, 54.0, 54.0, "OVER", 1.91, None),
    ]
    report = backtest.backtest_totals(results)
    assert report.push_count == 1
    assert report.win_rate == 0.5


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def test_certainty_language_is_detected():
    assert certainty_language_violations("a guaranteed lock, basically risk-free")
    # Ordinary football vocabulary must not trip the guard.
    assert certainty_language_violations("an elite lockdown corner and a rangy safety") == ()


def test_report_refuses_to_emit_certainty_language():
    analysis = analyze_slate([_game()], NcaaConfig(), label="Slate")
    text = render_slate_report(analysis)
    assert certainty_language_violations(text) == ()
    broken = analysis.__class__(
        label="This parlay is a lock",
        games_evaluated=analysis.games_evaluated,
        assessments=analysis.assessments,
        parlays=analysis.parlays,
        best_parlay=analysis.best_parlay,
        fragilities=analysis.fragilities,
        calibration_method=analysis.calibration_method,
        flags=analysis.flags,
    )
    try:
        render_slate_report(broken)
    except CertaintyLanguageError:
        pass
    else:  # pragma: no cover
        raise AssertionError("certainty language passed the report guard")


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------


def test_slate_analysis_runs_end_to_end():
    games = [
        _game("g1", "OSU", "AKR", 30.0, -14.0, -40.5, -9000, 2600, 58.5),
        _game("g2", "ALA", "VAN", 24.0, -6.0, -27.5, -2600, 1250, 52.5),
        _game("g3", "UGA", "KEN", 21.0, -2.0, -21.5, -1300, 800, 49.5),
        _game("g4", "TEX", "BAY", 14.0, 3.0, -10.5, -380, 300, 55.5),
    ]
    analysis = analyze_slate(games, NcaaConfig(), label="Saturday")
    assert analysis.games_evaluated == 4
    assert len(analysis.core) >= 1
    assert all(a.win.model_prob is not None for a in analysis.assessments)
    text = render_slate_report(analysis)
    for section in "ABCDEFG":
        assert f"## Section {section}" in text


def test_calibrator_applied_before_tiering():
    rng = random.Random(2)
    data = []
    for _ in range(3000):
        stated = rng.uniform(0.55, 0.99)
        true = stats.expit(0.6 * stats.logit(stated))
        data.append((stated, 1 if rng.random() < true else 0))
    calibrator = calibrate.fit_calibrator(data)
    game = _game()
    plain = analyze_slate([game], NcaaConfig())
    shrunk = analyze_slate([game], NcaaConfig(), calibrator=calibrator)
    assert shrunk.calibration_method != "identity"
    assert shrunk.assessments[0].win.model_prob < plain.assessments[0].win.model_prob
    # The value model must have seen the calibrated number, not the raw one.
    assert shrunk.assessments[0].value.model_prob == shrunk.assessments[0].win.model_prob


def test_unscorable_games_are_counted_not_dropped():
    analysis = analyze_slate(
        [_game(), GameInputs("bare", _side("A"), _side("B"), GameContext("A", "B"))],
        NcaaConfig(),
    )
    assert analysis.games_evaluated == 2
    assert any(flag.startswith("unscored_games:") for flag in analysis.flags)
