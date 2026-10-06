"""Rank / de-correlate NFL high-prob (Tier-1) prop boards.

Same-player correlation guard (issue #217 PR2): props on the same player in the
same event share one usage driver. Keep a single PRIMARY actionable row per
(event_id, player); tag the rest ``CORRELATED_SAME_PLAYER`` and exclude them
from the actionable set. The full Tier-1 dump can still retain every row.

Tiebreak for PRIMARY (descending):
1. ``sportsbook_edge_pts`` when present (PrizePicks-excluded edge from PR1)
2. else ``model_p``
3. else ``best_odds`` (American; higher / less-negative wins)
4. stable: market, line, position, outcome_id
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, MutableMapping, Sequence

CORRELATION_ROLE_PRIMARY = "PRIMARY"
CORRELATION_ROLE_CORRELATED = "CORRELATED_SAME_PLAYER"

CORRELATION_GUARD_NOTE = (
    "Same-player correlation guard: one PRIMARY actionable prop per "
    "(event_id, player_id|player_name). Others tagged CORRELATED_SAME_PLAYER "
    "and excluded from actionable_records; full records list is retained."
)


def player_correlation_key(row: Mapping[str, Any]) -> tuple[str, str]:
    """Group key: (event_id, player identity). Prefer player_id over name."""
    event_id = str(row.get("event_id") or "").strip()
    player_id = str(row.get("player_id") or "").strip()
    if player_id:
        return (event_id, f"id:{player_id}")
    name = str(row.get("player_name") or "").strip().casefold()
    return (event_id, f"name:{name}")


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def primary_sort_key(row: Mapping[str, Any]) -> tuple[float, float, float, str, float, str, str]:
    """Descending sort key for choosing the PRIMARY row in a player group."""
    edge = _as_float(row.get("sportsbook_edge_pts"))
    model_p = _as_float(row.get("model_p"))
    best_odds = _as_float(row.get("best_odds"))
    line = _as_float(row.get("line"))
    return (
        edge if edge is not None else float("-inf"),
        model_p if model_p is not None else float("-inf"),
        best_odds if best_odds is not None else float("-inf"),
        str(row.get("market") or ""),
        line if line is not None else float("-inf"),
        str(row.get("position") or ""),
        str(row.get("outcome_id") or row.get("market_id") or ""),
    )


def apply_same_player_correlation_guard(
    records: Sequence[MutableMapping[str, Any]],
) -> list[dict[str, Any]]:
    """Tag each row PRIMARY or CORRELATED_SAME_PLAYER; set actionable bool.

    Mutates row mappings in place and returns the same list (as dicts). Rows
    lacking event_id+player identity are treated as their own group (PRIMARY).
    """
    groups: dict[tuple[str, str], list[MutableMapping[str, Any]]] = defaultdict(list)
    ordered: list[MutableMapping[str, Any]] = []
    for raw in records:
        if not isinstance(raw, MutableMapping):
            continue
        ordered.append(raw)
        groups[player_correlation_key(raw)].append(raw)

    for group in groups.values():
        ranked = sorted(group, key=primary_sort_key, reverse=True)
        primary = ranked[0]
        primary_id = id(primary)
        for row in ranked:
            if id(row) == primary_id:
                row["correlation_role"] = CORRELATION_ROLE_PRIMARY
                row["actionable"] = True
                row.pop("correlation_excluded_reason", None)
            else:
                row["correlation_role"] = CORRELATION_ROLE_CORRELATED
                row["actionable"] = False
                row["correlation_excluded_reason"] = CORRELATION_ROLE_CORRELATED

    return [dict(r) for r in ordered]


def actionable_high_prob_records(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rows marked actionable / PRIMARY after the correlation guard."""
    out: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, Mapping):
            continue
        role = row.get("correlation_role")
        actionable = row.get("actionable")
        if actionable is True or (
            actionable is None and role == CORRELATION_ROLE_PRIMARY
        ):
            out.append(dict(row))
        elif actionable is None and role is None:
            # Guard not applied yet — treat as actionable for safety.
            out.append(dict(row))
    return out


def correlation_guard_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts for artifact metadata."""
    n_primary = 0
    n_correlated = 0
    n_unlabeled = 0
    for row in records:
        role = row.get("correlation_role")
        if role == CORRELATION_ROLE_PRIMARY:
            n_primary += 1
        elif role == CORRELATION_ROLE_CORRELATED:
            n_correlated += 1
        else:
            n_unlabeled += 1
    actionable = actionable_high_prob_records(records)
    return {
        "note": CORRELATION_GUARD_NOTE,
        "n_records": len(list(records)),
        "n_primary": n_primary,
        "n_correlated_same_player": n_correlated,
        "n_unlabeled": n_unlabeled,
        "n_actionable": len(actionable),
    }


__all__ = [
    "CORRELATION_GUARD_NOTE",
    "CORRELATION_ROLE_CORRELATED",
    "CORRELATION_ROLE_PRIMARY",
    "actionable_high_prob_records",
    "apply_same_player_correlation_guard",
    "correlation_guard_summary",
    "player_correlation_key",
    "primary_sort_key",
]
