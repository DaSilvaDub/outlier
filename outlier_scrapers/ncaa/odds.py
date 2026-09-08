"""Price arithmetic and vig removal for the NCAA football pipeline.

Part 9 of the operating spec turns on one distinction: a *good prediction* and
a *good bet* are different objects. Everything in this module exists to make
the second one measurable -- converting a posted American price into a fair
probability the model can actually be compared against.

Devig method matters more here than in most markets. Moneyline parlays live on
heavy favorites, and the proportional (multiplicative) method is known to
misallocate the overround at extreme prices: it charges the favorite and the
longshot the same *relative* margin, when books empirically load more of the
margin onto the longshot. ``power`` and ``shin`` both correct in that
direction, so ``power`` is the default for this pipeline. The method used is
always reported alongside the number -- a probability edge is not interpretable
without it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from outlier_scrapers.ncaa.stats import clamp

__all__ = [
    "DEVIG_METHODS",
    "DevigResult",
    "american_to_decimal",
    "decimal_to_american",
    "devig",
    "ev_per_unit",
    "fair_decimal",
    "hold_pct",
    "implied_prob",
    "kelly_fraction",
    "kelly_growth",
    "parlay_decimal",
    "two_way_devig",
]

DEVIG_METHODS = ("multiplicative", "additive", "power", "shin")

_MIN_PROB = 1e-6
_MAX_PROB = 1.0 - 1e-6


@dataclass(frozen=True)
class DevigResult:
    """Fair probabilities plus the provenance needed to audit them."""

    probabilities: tuple[float, ...]
    method: str
    hold: float
    #: ``power`` exponent or ``shin`` z. ``None`` for the closed-form methods.
    parameter: float | None = None
    flags: tuple[str, ...] = ()


def american_to_decimal(american: float | int | None) -> float | None:
    """Convert an American price to decimal. ``None`` for unusable input."""
    if american is None:
        return None
    try:
        value = float(american)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or -100.0 < value < 100.0:
        # Prices strictly inside (-100, +100) are not valid American odds;
        # 0 in particular is the usual "missing" sentinel in scraped feeds.
        return None
    if value > 0:
        return 1.0 + value / 100.0
    return 1.0 + 100.0 / abs(value)


def decimal_to_american(decimal_price: float | None) -> float | None:
    """Convert a decimal price back to American."""
    if decimal_price is None or not math.isfinite(decimal_price) or decimal_price <= 1.0:
        return None
    if decimal_price >= 2.0:
        return round((decimal_price - 1.0) * 100.0, 2)
    return round(-100.0 / (decimal_price - 1.0), 2)


def implied_prob(american: float | int | None) -> float | None:
    """Vig-inclusive implied probability of an American price."""
    decimal_price = american_to_decimal(american)
    if decimal_price is None:
        return None
    return 1.0 / decimal_price


def fair_decimal(probability: float) -> float | None:
    """Break-even decimal price for ``probability``."""
    if not 0.0 < probability < 1.0:
        return None
    return 1.0 / probability


def hold_pct(raw_probabilities: Iterable[float]) -> float:
    """Book overround: ``sum(raw) - 1``. 0.045 means a 4.5-point hold."""
    return sum(raw_probabilities) - 1.0


def _normalise_inputs(raw: Sequence[float]) -> tuple[list[float], list[str]]:
    flags: list[str] = []
    cleaned = [clamp(float(p), _MIN_PROB, _MAX_PROB) for p in raw]
    total = sum(cleaned)
    if total <= 1.0:
        # A non-positive hold is either a genuine arb across books or, far more
        # often, a stale/mismatched quote. Flag it; never silently trade on it.
        flags.append("non_positive_hold")
    return cleaned, flags


def devig(
    raw_probabilities: Sequence[float],
    *,
    method: str = "power",
) -> DevigResult | None:
    """Strip the book margin from a complete set of raw implied probabilities.

    ``raw_probabilities`` must cover every outcome of the market exactly once.
    Returns ``None`` when fewer than two outcomes are supplied -- a one-sided
    quote carries no information about the margin and must not be devigged by
    assumption.
    """
    if method not in DEVIG_METHODS:
        raise ValueError(f"devig(): unknown method {method!r}")
    if len(raw_probabilities) < 2:
        return None
    cleaned, flags = _normalise_inputs(raw_probabilities)
    overround = hold_pct(cleaned)

    if method == "multiplicative":
        total = sum(cleaned)
        fair = [p / total for p in cleaned]
        parameter: float | None = None
    elif method == "additive":
        excess = overround / len(cleaned)
        fair = [clamp(p - excess, _MIN_PROB, _MAX_PROB) for p in cleaned]
        total = sum(fair)
        fair = [p / total for p in fair]
        parameter = None
    elif method == "power":
        parameter = _solve_power(cleaned)
        if parameter is None:
            return None
        fair = [p ** parameter for p in cleaned]
        total = sum(fair)
        fair = [p / total for p in fair]
    else:  # shin
        parameter = _solve_shin(cleaned)
        if parameter is None:
            return None
        fair = _shin_probabilities(cleaned, parameter)
        total = sum(fair)
        fair = [p / total for p in fair]

    return DevigResult(
        probabilities=tuple(fair),
        method=method,
        hold=overround,
        parameter=parameter,
        flags=tuple(flags),
    )


def two_way_devig(
    price_a: float | int | None,
    price_b: float | int | None,
    *,
    method: str = "power",
) -> tuple[float, float] | None:
    """Fair ``(p_a, p_b)`` for a two-way market quoted in American odds."""
    raw_a = implied_prob(price_a)
    raw_b = implied_prob(price_b)
    if raw_a is None or raw_b is None:
        return None
    result = devig([raw_a, raw_b], method=method)
    if result is None:
        return None
    return result.probabilities[0], result.probabilities[1]


def _solve_power(raw: Sequence[float], *, tolerance: float = 1e-12) -> float | None:
    """Find ``k`` with ``sum(p_i ** k) == 1``.

    ``sum(p_i ** k)`` is strictly decreasing in ``k`` for ``p_i < 1``, so plain
    bisection is both safe and fast.
    """

    def total(k: float) -> float:
        return sum(p ** k for p in raw)

    low, high = 1e-6, 1.0
    if total(high) < 1.0:
        # Book sum below 1 (flagged upstream): the exponent lies below 1.
        low, high = 1e-6, 1.0
        while total(low) < 1.0 and low > 1e-12:
            low /= 2.0
        if total(low) < 1.0:
            return None
    else:
        high = 1.0
        while total(high) > 1.0:
            high *= 2.0
            if high > 512.0:
                return None
        low = 1.0
    for _ in range(200):
        mid = 0.5 * (low + high)
        value = total(mid)
        if abs(value - 1.0) < tolerance:
            return mid
        if value > 1.0:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _shin_probabilities(raw: Sequence[float], z: float) -> list[float]:
    total = sum(raw)
    out: list[float] = []
    for p in raw:
        inner = z * z + 4.0 * (1.0 - z) * p * p / total
        out.append((math.sqrt(max(inner, 0.0)) - z) / (2.0 * (1.0 - z)))
    return out


def _solve_shin(raw: Sequence[float], *, tolerance: float = 1e-12) -> float | None:
    """Find the Shin insider-trading parameter ``z`` with ``sum(pi_i) == 1``."""
    if sum(raw) <= 1.0:
        return 0.0
    low, high = 0.0, 0.99
    for _ in range(200):
        mid = 0.5 * (low + high)
        value = sum(_shin_probabilities(raw, mid))
        if abs(value - 1.0) < tolerance:
            return mid
        if value > 1.0:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def parlay_decimal(prices: Iterable[float]) -> float:
    """Multiply decimal leg prices into the parlay price."""
    total = 1.0
    for price in prices:
        total *= price
    return total


def ev_per_unit(probability: float, decimal_price: float) -> float:
    """Expected profit per unit staked."""
    return probability * (decimal_price - 1.0) - (1.0 - probability)


def kelly_fraction(probability: float, decimal_price: float) -> float:
    """Full-Kelly stake fraction; 0.0 when the bet has no edge."""
    b = decimal_price - 1.0
    if b <= 0.0:
        return 0.0
    fraction = (probability * b - (1.0 - probability)) / b
    return max(0.0, fraction)


def kelly_growth(probability: float, decimal_price: float, *, cap: float = 1.0) -> float:
    """Expected log-growth per unit of bankroll at the (capped) Kelly stake.

    This is the parlay optimizer's default objective. Unlike raw EV it is
    concave in the payout, so it will not accept a marginal leg whose only
    contribution is a longer price -- which is precisely the behaviour the
    spec's "do not maximize payout" requirement asks for.
    """
    fraction = min(kelly_fraction(probability, decimal_price), cap, 0.999999)
    if fraction <= 0.0:
        return 0.0
    b = decimal_price - 1.0
    return probability * math.log(1.0 + fraction * b) + (1.0 - probability) * math.log(
        1.0 - fraction
    )
