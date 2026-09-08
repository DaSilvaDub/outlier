"""Slate orchestration: raw inputs to a ranked, parlay-ready analysis.

Stage order, which is also the dependency order:

    game inputs
      -> matchup features        (features.build_matchup_features)
      -> mismatch score          (mismatch.score_mismatch)
      -> win probability         (safety.estimate_win_probability)
      -> calibration             (calibrate.Calibrator, when promoted)
      -> price comparison        (value.assess_value)
      -> tier admission          (tiers.assign_tier)
      -> parlay optimizer        (parlay.build_parlay_ladder)
      -> totals model            (totals.project_total)

The totals model deliberately hangs off the feature layer rather than the
moneyline chain: it consumes the same inputs but shares none of the moneyline
machinery, so a change to tiering cannot silently move a totals number.

Calibration enters at exactly one point. A promoted :class:`Calibrator` is
applied to the safety model's probability *before* it reaches the value model,
tiering, or the optimizer, so every downstream consumer sees one probability
and there is no path by which a calibrated and an uncalibrated number can be
compared against each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from outlier_scrapers.ncaa.calibrate import Calibrator
from outlier_scrapers.ncaa.config import NcaaConfig
from outlier_scrapers.ncaa.features import GameInputs, MatchupFeatures, build_matchup_features
from outlier_scrapers.ncaa.mismatch import MismatchScore, score_mismatch
from outlier_scrapers.ncaa.parlay import Parlay, ParlayLeg, build_parlay_ladder, fragility
from outlier_scrapers.ncaa.parlay import ParlayFragility
from outlier_scrapers.ncaa.safety import WinProbability, estimate_win_probability
from outlier_scrapers.ncaa.tiers import TIER_CORE, TIER_SUPPORTING, TierAssignment, assign_tier
from outlier_scrapers.ncaa.totals import TotalsProjection, project_total
from outlier_scrapers.ncaa.value import ValueAssessment, assess_value

__all__ = ["GameAssessment", "SlateAnalysis", "analyze_slate", "build_legs"]


@dataclass(frozen=True)
class GameAssessment:
    """Every model's view of one game, joined."""

    game_id: str
    features: MatchupFeatures
    mismatch: MismatchScore
    win: WinProbability
    value: ValueAssessment
    tier: TierAssignment
    total: TotalsProjection
    #: Correlation tags derived for this game, carried onto any parlay leg.
    risk_tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def parlay_eligible(self) -> bool:
        return (
            self.tier.parlay_eligible
            and self.win.model_prob is not None
            and self.value.decimal_price is not None
        )


@dataclass(frozen=True)
class SlateAnalysis:
    """The complete output for one slate."""

    label: str
    games_evaluated: int
    assessments: tuple[GameAssessment, ...]
    parlays: Mapping[int, Parlay]
    best_parlay: Parlay | None
    fragilities: Mapping[int, ParlayFragility]
    calibration_method: str
    flags: tuple[str, ...]

    @property
    def core(self) -> tuple[GameAssessment, ...]:
        return tuple(a for a in self.assessments if a.tier.tier == TIER_CORE)

    @property
    def supporting(self) -> tuple[GameAssessment, ...]:
        return tuple(a for a in self.assessments if a.tier.tier == TIER_SUPPORTING)

    @property
    def avoid(self) -> tuple[GameAssessment, ...]:
        return tuple(a for a in self.assessments if not a.tier.parlay_eligible)

    @property
    def actionable_totals(self) -> tuple[TotalsProjection, ...]:
        return tuple(
            sorted(
                (a.total for a in self.assessments if a.total.actionable),
                key=lambda t: (-abs(t.total_edge or 0.0), -t.confidence),
            )
        )


