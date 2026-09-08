"""Game-totals model: expected possessions x expected points per possession.

The third independent model (spec Parts 7-8). It shares the feature layer with
the moneyline models but none of their machinery: a total is not a win
probability and the things that make a favorite hard to beat are largely not
the things that make a game go over.

Structure:

1. **Possessions.** Drives per team from neutral-situation tempo. Both teams get
   the same drive count -- in football possessions alternate, so pace is a
   property of the game, not of one side.
2. **Points per drive.** Opponent-adjusted multiplicatively *with damping*: an
   offense scoring at ``o`` against a defense allowing ``d`` in a league
   averaging ``l`` projects at ``l * (o/l)^a * (d/l)^a``. The undamped product
   (``a = 1``) is the textbook log-additive adjustment and it over-extrapolates
   badly at the extremes -- an elite offense against a poor defense comes out
   further above league average than any real matchup lands, because both
   ratios multiply. ``a = 0.75`` is the usual correction. The result is then
   clamped to physically plausible bounds.
3. **Environmental and game-script adjustments** applied to the projected total.
4. **Distribution.** Normal around the projection with ``total_sigma``, which is
   what converts a point projection into an Over/Under probability.

One deliberate omission: red-zone efficiency and finishing drives are *not*
applied as a separate multiplier. Points per drive already contains them --
that is what the statistic measures -- and adding a red-zone term on top would
count the same skill twice. Red-zone strength still appears, as an archetype
driver in the explanation.

Integer market totals can push. Consistent with the house rule the MLB pipeline
follows, a push probability is derived explicitly (continuity correction across
the ``+/-0.5`` ladder) rather than assumed away, and the row is flagged so a
reader knows the three-way nature of the market was handled.
"""

from __future__ import annotations

from dataclasses import dataclass

from outlier_scrapers.ncaa.config import NcaaConfig, TotalsConfig
from outlier_scrapers.ncaa.features import GameInputs, MatchupFeatures, TeamSide
from outlier_scrapers.ncaa.stats import clamp, normal_cdf

__all__ = [
    "OVER",
    "PASS",
    "UNDER",
    "TotalsProjection",
    "project_total",
]

OVER = "OVER"
UNDER = "UNDER"
PASS = "PASS"

#: FBS neutral-situation baselines used to convert raw tempo into a z-score.
LEAGUE_SECONDS_PER_PLAY = 27.0
SECONDS_PER_PLAY_SD = 3.0
#: Thresholds for archetype detection (spec Part 8).
_FAST_TEMPO_Z = 0.5
_SLOW_TEMPO_Z = -0.5
_ELITE_OFFENSE_PPD = 2.9
_WEAK_DEFENSE_PPD = 2.6
_STRONG_DEFENSE_PPD = 1.5
_RUN_HEAVY_RATE = 0.58
_POOR_QB_GRADE = 62.0
_WEAK_OL_GRADE = 62.0
_STRONG_FRONT_GRADE = 82.0
_HIGH_EXPLOSIVENESS = 0.85
_LOW_EXPLOSIVENESS = 0.60


@dataclass(frozen=True)
class TotalsProjection:
    """Projected total plus everything Section F asks to display."""

    game_id: str
    home_team: str
    away_team: str
    projected_home_score: float | None
    projected_away_score: float | None
    projected_total: float | None
    market_total: float | None
    total_edge: float | None
    over_probability: float | None
    under_probability: float | None
    push_probability: float | None
    expected_drives_per_team: float | None
    recommendation: str
    confidence: float
    primary_drivers: tuple[str, ...]
    archetypes: tuple[str, ...]
    adjustments: dict[str, float]
    flags: tuple[str, ...]

    @property
    def actionable(self) -> bool:
        return self.recommendation in (OVER, UNDER)


def _tempo_z(side: TeamSide) -> float | None:
    spp = side.efficiency.seconds_per_play
    if spp is None:
        return None
    return (LEAGUE_SECONDS_PER_PLAY - spp) / SECONDS_PER_PLAY_SD


def _expected_drives(home: TeamSide, away: TeamSide, cfg: TotalsConfig) -> tuple[float, bool]:
    """Drives per team, and whether tempo data was actually available."""
    zs = [z for z in (_tempo_z(home), _tempo_z(away)) if z is not None]
    if not zs:
        return cfg.base_drives_per_team, False
    combined = sum(zs) / len(zs)
    drives = cfg.base_drives_per_team + cfg.drives_per_tempo_unit * combined
    return clamp(drives, cfg.min_drives_per_team, cfg.max_drives_per_team), True


