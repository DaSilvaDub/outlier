"""Moneyline SAFETY model: how likely is this favorite to actually win?

This is one of the three independent models the architecture calls for. It
answers "who is least likely to lose?" and nothing else. It does not know what
the moneyline costs, and it must not: ``value.py`` owns the price comparison,
and keeping the two apart is the only way the question "is a 94% favorite worth
-2000?" stays answerable.

Construction, in order:

1. **Expected margin** from opponent-adjusted power ratings plus the
   situational adjustments that are actually measurable -- home field, travel,
   time zones, rest, altitude.
2. **Margin -> probability** through a normal CDF with ``margin_sigma``. This
   single parameter controls how fast probability saturates and is the first
   thing recalibration should touch.
3. **Spread blend.** The market's spread is the single strongest public signal
   available and ignoring it is a choice to be worse. It is blended in log-odds
   space at a configurable weight. Note carefully: only the *spread* enters.
   The moneyline never does, so the edge computed downstream against the
   moneyline stays a genuine comparison rather than a tautology. Setting
   ``market_spread_weight`` to 0 runs the model fully market-free, and
   ``fundamental_prob`` is always reported separately so a market-free edge can
   be computed at any time.
4. **Penalties** in log-odds space for the specific fragilities spec Part 3
   enumerates. Log-odds is the right space: the same worry should cost an 85%
   favorite more probability than a 97% one, which is exactly what subtracting
   a constant from the logit does.

One item from the spec's penalty list is deliberately *not* a probability
penalty: "inflated prices unsupported by matchup fundamentals". Docking a
team's win probability because its price is long would feed the market back
into the model it is supposed to be measured against. It is raised as a flag
here and priced in ``value.py``, where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from outlier_scrapers.ncaa.config import ConfidenceConfig, MarginConfig, NcaaConfig, PenaltyConfig
from outlier_scrapers.ncaa.features import GameInputs, MatchupFeatures, TeamSide
from outlier_scrapers.ncaa.stats import clamp, expit, logit, normal_cdf

__all__ = [
    "SOURCE_BLENDED",
    "SOURCE_FUNDAMENTAL_ONLY",
    "SOURCE_SPREAD_ONLY",
    "WinProbability",
    "estimate_win_probability",
]

SOURCE_BLENDED = "fundamental_spread_blend"
SOURCE_FUNDAMENTAL_ONLY = "fundamental_only"
SOURCE_SPREAD_ONLY = "market_spread_only"

#: Thresholds for the qualitative penalty detectors. These are judgement calls
#: about where "a bit weak" becomes "a real fragility"; they live here rather
#: than in the tuning config because moving them changes what the flag *means*,
#: not how strictly it is applied.
_WEAK_OL_GRADE = 60.0
_WEAK_OL_SACK_RATE_ALLOWED = 0.085
_POOR_TURNOVER_RATE = 0.035
_INCONSISTENT_SUCCESS_RATE = 0.42
_HIGH_EXPLOSIVENESS = 0.85
_POOR_COACH_GRADE = 55.0
_CAPABLE_OPPOSING_QB_GRADE = 80.0
_INJURY_CLUSTER_COUNT = 3
_HIGH_WIND_MPH = 20.0
_HIGH_PRECIP_CHANCE = 0.5
_TRANSFER_HEAVY_ADDITIONS = 20


@dataclass(frozen=True)
class WinProbability:
    """The safety model's output for one favorite."""

    game_id: str
    favorite: str
    underdog: str
    expected_margin: float | None
    fundamental_prob: float | None
    spread_prob: float | None
    blended_prob: float | None
    #: Final estimate after penalties. ``None`` when neither source was usable.
    model_prob: float | None
    penalties: Mapping[str, float]
    total_penalty: float
    confidence: float
    upset_risk: float
    primary_upset_path: str | None
    secondary_upset_path: str | None
    model_source: str
    flags: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return self.model_prob is not None