def _risk_tags(game: GameInputs) -> dict[str, str]:
    """Shared-exposure tags used by the parlay correlation model."""
    tags: dict[str, str] = {}
    context = game.context
    if context.conference_game and game.home.efficiency.team:
        # Conference is not carried on the inputs directly; a caller with real
        # conference data should override these tags. Using the flag alone at
        # least groups conference games against non-conference ones.
        tags["conference"] = "conference_game"
    if context.kickoff_utc:
        tags["kickoff_window"] = context.kickoff_utc[:13]
    if context.weather_forecast_stable is False or (
        context.wind_mph is not None and context.wind_mph >= 15.0
    ):
        # Games sharing a date and unsettled weather are plausibly under one
        # system; a caller with real venue geography should supply a better key.
        tags["weather_system"] = (context.kickoff_utc or "")[:10] or "unknown"
    if game.home.roster.injury_report_complete is False or (
        game.away.roster.injury_report_complete is False
    ):
        tags["injury_news_regime"] = "unresolved"
    for name, source in game.sources.items():
        if name == "ratings":
            tags["shared_data_source"] = source
    return tags


def assess_game(
    game: GameInputs,
    config: NcaaConfig | None = None,
    *,
    calibrator: Calibrator | None = None,
) -> GameAssessment:
    """Run every model over one game."""
    cfg = config or NcaaConfig()
    features = build_matchup_features(game)
    mismatch = score_mismatch(features, cfg.mismatch, margin_sigma=cfg.margin.margin_sigma)
    win = estimate_win_probability(game, features, cfg)

    if calibrator is not None and calibrator.method != "identity" and win.model_prob is not None:
        calibrated = calibrator.apply(win.model_prob)
        win = replace(
            win,
            model_prob=round(calibrated, 6),
            flags=tuple(dict.fromkeys(win.flags + (f"calibrated_{calibrator.method}",))),
        )

    value = assess_value(game, features, win, cfg)
    tier = assign_tier(win, value, mismatch, cfg.tiers)
    total = project_total(game, features, cfg, expected_margin=win.expected_margin)
    return GameAssessment(
        game_id=game.game_id,
        features=features,
        mismatch=mismatch,
        win=win,
        value=value,
        tier=tier,
        total=total,
        risk_tags=_risk_tags(game),
    )


def build_legs(assessments: Sequence[GameAssessment]) -> list[ParlayLeg]:
    """Convert tier-eligible assessments into parlay legs."""
    legs: list[ParlayLeg] = []
    for assessment in assessments:
        if not assessment.parlay_eligible:
            continue
        win = assessment.win
        value = assessment.value
        assert win.model_prob is not None and value.decimal_price is not None
        legs.append(
            ParlayLeg(
                game_id=assessment.game_id,
                team=win.favorite,
                opponent=win.underdog,
                model_prob=win.model_prob,
                market_prob_fair=value.market_prob_fair,
                decimal_price=value.decimal_price,
                confidence=win.confidence,
                upset_risk=win.upset_risk,
                tier=assessment.tier.tier,
                tags=dict(assessment.risk_tags),
                primary_upset_path=win.primary_upset_path,
            )
        )
    return legs


def analyze_slate(
    games: Sequence[GameInputs],
    config: NcaaConfig | None = None,
    *,
    label: str = "",
    calibrator: Calibrator | None = None,
) -> SlateAnalysis:
    """Run the full pipeline over a slate."""
    cfg = config or NcaaConfig()
    assessments = tuple(assess_game(game, cfg, calibrator=calibrator) for game in games)
    legs = build_legs(assessments)

    flags: list[str] = []
    if not legs:
        flags.append("no_parlay_eligible_legs")

    ladder = build_parlay_ladder(legs, cfg.parlay)
    if legs and not ladder:
        # Not an error. The optimizer refusing every combination is the
        # "do not force a play" rule doing its job.
        flags.append("no_feasible_parlay")

    best = None
    if ladder:
        if cfg.parlay.objective == "probability":
            best = max(ladder.values(), key=lambda p: p.p_correlated)
        elif cfg.parlay.objective == "ev":
            best = max(ladder.values(), key=lambda p: p.ev_per_unit)
        else:
            best = max(ladder.values(), key=lambda p: p.kelly_growth)

    fragilities = {size: fragility(parlay, cfg.parlay) for size, parlay in ladder.items()}

    unscored = sum(1 for a in assessments if a.win.model_prob is None)
    if unscored:
        flags.append(f"unscored_games:{unscored}")

    return SlateAnalysis(
        label=label,
        games_evaluated=len(games),
        assessments=assessments,
        parlays=ladder,
        best_parlay=best,
        fragilities=fragilities,
        calibration_method=(calibrator.method if calibrator else "identity"),
        flags=tuple(flags),
    )