def _points_per_drive(offense: TeamSide, defense: TeamSide, cfg: TotalsConfig) -> float | None:
    """Opponent-adjusted points per drive for ``offense`` facing ``defense``."""
    off = offense.efficiency.offense_points_per_drive
    allowed = defense.efficiency.defense_points_per_drive
    league = cfg.league_points_per_drive
    if off is None or allowed is None or league <= 0:
        return None
    if off <= 0 or allowed <= 0:
        return None
    alpha = cfg.opponent_adjustment_damping
    projected = league * (off / league) ** alpha * (allowed / league) ** alpha
    return clamp(projected, cfg.min_points_per_drive, cfg.max_points_per_drive)


def project_total(
    game: GameInputs,
    features: MatchupFeatures,
    config: NcaaConfig | None = None,
    *,
    expected_margin: float | None = None,
) -> TotalsProjection:
    """Project the game total and grade the market's number against it.

    ``expected_margin`` is the favorite-relative margin from the safety model.
    It is used only for the game-script adjustment; when absent the market
    spread substitutes, and when neither exists no script adjustment is made.
    """
    cfg = config or NcaaConfig()
    tcfg = cfg.totals
    flags: list[str] = []
    home, away = game.home, game.away

    drives, tempo_known = _expected_drives(home, away, tcfg)
    if not tempo_known:
        flags.append("tempo_unavailable_league_default_drives")

    home_ppd = _points_per_drive(home, away, tcfg)
    away_ppd = _points_per_drive(away, home, tcfg)

    market_total = game.market.total_current
    if market_total is None:
        market_total = game.market.total_open

    if home_ppd is None or away_ppd is None:
        flags.append("points_per_drive_unavailable")
        return TotalsProjection(
            game_id=game.game_id,
            home_team=home.team,
            away_team=away.team,
            projected_home_score=None,
            projected_away_score=None,
            projected_total=None,
            market_total=market_total,
            total_edge=None,
            over_probability=None,
            under_probability=None,
            push_probability=None,
            expected_drives_per_team=round(drives, 3),
            recommendation=PASS,
            confidence=0.0,
            primary_drivers=(),
            archetypes=(),
            adjustments={},
            flags=tuple(dict.fromkeys(flags)),
        )

    home_score = drives * home_ppd
    away_score = drives * away_ppd
    raw_total = home_score + away_score

    adjustments = _environmental_adjustments(game, tcfg, flags)
    script = _game_script_adjustment(game, features, tcfg, expected_margin)
    if script:
        adjustments["game_script"] = script

    total_adjustment = sum(adjustments.values())
    projected_total = raw_total + total_adjustment
    # Distribute the adjustment proportionally so the projected scores still
    # sum to the projected total.
    share = projected_total / raw_total if raw_total > 0 else 1.0
    projected_home = home_score * share
    projected_away = away_score * share

    edge = None if market_total is None else projected_total - market_total
    over_p, under_p, push_p = _side_probabilities(projected_total, market_total, tcfg, flags)

    archetypes, drivers = _classify(game, features, drives, home_ppd, away_ppd, adjustments)
    recommendation = _recommend(edge, over_p, under_p, tcfg)
    confidence = _confidence(game, features, tempo_known, edge, archetypes, recommendation, flags)

    return TotalsProjection(
        game_id=game.game_id,
        home_team=home.team,
        away_team=away.team,
        projected_home_score=round(projected_home, 2),
        projected_away_score=round(projected_away, 2),
        projected_total=round(projected_total, 2),
        market_total=market_total,
        total_edge=None if edge is None else round(edge, 2),
        over_probability=None if over_p is None else round(over_p, 6),
        under_probability=None if under_p is None else round(under_p, 6),
        push_probability=None if push_p is None else round(push_p, 6),
        expected_drives_per_team=round(drives, 3),
        recommendation=recommendation,
        confidence=round(confidence, 2),
        primary_drivers=drivers,
        archetypes=archetypes,
        adjustments={k: round(v, 3) for k, v in adjustments.items()},
        flags=tuple(dict.fromkeys(flags)),
    )


