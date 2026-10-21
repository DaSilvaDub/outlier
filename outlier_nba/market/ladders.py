"""Prop / total ladders: integer-line push from adjacent half-lines (plan M9/M11).

For an integer line ``L`` the over wins when the stat is ``>= L + 1`` and pushes
when it equals ``L``. With both neighbouring half-lines devigged,
``P(over wins) = P(over L + 0.5)``, ``P(push) = P(over L - 0.5) - P(over L + 0.5)``
and ``P(over loses) = 1 - P(over L - 0.5)``. Same construction as
``outlier_scrapers.game_totals.derive_push_prob``, generalised to a full triple.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from outlier_nba.market.consensus import BookQuote, consensus_two_way


@dataclass(frozen=True)
class Rung:
    line: float
    quotes: Sequence[BookQuote]  # a = OVER price, b = UNDER price (None when absent)


def monotonicity_violations(ladder: Mapping[float, float]) -> list[tuple[float, float]]:
    """Adjacent line pairs where P(over) fails to fall as the line rises."""
    lines = sorted(ladder)
    return [(lo, hi) for lo, hi in zip(lines, lines[1:]) if ladder[hi] > ladder[lo]]


def _is_integer(line: float) -> bool:
    return float(line).is_integer()


def push_win_loss(line: float, ladder: Mapping[float, float]) -> tuple[float, float, float] | None:
    """``(win, push, loss)`` for the OVER at ``line``, or None when it cannot be derived."""
    if not _is_integer(line):
        p = ladder.get(line)
        return None if p is None else (p, 0.0, 1.0 - p)
    lo, hi = ladder.get(line - 0.5), ladder.get(line + 0.5)
    if lo is None or hi is None or hi > lo:
        return None
    return hi, lo - hi, 1.0 - lo


def ladder_from_consensus(
    rungs: Sequence[Rung], *, method: str = "shin"
) -> tuple[dict[float, float], dict[float, list[str]]]:
    """Consensus ``P(over)`` per two-sided rung, plus per-rung flags.

    Alt rungs are usually OVER-only: with no opposite side there is nothing to
    devig against, so the rung is left out of the ladder and flagged
    ``ONE_SIDED_NO_DEVIG`` rather than given a vig-inflated probability.
    """
    ladder: dict[float, float] = {}
    flags: dict[float, list[str]] = {}
    for rung in rungs:
        two_sided = [q for q in rung.quotes if q.a is not None and q.b is not None]
        if not two_sided:
            flags[rung.line] = ["ONE_SIDED_NO_DEVIG"]
            continue
        c = consensus_two_way(two_sided, method=method)
        if c.p_a is None:
            flags[rung.line] = list(c.flags)
            continue
        ladder[rung.line] = c.p_a
        if c.flags:
            flags[rung.line] = list(c.flags)
    return ladder, flags
