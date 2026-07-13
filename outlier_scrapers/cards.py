"""Triage "cards" layer.

Joins normalized props + line-movement + EV + insights into one ranked,
per-``market_id`` view and emits two boards:

* **Board A - Verified EV**: cards backed by Outlier ``calculated_ev`` using the
  ``AVERAGE`` devig method. Ranked on the real EV number.
* **Board B - Signal candidates**: every other card. Ranked on a descriptive
  composite (hit-rate, insight agreement, line-movement corroboration, ORF).
  A two-way ``proxy_market`` edge is attached when both sides price the same
  line, but it is never presented as Outlier EV.

Inputs are the ``*_latest.json`` normalized files only. Output goes to
``data/<LEAGUE>/cards/`` as ``cards_latest.json`` + ``cards_latest.html`` plus a
timestamped archive copy, mirroring the other scraper modules.

CLI::

    python -m outlier_scrapers.cards --league WNBA
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cards_html import render_html
from .normalizer import _to_float, _to_int, implied_probability
from .paths import league_paths
from .registry import supported_leagues


def write_json(path, payload: dict[str, Any]) -> None:
    """Write ``payload`` as pretty JSON (local copy; mirrors props.write_json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

# --------------------------------------------------------------------------- #
# Tunable constants
# --------------------------------------------------------------------------- #

EV_METHOD = "AVERAGE"  # headline devig method per project decision.

# Cross-feed snapshot skew: warn if the three latest files were generated more
# than this many hours apart (joining stale movement onto fresh props, etc.).
MAX_SNAPSHOT_SKEW_HOURS = 6.0

# Recency weights for the side-specific hit-rate blend (renormalized over the
# components that are actually present).
HIT_WEIGHTS: dict[str, float] = {
    "l5_pct": 0.40,
    "l10_pct": 0.30,
    "l20_pct": 0.20,
    "season_pct": 0.10,
}

# Board B composite weights (must sum to 1.0).
SIGNAL_WEIGHTS: dict[str, float] = {
    "hit": 0.40,
    "insight": 0.20,
    "movement": 0.20,
    "orf": 0.20,
}

# Board A EV buckets (calculated_ev_pct thresholds).
EV_BUCKETS: tuple[tuple[float, str], ...] = (
    (5.0, "Strong"),
    (2.0, "Lean"),
    (0.0, "Monitor"),
)

# Board B signal buckets (composite 0-100 thresholds).
SIGNAL_BUCKETS: tuple[tuple[float, str], ...] = (
    (68.0, "Strong Signal"),
    (58.0, "Lean"),
    (50.0, "Monitor"),
)

HIGH_VIG_PCT = 8.0  # flag Board A cards whose EV side carries more vig than this.


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _normalized_latest_path(league: str, stem: str):
    paths = league_paths(league)
    return paths.normalized / f"{paths.league.lower()}_{stem}_latest.json"


def load_latest(league: str, stem: str) -> dict[str, Any] | None:
    """Load a ``*_latest.json`` normalized payload, or ``None`` if missing."""
    path = _normalized_latest_path(league, stem)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _records(payload: dict[str, Any] | None, key: str = "records") -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


# --------------------------------------------------------------------------- #
# Snapshot freshness / skew
# --------------------------------------------------------------------------- #


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def snapshot_skew(generated: dict[str, str | None]) -> dict[str, Any]:
    """Report the max pairwise age gap (hours) across feed snapshots."""
    parsed = {k: _parse_iso(v) for k, v in generated.items()}
    present = {k: v for k, v in parsed.items() if v is not None}
    if len(present) < 2:
        return {"skew_hours": None, "is_skewed": False, "generated_at": generated, "reason": None}
    # Compare on a common tz-aware basis.
    stamps = [v if v.tzinfo else v.astimezone() for v in present.values()]
    lo, hi = min(stamps), max(stamps)
    skew_hours = round((hi - lo).total_seconds() / 3600.0, 2)
    reason = None
    if skew_hours > MAX_SNAPSHOT_SKEW_HOURS:
        newest = max(present, key=lambda k: present[k])
        oldest = min(present, key=lambda k: present[k])
        reason = f"{oldest} is {skew_hours:.2f}h older than {newest}"
    return {
        "skew_hours": skew_hours,
        "is_skewed": skew_hours > MAX_SNAPSHOT_SKEW_HOURS,
        "generated_at": generated,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# Devig / proxy market edge (Board B only, never labelled as EV)
# --------------------------------------------------------------------------- #


def two_way_fair(over_odds: Any, under_odds: Any) -> dict[str, float] | None:
    """No-vig fair probabilities (%) for a two-way market.

    Returns ``{"OVER": p, "UNDER": p, "overround": r}`` or ``None`` when either
    side's price is unusable.
    """
    p_over = implied_probability(over_odds)
    p_under = implied_probability(under_odds)
    if p_over is None or p_under is None:
        return None
    overround = p_over + p_under
    if overround <= 0:
        return None
    return {
        "OVER": round(100.0 * p_over / overround, 3),
        "UNDER": round(100.0 * p_under / overround, 3),
        "overround": round(overround, 3),
    }


def proxy_market_edge(
    side: str,
    side_books: list[dict[str, Any]],
    fair_prob_pct: float | None,
) -> dict[str, Any] | None:
    """Best per-book edge of ``side`` vs the two-way no-vig fair probability.

    Positive ``edge_pct`` means a book prices the side cheaper than the field's
    own de-vigged consensus. This is a *market* inefficiency proxy, NOT Outlier
    EV; callers must surface ``source = "proxy_market"`` alongside it.
    """
    if fair_prob_pct is None or not side_books:
        return None
    best = None
    for book in side_books:
        implied = implied_probability(book.get("odds"))
        if implied is None:
            continue
        edge = round(fair_prob_pct - implied, 3)
        if best is None or edge > best["edge_pct"]:
            best = {
                "book": book.get("book"),
                "odds": book.get("odds"),
                "implied_pct": implied,
                "edge_pct": edge,
            }
    if best is None:
        return None
    best.update({"fair_prob_pct": fair_prob_pct, "side": side, "source": "proxy_market"})
    return best


# --------------------------------------------------------------------------- #
# Descriptive scoring helpers
# --------------------------------------------------------------------------- #


def recency_hit_pct(side_data: dict[str, Any]) -> float | None:
    """Recency-weighted, side-specific hit rate (already 0-100 per side)."""
    num = 0.0
    den = 0.0
    for key, weight in HIT_WEIGHTS.items():
        value = side_data.get(key)
        if isinstance(value, (int, float)):
            num += weight * float(value)
            den += weight
    return round(num / den, 3) if den > 0 else None


def movement_corroboration(side: str, mv: dict[str, Any] | None) -> float:
    """-1..1: positive when the market is moving toward ``side``.

    For an OVER, a falling line and a shortening price both help; mirror for
    UNDER. Unknown deltas contribute nothing.
    """
    if not mv:
        return 0.0
    signals: list[float] = []
    line_delta = _to_float(mv.get("line_delta_from_open"))
    if line_delta:
        toward = -line_delta if side == "OVER" else line_delta
        signals.append(1.0 if toward > 0 else -1.0)
    odds_delta = _to_float(mv.get("odds_delta_from_open"))
    if odds_delta:
        # The side's own price shortening (delta < 0) = market firming toward it.
        signals.append(1.0 if odds_delta < 0 else -1.0)
    if not signals:
        return 0.0
    return round(sum(signals) / len(signals), 3)


def insight_component(side: str, insights: list[dict[str, Any]]) -> tuple[float, bool]:
    """Return (0-100 score, conflict?) from side-specific insights.

    Agreeing insights (same side) blend relevancy and hit-rate upward from the
    neutral 50; an opposing high-relevancy insight flips a conflict flag.
    """
    agree = [i for i in insights if i.get("side") == side]
    oppose = [i for i in insights if i.get("side") and i.get("side") != side]
    conflict = any((_to_int(i.get("relevancy")) or 0) >= 70 for i in oppose)
    if not agree:
        return (40.0 if conflict else 50.0), conflict
    parts: list[float] = []
    for i in agree:
        relevancy = _to_int(i.get("relevancy")) or 0
        hit = i.get("hit_rate_pct")
        hit = float(hit) if isinstance(hit, (int, float)) else 50.0
        parts.append(0.5 * min(relevancy, 100) + 0.5 * hit)
    score = sum(parts) / len(parts)
    if conflict:
        score = 0.5 * score + 0.5 * 40.0
    return round(min(max(score, 0.0), 100.0), 3), conflict


def signal_score(
    side: str,
    side_data: dict[str, Any],
    mv: dict[str, Any] | None,
    insights: list[dict[str, Any]],
) -> dict[str, Any]:
    """Board B composite (0-100) for one side, with component breakdown."""
    hit = recency_hit_pct(side_data)
    hit_component = hit if hit is not None else 50.0
    orf = side_data.get("orf_score")
    orf_component = (float(orf) * 100.0) if isinstance(orf, (int, float)) else 50.0
    insight_score, conflict = insight_component(side, insights)
    corro = movement_corroboration(side, mv)
    movement_component = 50.0 + 25.0 * corro
    composite = (
        SIGNAL_WEIGHTS["hit"] * hit_component
        + SIGNAL_WEIGHTS["orf"] * min(orf_component, 100.0)
        + SIGNAL_WEIGHTS["insight"] * insight_score
        + SIGNAL_WEIGHTS["movement"] * movement_component
    )
    return {
        "composite": round(min(max(composite, 0.0), 100.0), 3),
        "hit_pct": hit,
        "orf_component": round(orf_component, 3),
        "insight_component": insight_score,
        "movement_corroboration": corro,
        "insight_conflict": conflict,
    }


def _bucket(value: float | None, buckets: tuple[tuple[float, str], ...], default: str) -> str:
    if value is None:
        return default
    for threshold, label in buckets:
        if value >= threshold:
            return label
    return default


# --------------------------------------------------------------------------- #
# Indexing the feeds
# --------------------------------------------------------------------------- #


@dataclass
class Indexes:
    props_by_market: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    movement_by_market: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    ev_by_market: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    insights_by_outcome: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    insights_by_market_side: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    enrichment: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrichment_loaded: bool = False


def build_indexes(
    props: list[dict[str, Any]],
    movement: list[dict[str, Any]],
    ev_records: list[dict[str, Any]],
    insights: list[dict[str, Any]],
) -> Indexes:
    idx = Indexes()
    for row in props:
        mid = row.get("market_id")
        if mid:
            idx.props_by_market.setdefault(str(mid), []).append(row)
    for row in movement:
        mid = row.get("market_id")
        side = str(row.get("side") or "").upper()
        if mid and side:
            idx.movement_by_market.setdefault(str(mid), {})[side] = row
    for row in ev_records:
        src = str(row.get("ev_source") or "").upper()
        meth = str(row.get("calculated_ev_method") or "").upper()
        if not ((src == "NATIVE" and meth == EV_METHOD) or (src == "LOCAL" and meth == "LOCAL_PROPORTIONAL")):
            continue
        if row.get("calculated_ev_pct") is None:
            continue
        mid = row.get("market_id")
        if mid:
            idx.ev_by_market.setdefault(str(mid), []).append(row)
    for row in insights:
        # Player-prop insights only; team-level rows (no player/side) don't join
        # to a player card.
        if not row.get("player_id") and not row.get("market_outcome_id"):
            continue
        outcome = row.get("market_outcome_id")
        if outcome:
            idx.insights_by_outcome.setdefault(str(outcome), []).append(row)
        mid = row.get("market_id")
        side = str(row.get("side") or "").upper()
        if mid and side:
            idx.insights_by_market_side.setdefault((str(mid), side), []).append(row)
    return idx


def _match_insights(
    idx: Indexes, market_id: str, side: str, outcome_id: Any, line: Any
) -> list[dict[str, Any]]:
    # Exact outcome_id join is authoritative (encodes side+line).
    if outcome_id and str(outcome_id) in idx.insights_by_outcome:
        return idx.insights_by_outcome[str(outcome_id)]
    # Fallback by market_id+side must still match the card's line, so a 5.5
    # insight never attaches to a 4.5 card.
    fallback = idx.insights_by_market_side.get((market_id, side), [])
    if line is None:
        return fallback
    return [i for i in fallback if _to_float(i.get("line")) == _to_float(line)]


def _slim_insight(i: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": i.get("text"),
        "side": i.get("side"),
        "line": i.get("line"),
        "hit_rate_pct": i.get("hit_rate_pct"),
        "last_n_record": i.get("last_n_record"),
        "relevancy": i.get("relevancy"),
    }


# --------------------------------------------------------------------------- #
# Card assembly
# --------------------------------------------------------------------------- #


def _identity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Best-effort player/market identity from whichever feed rows exist."""
    for row in rows:
        if row and row.get("player"):
            sctx = row.get("sport_context") or {}
            return {
                "player": row.get("player"),
                "player_id": row.get("player_id"),
                "market": row.get("market") or row.get("market_raw"),
                "market_raw": row.get("market_raw"),
                "market_label": row.get("market_label") or sctx.get("market_label"),
                "proposition": row.get("proposition") or sctx.get("proposition"),
                "scope": row.get("scope") or sctx.get("scope"),
                "team": row.get("team"),
                "opponent": row.get("opponent"),
                "matchup": row.get("matchup") or row.get("matchup_raw"),
                "event_id": row.get("event_id"),
            }
    # Game/team-market rows carry no player; key on the event + proposition.
    # ``proposition`` must be present so assemble_game_card can resolve the valid
    # sides via game_sides() instead of falling back to positional sides.
    for row in rows:
        if row and (row.get("proposition") or row.get("position")):
            sctx = row.get("sport_context") or {}
            return {
                "event_id": row.get("event_id"),
                "proposition": row.get("proposition"),
                "market": row.get("market") or row.get("market_raw"),
                "market_raw": row.get("market_raw"),
                "market_label": row.get("market_label") or sctx.get("market_label"),
                "scope": row.get("scope") or sctx.get("scope"),
                "team": row.get("team"),
                "opponent": row.get("opponent"),
                "matchup": row.get("matchup") or row.get("matchup_raw"),
            }
    return {}


def _group_key(identity: dict[str, Any]) -> str:
    # Game/team cards carry no player_id, so key them on event + proposition to
    # keep distinct matchups from colliding. Player cards keep their existing
    # player_id|market|scope key (identity has "player", never "proposition").
    if "proposition" in identity:
        keys = ("event_id", "proposition", "market", "scope")
    else:
        keys = ("player_id", "market", "scope")
    return "|".join(str(identity.get(k) or "") for k in keys)


def _side_data_from_prop(prop: dict[str, Any]) -> dict[str, Any]:
    ctx = prop.get("sport_context") or {}
    return {
        "side": prop.get("side"),
        "line": prop.get("line"),
        "best_odds": prop.get("best_odds"),
        "books": prop.get("books") or [],
        "outcome_id": ctx.get("outcome_id"),
        "orf_score": ctx.get("orf_score"),
        "l5_pct": prop.get("l5_pct"),
        "l10_pct": prop.get("l10_pct"),
        "l20_pct": prop.get("l20_pct"),
        "h2h_pct": prop.get("h2h_pct"),
        "season_pct": prop.get("season_pct"),
    }


def _ev_for_side(
    ev_rows: list[dict[str, Any]],
    side: str,
    main_outcome_id: str | None = None,
    main_line: float | None = None,
) -> dict[str, Any] | None:
    """Aggregate book-level EV rows for one side; headline = best EV%.
    Prefers EV records matching the main line's outcome_id or exact line before falling back."""
    rows = [r for r in ev_rows if str(r.get("side") or "").upper() == side]
    if not rows:
        return None

    matched_rows = [r for r in rows if r.get("outcome_id") == main_outcome_id] if main_outcome_id else []
    if not matched_rows and main_line is not None:
        # Normalized EV rows carry the line as ``current_line`` (no ``line`` key).
        matched_rows = [r for r in rows if _to_float(r.get("current_line")) == main_line]

    is_fallback = False
    if matched_rows:
        rows = matched_rows
    else:
        is_fallback = True

    best = max(
        rows,
        key=lambda r: (
            r.get("calculated_ev_pct") if r.get("calculated_ev_pct") is not None else float("-inf"),
            r.get("record_id") or ""
        )
    )
    books = sorted(
        (
            {
                "book": r.get("book"),
                "book_odds": r.get("book_odds"),
                "calculated_ev_pct": r.get("calculated_ev_pct"),
                "kelly_pct": r.get("kelly_pct"),
                "max_bet": r.get("max_bet"),
                "book_state": r.get("book_state"),
            }
            for r in rows
            if r.get("book")
        ),
        key=lambda b: b.get("calculated_ev_pct") if b.get("calculated_ev_pct") is not None else float("-inf"),
        reverse=True,
    )
    return {
        "best_ev_pct": best.get("calculated_ev_pct"),
        "method": best.get("calculated_ev_method"),
        "kelly_pct": best.get("kelly_pct"),
        "vig_pct": best.get("vig_pct"),
        "width_pct": best.get("width_pct"),
        "devig_odds": best.get("devig_odds"),
        "devig_decimal": best.get("devig_decimal"),
        "outcome_id": best.get("outcome_id"),
        "best_record_id": best.get("record_id"),
        "ev_source": best.get("ev_source"),
        "is_alt_line_fallback": is_fallback,
        "ev_book_count": len(books),
        "ev_books": books,
    }


def _pick_main_side_row(
    side_rows: list[dict[str, Any]],
    mv: dict[str, Any] | None,
    ev_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose the *main* line for a side among a market's alt lines.

    A ``market_id`` bundles many alt lines (e.g. PTS 4.5..19.5). On an
    EV-bearing side we anchor to the EV outcome's line first: Board A ranks that
    exact bet, so the headline line must match it (otherwise the card shows one
    line's price with another line's EV). Otherwise use the movement-tracked
    line, then fall back to the price closest to pick'em (implied ~50%), since
    "most books" is unreliable (longshot alts can carry more books).
    """
    ev_lines = [_to_float(r.get("current_line")) for r in ev_rows if r.get("current_line") is not None]
    target = next((x for x in ev_lines if x is not None), None)
    if target is None and mv and mv.get("current_line") is not None:
        target = _to_float(mv.get("current_line"))
    if target is not None:
        exact = [r for r in side_rows if _to_float(r.get("line")) == target]
        if exact:
            return max(exact, key=lambda r: len(r.get("books") or []))

    def pickem_key(row: dict[str, Any]) -> tuple[float, int]:
        ip = implied_probability(row.get("best_odds"))
        dist = abs((ip if ip is not None else 0.0) - 50.0)
        return (dist, -len(row.get("books") or []))

    return min(side_rows, key=pickem_key)


def assemble_card(market_id: str, idx: Indexes) -> dict[str, Any]:
    props = idx.props_by_market.get(market_id, [])
    ev_rows = idx.ev_by_market.get(market_id, [])
    movement = idx.movement_by_market.get(market_id, {})
    identity = _identity(props + ev_rows)

    # Group prop rows per side, choose the main line, keep the alts (per
    # AGENTS.md: don't drop rows).
    rows_by_side: dict[str, list[dict[str, Any]]] = {"OVER": [], "UNDER": []}
    for prop in props:
        side = str(prop.get("side") or "").upper()
        if side in rows_by_side:
            rows_by_side[side].append(prop)

    main_row: dict[str, dict[str, Any]] = {}
    for side, rws in rows_by_side.items():
        if not rws:
            continue
        side_ev = [r for r in ev_rows if str(r.get("side") or "").upper() == side]
        main_row[side] = _pick_main_side_row(rws, movement.get(side), side_ev)

    _align_main_lines(main_row, rows_by_side, ev_rows, movement, str(identity.get("proposition") or ""))

    alt_lines: dict[str, list[dict[str, Any]]] = {}
    for side, rws in rows_by_side.items():
        if not rws or side not in main_row:
            continue
        chosen = main_row[side]
        alt_lines[side] = [
            {"line": r.get("line"), "best_odds": r.get("best_odds"), "book_count": len(r.get("books") or [])}
            for r in sorted(rws, key=lambda r: (_to_float(r.get("line")) or 0.0))
            if r is not chosen
        ]

    sides_data = {s: _side_data_from_prop(r) for s, r in main_row.items()}

    # Proxy two-way fair only when both sides quote the SAME line (review #3:
    # this is a book-vs-consensus market edge, never Outlier EV).
    over_r, under_r = main_row.get("OVER"), main_row.get("UNDER")
    fair = None
    if over_r and under_r and _to_float(over_r.get("line")) == _to_float(under_r.get("line")):
        fair = two_way_fair(over_r.get("best_odds"), under_r.get("best_odds"))

    side_views: dict[str, dict[str, Any]] = {}
    for side, sdata in sides_data.items():
        mv = movement.get(side)
        matched = _match_insights(idx, market_id, side, sdata.get("outcome_id"), sdata.get("line"))
        score = signal_score(side, sdata, mv, matched)
        proxy = proxy_market_edge(side, sdata.get("books", []), fair.get(side)) if fair else None

        outcome_id = str(sdata.get("outcome_id") or "")
        enrichment_data = idx.enrichment.get(outcome_id) if outcome_id else None

        side_view = {
            **{k: sdata.get(k) for k in ("side", "line", "best_odds", "outcome_id", "orf_score")},
            "hit_rates": {k: sdata.get(k) for k in ("l5_pct", "l10_pct", "l20_pct", "h2h_pct", "season_pct")},
            "alt_lines": alt_lines.get(side, []),
            "movement": _slim_movement(mv),
            "signal": score,
            "proxy_market_edge": proxy,
            "insights": [_slim_insight(i) for i in matched],
            "ev": _ev_for_side(ev_rows, side, sdata.get("outcome_id"), _to_float(sdata.get("line"))),
        }

        if idx.enrichment_loaded:
            side_view["per_book_odds"] = enrichment_data.get("per_book_odds") if enrichment_data else []
            side_view["public_money"] = enrichment_data.get("public_money") if enrichment_data else None

        side_views[side] = side_view

    card = {
        "card_id": market_id,
        "league": next((r.get("league") for r in props + ev_rows if r.get("league")), None),
        "group_key": _group_key(identity),
        **identity,
        "sides": side_views,
        "fair": fair,
    }
    _route_and_rank(card)
    return card



def _spread_sign_conflict(home_line: Any, away_line: Any) -> bool:
    """True when a two-sided SPREAD market's HOME/AWAY lines aren't mirror-image.

    A correctly-priced spread/run-line/puck-line always has HOME == -AWAY (e.g.
    -1.5 / +1.5). If a feed ever ships both sides with the same sign or mismatched
    magnitude, the sign is unrecoverable from either row alone — flag it instead
    of letting a corrupted line reach the desk looking legitimate.
    """
    home_f, away_f = _to_float(home_line), _to_float(away_line)
    if home_f is None or away_f is None:
        return False
    return abs(home_f + away_f) > 1e-9


def _align_main_lines(
    main_row: dict[str, dict[str, Any]],
    rows_by_side: dict[str, list[dict[str, Any]]],
    ev_rows: list[dict[str, Any]],
    movement: dict[str, Any],
    proposition: str,
) -> None:
    """Force mirror-image spreads or matching totals when independent selection diverges."""
    if len(main_row) != 2:
        return

    s1, s2 = list(main_row.keys())
    r1, r2 = main_row[s1], main_row[s2]

    # Local deferred import to avoid circular dependency
    from outlier_scrapers.pack import SIGNED_MARGIN_PROPOSITIONS
    is_spread = proposition.strip().upper() in SIGNED_MARGIN_PROPOSITIONS

    has_conflict = False
    if is_spread:
        has_conflict = _spread_sign_conflict(r1.get("line"), r2.get("line"))
    else:
        has_conflict = _to_float(r1.get("line")) != _to_float(r2.get("line"))

    if not has_conflict:
        return

    def _strength(side: str, row: dict[str, Any]) -> int:
        side_ev = [r for r in ev_rows if str(r.get("side") or "").upper() == side]
        line = _to_float(row.get("line"))
        if any(_to_float(e.get("current_line")) == line for e in side_ev):
            return 3
        mv = movement.get(side)
        if mv and _to_float(mv.get("current_line")) == line:
            return 2
        return 1

    str1 = _strength(s1, r1)
    str2 = _strength(s2, r2)

    winner_s, loser_s = (s1, s2) if str1 >= str2 else (s2, s1)
    winner_val = _to_float(main_row[winner_s].get("line"))

    if winner_val is not None:
        target_loser_val = -winner_val if is_spread else winner_val
        exact = [r for r in rows_by_side[loser_s] if _to_float(r.get("line")) == target_loser_val]
        if exact:
            main_row[loser_s] = max(exact, key=lambda r: len(r.get("books") or []))


def assemble_game_card(market_id: str, idx: Indexes) -> dict[str, Any]:
    from .normalizer import game_sides
    props = idx.props_by_market.get(market_id, [])
    ev_rows = idx.ev_by_market.get(market_id, [])
    movement = idx.movement_by_market.get(market_id, {})
    identity = _identity(props + ev_rows)
    proposition = str(identity.get("proposition") or "")

    valid_sides = game_sides(proposition)

    # Group by position
    rows_by_side: dict[str, list[dict[str, Any]]] = {s: [] for s in valid_sides} if valid_sides else {}
    for prop in props:
        pos = str(prop.get("position") or "").upper()
        if not valid_sides:
            if pos not in rows_by_side:
                rows_by_side[pos] = []
            rows_by_side[pos].append(prop)
        elif pos in rows_by_side:
            rows_by_side[pos].append(prop)

    main_row: dict[str, dict[str, Any]] = {}
    for side, rws in rows_by_side.items():
        if not rws:
            continue
        side_ev = [r for r in ev_rows if str(r.get("side") or "").upper() == side]
        main_row[side] = _pick_main_side_row(rws, movement.get(side), side_ev)

    _align_main_lines(main_row, rows_by_side, ev_rows, movement, proposition)

    alt_lines: dict[str, list[dict[str, Any]]] = {}
    for side, rws in rows_by_side.items():
        if not rws or side not in main_row:
            continue
        chosen = main_row[side]
        alt_lines[side] = [
            {"line": r.get("line"), "best_odds": r.get("best_odds"), "book_count": len(r.get("books") or [])}
            for r in sorted(rws, key=lambda r: (_to_float(r.get("line")) or 0.0))
            if r is not chosen
        ]

    sides_data = {s: _side_data_from_game(r, s) for s, r in main_row.items()}

    over_r, under_r = main_row.get("OVER"), main_row.get("UNDER")
    fair = None
    if over_r and under_r and _to_float(over_r.get("line")) == _to_float(under_r.get("line")):
        fair = two_way_fair(over_r.get("best_odds"), under_r.get("best_odds"))

    side_views: dict[str, dict[str, Any]] = {}
    for side, sdata in sides_data.items():
        mv = movement.get(side)
        matched = _match_insights(idx, market_id, side, sdata.get("outcome_id"), sdata.get("line"))
        score = signal_score(side, sdata, mv, matched)
        proxy = proxy_market_edge(side, sdata.get("books", []), fair.get(side)) if fair else None

        side_view = {
            **{k: sdata.get(k) for k in ("side", "line", "best_odds", "outcome_id")},
            "hit_rates": {},
            "alt_lines": alt_lines.get(side, []),
            "movement": _slim_movement(mv),
            "signal": score,
            "proxy_market_edge": proxy,
            "insights": [_slim_insight(i) for i in matched],
            "ev": _ev_for_side(ev_rows, side, sdata.get("outcome_id"), _to_float(sdata.get("line"))),
            "public_money": sdata.get("public_money"),
        }
        side_views[side] = side_view

    card = {
        "card_id": market_id,
        "league": next((r.get("league") for r in props + ev_rows if r.get("league")), None),
        "group_key": _group_key(identity),
        **identity,
        "sides": side_views,
        "fair": fair,
    }
    _route_and_rank(card)
    # Deferred import: mirrors the existing pack-module import pattern in
    # game_totals.py (avoids a module-load-order dependency between the
    # cards -> pack pipeline stages). Reuses pack's own signed-margin set so
    # this conflict guard can never drift out of sync with the rendering fix.
    from outlier_scrapers.pack import SIGNED_MARGIN_PROPOSITIONS

    if proposition.strip().upper() in SIGNED_MARGIN_PROPOSITIONS and _spread_sign_conflict(
        main_row.get("HOME", {}).get("line"), main_row.get("AWAY", {}).get("line")
    ):
        card.setdefault("flags", []).append("spread_sign_conflict")
    return card


def _best_american_price(row: dict[str, Any]) -> int | None:
    # Normalized game books carry the american price under ``odds`` (matching the
    # props book contract); tolerate a raw ``american`` key as a fallback.
    odds = [
        b.get("odds") if b.get("odds") is not None else b.get("american")
        for b in row.get("books", [])
        if isinstance(b, dict) and (b.get("odds") is not None or b.get("american") is not None)
    ]
    return max(odds) if odds else None

def _side_data_from_game(prop: dict[str, Any], side: str) -> dict[str, Any]:
    return {
        "side": side,
        "line": prop.get("line"),
        "best_odds": _best_american_price(prop) if "best_odds" not in prop else prop.get("best_odds"),
        "books": prop.get("books") or [],
        "outcome_id": prop.get("outcome_id"),
        "public_money": prop.get("public_money"),
    }


def _slim_movement(mv: dict[str, Any] | None) -> dict[str, Any] | None:
    if not mv:
        return None
    return {
        "open_line": mv.get("open_line"),
        "current_line": mv.get("current_line"),
        "open_odds": mv.get("open_odds"),
        "current_odds": mv.get("current_odds"),
        "line_delta_from_open": mv.get("line_delta_from_open"),
        "odds_delta_from_open": mv.get("odds_delta_from_open"),
        "movement_count": mv.get("movement_count"),
        "movement_types": mv.get("movement_types"),
    }


def _route_and_rank(card: dict[str, Any]) -> None:
    """Attach board, headline side, rank value, bucket, and conflict flags."""
    sides = card.get("sides", {})
    ev_sides = {
        s: v for s, v in sides.items()
        if v.get("ev") and (v["ev"].get("best_ev_pct") or 0.0) > 0.0
    }

    if ev_sides:
        headline = max(
            ev_sides,
            key=lambda s: ev_sides[s]["ev"].get("best_ev_pct", float("-inf")),
        )
        ev = sides[headline]["ev"]
        card["board"] = "A"
        card["headline_side"] = headline
        card["rank_value"] = ev.get("best_ev_pct")
        card["rank_metric"] = "calculated_ev_pct"
        card["bucket"] = _bucket(ev.get("best_ev_pct"), EV_BUCKETS, "Pass")
        card["flags"] = _board_a_flags(headline, sides[headline])
    else:
        if not sides:
            card["board"] = "B"
            card["headline_side"] = None
            card["rank_value"] = 0.0
            card["rank_metric"] = "signal_composite"
            card["bucket"] = "Pass"
            card["flags"] = []
            return
        headline = max(sides, key=lambda s: sides[s]["signal"]["composite"])
        sig = sides[headline]["signal"]
        card["board"] = "B"
        card["headline_side"] = headline
        card["rank_value"] = sig["composite"]
        card["rank_metric"] = "signal_composite"
        card["bucket"] = _bucket(sig["composite"], SIGNAL_BUCKETS, "Pass")
        card["flags"] = _board_b_flags(headline, sides[headline])


def _board_a_flags(side: str, view: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    ev = view.get("ev") or {}
    if movement_corroboration(side, _expand_movement(view)) < 0:
        flags.append("reverse_line_movement")
    if view.get("signal", {}).get("insight_conflict"):
        flags.append("insight_conflict")
    book_count = ev.get("ev_book_count") or 0
    # Only treat max_bet as a liquidity signal when it is actually reported;
    # Outlier leaves it null for many books, which must not false-flag.
    reported_limits = [
        b.get("max_bet") for b in ev.get("ev_books", []) if isinstance(b.get("max_bet"), (int, float))
    ]
    low_limits = bool(reported_limits) and all(limit <= 0 for limit in reported_limits)
    if book_count <= 1 or low_limits:
        flags.append("thin_liquidity")
    vig = ev.get("vig_pct")
    if isinstance(vig, (int, float)) and vig > HIGH_VIG_PCT:
        flags.append("high_vig")
    if ev.get("is_alt_line_fallback"):
        flags.append("ev_line_fallback")
    return flags


def _board_b_flags(side: str, view: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if view.get("signal", {}).get("insight_conflict"):
        flags.append("insight_conflict")
    if movement_corroboration(side, _expand_movement(view)) < 0:
        flags.append("reverse_line_movement")
    proxy = view.get("proxy_market_edge")
    if proxy and isinstance(proxy.get("edge_pct"), (int, float)) and proxy["edge_pct"] > 0:
        flags.append("proxy_market_value")
    return flags


def _expand_movement(view: dict[str, Any]) -> dict[str, Any] | None:
    """Re-expand slim movement back to the keys the scorer expects."""
    mv = view.get("movement")
    if not mv:
        return None
    return {
        "line_delta_from_open": mv.get("line_delta_from_open"),
        "odds_delta_from_open": mv.get("odds_delta_from_open"),
    }


# --------------------------------------------------------------------------- #
# Payload
# --------------------------------------------------------------------------- #


def build_cards_payload(league: str) -> dict[str, Any]:
    paths = league_paths(league)
    lg = paths.league
    props_payload = load_latest(lg, "props")
    movement_payload = load_latest(lg, "line_movement")
    insights_payload = load_latest(lg, "insights")

    if not props_payload:
        raise FileNotFoundError(f"Missing props payload for {lg}")
    if not movement_payload:
        raise FileNotFoundError(f"Missing line movement payload for {lg}")

    props = _records(props_payload)
    movement = _records(movement_payload)
    ev_records = _records(movement_payload, "ev_records")
    insights = _records(insights_payload)

    idx = build_indexes(props, movement, ev_records, insights)

    enrichment_payload = load_latest(lg, "games_enrichment")
    if enrichment_payload:
        idx.enrichment_loaded = True
        idx.enrichment = enrichment_payload.get("enrichment") or {}

    market_ids = sorted(set(idx.props_by_market) | set(idx.ev_by_market))
    cards = [assemble_card(mid, idx) for mid in market_ids]

    board_a = sorted(
        (c for c in cards if c.get("board") == "A"),
        key=lambda c: c.get("rank_value") if c.get("rank_value") is not None else float("-inf"),
        reverse=True,
    )
    board_b = sorted(
        (c for c in cards if c.get("board") == "B"),
        key=lambda c: c.get("rank_value") or 0.0,
        reverse=True,
    )

    skew = snapshot_skew(
        {
            "props": (props_payload or {}).get("generated_at"),
            "line_movement": (movement_payload or {}).get("generated_at"),
            "insights": (insights_payload or {}).get("generated_at"),
        }
    )

    payload = {
        "league": lg,
        "source_method": "join",
        "generated_at": datetime.now().astimezone().isoformat(),
        "missing_feeds": ["insights"] if not insights_payload else [],
        "snapshot_skew": skew,
        "coverage": {
            "props_records": len(props),
            "props_markets": len(idx.props_by_market),
            "movement_records": len(movement),
            "ev_records_eligible": sum(len(v) for v in idx.ev_by_market.values()),
            "ev_markets": len(idx.ev_by_market),
            "insights_records": len(insights),
            "cards_total": len(cards),
            "board_a_cards": len(board_a),
            "board_b_cards": len(board_b),
        },
        "board_a": board_a,
        "board_b": board_b,
        "data_contract": {
            "dataset": "outlier_triage_cards",
            "version": "1.0",
            "intended_use": "Ranked player+market triage view joining props, line movement, EV, and insights.",
            "card_id": "market_id",
            "group_key": "player_id+market+scope",
            "boards": {
                "A": "Verified Outlier EV (method=AVERAGE), ranked by calculated_ev_pct",
                "B": "Signal candidates, ranked by descriptive composite; proxy_market edge is not EV",
            },
        },
    }
    return payload


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def export_cards_for_league(league: str) -> dict[str, Any]:
    paths = league_paths(league)
    cards_dir = paths.root / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    payload = build_cards_payload(paths.league)

    latest_json = cards_dir / f"{paths.league.lower()}_cards_latest.json"
    archive_json = paths.timestamped(cards_dir, "cards")
    write_json(latest_json, payload)
    write_json(archive_json, payload)

    html = render_html(payload)
    latest_html = cards_dir / f"{paths.league.lower()}_cards_latest.html"
    latest_html.write_text(html, encoding="utf-8")

    status = {
        "league": paths.league,
        "status": "ok",
        "missing_feeds": payload.get("missing_feeds", []),
        "cards_latest_json": str(latest_json),
        "cards_latest_html": str(latest_html),
        "coverage": payload["coverage"],
        "snapshot_skew": payload["snapshot_skew"],
    }
    write_json(paths.reports / "cards_status_latest.json", status)
    return status


def build_game_cards_payload(league: str) -> dict[str, Any]:
    paths = league_paths(league)
    lg = paths.league
    games_payload = load_latest(lg, "games")
    movement_payload = load_latest(lg, "games_line_movement")
    # Insights are stored as context in games file, or we could load them. The plan says games insights are in games file context.
    # We will pass an empty list to insights since the game_board logic is similar, but insights can be extracted.

    if not games_payload:
        raise FileNotFoundError(f"Missing games payload for {lg}")

    props = _records(games_payload)
    movement = _records(movement_payload) if movement_payload else []
    ev_records = _records(movement_payload, "ev_records") if movement_payload else []

    # We could extract insights from games_payload context
    insights = []
    context = games_payload.get("context", {})
    insights_context = context.get("insights", {})
    for event_insights in insights_context.values():
        insights.extend(event_insights)

    idx = build_indexes(props, movement, ev_records, insights)
    market_ids = sorted(set(idx.props_by_market) | set(idx.ev_by_market))

    # Filter out WINNING_MARGIN markets from the board
    filtered_market_ids = []
    for mid in market_ids:
        mid_props = idx.props_by_market.get(mid, [])
        if any(p.get("proposition") == "WINNING_MARGIN" for p in mid_props):
            continue
        filtered_market_ids.append(mid)

    cards = [assemble_game_card(mid, idx) for mid in filtered_market_ids]

    board_a = sorted(
        (c for c in cards if c.get("board") == "A"),
        key=lambda c: c.get("rank_value") if c.get("rank_value") is not None else float("-inf"),
        reverse=True,
    )
    board_b = sorted(
        (c for c in cards if c.get("board") == "B"),
        key=lambda c: c.get("rank_value") or 0.0,
        reverse=True,
    )

    skew = snapshot_skew(
        {
            "games": (games_payload or {}).get("generated_at"),
            "line_movement": (movement_payload or {}).get("generated_at") if movement_payload else None,
        }
    )

    payload = {
        "league": lg,
        "source_method": "join",
        "generated_at": datetime.now().astimezone().isoformat(),
        "missing_feeds": ["line_movement"] if not movement_payload else [],
        "snapshot_skew": skew,
        "coverage": {
            "games_records": len(props),
            "games_markets": len(idx.props_by_market),
            "movement_records": len(movement),
            "ev_records_eligible": sum(len(v) for v in idx.ev_by_market.values()),
            "ev_markets": len(idx.ev_by_market),
            "cards_total": len(cards),
            "board_a_cards": len(board_a),
            "board_b_cards": len(board_b),
        },
        "board_a": board_a,
        "board_b": board_b,
        "context": context,
        "data_contract": {
            "dataset": "outlier_games_cards",
            "version": "1.0",
            "intended_use": "Ranked game/team market triage view joining games, line movement, EV, and insights.",
            "card_id": "market_id",
            "group_key": "player_id+market+scope",
            "boards": {
                "A": "Verified Outlier EV (method=AVERAGE), ranked by calculated_ev_pct",
                "B": "Signal candidates, ranked by descriptive composite",
            },
        },
    }
    return payload


def export_game_cards_for_league(league: str) -> dict[str, Any]:
    paths = league_paths(league)
    cards_dir = paths.root / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    payload = build_game_cards_payload(paths.league)

    latest_json = cards_dir / f"{paths.league.lower()}_games_cards_latest.json"
    archive_json = paths.timestamped(cards_dir, "games_cards")
    write_json(latest_json, payload)
    write_json(archive_json, payload)

    html = render_html(payload)
    latest_html = cards_dir / f"{paths.league.lower()}_games_cards_latest.html"
    latest_html.write_text(html, encoding="utf-8")

    status = {
        "league": paths.league,
        "status": "ok",
        "missing_feeds": payload.get("missing_feeds", []),
        "cards_latest_json": str(latest_json),
        "cards_latest_html": str(latest_html),
        "coverage": payload["coverage"],
        "snapshot_skew": payload["snapshot_skew"],
    }
    write_json(paths.reports / "games_cards_status_latest.json", status)
    return status

# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Outlier triage cards layer")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Accepted for parity with other modules; cards always use latest files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = league_paths(args.league)
    try:
        status = export_cards_for_league(paths.league)
    except FileNotFoundError as exc:
        report = {
            "league": paths.league,
            "status": "missing_inputs",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:300],
        }
        paths.ensure()
        write_json(paths.reports / "cards_status_latest.json", report)
        print(f"{paths.league}: missing_inputs ({exc})")
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        report = {
            "league": paths.league,
            "status": "error",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:300],
        }
        paths.ensure()
        write_json(paths.reports / "cards_status_latest.json", report)
        print(f"{paths.league}: error ({exc})")
        return 1

    cov = status["coverage"]
    skew = status["snapshot_skew"]
    if skew.get("is_skewed"):
        print(f"WARNING: {paths.league} snapshot skew {skew.get('skew_hours')}h - {skew.get('reason')}")
    print(
        f"{paths.league}: {cov['cards_total']} cards "
        f"(Board A={cov['board_a_cards']} verified-EV, Board B={cov['board_b_cards']} signal) "
        f"-> {status['cards_latest_html']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
