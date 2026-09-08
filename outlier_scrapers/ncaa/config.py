"""Configuration for the NCAA football pipeline.

Every threshold named in the operating spec is a value here, not a literal
buried in a model. The spec is explicit that its starting numbers ("CORE at
>= 90% model probability", "confidence >= 85") are a *starting framework* to be
recalibrated, and a threshold you cannot move without editing model code will
never actually be recalibrated.

Defaults live in code so the pipeline runs with no config file present;
``config/ncaa_pipeline.json`` overrides them. Unknown keys raise rather than
being ignored, because a silently-ignored typo in a threshold file is
indistinguishable from a threshold that was never applied.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from functools import lru_cache
from typing import Any, Mapping, TypeVar, get_type_hints

from outlier_scrapers import paths

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "BacktestConfig",
    "ConfidenceConfig",
    "MarginConfig",
    "MismatchConfig",
    "NcaaConfig",
    "ParlayConfig",
    "PenaltyConfig",
    "TierConfig",
    "TotalsConfig",
    "load_config",
]

DEFAULT_CONFIG_PATH = paths.PROJECT_ROOT / "config" / "ncaa_pipeline.json"

T = TypeVar("T")


@dataclass(frozen=True)
class MarginConfig:
    """Ratings -> expected margin -> win probability."""

    #: Standard deviation of actual margin around a well-specified projection.
    #: ~16 points is the long-run FBS figure; NFL sits near 13.5 because its
    #: talent distribution is far narrower. This single number sets how fast
    #: probability saturates, so it is the first thing to recalibrate.
    margin_sigma: float = 16.0
    #: Generic FBS home-field edge in points. Applied to the home side only.
    home_field_points: float = 2.4
    #: Points per 1000 miles of travel for the away side.
    travel_points_per_1000_miles: float = 0.4
    #: Points per hour of time-zone shift against the traveling side.
    timezone_points_per_hour: float = 0.3
    #: Points per day of rest advantage, capped by ``max_rest_points``.
    rest_points_per_day: float = 0.15
    max_rest_points: float = 1.5
    #: Altitude edge for the home side when the venue is materially elevated.
    altitude_points: float = 1.0
    altitude_threshold_feet: float = 4000.0
    #: Weight on the market's spread-implied probability when blending it with
    #: the fundamental estimate. 0.0 runs the model market-free. See
    #: ``safety.py`` for why this is a spread signal and never a moneyline one.
    market_spread_weight: float = 0.55
    #: Divergence (in probability) between fundamental and spread estimates
    #: beyond which the row is flagged for review.
    divergence_flag_threshold: float = 0.08


@dataclass(frozen=True)
class MismatchConfig:
    """Weights for the 0-100 Team Mismatch Score components (spec Part 2)."""

    weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "overall_quality_gap": 0.15,
            "quarterback_advantage": 0.12,
            "trenches": 0.10,
            "offensive_efficiency": 0.09,
            "defensive_efficiency": 0.09,
            "explosiveness": 0.05,
            "depth": 0.05,
            "roster_talent": 0.07,
            "coaching": 0.05,
            "home_field": 0.05,
            "injury": 0.06,
            "sos_adjusted": 0.05,
            "game_script_stability": 0.04,
            "underperformance_survival": 0.03,
        }
    )
    #: Scale (in the component's native differential units) mapped onto one
    #: logistic unit. Larger scale => the component saturates more slowly.
    component_scale: float = 1.0
    #: Minimum share of total weight that must have data before a Mismatch
    #: Score is emitted at all. Below this the game is unscored, not guessed.
    min_weight_coverage: float = 0.60
    #: Components at or above this sub-score count as structural advantages.
    structural_advantage_threshold: float = 72.0


@dataclass(frozen=True)
class PenaltyConfig:
    """Log-odds penalties applied to the favorite's win probability.

    Values are in logit units subtracted from the blended estimate. 0.10 shaves
    roughly one point of probability at p=0.90 and less at the extremes, which
    is the right shape: the same qualitative worry should matter more to an 85%
    favorite than to a 97% one.
    """

    quarterback_uncertain: float = 0.45
    quarterback_out: float = 0.85
    injury_cluster: float = 0.35
    weak_offensive_line: float = 0.20
    poor_turnover_profile: float = 0.18
    explosive_dependence: float = 0.15
    inconsistent_execution: float = 0.15
    poor_coaching: float = 0.12
    hostile_road_environment: float = 0.18
    rivalry_volatility: float = 0.15
    capable_opposing_quarterback: float = 0.20
    weather_uncertainty: float = 0.12
    look_ahead_spot: float = 0.12
    letdown_spot: float = 0.12
    short_week: float = 0.10
    #: Total penalty ceiling, so no stack of soft flags can collapse a genuine
    #: mismatch to a coin flip.
    max_total_penalty: float = 1.20


@dataclass(frozen=True)
class ConfidenceConfig:
    """How the 0-100 confidence score is assembled (spec Part 10).

    Confidence is *not* probability. It answers "how much should this number be
    trusted", and every driver below is a reason to trust it less.
    """

    base: float = 100.0
    #: Applied to the share of *critical* inputs that are absent. This is the
    #: dominant term: a game missing a power rating or a quarterback status is
    #: barely evaluated at all.
    critical_missing_penalty_per_point: float = 60.0
    #: Applied to the share of the full input surface that is absent. Small by
    #: design -- no production feed fills every optional field, so a large
    #: penalty here would just be a constant offset.
    missing_data_penalty_per_point: float = 12.0
    small_sample_penalty: float = 18.0
    small_sample_games: int = 4
    coaching_change_penalty: float = 8.0
    transfer_heavy_penalty: float = 6.0
    unstable_weather_penalty: float = 7.0
    stale_market_penalty: float = 10.0
    stale_market_hours: float = 24.0
    model_divergence_penalty: float = 12.0
    unresolved_quarterback_penalty: float = 15.0
    floor: float = 0.0


@dataclass(frozen=True)
class TierConfig:
    """CORE / SUPPORTING / AVOID admission (spec Part 4)."""

    core_min_win_prob: float = 0.90
    core_min_confidence: float = 85.0
    core_max_upset_risk: float = 30.0
    supporting_min_win_prob: float = 0.85
    supporting_min_confidence: float = 78.0
    supporting_max_upset_risk: float = 45.0
    #: Minimum probability edge over the devigged market for a leg to be
    #: considered priced efficiently enough to use. Negative values are allowed
    #: -- a very high-probability leg may be worth using at a small negative
    #: edge inside a parlay -- but the default requires the price to be fair.
    min_probability_edge: float = -0.005
    #: Red flags that disqualify a favorite from CORE regardless of numbers.
    core_blocking_flags: tuple[str, ...] = (
        "quarterback_out",
        "quarterback_uncertain",
        "injury_cluster",
        "unresolved_injury_report",
        "stale_market",
        # The model had no independent view and simply restated the spread.
        # Whatever edge that produces is market structure, not model skill.
        "fundamental_unavailable",
        "no_win_probability_source",
    )
    #: Require a component of the Mismatch Score to clear the structural
    #: threshold before a leg reaches CORE. Spec Part 2 asks for "structural
    #: advantages rather than teams that are merely highly ranked".
    core_requires_structural_advantage: bool = True


@dataclass(frozen=True)
class ParlayConfig:
    """Parlay construction and the leg-rejection rule (spec Parts 5-6)."""

    min_legs: int = 3
    max_legs: int = 10
    #: Objective used to rank parlays.
    #: ``kelly_growth`` (default) is concave in payout and therefore refuses
    #: legs added only to lengthen the price; ``probability`` maximizes the
    #: joint win probability subject to the EV floor; ``ev`` maximizes raw EV
    #: and is provided only for comparison -- it is the behaviour the spec
    #: explicitly warns against.
    objective: str = "kelly_growth"
    #: A parlay is not emitted at all if its correlated win probability falls
    #: below this. Keyed by leg count; missing counts fall back to ``default``.
    min_parlay_probability: Mapping[str, float] = field(
        default_factory=lambda: {
            "3": 0.70,
            "4": 0.62,
            "5": 0.55,
            "6": 0.48,
            "7": 0.42,
            "8": 0.36,
            "9": 0.30,
            "10": 0.25,
            "default": 0.25,
        }
    )
    #: A candidate leg is rejected if adding it drops the parlay's win
    #: probability by more than this multiplicative share -- 0.10 means a leg
    #: must behave like a >=90% proposition once correlation is accounted for.
    #: Applied both when considering an extra leg and as a feasibility test on
    #: every leg already in a parlay, so the ladder cannot contain a leg the
    #: marginal rule would refuse.
    #:
    #: Keep this aligned with ``TierConfig.core_min_win_prob``: the two express
    #: the same judgement at different levels, and the parlay bound is the
    #: binding one. Setting a 0.06 decay limit alongside a 0.90 CORE floor
    #: silently makes every 90-93% leg unusable, so the tier admits legs the
    #: optimizer can never place. The default relationship is
    #: ``decay ~= 1 - core_min_win_prob``.
    max_probability_decay_per_leg: float = 0.10
    #: A candidate leg is rejected if it lowers the objective at all. The
    #: tolerance exists only for floating-point noise.
    objective_tolerance: float = 1e-9
    #: Minimum EV per unit for the completed parlay.
    min_parlay_ev: float = 0.0
    #: Log-odds haircut applied to every leg before computing ``p_stressed``.
    #: This -- not a copula term -- is where model overconfidence belongs: it
    #: is a level shift affecting all legs at once, and routing it through the
    #: correlation matrix would *raise* the parlay's estimated probability.
    #: 0.20 logits costs a 90% leg about 1.8 points and a 97% leg about 0.6.
    calibration_haircut_logits: float = 0.20
    #: Correlation contributed by each shared risk tag, summed then capped.
    correlation_tags: Mapping[str, float] = field(
        default_factory=lambda: {
            # Residual *outcome* correlation between any two legs that this
            # pipeline has not tagged explicitly: shared officiating
            # environment, week-level scheduling effects, common travel
            # disruption. Deliberately small, and not a model-risk term.
            "model": 0.02,
            "weather_system": 0.18,
            "conference": 0.05,
            "kickoff_window": 0.03,
            "injury_news_regime": 0.08,
            "shared_data_source": 0.04,
            "same_game": 0.90,
        }
    )
    max_pairwise_correlation: float = 0.85
    #: Gauss-Hermite nodes for the copula integral. 24 is exact to ~1e-10 for
    #: the smooth integrands here.
    quadrature_nodes: int = 24
    #: Exhaustive subset search is used while the combination count stays under
    #: this bound; beyond it the optimizer falls back to a deterministic beam
    #: search of width ``beam_width``.
    max_exhaustive_combinations: int = 200_000
    beam_width: int = 24
    #: Legs whose upset risk exceeds this never enter any parlay.
    max_leg_upset_risk: float = 45.0


@dataclass(frozen=True)
class TotalsConfig:
    """Possessions x points-per-drive totals model (spec Parts 7-8)."""

    #: League-average offensive drives per team per game.
    base_drives_per_team: float = 12.0
    #: Drives added per unit of combined tempo z-score.
    drives_per_tempo_unit: float = 0.9
    min_drives_per_team: float = 8.0
    max_drives_per_team: float = 15.0
    #: League-average points per drive.
    league_points_per_drive: float = 2.05
    #: Damping exponent on the multiplicative opponent adjustment. A pure
    #: product (``alpha = 1``) over-extrapolates badly when both sides are
    #: extreme: an elite offense against a poor defense projects further above
    #: league average than any real matchup lands. Damping is the standard
    #: correction and 0.75 is the usual starting value -- it must be fitted
    #: against settled games before the projections are trusted.
    opponent_adjustment_damping: float = 0.75
    #: Physical bounds on projected points per drive. Seven is the ceiling a
    #: drive can produce; nothing realistic approaches it as an average.
    min_points_per_drive: float = 0.4
    max_points_per_drive: float = 4.5
    #: Ceiling on the summed environmental deduction, so a game that is windy
    #: *and* wet *and* cold is not triple-charged into an implausible total.
    max_environmental_points: float = 8.0
    #: Standard deviation of actual game total around the projection.
    total_sigma: float = 13.5
    #: Wind speed above which passing efficiency is degraded, and the points
    #: removed from the projected total per mph beyond it.
    wind_threshold_mph: float = 15.0
    points_per_mph_over_threshold: float = 0.35
    max_wind_points: float = 7.0
    precipitation_points: float = 2.0
    extreme_cold_points: float = 1.5
    extreme_cold_temp_f: float = 25.0
    #: Points removed for a lopsided game script (favorite drains clock, backups
    #: play), scaled by projected margin beyond the threshold.
    game_script_margin_threshold: float = 21.0
    game_script_points_per_margin: float = 0.12
    max_game_script_points: float = 5.0
    #: Minimum absolute edge (projected minus market) before a side is
    #: recommended. Below this the recommendation is PASS.
    min_total_edge: float = 3.0
    #: Integer market totals can push. Consistent with the MLB house rule, a
    #: push probability must be derived from the +/-0.5 ladder or the row is
    #: flagged rather than priced.
    push_flag: str = "push_capable_no_prob"


@dataclass(frozen=True)
class BacktestConfig:
    """Validation buckets and gates (spec Part 12)."""

    probability_buckets: tuple[tuple[float, float], ...] = (
        (0.80, 0.849),
        (0.85, 0.899),
        (0.90, 0.924),
        (0.925, 0.949),
        (0.95, 1.0),
    )
    #: Minimum settled samples in a bucket before its hit rate is reported as
    #: meaningful rather than descriptive.
    min_bucket_sample: int = 50
    #: Chronological holdout share used by calibration fitting.
    holdout_fraction: float = 0.25


@dataclass(frozen=True)
class NcaaConfig:
    """Root configuration object."""

    devig_method: str = "power"
    margin: MarginConfig = field(default_factory=MarginConfig)
    mismatch: MismatchConfig = field(default_factory=MismatchConfig)
    penalties: PenaltyConfig = field(default_factory=PenaltyConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    tiers: TierConfig = field(default_factory=TierConfig)
    parlay: ParlayConfig = field(default_factory=ParlayConfig)
    totals: TotalsConfig = field(default_factory=TotalsConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=32)
def _resolved_types(cls: type) -> Mapping[str, Any]:
    """Field name -> real type object.

    ``dataclasses.fields()`` reports ``field.type`` as a *string* under
    ``from __future__ import annotations``, so ``is_dataclass(field.type)`` is
    always False and nested sections would be handed through as raw dicts.
    Resolving the annotations is what makes nested config objects work.
    """
    return get_type_hints(cls)


def _build(cls: type[T], payload: Mapping[str, Any], *, where: str) -> T:
    """Instantiate a (possibly nested) dataclass from a mapping, strictly."""
    if not is_dataclass(cls):  # pragma: no cover - guarded by call sites
        raise TypeError(f"{cls!r} is not a dataclass")
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ValueError(f"{where}: unknown configuration key(s): {', '.join(unknown)}")
    types = _resolved_types(cls)
    kwargs: dict[str, Any] = {}
    for name, value in payload.items():
        field_type = types.get(name)
        if isinstance(field_type, type) and is_dataclass(field_type) and isinstance(
            value, Mapping
        ):
            kwargs[name] = _build(field_type, value, where=f"{where}.{name}")
        elif isinstance(value, list):
            kwargs[name] = tuple(tuple(v) if isinstance(v, list) else v for v in value)
        else:
            kwargs[name] = value
    return cls(**kwargs)  # type: ignore[return-value]


def load_config(path: Path | str | None = None) -> NcaaConfig:
    """Load configuration, falling back to defaults when no file exists."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        return NcaaConfig()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{target}: configuration root must be an object")
    payload.pop("_comment", None)
    payload.pop("schema_version", None)
    try:
        return _build(NcaaConfig, payload, where=target.name)
    except ValueError as exc:
        raise ValueError(f"{target}: {exc}") from exc
