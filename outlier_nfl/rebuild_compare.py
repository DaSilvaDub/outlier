"""Compare an original card with its rebuild and label the cause of every difference.

The cards are ``nfl_best_bets.json`` from the original run bundle and from the
rebuild's replay bundle (``picks[]`` with ``event_id, player_name, market,
position, line, best_odds, best_book, final_p, ev_per_unit, verdict,
pillars{name: {status}}``, as in ``best_bets.py``).

**Edge is measured against sportsbook prices only.** ``sportsbook_edge`` is
``final_p`` minus the implied probability of ``best_odds``, and only when
``best_book`` is a named sportsbook (:data:`SPORTSBOOKS`: the alt-floor retail
books plus the Hard Rock target). DFS apps and unknown books give ``None``
(``no_sportsbook_price``), never an edge.

Cause labels (a difference can carry several):

- ``line_changed``: the primary line moved (line selection, F19/F20).
- ``price_changed``: best price or book changed.
- ``probability_changed``: ``final_p`` moved by more than 1e-4.
- ``evidence_changed:<pillar>``: a pillar's status changed (tape, injury,
  weather and other source gates).
- ``ev_formula``: EV changed while price and probability did not (F05).
- ``verdict_rule``: the verdict changed while none of the above did.
- ``sportsbook_edge_sign_flip``: the sign of the sportsbook edge flipped.
- ``not_rebuilt_as_candidate`` / ``new_candidate``: the pick exists on one side only.
- ``evidence_changed:nflverse`` / ``:weather`` / ``:injury``: added by the rebuild
  (``context``) to the causes those sources explain.
"""

from __future__ import annotations

from collections import Counter
import math
from typing import Any

from outlier_nfl.alt_floors import RETAIL_BOOKS, compact_book

SPORTSBOOKS = frozenset(RETAIL_BOOKS | {"HARDROCK"})
P_TOL = 1e-4
EV_TOL = 1e-4


def implied_probability(odds: Any) -> float | None:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(o) or -100 < o < 100:
        return None
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def sportsbook_edge(pick: dict[str, Any]) -> float | None:
    if compact_book(pick.get("best_book")) not in SPORTSBOOKS:
        return None
    imp = implied_probability(pick.get("best_odds"))
    p = pick.get("final_p")
    if imp is None or not isinstance(p, (int, float)) or not math.isfinite(p):
        return None
    return round(float(p) - imp, 4)


def _base(p: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(p.get("event_id")), str(p.get("player_name", "")).strip().lower(),
            str(p.get("market")), str(p.get("position")))


