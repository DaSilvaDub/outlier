"""Team Mismatch Score, 0-100 (spec Part 2).

The score answers one question: *how structurally lopsided is this matchup?*
It is deliberately not a win probability and never becomes one -- ``safety.py``
owns that conversion. Keeping them apart is what allows a 40-point favorite
over a hopeless opponent and a 40-point favorite over an opponent with a real
quarterback to score differently even when both are priced at -2500.

Each of the fourteen components returns either a 0-100 sub-score (50 = neutral)
or ``None`` when its inputs are absent. The composite is a weighted mean over
*available* components only, renormalized by the weight that was actually
observed. If observed weight falls below ``min_weight_coverage`` the game is
left unscored rather than scored on a fragment.

Spec Part 2 asks for particular attention to favorites with structural
advantages rather than merely high rankings; ``structural_advantages`` names
the components that cleared the threshold, so a reader can tell a team that is
dominant in the trenches from a team that is simply well-regarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from outlier_scrapers.ncaa.config import MismatchConfig
from outlier_scrapers.ncaa.features import (
    EPA_SCALE,
    MatchupFeatures,
    POWER_RATING_SCALE,
    TALENT_SCALE,
)
from outlier_scrapers.ncaa.stats import clamp, expit, normal_cdf

__all__ = [
    "COMPONENT_LABELS",
    "MismatchScore",
    "score_mismatch",
]

#: Human-readable labels, keyed to spec Part 2's A-N enumeration.
COMPONENT_LABELS: Mapping[str, str] = {
    "overall_quality_gap": "A. Overall team-quality gap",
    "quarterback_advantage": "B. Quarterback advantage",
    "trenches": "C. O-line vs defensive-front mismatch",
    "offensive_efficiency": "D. Offensive efficiency differential",
    "defensive_efficiency": "E. Defensive efficiency differential",
    "explosiveness": "F. Explosive-play advantage",
    "depth": "G. Depth advantage",
    "roster_talent": "H. Roster-talent advantage",
    "coaching": "I. Coaching advantage",
    "home_field": "J. Home-field advantage",
    "injury": "K. Injury advantage/disadvantage",
    "sos_adjusted": "L. SOS-adjusted performance",
    "game_script_stability": "M. Expected game-script stability",
    "underperformance_survival": "N. Survives an underperformance",
}

#: Divisor mapping each component's native differential onto logistic units.
_COMPONENT_SCALES: Mapping[str, float] = {
    "overall_quality_gap": POWER_RATING_SCALE,
    "quarterback_advantage": 1.0,
    "trenches": 1.0,
    "offensive_efficiency": EPA_SCALE,
    "defensive_efficiency": EPA_SCALE,
    "explosiveness": 0.15,
    "depth": 1.0,
    "roster_talent": TALENT_SCALE,
    "coaching": 1.0,
    "home_field": 1.0,
    "injury": 2.0,
    "sos_adjusted": 5.0,
    "game_script_stability": 1.0,
    "underperformance_survival": 1.0,
}

#: Components whose presence at a high sub-score constitutes a *structural*
#: rather than reputational advantage (spec Part 2's closing list).
_STRUCTURAL_COMPONENTS = (
    "trenches",
    "quarterback_advantage",
    "offensive_efficiency",
    "defensive_efficiency",
    "explosiveness",
    "depth",
    "roster_talent",
    "home_field",
)


@dataclass(frozen=True)
class MismatchScore:
    """Composite score plus the decomposition that justifies it."""

    game_id: str
    favorite: str
    underdog: str
    #: ``None`` when observed weight fell below the coverage floor.
    score: float | None
    components: Mapping[str, float]
    missing_components: tuple[str, ...]
    observed_weight: float
    structural_advantages: tuple[str, ...]
    flags: tuple[str, ...]

    @property
    def scored(self) -> bool:
        return self.score is not None


def _sub_score(value: float | None, scale: float) -> float | None:
    """Map a differential onto 0-100 with 50 = neutral.

    A logistic squash rather than a linear rescale, because the marginal
    meaning of an extra unit of edge falls off: the difference between a
    +1-sd and a +2-sd trenches edge matters far more than between +4 and +5.
    """
    if value is None or scale <= 0:
        return None
    return 100.0 * expit(value / scale)


def _home_field_value(features: MatchupFeatures) -> float | None:
    if features.neutral_site:
        return 0.0
    return 1.0 if features.favorite_is_home else -1.0


def _game_script_value(features: MatchupFeatures) -> float | None:
    """Stability of the expected script: protecting leads and not gifting the
    opponent short fields."""
    parts = [
        (features.lead_protection_edge, 0.08),
        (features.turnover_edge, 0.02),
    ]
    scaled = [v / s for v, s in parts if v is not None and s > 0]
    if not scaled:
        return None
    return sum(scaled) / len(scaled)


def _survival_value(features: MatchupFeatures, margin_sigma: float) -> float | None:
    """Component N: does the cushion survive a bad day?

    Read the quality gap as an expected neutral-field margin and ask for the
    win probability *after* removing one standard deviation of the favorite's
    own performance. A 40-point favorite still wins comfortably; a 6-point
    favorite does not. Returned in logistic units so it composes with the rest.
    """
    if features.quality_gap is None or margin_sigma <= 0:
        return None
    shocked_margin = features.quality_gap - margin_sigma
    survival = normal_cdf(shocked_margin / margin_sigma)
    # Map a probability back onto the shared logistic axis: 0.5 -> 0.0.
    return (survival - 0.5) * 4.0


def score_mismatch(
    features: MatchupFeatures,
    config: MismatchConfig | None = None,
    *,
    margin_sigma: float = 16.0,
) -> MismatchScore:
    """Compute the 0-100 Team Mismatch Score for ``features``."""
    cfg = config or MismatchConfig()
    raw_values: dict[str, float | None] = {
        "overall_quality_gap": features.quality_gap,
        "quarterback_advantage": features.quarterback_edge,
        "trenches": features.trenches_edge,
        "offensive_efficiency": features.offense_epa_edge,
        "defensive_efficiency": features.defense_epa_edge,
        "explosiveness": features.explosiveness_edge,
        "depth": features.depth_edge,
        "roster_talent": features.talent_edge,
        "coaching": features.coaching_edge,
        "home_field": _home_field_value(features),
        "injury": features.injury_edge,
        "sos_adjusted": features.opponent_adjusted_edge
        if features.opponent_adjusted_edge is not None
        else features.sos_edge,
        "game_script_stability": _game_script_value(features),
        "underperformance_survival": _survival_value(features, margin_sigma),
    }

    components: dict[str, float] = {}
    missing: list[str] = []
    observed_weight = 0.0
    weighted_total = 0.0

    for name, weight in cfg.weights.items():
        scale = _COMPONENT_SCALES.get(name, 1.0) * max(cfg.component_scale, 1e-9)
        sub = _sub_score(raw_values.get(name), scale)
        if sub is None:
            missing.append(name)
            continue
        components[name] = round(sub, 3)
        observed_weight += weight
        weighted_total += weight * sub

    total_weight = sum(cfg.weights.values())
    coverage = observed_weight / total_weight if total_weight > 0 else 0.0

    flags: list[str] = list(features.flags)
    score: float | None = None
    if coverage < cfg.min_weight_coverage:
        flags.append("mismatch_insufficient_coverage")
    else:
        score = round(clamp(weighted_total / observed_weight, 0.0, 100.0), 3)

    structural = tuple(
        name
        for name in _STRUCTURAL_COMPONENTS
        if components.get(name, 0.0) >= cfg.structural_advantage_threshold
    )
    if score is not None and not structural:
        # A high score with no structural component behind it is exactly the
        # "highly ranked but not structurally dominant" case spec Part 2 warns
        # about, and downstream tiering treats it more cautiously.
        flags.append("no_structural_advantage")

    return MismatchScore(
        game_id=features.game_id,
        favorite=features.favorite,
        underdog=features.underdog,
        score=score,
        components=components,
        missing_components=tuple(missing),
        observed_weight=round(coverage, 4),
        structural_advantages=structural,
        flags=tuple(dict.fromkeys(flags)),
    )
