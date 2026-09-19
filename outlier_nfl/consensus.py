"""Consensus line identification and alternate ladder filtering for outlier_nfl.

Enforces balanced two-way market selection to eliminate extreme alternate ladder
distortions (e.g. +750 to -900 odds) and exchange outliers.
"""

from __future__ import annotations

from dataclasses import replace
import logging

from outlier_nfl.models import NflPlayerProp

logger = logging.getLogger("outlier_nfl.consensus")

BALANCED_MIN_ODDS: int = -220
BALANCED_MAX_ODDS: int = 180
TOUCHDOWN_MARKETS: tuple[str, ...] = ("ANYTIME_TD", "FIRST_TD", "LAST_TOUCHDOWN")


def identify_consensus_lines_for_group(
    group: list[NflPlayerProp],
) -> set[tuple[float, str]]:
    """Identify the winning consensus (line, position) pair(s) for a single player+market group.

    Returns a set of (line, position) tuples that qualify as consensus lines.
    """
    if not group:
        return set()

    market = group[0].market

    # 1. Touchdown Markets: Require line == 0.5 and position == 'OVER'
    if market in TOUCHDOWN_MARKETS:
        td_candidates = [
            p for p in group
            if float(p.line) == 0.5 and p.position in ("OVER", "YES")
        ]
        if td_candidates:
            best_td = max(
                td_candidates,
                key=lambda x: (len(x.books), x.best_odds or -9999),
            )
            return {(best_td.line, best_td.position)}
        return set()

    # 2. Standard Two-Way Markets: Find balanced line with both OVER and UNDER
    by_line: dict[float, list[NflPlayerProp]] = {}
    for p in group:
        by_line.setdefault(float(p.line), []).append(p)

    scored: list[tuple[int, int, float]] = []
    for line_val, rows in by_line.items():
        overs = [r for r in rows if r.position == "OVER"]
        unders = [r for r in rows if r.position == "UNDER"]
        if not overs or not unders:
            continue

        best_over = max(overs, key=lambda x: len(x.books))
        best_under = max(unders, key=lambda x: len(x.books))
        o_odds = best_over.best_odds or 0
        u_odds = best_under.best_odds or 0

        # Balanced betting line filter
        if BALANCED_MIN_ODDS <= o_odds <= BALANCED_MAX_ODDS and BALANCED_MIN_ODDS <= u_odds <= BALANCED_MAX_ODDS:
            books_count = len(best_over.books) + len(best_under.books)
            balance = abs(o_odds - (-110)) + abs(u_odds - (-110))
            scored.append((books_count, -balance, line_val))

    if scored:
        scored.sort(reverse=True)
        _, _, best_line = scored[0]
        return {(best_line, "OVER"), (best_line, "UNDER")}

    # Fallback: Line with most total book quotes across all outcomes
    best_fallback_line = max(
        by_line.keys(),
        key=lambda k: sum(len(x.books) for x in by_line[k]),
    )
    return {(best_fallback_line, "OVER"), (best_fallback_line, "UNDER")}


def select_consensus_player_props(props: list[NflPlayerProp]) -> list[NflPlayerProp]:
    """Annotate and select consensus lines across all player propositions.

    Returns a new list of NflPlayerProp instances with is_consensus_line populated.
    """
    by_player_market: dict[tuple[str, str, str], list[NflPlayerProp]] = {}
    for p in props:
        key = (p.event_id, p.player_name, p.market)
        by_player_market.setdefault(key, []).append(p)

    updated_props: list[NflPlayerProp] = []
    for key, group in by_player_market.items():
        consensus_targets = identify_consensus_lines_for_group(group)
        for p in group:
            is_consensus = (float(p.line), p.position) in consensus_targets
            if is_consensus:
                updated_props.append(replace(p, is_consensus_line=True))
            else:
                updated_props.append(p)

    return updated_props
