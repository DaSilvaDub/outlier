"""Deterministic game/team totals projection board.

Derives fair totals, edges, and actionable flags from normalized games data.
Reasoning agents consume the output; they never recompute probability or edge.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from outlier_scrapers.cards import two_way_fair
from outlier_scrapers.normalizer import implied_probability


def _american_to_decimal(american: Any) -> float | None:
    if american in (None, ""):
        return None
    try:
        val = float(american)
    except (ValueError, TypeError):
        return None
    if val > 0:
        return (val / 100.0) + 1.0
    if val < 0:
        return (100.0 / abs(val)) + 1.0
    return 2.0


def _is_candidate_total(row: dict[str, Any]) -> bool:
    mt = row.get("market_type")
    if mt == "GAMELINE" and not row.get("player_id"):
        sel = (row.get("selection") or "").lower()
        prop = str(row.get("_proposition") or row.get("market") or "").upper()
        return "total o/u" in sel or prop == "TOTAL"
    if mt == "TEAM_PROP" and not row.get("player_id"):
        sel = (row.get("selection") or "").lower()
        prop = str(row.get("_proposition") or "").upper()
        return "team total" in sel or prop == "POINTS"
    return False


def _research_leverage(prop: str, scope: str, sport: str) -> str:
    token = (prop or "").upper()
    scope_l = (scope or "").lower()
    if sport.upper() == "MLB":
        if token == "TOTAL" or scope_l in ("first_5_innings", "first_3_innings") or "nrfi" in scope_l:
            return "HIGH"
        if token in ("SPREAD", "MONEYLINE", "RUN_LINE", "GAMELINE"):
            return "LOW"
    return "MED"

MIN_EDGE_TOTALS = 0.03
FULL_GAME_SCOPES = frozenset({"", "full_game", "game", "full"})

GAME_TOTALS_HEADER = [
    "totals_id",
    "sport",
    "event_id",
    "market_id",
    "total_kind",
    "team",
    "selection",
    "line",
    "price",
    "decimal_price",
    "book",
    "best_side",
    "best_price",
    "projected_over_prob",
    "projected_under_prob",
    "fair_total",
    "edge_pct",
    "implied_prob",
    "actionable",
    "quality_flags",
    "devig_source",
    "recommended_units_pre_news",
    "sizing_flags",
    "push_prob",
    "line_open",
    "line_now",
    "public_money_pct",
    "money_pct",
    "injury_flags",
    "research_leverage",
    "scope",
    "as_of",
    "source_timestamps",
]


def _to_float(line: Any) -> float | None:
    if line in (None, ""):
        return None
    try:
        return float(line)
    except (ValueError, TypeError):
        return None


def _book_key(book_entry: dict[str, Any]) -> str:
    return str(book_entry.get("book") or book_entry.get("book_raw") or "").strip().lower()


def _book_american(book_entry: dict[str, Any]) -> int | float | None:
    odds = book_entry.get("odds")
    if odds is None:
        odds = book_entry.get("american")
    if odds is None:
        return None
    try:
        return float(odds)
    except (ValueError, TypeError):
        return None


def devig_book_pair(over_odds: Any, under_odds: Any) -> tuple[float, float] | None:
    """Return (p_over, p_under) as 0–1 probabilities."""
    fair = two_way_fair(over_odds, under_odds)
    if not fair:
        return None
    return fair["OVER"] / 100.0, fair["UNDER"] / 100.0


def median_prob(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def is_eligible_total_record(rec: dict[str, Any]) -> bool:
    mt = str(rec.get("market_type") or "").upper()
    prop = str(rec.get("proposition") or rec.get("market") or "").upper()
    scope = str(rec.get("scope") or "").lower()
    if scope and scope not in FULL_GAME_SCOPES:
        return False
    if mt == "GAMELINE" and prop == "TOTAL":
        return True
    if mt == "TEAM_PROP" and prop == "POINTS":
        return True
    return False


def build_market_ladder(
    records: list[dict[str, Any]],
) -> dict[float, dict[str, dict[str, int | float]]]:
    """Map line -> {over: {book: odds}, under: {book: odds}}."""
    ladder: dict[float, dict[str, dict[str, int | float]]] = {}
    for rec in records:
        line = _to_float(rec.get("line"))
        if line is None:
            continue
        pos = str(rec.get("position") or "").upper()
        if pos not in {"OVER", "UNDER"}:
            continue
        side_key = pos.lower()
        bucket = ladder.setdefault(line, {"over": {}, "under": {}})
        for book_entry in rec.get("books") or []:
            if not isinstance(book_entry, dict):
                continue
            bk = _book_key(book_entry)
            american = _book_american(book_entry)
            if not bk or american is None:
                continue
            bucket[side_key][bk] = american
    return ladder


def aggregate_line_p_over(
    over_books: dict[str, int | float],
    under_books: dict[str, int | float],
) -> tuple[float | None, int, list[str]]:
    if not over_books or not under_books:
        return None, 0, ["MISSING_SIDE"]
    probs: list[float] = []
    for book, over_odds in over_books.items():
        under_odds = under_books.get(book)
        if under_odds is None:
            continue
        pair = devig_book_pair(over_odds, under_odds)
        if pair:
            probs.append(pair[0])
    if not probs:
        return None, 0, ["MISSING_SIDE"]
    if len(probs) < 2:
        return median_prob(probs), len(probs), ["SINGLE_BOOK"]
    return median_prob(probs), len(probs), []


def interpolate_fair_total(ladder_p: dict[float, float]) -> tuple[float | None, list[str]]:
    if len(ladder_p) < 2:
        return None, ["NON_BRACKETING_LADDER"]
    sorted_lines = sorted(ladder_p.keys())
    for idx in range(len(sorted_lines) - 1):
        l1, l2 = sorted_lines[idx], sorted_lines[idx + 1]
        p1, p2 = ladder_p[l1], ladder_p[l2]
        if p1 == p2:
            continue
        if (p1 - 0.5) * (p2 - 0.5) <= 0:
            fair = l1 + (0.5 - p1) * (l2 - l1) / (p2 - p1)
            return round(fair * 2) / 2.0, []
    return None, ["NON_BRACKETING_LADDER"]


def compute_side_edge(side: str, p_over: float, price: Any) -> tuple[float | None, float | None]:
    implied_pct = implied_probability(price)
    if implied_pct is None:
        return None, None
    implied = implied_pct / 100.0
    p_side = p_over if side == "OVER" else 1.0 - p_over
    return round(p_side - implied, 4), implied


def pick_best_side(p_over: float, over_price: Any, under_price: Any) -> tuple[str, Any, float | None]:
    over_edge, _ = compute_side_edge("OVER", p_over, over_price)
    under_edge, _ = compute_side_edge("UNDER", p_over, under_price)
    if over_edge is None and under_edge is None:
        return "OVER", over_price, None
    if (under_edge or float("-inf")) > (over_edge or float("-inf")):
        return "UNDER", under_price, under_edge
    return "OVER", over_price, over_edge


def derive_push_prob(line: float, ladder_p: dict[float, float]) -> float | None:
    if not _is_integer_line(line):
        return None
    p_over_high = ladder_p.get(line + 0.5)
    p_over_low = ladder_p.get(line - 0.5)
    if p_over_high is None or p_over_low is None:
        return None
    prob = p_over_low - p_over_high
    return max(0.0, min(1.0, prob))


def _totals_id(market_id: str, line: float, side: str) -> str:
    line_s = str(int(line)) if line == int(line) else str(line)
    return f"{market_id}:{line_s}:{side}"


def _is_integer_line(line: float) -> bool:
    return line == int(line)


def _index_candidates_by_market(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_candidate_total(row):
            continue
        mid = str(row.get("market_id") or "")
        if mid and mid not in out:
            out[mid] = row
    return out


def _group_records_by_market(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not is_eligible_total_record(rec):
            continue
        mid = str(rec.get("market_id") or "")
        if not mid:
            continue
        grouped.setdefault(mid, []).append(rec)
    return grouped


def build_game_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for all eligible full-game totals in games_norm."""
    records = (games_norm or {}).get("records") or []
    by_market = _group_records_by_market(records)
    cand_by_market = _index_candidates_by_market(candidate_rows)
    now = now or datetime.now().astimezone()
    output: list[dict[str, Any]] = []

    for market_id, market_records in by_market.items():
        identity = market_records[0]
        event_id = str(identity.get("event_id") or "")
        mt = str(identity.get("market_type") or "")
        prop = str(identity.get("proposition") or identity.get("market") or "")
        scope = str(identity.get("scope") or "full_game")
        total_kind = "team" if mt == "TEAM_PROP" else "game"
        team = identity.get("team") or identity.get("team_raw") or ""
        matchup = identity.get("matchup") or identity.get("matchup_raw") or ""

        flags: list[str] = []
        cand = cand_by_market.get(market_id, {})

        event_start = cand.get("_event_starts_at")
        if event_start:
            try:
                start = datetime.fromisoformat(str(event_start).replace("Z", "+00:00"))
                if start.tzinfo and start <= now.astimezone(start.tzinfo):
                    flags.append("LIVE_EVENT")
            except (ValueError, TypeError):
                flags.append("INSUFFICIENT_DATA")

        ladder = build_market_ladder(market_records)
        if not ladder:
            flags.append("INSUFFICIENT_DATA")
            output.append(_empty_row(
                sport, event_id, market_id, total_kind, team, matchup, scope, flags, cand, identity
            ))
            continue

        ladder_p: dict[float, float] = {}
        line_flags: dict[float, list[str]] = {}
        for line, sides in ladder.items():
            p_over, book_count, lf = aggregate_line_p_over(sides.get("over", {}), sides.get("under", {}))
            if lf:
                line_flags[line] = lf
            if p_over is not None:
                ladder_p[line] = p_over

        fair_total, fair_flags = interpolate_fair_total(ladder_p)
        flags.extend(fair_flags)

        headline_line = _to_float(cand.get("line"))
        if headline_line is None or headline_line not in ladder:
            headline_line = max(
                ladder.keys(),
                key=lambda ln: len(
                    set(ladder[ln].get("over", {})) & set(ladder[ln].get("under", {}))
                ),
            )

        sides_at_line = ladder.get(headline_line, {})
        over_books, under_books = sides_at_line.get("over", {}), sides_at_line.get("under", {})
        p_over_headline, book_count, headline_flags = aggregate_line_p_over(over_books, under_books)
        flags.extend(headline_flags)
        if p_over_headline is None:
            flags.append("INSUFFICIENT_DATA")

        over_price = max(over_books.values()) if over_books else cand.get("price")
        under_price = max(under_books.values()) if under_books else None
        best_side, best_price, edge_pct = (
            pick_best_side(p_over_headline, over_price, under_price) if p_over_headline is not None else ("OVER", over_price, None)
        )

        p_under = (1.0 - p_over_headline) if p_over_headline is not None else None
        decimal_price = _american_to_decimal(best_price)
        implied_prob = implied_probability(best_price)

        push_prob: float | str = ""
        sizing_flags = ""
        push_blocked = _is_integer_line(headline_line)
        if push_blocked:
            derived = derive_push_prob(headline_line, ladder_p)
            if derived is not None:
                push_prob = round(derived, 4)
            else:
                push_prob = ""
                sizing_flags = "push_capable_no_prob"


        quality_flags = ",".join(dict.fromkeys(flags)) if flags else ""
        devig_source = "book_median" if book_count >= 2 else ("single_book" if book_count == 1 else "")

        actionable = (
            "true"
            if (
                p_over_headline is not None
                and edge_pct is not None
                and edge_pct >= MIN_EDGE_TOTALS
                and book_count >= 2
                and "MISSING_SIDE" not in headline_flags
                and "SINGLE_BOOK" not in headline_flags
                and "LIVE_EVENT" not in flags
                and not push_blocked
                and fair_total is not None
            )
            else "false"
        )
        if not quality_flags and actionable == "false" and p_over_headline is not None:
            if edge_pct is not None and edge_pct < MIN_EDGE_TOTALS:
                quality_flags = "BELOW_MIN_EDGE"
            elif fair_total is None:
                quality_flags = "NON_BRACKETING_LADDER"

        side_for_selection = best_side or "OVER"
        label = "Total O/U" if total_kind == "game" else "Team Total"
        name = matchup if total_kind == "game" else (team or matchup)
        selection = f"{name} {label} {side_for_selection} {headline_line}".strip()

        row = {k: "" for k in GAME_TOTALS_HEADER}
        row.update(
            {
                "totals_id": _totals_id(market_id, headline_line, side_for_selection),
                "sport": sport,
                "event_id": event_id,
                "market_id": market_id,
                "total_kind": total_kind,
                "team": team,
                "selection": selection,
                "line": headline_line,
                "price": best_price,
                "decimal_price": decimal_price,
                "book": cand.get("book") or "",
                "best_side": best_side,
                "best_price": best_price,
                "projected_over_prob": round(p_over, 4) if p_over is not None else "",
                "projected_under_prob": round(p_under, 4) if p_under is not None else "",
                "fair_total": fair_total if fair_total is not None else "",
                "edge_pct": edge_pct if edge_pct is not None else "",
                "implied_prob": implied_prob if implied_prob is not None else "",
                "actionable": actionable,
                "quality_flags": quality_flags or ("INSUFFICIENT_DATA" if p_over is None else ""),
                "devig_source": devig_source,
                "recommended_units_pre_news": cand.get("recommended_units_pre_news") or "",
                "sizing_flags": sizing_flags or cand.get("sizing_flags") or "",
                "push_prob": push_prob if push_prob != "" else cand.get("push_prob", ""),
                "line_open": cand.get("line_open") or "",
                "line_now": cand.get("line_now") or "",
                "public_money_pct": cand.get("public_money_pct") or "",
                "money_pct": cand.get("money_pct") or "",
                "injury_flags": cand.get("injury_flags") or "",
                "research_leverage": cand.get("research_leverage")
                or _research_leverage(prop, scope, sport),
                "scope": scope,
                "as_of": cand.get("as_of") or (games_norm or {}).get("generated_at") or "",
                "source_timestamps": cand.get("source_timestamps") or "",
            }
        )
        output.append(row)

    output.sort(
        key=lambda r: (-(float(r["edge_pct"]) if r.get("edge_pct") not in (None, "") else -1.0), r.get("market_id", ""))
    )
    return output


def _empty_row(
    sport: str,
    event_id: str,
    market_id: str,
    total_kind: str,
    team: str,
    matchup: str,
    scope: str,
    flags: list[str],
    cand: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    row = {k: "" for k in GAME_TOTALS_HEADER}
    row.update(
        {
            "totals_id": market_id,
            "sport": sport,
            "event_id": event_id,
            "market_id": market_id,
            "total_kind": total_kind,
            "team": team,
            "selection": cand.get("selection") or matchup,
            "actionable": "false",
            "quality_flags": ",".join(dict.fromkeys(flags)) or "INSUFFICIENT_DATA",
            "scope": scope,
            "research_leverage": _research_leverage(
                str(identity.get("proposition") or identity.get("market") or ""),
                scope,
                sport,
            ),
        }
    )
    return row