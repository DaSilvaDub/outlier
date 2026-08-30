"""Board ranking and round-robin quota fill for the pack writer."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable

from outlier_scrapers import slate_quality


def _rank_key(r: dict[str, Any]) -> tuple[float, float, str]:
    if r.get("_board") in {"board_a", "flagged"}:
        return slate_quality.playable_prop_sort_key(r)
    return (0.0, -(r.get("_rank_value") or 0.0), str(r.get("market_id") or ""))


def _bucket_key(r: dict[str, Any]) -> tuple[str, str]:
    return (str(r.get("sport") or ""), str(r.get("_stream") or "props"))


def _round_robin_then_fill(
    cands: list[dict[str, Any]],
    limit: int,
    key: Callable[[dict[str, Any]], Any] = _rank_key,
    bucket: Callable[[dict[str, Any]], Any] = _bucket_key,
) -> list[dict[str, Any]]:
    if not cands or limit <= 0:
        return []
    buckets: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in cands:
        buckets[bucket(r)].append(r)
    for k in buckets:
        buckets[k].sort(key=key)
    selected: list[dict[str, Any]] = []
    deques = {k: deque(v) for k, v in buckets.items() if v}
    while len(selected) < limit and deques:
        for k in list(deques.keys()):
            if not deques[k]:
                deques.pop(k, None)
                continue
            selected.append(deques[k].popleft())
            if len(selected) >= limit:
                break
        for k in list(deques):
            if not deques[k]:
                deques.pop(k, None)
    if len(selected) < limit:
        seen = {id(x) for x in selected}
        remain = [r for r in cands if id(r) not in seen]
        remain.sort(key=key)
        selected.extend(remain[: limit - len(selected)])
    return selected


def rank_rows(rows: list[dict[str, Any]], top_ev_n: int, top_signal_n: int) -> list[dict[str, Any]]:
    # Immutability: do not mutate caller's rows. Create new objects (AGENTS.md).
    rows = [({**r, "_stream": "props"} if "_stream" not in r else r) for r in rows]
    board_a = [r for r in rows if r.get("_board") == "board_a"]
    board_b = [r for r in rows if r.get("_board") == "board_b"]
    flagged = [r for r in rows if r.get("_board") == "flagged"]

    ev_audit = _round_robin_then_fill(board_a + flagged, top_ev_n)
    ev = [row for row in ev_audit if row.get("_board") == "board_a"]
    audit = [row for row in ev_audit if row.get("_board") == "flagged"]
    sig = _round_robin_then_fill(board_b, top_signal_n)
    return ev + sig + audit