def estimate_win_probability(
    game: GameInputs,
    features: MatchupFeatures,
    config: NcaaConfig | None = None,
) -> WinProbability:
    """Estimate the favorite's true win probability."""
    cfg = config or NcaaConfig()
    flags: list[str] = list(features.flags)
    favorite = game.side(features.favorite)
    underdog = game.opponent_of(features.favorite)

    expected_margin = _expected_margin(game, features, cfg.margin)
    fundamental_prob = (
        normal_cdf(expected_margin / cfg.margin.margin_sigma)
        if expected_margin is not None and cfg.margin.margin_sigma > 0
        else None
    )
    spread_prob = _spread_probability(game, features, cfg.margin)

    blended_prob, source = _blend(fundamental_prob, spread_prob, cfg.margin, flags)
    if blended_prob is None:
        flags.append("no_win_probability_source")
        return WinProbability(
            game_id=features.game_id,
            favorite=features.favorite,
            underdog=features.underdog,
            expected_margin=expected_margin,
            fundamental_prob=fundamental_prob,
            spread_prob=spread_prob,
            blended_prob=None,
            model_prob=None,
            penalties={},
            total_penalty=0.0,
            confidence=0.0,
            upset_risk=100.0,
            primary_upset_path=None,
            secondary_upset_path=None,
            model_source="unavailable",
            flags=tuple(dict.fromkeys(flags)),
        )

    penalties = _detect_penalties(game, features, favorite, underdog, cfg.penalties)
    total_penalty = min(sum(penalties.values()), cfg.penalties.max_total_penalty)
    model_prob = expit(logit(blended_prob) - total_penalty)

    if _price_looks_inflated(game, features, model_prob, cfg):
        flags.append("price_inflated_vs_fundamentals")

    confidence = _confidence(
        game, features, favorite, fundamental_prob, spread_prob, cfg.confidence, cfg.margin, flags
    )
    upset_risk, primary, secondary = _upset_profile(model_prob, penalties, features, underdog)

    return WinProbability(
        game_id=features.game_id,
        favorite=features.favorite,
        underdog=features.underdog,
        expected_margin=None if expected_margin is None else round(expected_margin, 3),
        fundamental_prob=None if fundamental_prob is None else round(fundamental_prob, 6),
        spread_prob=None if spread_prob is None else round(spread_prob, 6),
        blended_prob=round(blended_prob, 6),
        model_prob=round(model_prob, 6),
        penalties={k: round(v, 4) for k, v in penalties.items()},
        total_penalty=round(total_penalty, 4),
        confidence=round(confidence, 2),
        upset_risk=round(upset_risk, 2),
        primary_upset_path=primary,
        secondary_upset_path=secondary,
        model_source=source,
        flags=tuple(dict.fromkeys(flags)),
    )


def _expected_margin(
    game: GameInputs,
    features: MatchupFeatures,
    cfg: MarginConfig,
) -> float | None:
    """Favorite-relative expected margin in points."""
    if features.quality_gap is None:
        return None
    margin = features.quality_gap
    context = game.context

    if not features.neutral_site:
        hfa = cfg.home_field_points
        margin += hfa if features.favorite_is_home else -hfa

        if context.travel_miles is not None:
            travel = context.travel_miles / 1000.0 * cfg.travel_points_per_1000_miles
            # The traveling side pays it; the favorite gains only when it is home.
            margin += travel if features.favorite_is_home else -travel

        if context.timezone_shift_hours is not None:
            shift = abs(context.timezone_shift_hours) * cfg.timezone_points_per_hour
            margin += shift if features.favorite_is_home else -shift

        if (
            context.altitude_feet is not None
            and context.altitude_feet >= cfg.altitude_threshold_feet
        ):
            margin += cfg.altitude_points if features.favorite_is_home else -cfg.altitude_points

    if features.rest_edge is not None:
        rest = clamp(
            features.rest_edge * cfg.rest_points_per_day,
            -cfg.max_rest_points,
            cfg.max_rest_points,
        )
        margin += rest

    return margin


def _spread_probability(
    game: GameInputs,
    features: MatchupFeatures,
    cfg: MarginConfig,
) -> float | None:
    """Win probability implied by the market spread alone."""
    spread = game.market.spread_current
    if spread is None:
        spread = game.market.spread_open
    if spread is None or cfg.margin_sigma <= 0:
        return None
    # ``spread`` is home-relative; a favorite laying points carries a negative
    # number, so the favorite's market margin is the negated favorite-relative
    # spread.
    favorite_spread = spread if features.favorite_is_home else -spread
    return normal_cdf(-favorite_spread / cfg.margin_sigma)


def _blend(
    fundamental: float | None,
    spread: float | None,
    cfg: MarginConfig,
    flags: list[str],
) -> tuple[float | None, str]:
    """Combine the two probability sources in log-odds space."""
    weight = clamp(cfg.market_spread_weight, 0.0, 1.0)
    if fundamental is not None and spread is not None:
        if abs(fundamental - spread) >= cfg.divergence_flag_threshold:
            flags.append("model_market_divergence")
        blended = expit((1.0 - weight) * logit(fundamental) + weight * logit(spread))
        return blended, SOURCE_BLENDED
    if fundamental is not None:
        flags.append("no_market_spread")
        return fundamental, SOURCE_FUNDAMENTAL_ONLY
    if spread is not None:
        # Nothing independent was available. The estimate is the market's, so
        # any "edge" it produces against the moneyline is a spread-vs-moneyline
        # discrepancy, not model skill. Tiering refuses CORE on this flag.
        flags.append("fundamental_unavailable")
        return spread, SOURCE_SPREAD_ONLY
    return None, "unavailable"


