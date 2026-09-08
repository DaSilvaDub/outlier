"""Moneyline VALUE model: is this price worth paying?

The second of the three independent models. ``safety.py`` decided how likely
the favorite is to win; this module decides whether the market is charging too
much for that. Spec Part 9 is the whole reason it exists as a separate object:

  A team can have a 94% win probability and still be unattractive if the market
  price implies 97%. A team with a 90% win probability may offer greater
  betting value if the market implies only 84%.

Two edges are reported, never one. ``probability_edge`` compares the blended
model probability against the devigged market; ``market_free_edge`` compares
the *fundamental-only* probability, which never saw the spread. When the two
disagree sharply, the apparent edge is coming from the spread-vs-moneyline
relationship rather than from anything the model knows -- worth acting on
sometimes, but never worth confusing with model skill.
"""

from __future__ import annotations

from dataclasses import dataclass

from outlier_scrapers.ncaa.config import NcaaConfig
from outlier_scrapers.ncaa.features import GameInputs, MatchupFeatures
from outlier_scrapers.ncaa.odds import (
    american_to_decimal,
    devig,
    ev_per_unit,
    implied_prob,
    kelly_fraction,
    kelly_growth,
)
from outlier_scrapers.ncaa.safety import WinProbability

__all__ = ["ValueAssessment", "assess_value"]

#: Divergence between the blended and market-free edges beyond which the edge
#: is attributed to market structure rather than model skill.
_EDGE_ATTRIBUTION_THRESHOLD = 0.02


@dataclass(frozen=True)
class ValueAssessment:
    """Price-side evaluation of one moneyline."""

    game_id: str
    favorite: str
    price_american: float | None
    decimal_price: float | None
    best_price_american: float | None
    market_prob_raw: float | None
    market_prob_fair: float | None
    devig_method: str
    hold: float | None
    model_prob: float | None
    probability_edge: float | None
    market_free_edge: float | None
    #: Edge as a share of the fair price -- 0.02 against a 0.90 market is a
    #: very different proposition from 0.02 against a 0.20 market.
    price_efficiency: float | None
    ev_per_unit: float | None
    kelly_fraction: float | None
    quarter_kelly: float | None
    kelly_growth: float | None
    closing_line_value: float | None
    flags: tuple[str, ...]

    @property
    def priced(self) -> bool:
        return self.market_prob_fair is not None and self.model_prob is not None


def assess_value(
    game: GameInputs,
    features: MatchupFeatures,
    win: WinProbability,
    config: NcaaConfig | None = None,
) -> ValueAssessment:
    """Compare the model's probability against what the market charges."""
    cfg = config or NcaaConfig()
    flags: list[str] = []
    market = game.market
    is_home = features.favorite_is_home

    price = market.moneyline_home_current if is_home else market.moneyline_away_current
    opposite = market.moneyline_away_current if is_home else market.moneyline_home_current
    best_price = market.moneyline_home_best if is_home else market.moneyline_away_best

    decimal_price = american_to_decimal(best_price if best_price is not None else price)
    raw = implied_prob(price)
    raw_opposite = implied_prob(opposite)

    fair: float | None = None
    hold: float | None = None
    if raw is not None and raw_opposite is not None:
        result = devig([raw, raw_opposite], method=cfg.devig_method)
        if result is not None:
            fair = result.probabilities[0]
            hold = result.hold
            flags.extend(result.flags)
    elif raw is not None:
        # A one-sided quote cannot be devigged. Using the raw price as if it
        # were fair would manufacture an edge equal to the book's margin.
        flags.append("one_sided_market_no_devig")

    model_prob = win.model_prob
    edge = None if (fair is None or model_prob is None) else model_prob - fair
    market_free = (
        None if (fair is None or win.fundamental_prob is None) else win.fundamental_prob - fair
    )

    if (
        edge is not None
        and market_free is not None
        and abs(edge - market_free) >= _EDGE_ATTRIBUTION_THRESHOLD
    ):
        flags.append("edge_driven_by_spread_signal")

    efficiency = None if (edge is None or not fair) else edge / fair
    ev = (
        None
        if (model_prob is None or decimal_price is None)
        else ev_per_unit(model_prob, decimal_price)
    )
    kelly = (
        None
        if (model_prob is None or decimal_price is None)
        else kelly_fraction(model_prob, decimal_price)
    )
    growth = (
        None
        if (model_prob is None or decimal_price is None)
        else kelly_growth(model_prob, decimal_price)
    )

    if ev is not None and ev <= 0:
        flags.append("negative_expected_value")

    closing = market.closing_moneyline_home if is_home else market.closing_moneyline_away
    clv = _closing_line_value(price, closing, opposite, market, is_home, cfg)

    return ValueAssessment(
        game_id=features.game_id,
        favorite=features.favorite,
        price_american=price,
        decimal_price=None if decimal_price is None else round(decimal_price, 6),
        best_price_american=best_price,
        market_prob_raw=None if raw is None else round(raw, 6),
        market_prob_fair=None if fair is None else round(fair, 6),
        devig_method=cfg.devig_method,
        hold=None if hold is None else round(hold, 6),
        model_prob=model_prob,
        probability_edge=None if edge is None else round(edge, 6),
        market_free_edge=None if market_free is None else round(market_free, 6),
        price_efficiency=None if efficiency is None else round(efficiency, 6),
        ev_per_unit=None if ev is None else round(ev, 6),
        kelly_fraction=None if kelly is None else round(kelly, 6),
        quarter_kelly=None if kelly is None else round(kelly * 0.25, 6),
        kelly_growth=None if growth is None else round(growth, 8),
        closing_line_value=clv,
        flags=tuple(dict.fromkeys(flags)),
    )


def _closing_line_value(
    price: float | None,
    closing: float | None,
    opposite: float | None,
    market: object,
    is_home: bool,
    cfg: NcaaConfig,
) -> float | None:
    """CLV in fair-probability terms: entry fair prob minus closing fair prob.

    Negative means the market moved toward us after entry, which is the
    outcome to want. Requires both sides of both quotes -- comparing a vigged
    entry price against a vigged close would measure the books' margin drift
    as much as our own timing.
    """
    closing_opposite = getattr(
        market, "closing_moneyline_away" if is_home else "closing_moneyline_home"
    )
    if closing is None or closing_opposite is None or price is None or opposite is None:
        return None
    entry_raw = implied_prob(price)
    entry_other = implied_prob(opposite)
    close_raw = implied_prob(closing)
    close_other = implied_prob(closing_opposite)
    if None in (entry_raw, entry_other, close_raw, close_other):
        return None
    entry = devig([entry_raw, entry_other], method=cfg.devig_method)  # type: ignore[list-item]
    close = devig([close_raw, close_other], method=cfg.devig_method)  # type: ignore[list-item]
    if entry is None or close is None:
        return None
    return round(close.probabilities[0] - entry.probabilities[0], 6)