def _environmental_adjustments(
    game: GameInputs,
    cfg: TotalsConfig,
    flags: list[str],
) -> dict[str, float]:
    """Weather effects, in points removed from the projected total."""
    adjustments: dict[str, float] = {}
    context = game.context
    if context.dome:
        return adjustments

    if context.wind_mph is not None and context.wind_mph > cfg.wind_threshold_mph:
        excess = context.wind_mph - cfg.wind_threshold_mph
        adjustments["wind"] = -min(
            excess * cfg.points_per_mph_over_threshold, cfg.max_wind_points
        )
    if (
        context.precipitation_chance is not None
        and context.precipitation_chance >= 0.5
    ):
        adjustments["precipitation"] = -cfg.precipitation_points
    if context.temperature_f is not None and context.temperature_f <= cfg.extreme_cold_temp_f:
        adjustments["extreme_cold"] = -cfg.extreme_cold_points
    if context.dome is None and context.wind_mph is None:
        flags.append("weather_unavailable")

    total = sum(adjustments.values())
    if total < -cfg.max_environmental_points:
        # Scale the components back proportionally so the cap is visible in
        # every line item rather than silently applied to the sum.
        scale = cfg.max_environmental_points / abs(total)
        adjustments = {name: value * scale for name, value in adjustments.items()}
        flags.append("environmental_adjustment_capped")
    return adjustments


def _game_script_adjustment(
    game: GameInputs,
    features: MatchupFeatures,
    cfg: TotalsConfig,
    expected_margin: float | None,
) -> float:
    """Lopsided games drain clock and empty benches; both suppress scoring."""
    margin = expected_margin
    if margin is None:
        spread = game.market.spread_current or game.market.spread_open
        if spread is not None:
            favorite_spread = spread if features.favorite_is_home else -spread
            margin = -favorite_spread
    if margin is None:
        return 0.0
    excess = abs(margin) - cfg.game_script_margin_threshold
    if excess <= 0:
        return 0.0
    return -min(excess * cfg.game_script_points_per_margin, cfg.max_game_script_points)


def _side_probabilities(
    projected_total: float,
    market_total: float | None,
    cfg: TotalsConfig,
    flags: list[str],
) -> tuple[float | None, float | None, float | None]:
    """Over/under/push probabilities from the projection distribution."""
    if market_total is None:
        flags.append("no_market_total")
        return None, None, None
    if cfg.total_sigma <= 0:
        flags.append(cfg.push_flag)
        return None, None, None

    sigma = cfg.total_sigma
    if abs(market_total - round(market_total)) > 1e-9:
        # Half-point line: no push is possible.
        over = 1.0 - normal_cdf((market_total - projected_total) / sigma)
        return clamp(over, 0.0, 1.0), clamp(1.0 - over, 0.0, 1.0), 0.0

    # Integer line: derive the push band across the +/-0.5 ladder rather than
    # pretending the market is two-way.
    flags.append("integer_line_push_derived")
    upper = normal_cdf((market_total + 0.5 - projected_total) / sigma)
    lower = normal_cdf((market_total - 0.5 - projected_total) / sigma)
    push = clamp(upper - lower, 0.0, 1.0)
    over = clamp(1.0 - upper, 0.0, 1.0)
    under = clamp(lower, 0.0, 1.0)
    return over, under, push


def _recommend(
    edge: float | None,
    over_p: float | None,
    under_p: float | None,
    cfg: TotalsConfig,
) -> str:
    """OVER, UNDER, or PASS. PASS is the default, not the fallback."""
    if edge is None or over_p is None or under_p is None:
        return PASS
    if abs(edge) < cfg.min_total_edge:
        return PASS
    if edge > 0 and over_p > under_p:
        return OVER
    if edge < 0 and under_p > over_p:
        return UNDER
    return PASS


