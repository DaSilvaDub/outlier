"""Parlay construction, correlation modelling, and fragility analysis.

Spec Parts 5 and 6. The design requirement that shapes everything here is:

  The safest parlay should NOT automatically contain the largest possible
  number of legs. Determine the optimal number of legs based on the decay in
  combined win probability.

Two mechanisms enforce that.

**Correlation, with its sign stated correctly.** Legs are not independent, and
it is worth being precise about which way that cuts. For the event "every leg
wins", *positive* correlation makes the parlay **more** likely than the naive
product, not less: in the limit of perfectly correlated legs the parlay wins
whenever its weakest leg does. So ``p_correlated >= p_independent`` always, and
the difference is reported as ``correlation_lift`` rather than a drag. This is
not good news to be banked. Correlation concentrates risk: it fattens both
tails, so a slate of correlated legs wins together and loses together, and
across repeated play it removes the diversification that makes a sequence of
independent edges survivable. ``correlated_risk_tags`` names the shared
exposures so the concentration is visible even though the headline probability
moved up.

Pairwise correlations are assembled from shared risk tags, compressed to a
single common factor (see ``stats.rank_one_factor``), and integrated as a
one-factor Gaussian copula.

**Model risk is handled separately, and not as correlation.** The real hazard
in a multi-leg parlay is that one model produced every probability and that
model is overconfident. That is not an outcome correlation -- it is a level
shift in every leg at once, and pushing it through the copula would *raise*
the parlay's estimated probability, which is precisely backwards. It is
instead applied as a calibration haircut in log-odds space, and every parlay
reports ``p_stressed`` alongside ``p_correlated``: what the parlay is worth if
each leg is a little less certain than claimed.

**A concave objective.** The default ranking objective is expected log growth
at the Kelly stake, not expected value. EV is linear in the payout, so
maximizing it will happily bolt on a ninth leg to lengthen the price. Log
growth is concave, so a leg has to genuinely improve the probability-adjusted
position to be accepted. ``objective="ev"`` is available purely for comparison
and reproduces the behaviour the spec warns against.

Every parlay therefore records not only the legs it used but the legs it
*rejected* and why -- which is the auditable form of "this leg increased the
payout but damaged the probability profile too much".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Mapping, Sequence

from outlier_scrapers.ncaa.config import ParlayConfig
from outlier_scrapers.ncaa.odds import (
    decimal_to_american,
    ev_per_unit,
    kelly_growth,
    parlay_decimal,
)
from outlier_scrapers.ncaa.stats import (
    clamp,
    expit,
    logit,
    normal_cdf,
    normal_ppf,
    rank_one_factor,
    standard_normal_expectation_nodes,
)

__all__ = [
    "OBJECTIVES",
    "LegRejection",
    "Parlay",
    "ParlayFragility",
    "ParlayLeg",
    "build_parlay_ladder",
    "correlation_matrix",
    "evaluate_parlay",
    "fragility",
    "joint_probability",
    "marginal_leg_analysis",
    "stressed_probability",
]

OBJECTIVES = ("kelly_growth", "probability", "ev")

#: Pairs at or above this correlation are counted as a "correlated risk" in the
#: parlay summary the spec asks for.
_MATERIAL_CORRELATION = 0.10


@dataclass(frozen=True)
class ParlayLeg:
    """One moneyline selection, already through safety, value, and tiering."""

    game_id: str
    team: str
    opponent: str
    model_prob: float
    market_prob_fair: float | None
    decimal_price: float
    confidence: float
    upset_risk: float
    tier: str
    #: Shared-risk tags. Legs matching on a tag inherit that tag's correlation:
    #: e.g. ``{"weather_system": "gulf-coast-2026-09-12", "conference": "SEC"}``.
    tags: Mapping[str, str] = field(default_factory=dict)
    primary_upset_path: str | None = None

    @property
    def american_price(self) -> float | None:
        return decimal_to_american(self.decimal_price)


@dataclass(frozen=True)
class LegRejection:
    """A leg the optimizer declined to add, and the rule that stopped it."""

    team: str
    rule: str
    detail: str


@dataclass(frozen=True)
class ParlayFragility:
    """Spec Part 6 / Section E: which leg breaks this, and how likely is that?"""

    #: ``P(leg fails and every other leg wins)`` for each leg.
    sole_failure_prob: Mapping[str, float]
    #: Sole-failure probabilities normalized over all losing outcomes.
    blame_share: Mapping[str, float]
    #: Share of losing outcomes in which more than one leg failed. High values
    #: mean the parlay does not have a single point of failure -- it has a
    #: common one.
    multi_leg_failure_share: float
    most_likely_culprit: str | None
    explanation: str


@dataclass(frozen=True)
class Parlay:
    """A constructed parlay with everything spec Part 5 asks to be displayed."""

    legs: tuple[ParlayLeg, ...]
    p_independent: float
    p_correlated: float
    #: ``p_correlated - p_independent``. Positive: shared risk makes the
    #: all-win event more likely, while concentrating the downside.
    correlation_lift: float
    #: Joint probability after the calibration haircut -- the honest downside
    #: if every leg is slightly overconfident.
    p_stressed: float
    decimal_price: float
    american_price: float | None
    sportsbook_implied_prob: float
    fair_market_parlay_prob: float | None
    estimated_edge: float | None
    ev_per_unit: float
    kelly_growth: float
    risk_score: float
    average_confidence: float
    weakest_leg: str | None
    second_weakest_leg: str | None
    correlated_risk_count: int
    correlated_risk_tags: tuple[str, ...]
    max_pairwise_correlation: float
    rejected_legs: tuple[LegRejection, ...] = ()
    construction_reason: str = ""

    @property
    def leg_count(self) -> int:
        return len(self.legs)


def correlation_matrix(
    legs: Sequence[ParlayLeg],
    config: ParlayConfig | None = None,
) -> list[list[float]]:
    """Pairwise correlation built from shared risk tags.

    Every pair starts at the ``model`` floor, which represents residual
    *outcome* correlation this pipeline has not tagged explicitly -- shared
    officiating environments, week-level scheduling effects, common travel
    disruption. It is deliberately small. Model overconfidence is **not**
    represented here; see the calibration haircut in :func:`evaluate_parlay`.
    """
    cfg = config or ParlayConfig()
    size = len(legs)
    matrix = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]
    base = cfg.correlation_tags.get("model", 0.0)
    same_game = cfg.correlation_tags.get("same_game", 0.9)
    for i in range(size):
        for j in range(i + 1, size):
            rho = base
            if legs[i].game_id == legs[j].game_id:
                rho = max(rho, same_game)
            for tag, weight in cfg.correlation_tags.items():
                if tag in ("model", "same_game"):
                    continue
                left = legs[i].tags.get(tag)
                right = legs[j].tags.get(tag)
                if left is not None and left == right:
                    rho += weight
            rho = clamp(rho, 0.0, cfg.max_pairwise_correlation)
            matrix[i][j] = rho
            matrix[j][i] = rho
    return matrix


def joint_probability(
    legs: Sequence[ParlayLeg],
    config: ParlayConfig | None = None,
) -> tuple[float, float, list[float]]:
    """Return ``(p_independent, p_correlated, factor_loadings)``.

    One-factor Gaussian copula. Leg ``i`` wins when a latent standard normal
    ``V_i = a_i * M + sqrt(1 - a_i^2) * e_i`` falls below ``z_i = Phi^-1(p_i)``,
    so conditioning on the common factor ``M`` makes the legs independent and
    the joint probability is a single integral over ``M``.
    """
    cfg = config or ParlayConfig()
    if not legs:
        return 1.0, 1.0, []
    probabilities = [clamp(leg.model_prob, 1e-9, 1.0 - 1e-9) for leg in legs]
    independent = 1.0
    for p in probabilities:
        independent *= p
    if len(legs) == 1:
        return independent, independent, [0.0]

    loadings = rank_one_factor(correlation_matrix(legs, cfg))
    thresholds = [normal_ppf(p) for p in probabilities]
    nodes, weights = standard_normal_expectation_nodes(cfg.quadrature_nodes)

    total = 0.0
    for node, weight in zip(nodes, weights):
        conditional = 1.0
        for z, a in zip(thresholds, loadings):
            residual = math.sqrt(max(1.0 - a * a, 1e-12))
            conditional *= normal_cdf((z - a * node) / residual)
            if conditional == 0.0:
                break
        total += weight * conditional
    correlated = clamp(total, 0.0, 1.0)
    # With non-negative factor loadings the joint success probability is
    # provably at least the independent product, so this floor only absorbs
    # quadrature noise at extreme probabilities; it never masks a modelling
    # error. See the module docstring on the sign of the correlation effect.
    correlated = max(correlated, independent)
    return independent, correlated, loadings


def stressed_probability(
    legs: Sequence[ParlayLeg],
    config: ParlayConfig | None = None,
) -> float:
    """Joint probability after a calibration haircut on every leg.

    Answers the question a multi-leg parlay actually turns on: *what is this
    worth if the model is a little overconfident on each leg?* The haircut is
    subtracted in log-odds space, so it costs a 90% leg more probability than a
    97% one, matching how calibration error usually behaves.
    """
    cfg = config or ParlayConfig()
    haircut = cfg.calibration_haircut_logits
    if haircut <= 0.0 or not legs:
        _, correlated, _ = joint_probability(legs, cfg)
        return correlated
    stressed = [
        ParlayLeg(
            game_id=leg.game_id,
            team=leg.team,
            opponent=leg.opponent,
            model_prob=expit(logit(leg.model_prob) - haircut),
            market_prob_fair=leg.market_prob_fair,
            decimal_price=leg.decimal_price,
            confidence=leg.confidence,
            upset_risk=leg.upset_risk,
            tier=leg.tier,
            tags=leg.tags,
            primary_upset_path=leg.primary_upset_path,
        )
        for leg in legs
    ]
    _, correlated, _ = joint_probability(stressed, cfg)
    return correlated


def evaluate_parlay(
    legs: Sequence[ParlayLeg],
    config: ParlayConfig | None = None,
    *,
    rejected: Sequence[LegRejection] = (),
    reason: str = "",
) -> Parlay:
    """Compute every published figure for a set of legs."""
    cfg = config or ParlayConfig()
    legs = tuple(legs)
    independent, correlated, _ = joint_probability(legs, cfg)
    price = parlay_decimal(leg.decimal_price for leg in legs)
    implied = 1.0 / price if price > 0 else 1.0

    # A fair parlay probability is only meaningful when every leg was priced;
    # filling a gap with the model's own number would compare the model to
    # itself on that leg.
    fair_probs = [leg.market_prob_fair for leg in legs]
    fair_parlay: float | None = None
    if fair_probs and all(p is not None for p in fair_probs):
        product = 1.0
        for probability in fair_probs:
            assert probability is not None
            product *= probability
        fair_parlay = product

    matrix = correlation_matrix(legs, cfg)
    pairs = [
        (matrix[i][j], legs[i], legs[j])
        for i in range(len(legs))
        for j in range(i + 1, len(legs))
    ]
    material = [p for p in pairs if p[0] >= _MATERIAL_CORRELATION]
    tags = _shared_tags(material, cfg)

    ordered = sorted(legs, key=lambda leg: (leg.model_prob, -leg.upset_risk))
    lift = correlated - independent
    stressed = stressed_probability(legs, cfg)
    # Risk rises with the chance of losing, with the worst leg on the ticket,
    # with how much of the estimate rests on correlated exposure, and with how
    # far the parlay falls under the calibration stress.
    risk = clamp(
        80.0 * (1.0 - correlated)
        + 0.25 * max((leg.upset_risk for leg in legs), default=0.0)
        + 150.0 * lift
        + 100.0 * max(0.0, correlated - stressed),
        0.0,
        100.0,
    )

    return Parlay(
        legs=legs,
        p_independent=round(independent, 6),
        p_correlated=round(correlated, 6),
        correlation_lift=round(lift, 6),
        p_stressed=round(stressed, 6),
        decimal_price=round(price, 4),
        american_price=decimal_to_american(price),
        sportsbook_implied_prob=round(implied, 6),
        fair_market_parlay_prob=None if fair_parlay is None else round(fair_parlay, 6),
        estimated_edge=None if fair_parlay is None else round(correlated - implied, 6),
        ev_per_unit=round(ev_per_unit(correlated, price), 6),
        kelly_growth=round(kelly_growth(correlated, price), 8),
        risk_score=round(risk, 2),
        average_confidence=round(
            sum(leg.confidence for leg in legs) / len(legs) if legs else 0.0, 2
        ),
        weakest_leg=ordered[0].team if ordered else None,
        second_weakest_leg=ordered[1].team if len(ordered) > 1 else None,
        correlated_risk_count=len(material),
        correlated_risk_tags=tags,
        max_pairwise_correlation=round(max((p[0] for p in pairs), default=0.0), 4),
        rejected_legs=tuple(rejected),
        construction_reason=reason,
    )


def _shared_tags(
    material: Sequence[tuple[float, ParlayLeg, ParlayLeg]],
    cfg: ParlayConfig,
) -> tuple[str, ...]:
    found: list[str] = []
    for _, left, right in material:
        if left.game_id == right.game_id:
            found.append("same_game")
        for tag in cfg.correlation_tags:
            if tag in ("model", "same_game"):
                continue
            value = left.tags.get(tag)
            if value is not None and value == right.tags.get(tag):
                found.append(f"{tag}={value}")
    return tuple(dict.fromkeys(found))


def _objective_value(parlay: Parlay, objective: str) -> float:
    if objective == "kelly_growth":
        return parlay.kelly_growth
    if objective == "probability":
        return parlay.p_correlated
    if objective == "ev":
        return parlay.ev_per_unit
    raise ValueError(f"unknown parlay objective {objective!r}")


def _min_probability(cfg: ParlayConfig, leg_count: int) -> float:
    table = cfg.min_parlay_probability
    return float(table.get(str(leg_count), table.get("default", 0.0)))


def _feasible(parlay: Parlay, cfg: ParlayConfig) -> bool:
    """Floors every emitted parlay must clear.

    Beyond the probability and EV floors, *every* leg must survive the same
    marginal test :func:`marginal_leg_analysis` applies to a candidate: drop
    the leg, and the parlay's probability must not rise by more than
    ``max_probability_decay_per_leg``. Without this the ladder would happily
    return an eight-leg parlay containing a leg the marginal rule would have
    refused, and the rejection rule would be advisory rather than structural.
    """
    if parlay.p_correlated < _min_probability(cfg, parlay.leg_count):
        return False
    if parlay.ev_per_unit < cfg.min_parlay_ev:
        return False
    return _every_leg_survives_removal(parlay.legs, parlay.p_correlated, cfg)


def _every_leg_survives_removal(
    legs: Sequence[ParlayLeg],
    full_probability: float,
    cfg: ParlayConfig,
) -> bool:
    if len(legs) <= 1:
        return True
    for index in range(len(legs)):
        remainder = [leg for i, leg in enumerate(legs) if i != index]
        _, without, _ = joint_probability(remainder, cfg)
        if without <= 0.0:
            continue
        decay = (without - full_probability) / without
        if decay > cfg.max_probability_decay_per_leg:
            return False
    return True


def _eligible_legs(
    candidates: Iterable[ParlayLeg],
    cfg: ParlayConfig,
) -> tuple[list[ParlayLeg], list[LegRejection]]:
    kept: list[ParlayLeg] = []
    rejected: list[LegRejection] = []
    for leg in candidates:
        if leg.upset_risk > cfg.max_leg_upset_risk:
            rejected.append(
                LegRejection(
                    team=leg.team,
                    rule="max_leg_upset_risk",
                    detail=f"upset risk {leg.upset_risk:.0f} exceeds {cfg.max_leg_upset_risk:.0f}",
                )
            )
            continue
        kept.append(leg)
    return kept, rejected


def _best_subset(
    candidates: Sequence[ParlayLeg],
    size: int,
    cfg: ParlayConfig,
) -> Parlay | None:
    """Highest-objective feasible subset of exactly ``size`` legs."""
    if size <= 0 or size > len(candidates):
        return None
    if math.comb(len(candidates), size) <= cfg.max_exhaustive_combinations:
        best: Parlay | None = None
        best_value = -math.inf
        for combo in combinations(range(len(candidates)), size):
            parlay = evaluate_parlay([candidates[i] for i in combo], cfg)
            if not _feasible(parlay, cfg):
                continue
            value = _objective_value(parlay, cfg.objective)
            if value > best_value:
                best, best_value = parlay, value
        return best
    return _beam_search(candidates, size, cfg)


def _beam_search(
    candidates: Sequence[ParlayLeg],
    size: int,
    cfg: ParlayConfig,
) -> Parlay | None:
    """Deterministic beam search for large candidate pools.

    Subsets are grown in increasing index order so each is reached exactly
    once; at every depth the ``beam_width`` best partial subsets by objective
    are retained.
    """
    beam: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]
    for depth in range(size):
        expanded: list[tuple[float, tuple[int, ...]]] = []
        for _, indices in beam:
            start = indices[-1] + 1 if indices else 0
            for i in range(start, len(candidates)):
                combo = indices + (i,)
                parlay = evaluate_parlay([candidates[k] for k in combo], cfg)
                expanded.append((_objective_value(parlay, cfg.objective), combo))
        if not expanded:
            return None
        expanded.sort(key=lambda item: (-item[0], item[1]))
        beam = expanded[: cfg.beam_width]
        if depth == size - 1:
            for _, combo in beam:
                parlay = evaluate_parlay([candidates[k] for k in combo], cfg)
                if _feasible(parlay, cfg):
                    return parlay
            return None
    return None


def marginal_leg_analysis(
    parlay: Parlay,
    candidates: Sequence[ParlayLeg],
    config: ParlayConfig | None = None,
) -> tuple[list[tuple[ParlayLeg, Parlay]], list[LegRejection]]:
    """Would adding each remaining candidate improve this parlay?

    This is the rule the architecture calls the most important feature of the
    optimizer: a leg that lengthens the price is rejected unless it survives
    every probability test as well. Returns the accepted extensions and an
    explicit rejection reason for everything else.
    """
    cfg = config or ParlayConfig()
    used = {leg.game_id for leg in parlay.legs}
    accepted: list[tuple[ParlayLeg, Parlay]] = []
    rejected: list[LegRejection] = []
    baseline = _objective_value(parlay, cfg.objective)

    for leg in candidates:
        if leg.game_id in used:
            continue
        if leg.upset_risk > cfg.max_leg_upset_risk:
            rejected.append(
                LegRejection(
                    leg.team,
                    "max_leg_upset_risk",
                    f"upset risk {leg.upset_risk:.0f} exceeds {cfg.max_leg_upset_risk:.0f}",
                )
            )
            continue

        extended = evaluate_parlay(list(parlay.legs) + [leg], cfg)
        floor = _min_probability(cfg, extended.leg_count)
        if extended.p_correlated < floor:
            rejected.append(
                LegRejection(
                    leg.team,
                    "min_parlay_probability",
                    f"parlay probability would fall to {extended.p_correlated:.1%},"
                    f" below the {floor:.0%} floor for {extended.leg_count} legs",
                )
            )
            continue

        decay = (
            (parlay.p_correlated - extended.p_correlated) / parlay.p_correlated
            if parlay.p_correlated > 0
            else 1.0
        )
        if decay > cfg.max_probability_decay_per_leg:
            rejected.append(
                LegRejection(
                    leg.team,
                    "max_probability_decay_per_leg",
                    f"costs {decay:.1%} of the parlay's win probability, above the"
                    f" {cfg.max_probability_decay_per_leg:.1%} limit"
                    f" (price would lengthen to {extended.decimal_price:.2f})",
                )
            )
            continue

        if extended.ev_per_unit < cfg.min_parlay_ev:
            rejected.append(
                LegRejection(
                    leg.team,
                    "min_parlay_ev",
                    f"expected value would fall to {extended.ev_per_unit:+.3f}",
                )
            )
            continue

        value = _objective_value(extended, cfg.objective)
        if cfg.objective != "probability" and value < baseline - cfg.objective_tolerance:
            rejected.append(
                LegRejection(
                    leg.team,
                    "objective_not_improved",
                    f"lengthens the price to {extended.decimal_price:.2f} but lowers"
                    f" {cfg.objective} from {baseline:.5f} to {value:.5f}",
                )
            )
            continue

        accepted.append((leg, extended))

    accepted.sort(key=lambda item: -_objective_value(item[1], cfg.objective))
    return accepted, rejected


def build_parlay_ladder(
    candidates: Sequence[ParlayLeg],
    config: ParlayConfig | None = None,
) -> dict[int, Parlay]:
    """Best feasible parlay at every leg count from ``min_legs`` to ``max_legs``.

    Sizes with no feasible combination are simply absent from the result --
    "PASS is a valid and often preferable recommendation" applies to leg counts
    too, and emitting a padded parlay to fill a slot would defeat the point.
    """
    cfg = config or ParlayConfig()
    eligible, _ = _eligible_legs(candidates, cfg)
    # One leg per game: two selections from the same game are not a parlay, they
    # are a correlated position the book will refuse or reprice.
    by_game: dict[str, ParlayLeg] = {}
    for leg in sorted(eligible, key=lambda x: (-x.model_prob, x.team)):
        by_game.setdefault(leg.game_id, leg)
    pool = sorted(by_game.values(), key=lambda x: (-x.model_prob, x.team))

    ladder: dict[int, Parlay] = {}
    for size in range(cfg.min_legs, min(cfg.max_legs, len(pool)) + 1):
        parlay = _best_subset(pool, size, cfg)
        if parlay is None:
            continue
        _, rejections = marginal_leg_analysis(parlay, pool, cfg)
        ladder[size] = evaluate_parlay(
            parlay.legs,
            cfg,
            rejected=rejections,
            reason=_construction_reason(parlay, cfg),
        )
    return ladder


def _construction_reason(parlay: Parlay, cfg: ParlayConfig) -> str:
    parts = [
        f"{parlay.leg_count} legs selected to maximize {cfg.objective}",
        f"win probability {parlay.p_correlated:.1%}"
        f" ({parlay.p_independent:.1%} if the legs were independent)",
        f"{parlay.p_stressed:.1%} under the calibration stress",
    ]
    if parlay.correlation_lift >= 0.005:
        parts.append(
            f"shared exposure lifts the headline number by {parlay.correlation_lift:.1%}"
            " while concentrating the downside"
        )
    if parlay.correlated_risk_tags:
        parts.append("shared risk: " + ", ".join(parlay.correlated_risk_tags[:3]))
    return "; ".join(parts)


def fragility(
    parlay: Parlay,
    config: ParlayConfig | None = None,
) -> ParlayFragility:
    """Spec Section E: if this parlay loses, which leg is most likely to blame?

    Computes ``P(leg i loses AND every other leg wins)`` under the same copula
    used for the joint probability, then normalizes over all losing outcomes.
    The residual -- losing outcomes where more than one leg failed -- is
    reported rather than hidden, because a parlay whose failures are mostly
    multi-leg does not have a weak link to swap out; it has a common exposure.
    """
    cfg = config or ParlayConfig()
    legs = parlay.legs
    if not legs:
        return ParlayFragility({}, {}, 0.0, None, "No legs to analyse.")

    _, correlated, loadings = joint_probability(legs, cfg)
    thresholds = [normal_ppf(clamp(leg.model_prob, 1e-9, 1.0 - 1e-9)) for leg in legs]
    nodes, weights = standard_normal_expectation_nodes(cfg.quadrature_nodes)

    sole: dict[str, float] = {}
    for target in range(len(legs)):
        total = 0.0
        for node, weight in zip(nodes, weights):
            product = 1.0
            for index, (z, a) in enumerate(zip(thresholds, loadings)):
                residual = math.sqrt(max(1.0 - a * a, 1e-12))
                win = normal_cdf((z - a * node) / residual)
                product *= (1.0 - win) if index == target else win
            total += weight * product
        sole[legs[target].team] = round(clamp(total, 0.0, 1.0), 6)

    lose_prob = 1.0 - correlated
    if lose_prob <= 0.0:
        shares = {team: 0.0 for team in sole}
        multi = 0.0
    else:
        shares = {team: round(value / lose_prob, 6) for team, value in sole.items()}
        multi = round(max(0.0, 1.0 - sum(shares.values())), 6)

    culprit = max(sole, key=lambda team: sole[team]) if sole else None
    if culprit is None:
        explanation = "No legs to analyse."
    elif multi >= 0.5:
        explanation = (
            f"Most losing outcomes ({multi:.0%}) involve more than one leg failing, so this"
            f" parlay's exposure is shared rather than concentrated. {culprit} is still the"
            " single most likely individual failure."
        )
    else:
        leg = next(x for x in legs if x.team == culprit)
        path = leg.primary_upset_path or "no specific upset path identified"
        explanation = (
            f"{culprit} accounts for {shares[culprit]:.0%} of losing outcomes -- the most"
            f" likely single point of failure. Primary upset path: {path}."
        )

    return ParlayFragility(
        sole_failure_prob=sole,
        blame_share=shares,
        multi_leg_failure_share=multi,
        most_likely_culprit=culprit,
        explanation=explanation,
    )
