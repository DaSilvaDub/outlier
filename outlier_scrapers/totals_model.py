"""Totals ladder + L10 probability model for candidate/opportunity rows.

Game and team total rows in ``opportunities.csv`` historically only received a
``model_prob``/``edge_pct`` when Outlier's EV summary or a proxy market priced
them, which left every OVER total blank. This module derives probabilities for
those rows deterministically from the normalized games feed:

- ``market_consensus_prob``: two-sided devig of the full book ladder at the
  row's exact line (median across books, same math as the totals boards).
- ``independent_model_prob``: the side-specific recent-games signal — Outlier's
  per-line L10 hit rate (``l10Results``). Game totals combine the home and away
  team samples; team totals use the matching team's sample.
- ``final_blended_prob``: sample-weighted blend, market-dominant.

Reasoning agents consume the output; they never recompute probability or edge.
"""

from __future__ import annotations

import re
from typing import Any

from outlier_scrapers.game_totals import (
    BASE_INDEPENDENT_WEIGHT,
    TOTALS_MODEL_DIVERGENCE_THRESHOLD,
    _l10_over_for_record,
    _to_float,
    aggregate_line_p_over,
    blend_over_probability,
    build_market_ladder,
    derive_push_prob,
    is_full_game_total,
    logical_market_key,
)
from outlier_scrapers.sizing import compute_sizing
from outlier_scrapers.team_totals import team_total_propositions

__all__ = [
    "BASE_INDEPENDENT_WEIGHT",
    "SOURCE_BLEND",
    "SOURCE_DEVIG",
    "backfill_totals_probabilities",
    "blend_over_probability",
    "build_totals_prob_index",
]

SOURCE_BLEND = "totals_ladder_blend"
SOURCE_DEVIG = "totals_ladder_devig"

FLAG_MODEL = "totals_ladder_model"
FLAG_SINGLE_BOOK = "totals_single_book"
FLAG_PUSH_NO_PROB = "push_capable_no_prob"

_SIDE_RE = re.compile(r"\b(OVER|UNDER)\b", re.IGNORECASE)


def _is_indexable_total(rec: dict[str, Any], *, league: str) -> bool:
    """Full-game game totals plus the league's team-total propositions."""
    if not is_full_game_total(rec):
        return False
    mt = str(rec.get("market_type") or "").upper()
    prop = str(rec.get("proposition") or rec.get("market") or "").upper()
    if mt == "GAMELINE":
        return prop == "TOTAL"
    if mt == "TEAM_PROP":
        return prop in team_total_propositions(league)
    return False


def build_totals_prob_index(
    games_norm: dict[str, Any] | None, *, league: str
) -> dict[str, dict[float, dict[str, Any]]]:
    """Map every raw totals market_id -> {line: probability entry}.

    Entries carry ``p_over`` (two-sided ladder devig, None when no valid
    consensus), ``book_count``, ``flags``, ``l10_over`` (recent-games signal
    for the OVER side) and ``push_prob`` (0.0 for half lines, derived from the
    bracketing ladder for integer lines, None when underivable). Raw market_ids
    that split one logical market share the same line map.
    """
    records = (games_norm or {}).get("records") or []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not _is_indexable_total(rec, league=league):
            continue
        if not str(rec.get("event_id") or "").strip():
            continue
        grouped.setdefault(logical_market_key(rec), []).append(rec)

    index: dict[str, dict[float, dict[str, Any]]] = {}
    for market_records in grouped.values():
        ladder = build_market_ladder(market_records)
        if not ladder:
            continue
        ladder_p: dict[float, float] = {}
        raw_entries: dict[float, dict[str, Any]] = {}
        for line, sides in ladder.items():
            p_over, book_count, flags = aggregate_line_p_over(
                sides.get("over", {}), sides.get("under", {})
            )
            if p_over is not None:
                ladder_p[line] = p_over
            raw_entries[line] = {
                "p_over": p_over,
                "book_count": book_count,
                "flags": flags,
            }

        l10_by_line: dict[float, dict[str, Any]] = {}
        for rec in market_records:
            if str(rec.get("position") or "").upper() != "OVER":
                continue
            rec_line = _to_float(rec.get("line"))
            if rec_line is None or rec_line in l10_by_line:
                continue
            l10 = _l10_over_for_record(rec)
            if l10 is not None:
                l10_by_line[rec_line] = l10

        lines: dict[float, dict[str, Any]] = {}
        for line, entry in raw_entries.items():
            if line == int(line):
                push_prob = derive_push_prob(line, ladder_p)
            else:
                push_prob = 0.0
            lines[line] = {
                **entry,
                "l10_over": l10_by_line.get(line),
                "push_prob": push_prob,
            }

        for rec in market_records:
            mid = str(rec.get("market_id") or "").strip()
            if mid:
                index.setdefault(mid, lines)
    return index