def _classify(
    game: GameInputs,
    features: MatchupFeatures,
    drives: float,
    home_ppd: float,
    away_ppd: float,
    adjustments: dict[str, float],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Spec Part 8: name the repeatable situation, not just the number."""
    home, away = game.home, game.away
    archetypes: list[str] = []
    drivers: list[str] = []

    zs = [z for z in (_tempo_z(home), _tempo_z(away)) if z is not None]
    if len(zs) == 2 and all(z >= _FAST_TEMPO_Z for z in zs):
        archetypes.append("both_teams_play_fast")
        drivers.append(f"Both offenses play fast ({drives:.1f} projected drives each)")
    if len(zs) == 2 and all(z <= _SLOW_TEMPO_Z for z in zs):
        archetypes.append("slow_tempo")
        drivers.append(f"Both offenses play slow ({drives:.1f} projected drives each)")

    for offense, defense, ppd in ((home, away, home_ppd), (away, home, away_ppd)):
        off_ppd = offense.efficiency.offense_points_per_drive
        def_ppd = defense.efficiency.defense_points_per_drive
        if (
            off_ppd is not None
            and def_ppd is not None
            and off_ppd >= _ELITE_OFFENSE_PPD
            and def_ppd >= _WEAK_DEFENSE_PPD
        ):
            archetypes.append("elite_offense_vs_weak_defense")
            drivers.append(
                f"{offense.team}'s offense ({off_ppd:.2f} pts/drive) against "
                f"{defense.team}'s defense ({def_ppd:.2f} allowed) projects {ppd:.2f}"
            )
        if (
            off_ppd is not None
            and def_ppd is not None
            and def_ppd <= _STRONG_DEFENSE_PPD
        ):
            archetypes.append("strong_defensive_front")
            drivers.append(f"{defense.team} allows only {def_ppd:.2f} points per drive")

        rate = offense.efficiency.rush_rate
        if rate is not None and rate >= _RUN_HEAVY_RATE:
            archetypes.append("run_heavy_offense")
            drivers.append(f"{offense.team} runs on {rate:.0%} of snaps")

        grade = offense.roster.qb_grade
        if grade is not None and grade <= _POOR_QB_GRADE:
            archetypes.append("poor_quarterback_play")
            drivers.append(f"{offense.team} quarterback grade {grade:.0f}")

        ol = offense.efficiency.offensive_line_grade
        if ol is not None and ol <= _WEAK_OL_GRADE:
            archetypes.append("weak_offensive_line")
        front = defense.efficiency.defensive_front_grade
        if front is not None and front >= _STRONG_FRONT_GRADE:
            archetypes.append("strong_defensive_front")

        explosive = offense.efficiency.offense_explosiveness
        if explosive is not None and explosive >= _HIGH_EXPLOSIVENESS:
            archetypes.append("high_explosive_environment")
        if explosive is not None and explosive <= _LOW_EXPLOSIVENESS:
            archetypes.append("low_explosive_environment")

    underdog = game.opponent_of(features.favorite)
    dog_ppd = underdog.efficiency.offense_points_per_drive
    if dog_ppd is not None:
        if dog_ppd >= 2.5:
            archetypes.append("capable_underdog_offense")
            drivers.append(f"{underdog.team} can still move the ball ({dog_ppd:.2f} pts/drive)")
        elif dog_ppd <= 1.4:
            archetypes.append("underdog_unlikely_to_score")
            drivers.append(f"{underdog.team} projects only {dog_ppd:.2f} points per drive")

    for name, value in adjustments.items():
        if name == "wind":
            archetypes.append("wind_suppressed")
            drivers.append(f"Wind removes {abs(value):.1f} points from the projection")
        elif name == "precipitation":
            archetypes.append("wet_weather")
            drivers.append(f"Precipitation removes {abs(value):.1f} points")
        elif name == "extreme_cold":
            archetypes.append("extreme_cold")
            drivers.append(f"Cold removes {abs(value):.1f} points")
        elif name == "game_script":
            archetypes.append("lopsided_game_script")
            drivers.append(
                f"Lopsided script (clock drain, reserves) removes {abs(value):.1f} points"
            )

    return tuple(dict.fromkeys(archetypes)), tuple(dict.fromkeys(drivers))


def _confidence(
    game: GameInputs,
    features: MatchupFeatures,
    tempo_known: bool,
    edge: float | None,
    archetypes: tuple[str, ...],
    recommendation: str,
    flags: list[str],
) -> float:
    """Totals confidence, on the same 0-100 scale as the moneyline models."""
    score = 100.0
    score -= 55.0 * (1.0 - features.critical_coverage.ratio)
    score -= 15.0 * (1.0 - features.coverage.ratio)
    if not tempo_known:
        score -= 12.0
    if game.context.weather_forecast_stable is False:
        score -= 10.0
    if game.context.dome is None and game.context.wind_mph is None:
        score -= 6.0
    if game.market.book_count is not None and game.market.book_count < 3:
        score -= 6.0
        flags.append("thin_totals_market")

    over_tags = {
        "both_teams_play_fast",
        "elite_offense_vs_weak_defense",
        "capable_underdog_offense",
        "high_explosive_environment",
    }
    under_tags = {
        "slow_tempo",
        "run_heavy_offense",
        "strong_defensive_front",
        "poor_quarterback_play",
        "weak_offensive_line",
        "wind_suppressed",
        "wet_weather",
        "extreme_cold",
        "lopsided_game_script",
        "underdog_unlikely_to_score",
        "low_explosive_environment",
    }
    present_over = len(over_tags & set(archetypes))
    present_under = len(under_tags & set(archetypes))
    if recommendation == OVER and present_under > present_over:
        # The number says over, the situation says under. Say so rather than
        # quietly averaging the disagreement away.
        score -= 12.0
        flags.append("archetypes_contradict_recommendation")
    elif recommendation == UNDER and present_over > present_under:
        score -= 12.0
        flags.append("archetypes_contradict_recommendation")

    return clamp(score, 0.0, 100.0)
