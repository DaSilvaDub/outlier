"""Market blend: ``logit p_final = w*logit p_model + (1-w)*logit p_market`` (plan M10).

The weight comes from the promotion evaluator and is 0 until a market family passes its
gate, so ``p_final`` is the vig-free market probability. A missing or degenerate input
yields ``p_final = None`` with a flag; nothing is substituted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FinalProb:
    p_final: float | None
    flags: tuple[str, ...] = ()


def _usable(p: float | None) -> bool:
    return p is not None and math.isfinite(p) and 0.0 < p < 1.0


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def final_probability(p_model: float | None, p_market: float | None, weight: float) -> FinalProb:
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"blend weight must be in [0, 1], got {weight}")
    flags: list[str] = []
    if not _usable(p_market):
        flags.append("MISSING_MARKET_PROB")
    if weight > 0.0 and not _usable(p_model):
        flags.append("MISSING_MODEL_PROB")
    if flags or p_market is None:
        return FinalProb(None, tuple(flags))
    if weight == 0.0:
        return FinalProb(float(p_market))
    if p_model is None:  # unreachable once flagged above; keeps the types honest
        return FinalProb(None, ("MISSING_MODEL_PROB",))
    z = weight * _logit(p_model) + (1.0 - weight) * _logit(p_market)
    return FinalProb(1.0 / (1.0 + math.exp(-z)))
