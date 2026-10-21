"""Cross-book consensus for a two-sided market (plan M9).

Each book is devigged against itself first; the consensus is the median of those
per-book probabilities, renormalised. Aggregating raw odds across books before
devigging mixes different book sets and distorts the answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from outlier_nba.market.devig import (
    DEFAULT_METHOD,
    DevigError,
    american_to_decimal,
    american_to_prob,
    devig,
)

# Placeholder window: a per-book devigged probability outside it is a stub price.
PLACEHOLDER_LOW = 0.0005
PLACEHOLDER_HIGH = 0.995
MIN_BOOKS = 3

# Pick'em / DFS operators pay flat multipliers, not priced two-way odds; their
# "prices" must never enter a vig-free consensus. Compared after book_key().
DFS_BOOKS = frozenset({"prizepicks", "underdog", "sleeper"})


def book_key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


@dataclass(frozen=True)
class BookQuote:
    """One book's two-sided American prices (``over``/``home``-style side A and B)."""

    book: str
    a: Any
    b: Any


@dataclass(frozen=True)
class Consensus:
    p_a: float | None
    p_b: float | None
    n_books: int
    method: str
    thin_market: bool
    books_used: list[str] = field(default_factory=list)
    dropped_books: list[str] = field(default_factory=list)
    excluded_books: list[str] = field(default_factory=list)
    best_a: tuple[Any, str] | None = None
    best_b: tuple[Any, str] | None = None
    flags: list[str] = field(default_factory=list)


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _best(quotes: Iterable[tuple[str, Any]]) -> tuple[Any, str] | None:
    best: tuple[Any, str] | None = None
    best_dec = 0.0
    for book, odds in quotes:
        dec = american_to_decimal(odds)
        if dec is not None and dec > best_dec:
            best_dec, best = dec, (odds, book)
    return best


def consensus_two_way(
    quotes: Sequence[BookQuote],
    *,
    method: str = DEFAULT_METHOD,
    min_books: int = MIN_BOOKS,
    excluded_books: frozenset[str] = DFS_BOOKS,
) -> Consensus:
    used: list[str] = []
    dropped: list[str] = []
    excluded: list[str] = []
    side_a: list[float] = []
    side_b: list[float] = []
    for quote in quotes:
        if book_key(quote.book) in excluded_books:
            excluded.append(quote.book)
            continue
        qa, qb = american_to_prob(quote.a), american_to_prob(quote.b)
        if qa is None or qb is None:
            dropped.append(quote.book)
            continue
        try:
            pa, pb = devig([qa, qb], method)
        except DevigError:
            dropped.append(quote.book)
            continue
        if not (PLACEHOLDER_LOW <= pa <= PLACEHOLDER_HIGH):
            dropped.append(quote.book)
            continue
        used.append(quote.book)
        side_a.append(pa)
        side_b.append(pb)

    priced = [q for q in quotes if book_key(q.book) not in excluded_books]
    best_a = _best((q.book, q.a) for q in priced)
    best_b = _best((q.book, q.b) for q in priced)
    n = len(used)
    flags: list[str] = []
    if n == 0:
        flags.append("NO_USABLE_BOOKS")
        return Consensus(
            None, None, 0, method, True, used, dropped, excluded, best_a, best_b, flags
        )
    med_a, med_b = median(side_a), median(side_b)
    total = med_a + med_b
    thin = n < min_books
    if thin:
        flags.append("THIN_MARKET")
    return Consensus(
        med_a / total,
        med_b / total,
        n,
        method,
        thin,
        used,
        dropped,
        excluded,
        best_a,
        best_b,
        flags,
    )