def _match(orig: list[dict[str, Any]], new: list[dict[str, Any]]):
    """Pair picks by event/player/market/side; by line too when a side has several."""
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    groups: dict[tuple[str, str, str, str], tuple[list[Any], list[Any]]] = {}
    for p in orig:
        groups.setdefault(_base(p), ([], []))[0].append(p)
    for p in new:
        groups.setdefault(_base(p), ([], []))[1].append(p)
    for key in sorted(groups):
        a, b = groups[key]
        if len(a) == 1 and len(b) == 1:
            pairs.append((a[0], b[0]))
            continue
        by_line = {p.get("line"): p for p in b}
        for p in a:
            pairs.append((p, by_line.pop(p.get("line"), None)))
        pairs.extend((None, p) for p in by_line.values())
    return pairs


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def causes(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if a.get("line") != b.get("line"):
        out.append("line_changed")
    price = (a.get("best_odds"), compact_book(a.get("best_book"))) != (
        b.get("best_odds"), compact_book(b.get("best_book")))
    if price:
        out.append("price_changed")
    pa, pb = _num(a.get("final_p")), _num(b.get("final_p"))
    prob = (pa is None) != (pb is None) or (pa is not None and pb is not None
                                            and abs(pa - pb) > P_TOL)
    if prob:
        out.append("probability_changed")
    pil_a, pil_b = a.get("pillars") or {}, b.get("pillars") or {}
    for name in sorted(set(pil_a) | set(pil_b)):
        sa = (pil_a.get(name) or {}).get("status")
        sb = (pil_b.get(name) or {}).get("status")
        if sa != sb:
            out.append(f"evidence_changed:{name}")
    ea, eb = _num(a.get("ev_per_unit")), _num(b.get("ev_per_unit"))
    ev = (ea is None) != (eb is None) or (ea is not None and eb is not None
                                          and abs(ea - eb) > EV_TOL)
    if ev and not price and not prob and "line_changed" not in out:
        out.append("ev_formula")
    if a.get("verdict") != b.get("verdict") and not out:
        out.append("verdict_rule")
    sa_e, sb_e = sportsbook_edge(a), sportsbook_edge(b)
    if sa_e is not None and sb_e is not None and (sa_e > 0) != (sb_e > 0):
        out.append("sportsbook_edge_sign_flip")
    return out


def _view(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {k: p.get(k) for k in ("verdict", "line", "best_odds", "best_book", "final_p",
                                  "ev_per_unit")} | {"sportsbook_edge": sportsbook_edge(p)}


def compare_cards(original: dict[str, Any], rebuilt: dict[str, Any],
                  context: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """``context`` maps a cause to extra labels that explain it (``rebuild.context_labels``)."""
    diffs: list[dict[str, Any]] = []
    unchanged = 0
    for a, b in _match(list(original.get("picks") or []), list(rebuilt.get("picks") or [])):
        if a is None or b is None:
            label = "new_candidate" if a is None else "not_rebuilt_as_candidate"
            cause = [label]
        else:
            cause = causes(a, b)
            if not cause and a.get("verdict") == b.get("verdict"):
                unchanged += 1
                continue
        for c in list(cause):
            for extra in (context or {}).get(c, []):
                if extra not in cause:
                    cause.append(extra)
        ref = a or b or {}
        diffs.append({
            "event_id": ref.get("event_id"), "player_name": ref.get("player_name"),
            "market": ref.get("market"), "position": ref.get("position"),
            "causes": cause,
            "verdict": [a.get("verdict") if a else None, b.get("verdict") if b else None],
            "original": _view(a), "rebuilt": _view(b),
        })
    cause_counts = Counter(c for d in diffs for c in d["causes"])
    transitions = Counter(f"{d['verdict'][0]}->{d['verdict'][1]}" for d in diffs
                          if d["verdict"][0] != d["verdict"][1])
    neg_validated = [
        {"event_id": p.get("event_id"), "player_name": p.get("player_name"),
         "market": p.get("market"), "position": p.get("position"), "line": p.get("line"),
         "sportsbook_edge": sportsbook_edge(p)}
        for p in original.get("picks") or []
        if p.get("verdict") == "VALIDATED" and (sportsbook_edge(p) or 0.0) < 0
    ]
    return {
        "edge_basis": "sportsbook prices only: " + ", ".join(sorted(SPORTSBOOKS)),
        "original_picks": len(original.get("picks") or []),
        "rebuilt_picks": len(rebuilt.get("picks") or []),
        "unchanged": unchanged,
        "differences": diffs,
        "cause_counts": dict(sorted(cause_counts.items())),
        "verdict_transitions": dict(sorted(transitions.items())),
        "original_validated_negative_sportsbook_edge": neg_validated,
    }


def render_markdown(report: dict[str, Any], header: dict[str, Any]) -> str:
    lines = [f"# NFL rebuild comparison: {header.get('slate')} ({header.get('rebuild_id')})", "",
             f"- Source run: `{header.get('source_run_id')}` at as_of `{header.get('as_of_utc')}`",
             f"- Rebuilt run: `{header.get('rebuilt_run_id')}` (mode "
             f"{header.get('rebuilt_mode')}, bundle-only)",
             f"- Edge basis: {report['edge_basis']}",
             *([f"- **nflverse fetched after as_of:** "
                f"{', '.join(header.get('nflverse_post_as_of_files') or [])} "
                "(stats may include later revisions)"]
               if header.get("nflverse_post_as_of") else []),
             f"- Picks: original {report['original_picks']}, rebuilt {report['rebuilt_picks']}, "
             f"unchanged {report['unchanged']}, differences {len(report['differences'])}", "",
             "## Causes", ""]
    lines += [f"- {k}: {v}" for k, v in report["cause_counts"].items()] or ["- none"]
    lines += ["", "## Verdict transitions", ""]
    lines += [f"- {k}: {v}" for k, v in report["verdict_transitions"].items()] or ["- none"]
    lines += ["", "## Original VALIDATED picks with negative sportsbook edge", ""]
    lines += [f"- {r['player_name']} {r['market']} {r['position']} {r['line']} "
              f"(edge {r['sportsbook_edge']})"
              for r in report["original_validated_negative_sportsbook_edge"]] or ["- none"]
    lines += ["", "## Differences", "", "| Player | Market | Side | Verdict | Causes |",
              "|---|---|---|---|---|"]
    for d in report["differences"]:
        lines.append(f"| {d['player_name']} | {d['market']} | {d['position']} | "
                     f"{d['verdict'][0]} -> {d['verdict'][1]} | {', '.join(d['causes'])} |")
    return "\n".join(lines) + "\n"