def _detect_penalties(
    game: GameInputs,
    features: MatchupFeatures,
    favorite: TeamSide,
    underdog: TeamSide,
    cfg: PenaltyConfig,
) -> dict[str, float]:
    """Apply spec Part 3's fragility list, one detector per penalty."""
    penalties: dict[str, float] = {}
    roster = favorite.roster
    eff = favorite.efficiency
    coach = favorite.coaching
    context = game.context

    if roster.starter_unavailable:
        penalties["quarterback_out"] = cfg.quarterback_out
    elif roster.starter_uncertain:
        penalties["quarterback_uncertain"] = cfg.quarterback_uncertain

    out_count = roster.players_out or 0
    if out_count >= _INJURY_CLUSTER_COUNT or len(roster.key_injuries) >= _INJURY_CLUSTER_COUNT:
        penalties["injury_cluster"] = cfg.injury_cluster

    if (eff.offensive_line_grade is not None and eff.offensive_line_grade < _WEAK_OL_GRADE) or (
        eff.sack_rate_allowed is not None and eff.sack_rate_allowed > _WEAK_OL_SACK_RATE_ALLOWED
    ):
        penalties["weak_offensive_line"] = cfg.weak_offensive_line

    if (
        eff.turnover_worthy_play_rate is not None
        and eff.turnover_worthy_play_rate > _POOR_TURNOVER_RATE
    ):
        penalties["poor_turnover_profile"] = cfg.poor_turnover_profile

    inconsistent = (
        eff.offense_success_rate is not None
        and eff.offense_success_rate < _INCONSISTENT_SUCCESS_RATE
    )
    if inconsistent:
        penalties["inconsistent_execution"] = cfg.inconsistent_execution
        if (
            eff.offense_explosiveness is not None
            and eff.offense_explosiveness >= _HIGH_EXPLOSIVENESS
        ):
            # Efficient-when-explosive but nothing else: a defense that limits
            # chunk plays takes the whole offense away.
            penalties["explosive_dependence"] = cfg.explosive_dependence

    if coach.head_coach_grade is not None and coach.head_coach_grade < _POOR_COACH_GRADE:
        penalties["poor_coaching"] = cfg.poor_coaching

    # Generic road status is already priced into expected margin through
    # ``home_field_points``; penalising every road favorite again here would
    # charge the same disadvantage twice. Only a venue explicitly marked
    # hostile adds anything beyond that.
    if (
        context.hostile_venue
        and not features.favorite_is_home
        and not features.neutral_site
    ):
        penalties["hostile_road_environment"] = cfg.hostile_road_environment

    if context.rivalry:
        penalties["rivalry_volatility"] = cfg.rivalry_volatility

    if (
        underdog.roster.qb_grade is not None
        and underdog.roster.qb_grade >= _CAPABLE_OPPOSING_QB_GRADE
    ):
        penalties["capable_opposing_quarterback"] = cfg.capable_opposing_quarterback

    if not context.dome and _weather_is_uncertain(game):
        penalties["weather_uncertainty"] = cfg.weather_uncertainty

    look_ahead = context.look_ahead_home if features.favorite_is_home else context.look_ahead_away
    if look_ahead:
        penalties["look_ahead_spot"] = cfg.look_ahead_spot

    letdown = context.letdown_home if features.favorite_is_home else context.letdown_away
    if letdown:
        penalties["letdown_spot"] = cfg.letdown_spot

    short_week = context.short_week_home if features.favorite_is_home else context.short_week_away
    if short_week:
        penalties["short_week"] = cfg.short_week

    return penalties


def _weather_is_uncertain(game: GameInputs) -> bool:
    context = game.context
    if context.weather_forecast_stable is False:
        return True
    if context.wind_mph is not None and context.wind_mph >= _HIGH_WIND_MPH:
        return True
    return (
        context.precipitation_chance is not None
        and context.precipitation_chance >= _HIGH_PRECIP_CHANCE
    )


def _price_looks_inflated(
    game: GameInputs,
    features: MatchupFeatures,
    model_prob: float,
    cfg: NcaaConfig,
) -> bool:
    """Flag prices the fundamentals do not support (spec Part 3, final item)."""
    from outlier_scrapers.ncaa.odds import two_way_devig

    price = (
        game.market.moneyline_home_current
        if features.favorite_is_home
        else game.market.moneyline_away_current
    )
    other = (
        game.market.moneyline_away_current
        if features.favorite_is_home
        else game.market.moneyline_home_current
    )
    fair = two_way_devig(price, other, method=cfg.devig_method)
    if fair is None:
        return False
    return fair[0] - model_prob >= 0.03


