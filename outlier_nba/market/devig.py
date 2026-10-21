"""Remove a bookmaker's margin from a two-sided (or n-way) set of quoted prices.

Clean-room stdlib implementations of three estimators; none of this code is
imported from or copied out of ``cfb_analytics``. They disagree most on heavy
favourites, so the method used is always recorded next to the probability and
the default is chosen by NBA out-of-sample log loss once that exists.

* ``multiplicative``: ``p_i = q_i / sum(q)``.
* ``shin``: Shin (1993) informed-money model; the root ``z`` of
  ``sum(p_i(z)) = 1`` is found by bisection. When the book is an arbitrage
  (``sum(q) < 1``) there is no informed-money interpretation, so it falls back
  to multiplicative. For two outcomes it equals the equal-margin estimator.
* ``power``: ``p_i = q_i ** k`` with ``k`` solved so the probabilities sum to 1.

``additive``/equal-margin is deliberately absent: for two-way markets it is the
same estimator as ``shin`` and would be scored twice.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

METHODS = ("multiplicative", "shin", "power")
DEFAULT_METHOD = "shin"

_TOL = 1e-12
_MAX_ITER = 200


class DevigError(ValueError):
    """The quoted prices cannot be devigged."""


def american_to_prob(odds: Any) -> float | None:
    """Vig-inclusive implied probability of an American price, or None if invalid.

    Prices with ``|odds| < 100`` do not exist in American notation (a stub or a
    misparse) and are rejected rather than converted.
    """
    value = _to_float(odds)
    if value is None or abs(value) < 100:
        return None
    return 100.0 / (value + 100.0) if value > 0 else -value / (-value + 100.0)


def american_to_decimal(odds: Any) -> float | None:
    value = _to_float(odds)
    if value is None or abs(value) < 100:
        return None
    return 1.0 + (value / 100.0 if value > 0 else 100.0 / -value)


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).replace("−", "-").replace(",", "").strip()
        if text.upper() in {"EVEN", "EVENS"}:
            return 100.0
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def hold(quoted: Sequence[float]) -> float:
    """Margin as a fraction of the market; negative means an arbitrage."""
    total = math.fsum(quoted)
    if total <= 0:
        raise DevigError("quoted probabilities must sum to a positive number")
    return (total - 1.0) / total


def _validate(quoted: Sequence[float]) -> list[float]:
    try:
        values = [float(q) for q in quoted]
    except (TypeError, ValueError) as exc:
        raise DevigError("quoted probabilities must be numeric") from exc
    if len(values) < 2:
        raise DevigError("devig needs at least two mutually exclusive outcomes")
    if any(not math.isfinite(q) or q <= 0.0 or q >= 1.0 for q in values):
        raise DevigError("every quoted probability must be finite and inside (0, 1)")
    return values


def _renormalise(values: list[float]) -> list[float]:
    total = math.fsum(values)
    return [v / total for v in values]


def multiplicative(quoted: Sequence[float]) -> list[float]:
    values = _validate(quoted)
    return _renormalise(values)


def power(quoted: Sequence[float]) -> list[float]:
    values = _validate(quoted)
    total = math.fsum(values)
    if abs(total - 1.0) < _TOL:
        return values

    def total_at(k: float) -> float:
        return math.fsum(q**k for q in values)

    # sum(q_i ** k) is strictly decreasing in k, so the root is unique.
    if total > 1.0:
        low, high = 1.0, 2.0
        while total_at(high) > 1.0:
            high *= 2.0
            if high > 1e12:
                raise DevigError("power devig failed to bracket a solution")
    else:
        low, high = 0.5, 1.0
        while total_at(low) < 1.0:
            low /= 2.0
            if low < 1e-12:
                raise DevigError("power devig failed to bracket a solution")
    for _ in range(_MAX_ITER):
        mid = (low + high) / 2.0
        if total_at(mid) > 1.0:
            low = mid
        else:
            high = mid
        if high - low < _TOL:
            break
    k = (low + high) / 2.0
    return _renormalise([q**k for q in values])


def shin(quoted: Sequence[float]) -> list[float]:
    values = _validate(quoted)
    total = math.fsum(values)
    if abs(total - 1.0) < _TOL:
        return values
    if total < 1.0:
        return multiplicative(values)  # arbitrage book: no informed-money model applies

    def prob(q: float, z: float) -> float:
        # Rationalised form of (sqrt(z^2 + 4(1-z)q^2/S) - z) / (2(1-z)): no cancellation.
        radicand = z * z + 4.0 * (1.0 - z) * (q * q / total)
        return (2.0 * q * q / total) / (math.sqrt(radicand) + z)

    def total_at(z: float) -> float:
        return math.fsum(prob(q, z) for q in values)

    low, high = 0.0, 1.0
    if total_at(low) <= 1.0 or total_at(high) >= 1.0:
        return multiplicative(values)
    for _ in range(_MAX_ITER):
        mid = (low + high) / 2.0
        if total_at(mid) > 1.0:
            low = mid
        else:
            high = mid
        if high - low < _TOL:
            break
    z = (low + high) / 2.0
    return _renormalise([prob(q, z) for q in values])


_DISPATCH: dict[str, Callable[[Sequence[float]], list[float]]] = {
    "multiplicative": multiplicative,
    "shin": shin,
    "power": power,
}


def devig(quoted: Sequence[float], method: str = DEFAULT_METHOD) -> list[float]:
    try:
        fn = _DISPATCH[method]
    except KeyError as exc:
        raise DevigError(f"unknown devig method {method!r}; use one of {METHODS}") from exc
    return fn(quoted)
