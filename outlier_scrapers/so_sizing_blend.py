"""Leaf helpers for independent-SO sizing blends (no ledger / pack imports)."""

from __future__ import annotations


def temper_independent_prob(
    independent: float,
    market: float | None,
    *,
    independent_weight: float = 0.55,
) -> float:
    """Blend independent win prob toward market consensus for Kelly sizing.

    Offline replay on settled v1 gamelog rows: raw indep Brier ~0.40, tempered
    (~0.55/0.45) ~0.30 vs market ~0.28 — still loses, but far safer than raw
    overconfident tails when promotion is eventually enabled.
    """
    if market is None:
        return independent
    weight = min(1.0, max(0.0, float(independent_weight)))
    return weight * float(independent) + (1.0 - weight) * float(market)