def _confidence(
    game: GameInputs,
    features: MatchupFeatures,
    favorite: TeamSide,
    fundamental: float | None,
    spread: float | None,
    cfg: ConfidenceConfig,
    margin_cfg: MarginConfig,
    flags: list[str],
) -> float:
    """Assemble the 0-100 confidence score (spec Part 10).

    Confidence is not probability. Every term below is a reason to trust the
    probability less, and none of them move the probability itself.
    """
    score = cfg.base
    critical = features.critical_coverage
    score -= cfg.critical_missing_penalty_per_point * (1.0 - critical.ratio)
    score -= cfg.missing_data_penalty_per_point * (1.0 - features.coverage.ratio)
    if critical.missing:
        flags.append("incomplete_critical_inputs")

    games_played = favorite.efficiency.games_played
    if games_played is not None and games_played < cfg.small_sample_games:
        score -= cfg.small_sample_penalty
        flags.append("small_sample")

    coach = favorite.coaching
    if coach.first_year_staff or coach.coordinator_change or coach.scheme_change:
        score -= cfg.coaching_change_penalty
        flags.append("staff_change")

    additions = favorite.roster.portal_additions
    if additions is not None and additions >= _TRANSFER_HEAVY_ADDITIONS:
        score -= cfg.transfer_heavy_penalty
        flags.append("transfer_heavy_roster")

    if game.context.weather_forecast_stable is False:
        score -= cfg.unstable_weather_penalty

    hours = game.market.hours_since_capture
    if hours is not None and hours > cfg.stale_market_hours:
        score -= cfg.stale_market_penalty
        flags.append("stale_market")

    if (
        fundamental is not None
        and spread is not None
        and abs(fundamental - spread) >= margin_cfg.divergence_flag_threshold
    ):
        score -= cfg.model_divergence_penalty

    if favorite.roster.starter_uncertain or favorite.roster.injury_report_complete is False:
        score -= cfg.unresolved_quarterback_penalty
        if favorite.roster.injury_report_complete is False:
            flags.append("unresolved_injury_report")

    return clamp(score, cfg.floor, 100.0)


def _upset_profile(
    model_prob: float,
    penalties: Mapping[str, float],
    features: MatchupFeatures,
    underdog: TeamSide,
) -> tuple[float, str | None, str | None]:
    """Upset Risk Score plus the two most likely paths to losing (spec Part 6).

    The base term doubles the loss probability so the tier thresholds land
    where the spec's example framework expects: a 90% favorite scores 20, an
    85% favorite scores 30.
    """
    risk = 200.0 * (1.0 - model_prob)
    risk += 8.0 * len(penalties)

    paths: list[tuple[float, str]] = [
        (weight, _UPSET_PATH_LABELS.get(name, name)) for name, weight in penalties.items()
    ]
    if underdog.efficiency.offense_explosiveness is not None and (
        underdog.efficiency.offense_explosiveness >= _HIGH_EXPLOSIVENESS
    ):
        paths.append((0.20, "Underdog explosive-play variance"))
    if features.turnover_edge is not None and features.turnover_edge < 0:
        paths.append((0.16, "Favorite's turnover profile is the worse of the two"))
    if features.quality_gap is not None and features.quality_gap < 10.0:
        paths.append((0.30, "Quality gap is narrow enough that one swing decides it"))

    paths.sort(key=lambda item: -item[0])
    primary = paths[0][1] if paths else None
    secondary = paths[1][1] if len(paths) > 1 else None
    return clamp(risk, 0.0, 100.0), primary, secondary


_UPSET_PATH_LABELS: Mapping[str, str] = {
    "quarterback_out": "Starting quarterback unavailable",
    "quarterback_uncertain": "Unresolved quarterback status",
    "injury_cluster": "Injury cluster on the favorite",
    "weak_offensive_line": "Offensive line can be disrupted",
    "poor_turnover_profile": "Turnover-prone offense",
    "explosive_dependence": "Offense depends on explosive plays",
    "inconsistent_execution": "Inconsistent down-to-down execution",
    "poor_coaching": "Coaching disadvantage",
    "hostile_road_environment": "Hostile road environment",
    "rivalry_volatility": "Rivalry-game volatility",
    "capable_opposing_quarterback": "Opposing quarterback can carry an upset",
    "weather_uncertainty": "Unsettled weather",
    "look_ahead_spot": "Look-ahead spot",
    "letdown_spot": "Letdown spot",
    "short_week": "Short week",
}
