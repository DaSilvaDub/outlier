"""Shared sport-aware team-total market identity helpers."""

from __future__ import annotations

from typing import Any


# TEAM_PROP makes TOTAL unambiguous: game totals arrive as GAMELINE. MLB also
# exposes raw and normalized aliases for total runs, while basketball uses
# POINTS. GOALS is retained for hockey-shaped feeds covered by the house
# team-total contract even though NHL is not enabled in the current registry.
TEAM_TOTAL_PROPOSITIONS: dict[str, frozenset[str]] = {
    "MLB": frozenset({"POINTS", "RUNS", "R", "TOTAL_RUNS", "TOTAL"}),
    "WNBA": frozenset({"POINTS"}),
    "NBA": frozenset({"POINTS"}),
    "NHL": frozenset({"GOALS", "TOTAL"}),
}
DEFAULT_TEAM_TOTAL_PROPOSITIONS = frozenset({"POINTS"})


def team_total_propositions(sport: str | None) -> frozenset[str]:
    """Return the accepted normalized team-total propositions for a sport."""
    return TEAM_TOTAL_PROPOSITIONS.get(
        str(sport or "").strip().upper(), DEFAULT_TEAM_TOTAL_PROPOSITIONS
    )


def is_team_total_proposition(value: Any, *, sport: str | None) -> bool:
    """Whether *value* is a team-scoring proposition for *sport*."""
    return str(value or "").strip().upper() in team_total_propositions(sport)