def _side_from_selection(selection: Any) -> str | None:
    match = _SIDE_RE.search(str(selection or ""))
    return match.group(1).upper() if match else None


def _merge_flags(existing: Any, extra: list[str]) -> str:
    flags = [f for f in str(existing or "").split(";") if f]
    flags.extend(extra)
    return ";".join(dict.fromkeys(flags))


def backfill_totals_probabilities(
    rows: list[dict[str, Any]],
    games_norm_by_league: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return new rows with totals probabilities filled where missing.

    Rows that already carry a model_prob (Outlier devig / proxy market) are
    returned unchanged; inputs are never mutated. Mirrors the proxy-market
    path in pack.build_row: probabilities and edge are filled, but
    recommended_units_pre_news stays empty — these rows never passed the
    Outlier EV gates.
    """
    indexes = {
        str(lg).upper(): build_totals_prob_index(payload, league=str(lg).upper())
        for lg, payload in (games_norm_by_league or {}).items()
    }
    out: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        out.append(row)
        if str(row.get("model_prob") or "").strip():
            continue
        index = indexes.get(str(row.get("sport") or "").upper())
        if not index:
            continue
        lines = index.get(str(row.get("market_id") or "").strip())
        if not lines:
            continue
        line = _to_float(row.get("line"))
        if line is None or line not in lines:
            continue
        side = _side_from_selection(row.get("selection"))
        if side is None:
            continue
        entry = lines[line]
        p_over = entry.get("p_over")
        if p_over is None:
            continue

        l10_over = entry.get("l10_over")
        used_l10 = bool(isinstance(l10_over, dict) and l10_over.get("pct") is not None)
        p_side_market = p_over if side == "OVER" else 1.0 - p_over

        push_prob = _to_float(row.get("push_prob"))
        if push_prob is None:
            push_prob = entry.get("push_prob")
            if push_prob is not None:
                row["push_prob"] = push_prob

        # Two-way prices at integer lines are conditional on no push; convert
        # to unconditional win probability before sizing (same math as the
        # totals boards' push path).
        no_push_factor = 1.0 - push_prob if push_prob is not None else 1.0
        consensus = p_side_market * no_push_factor
        model_prob = consensus

        row["model_prob"] = model_prob
        row["market_consensus_prob"] = consensus
        row["final_blended_prob"] = model_prob
        row["model_prob_source"] = SOURCE_DEVIG
        extra_flags = [FLAG_MODEL]
        if used_l10:
            l10_side = l10_over["pct"] if side == "OVER" else 1.0 - l10_over["pct"]
            row["recency_hit_prob"] = l10_side * no_push_factor
            row["independent_model_prob"] = ""
            if abs(p_side_market - l10_side) >= TOTALS_MODEL_DIVERGENCE_THRESHOLD:
                extra_flags.append("totals_model_divergence")

        if int(entry.get("book_count") or 0) < 2:
            extra_flags.append(FLAG_SINGLE_BOOK)

        decimal_price = _to_float(row.get("decimal_price"))
        if push_prob is None:
            extra_flags.append(FLAG_PUSH_NO_PROB)
        elif decimal_price is not None:
            sizing = compute_sizing(
                decimal_price=decimal_price,
                model_prob=model_prob,
                push_prob=push_prob,
            )
            row["implied_prob"] = sizing.implied_prob
            if sizing.edge_pct is not None:
                row["edge_pct"] = sizing.edge_pct
            if sizing.kelly_025_units is not None:
                row["kelly_025_units"] = max(0.0, sizing.kelly_025_units)
            row["max_units"] = sizing.max_units
        row["sizing_flags"] = _merge_flags(row.get("sizing_flags"), extra_flags)
    return out
