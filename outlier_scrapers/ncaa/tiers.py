"""CORE / SUPPORTING / AVOID admission for moneyline legs (spec Part 4).

The tier is the join point between the three models: a leg reaches CORE only
when the safety model says it is hard to lose, the value model says the price
is not absurd, and the mismatch model can point at a structural reason. Any one
of those failing is enough to keep it out.

The spec's own numbers ("CORE: >= 90% and confidence >= 85") are treated as
what it calls them -- a starting framework. They live in :class:`TierConfig`
and are meant to move once ``backtest.py`` has something to say about them.

AVOID is not a leftover bucket. It is the answer to spec Section C: favorites
that look safe by ranking, reputation, or spread but carry a specific reason
not to use them. Every AVOID carries that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from outlier_scrapers.ncaa.config import TierConfig
from outlier_scrapers.ncaa.mismatch import MismatchScore
from outlier_scrapers.ncaa.safety import WinProbability
from outlier_scrapers.ncaa.value import ValueAssessment

__all__ = ["TIER_AVOID", "TIER_CORE", "TIER_SUPPORTING", "TierAssignment", "assign_tier"]

TIER_CORE = "CORE"
TIER_SUPPORTING = "SUPPORTING"
TIER_AVOID = "AVOID"


@dataclass(frozen=True)
class TierAssignment:
    """Tier plus the reasoning a reader needs to disagree with it."""

    game_id: str
    favorite: str
    underdog: str
    tier: str
    primary_reason: str
    primary_concern: str | None
    blocking: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def parlay_eligible(self) -> bool:
        return self.tier in (TIER_CORE, TIER_SUPPORTING)


def assign_tier(
    win: WinProbability,
    value: ValueAssessment,
    mismatch: MismatchScore,
    config: TierConfig | None = None,
) -> TierAssignment:
    """Classify one favorite into CORE, SUPPORTING, or AVOID."""
    cfg = config or TierConfig()
    blocking: list[str] = []
    notes: list[str] = []

    if win.model_prob is None:
        return TierAssignment(
            game_id=win.game_id,
            favorite=win.favorite,
            underdog=win.underdog,
            tier=TIER_AVOID,
            primary_reason="No usable win-probability source",
            primary_concern="Neither ratings nor a market spread were available",
            blocking=("no_win_probability_source",),
            notes=tuple(win.flags),
        )

    probability = win.model_prob

    # Hard disqualifiers first: these keep a leg out of CORE no matter how
    # attractive its numbers look.
    observed_flags = set(win.flags) | set(win.penalties)
    for flag in cfg.core_blocking_flags:
        if flag in observed_flags:
            blocking.append(flag)

    edge = value.probability_edge
    if edge is not None and edge < cfg.min_probability_edge:
        blocking.append("price_below_edge_floor")
        notes.append(f"Probability edge {edge:+.3f} is below the {cfg.min_probability_edge:+.3f} floor")
    elif edge is None:
        notes.append("Market not priced; edge unknown")

    if cfg.core_requires_structural_advantage and not mismatch.structural_advantages:
        blocking.append("no_structural_advantage")

    meets_core = (
        probability >= cfg.core_min_win_prob
        and win.confidence >= cfg.core_min_confidence
        and win.upset_risk <= cfg.core_max_upset_risk
        and not blocking
    )
    meets_supporting = (
        probability >= cfg.supporting_min_win_prob
        and win.confidence >= cfg.supporting_min_confidence
        and win.upset_risk <= cfg.supporting_max_upset_risk
        and "price_below_edge_floor" not in blocking
    )

    if meets_core:
        tier = TIER_CORE
    elif meets_supporting:
        tier = TIER_SUPPORTING
    else:
        tier = TIER_AVOID

    reason = _primary_reason(win, value, mismatch, tier)
    concern = _primary_concern(win, value, blocking, cfg)

    if tier != TIER_CORE and probability >= cfg.core_min_win_prob:
        # The Section C case: the number looks like a CORE leg but something
        # else disqualified it. Say so explicitly.
        notes.append("Meets the CORE probability threshold but was held out")

    return TierAssignment(
        game_id=win.game_id,
        favorite=win.favorite,
        underdog=win.underdog,
        tier=tier,
        primary_reason=reason,
        primary_concern=concern,
        blocking=tuple(dict.fromkeys(blocking)),
        notes=tuple(dict.fromkeys(notes)),
    )


def _primary_reason(
    win: WinProbability,
    value: ValueAssessment,
    mismatch: MismatchScore,
    tier: str,
) -> str:
    if tier == TIER_AVOID:
        if win.model_prob is not None:
            return f"Model win probability {win.model_prob:.1%} with upset risk {win.upset_risk:.0f}"
        return "Insufficient basis to evaluate"
    if mismatch.structural_advantages:
        label = _STRUCTURAL_LABELS.get(
            mismatch.structural_advantages[0],
            mismatch.structural_advantages[0].replace("_", " "),
        )
        return f"Structural edge: {label}; model win probability {win.model_prob:.1%}"
    if value.probability_edge is not None and value.probability_edge > 0:
        return (
            f"Model win probability {win.model_prob:.1%}, "
            f"{value.probability_edge:+.1%} against the fair price"
        )
    return f"Model win probability {win.model_prob:.1%}"


#: Readable names for the structural components the mismatch model reports.
_STRUCTURAL_LABELS = {
    "trenches": "offensive line against the defensive front",
    "quarterback_advantage": "quarterback play",
    "offensive_efficiency": "offensive efficiency",
    "defensive_efficiency": "defensive efficiency",
    "explosiveness": "explosive-play generation",
    "depth": "roster depth",
    "roster_talent": "roster talent",
    "home_field": "home environment",
}


def _primary_concern(
    win: WinProbability,
    value: ValueAssessment,
    blocking: list[str],
    cfg: TierConfig,
) -> str | None:
    if blocking:
        return blocking[0].replace("_", " ")
    if win.primary_upset_path:
        return win.primary_upset_path
    if value.probability_edge is not None and value.probability_edge < 0:
        return f"Priced {abs(value.probability_edge):.1%} above the model"
    if win.confidence < cfg.core_min_confidence:
        return f"Confidence {win.confidence:.0f} below the CORE floor"
    return None
