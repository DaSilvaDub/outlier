"""Deterministic game/team totals projection board.

Derives fair totals, edges, and actionable flags from normalized games data.
Reasoning agents consume the output; they never recompute probability or edge.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from outlier_scrapers.line_movement import build_consensus_operator_refs, _props_freshness
from outlier_scrapers.normalizer import detect_scope, implied_probability
from outlier_scrapers.sizing import compute_sizing


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


TOTAL_KIND_GAME = "game"
TOTAL_KIND_TEAM = "team"


def _is_candidate_game_total(row: dict[str, Any]) -> bool:
    if row.get("player_id"):
        return False
    mt = row.get("market_type")
    if mt != "GAMELINE":
        return False
    sel = (row.get("selection") or "").lower()
    prop = str(row.get("_proposition") or row.get("market") or "").upper()
    return "total o/u" in sel or prop == "TOTAL"


def _is_candidate_team_total(row: dict[str, Any]) -> bool:
    if row.get("player_id"):
        return False
    mt = row.get("market_type")
    if mt != "TEAM_PROP":
        return False
    sel = (row.get("selection") or "").lower()
    prop = str(row.get("_proposition") or row.get("market") or "").upper()
    return "team total" in sel or prop == "POINTS"


def _is_candidate_total(row: dict[str, Any], *, kind: str | None = None) -> bool:
    """Match candidate rows that correspond to totals markets.

    kind=None matches either game or team totals (legacy helpers).
    """
    if kind == TOTAL_KIND_GAME:
        return _is_candidate_game_total(row)
    if kind == TOTAL_KIND_TEAM:
        return _is_candidate_team_total(row)
    return _is_candidate_game_total(row) or _is_candidate_team_total(row)


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
    "outcome_id",
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
    "market_consensus_prob",
    "independent_model_prob",
    "final_blended_prob",
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
# Shared schema: team totals use the same columns; files are split by stream.
TEAM_TOTALS_HEADER = GAME_TOTALS_HEADER
TOTALS_HEADER = GAME_TOTALS_HEADER


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
    refs, _accepted = build_consensus_operator_refs([("DraftKings", over_odds, under_odds)])
    fair = refs.get("DRAFTKINGS")
    if fair is None:
        return None
    return fair


def median_prob(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def period_identity(rec: dict[str, Any]) -> str:
    """Stable period token for grouping / full-game eligibility.

    Prefer structured ``period_label`` / ``periods`` over ``scope`` — historical
    games_norm files often stamp inning totals as scope=full_game while the
    only real signal is periodLabel (e.g. ``6I``, ``1st 7I``).
    """
    period_label = rec.get("period_label")
    if period_label not in (None, ""):
        detected = detect_scope(period_label)
        if detected not in FULL_GAME_SCOPES:
            return detected
        token = str(period_label).strip().lower()
        if token and token not in FULL_GAME_SCOPES:
            return token
    periods = rec.get("periods")
    if isinstance(periods, list) and periods:
        return "p" + "-".join(str(p) for p in periods)
    scope = str(rec.get("scope") or "").lower()
    if scope and scope not in FULL_GAME_SCOPES:
        return scope
    return "full_game"


def is_full_game_total(rec: dict[str, Any]) -> bool:
    return period_identity(rec) in FULL_GAME_SCOPES


def _record_market_and_prop(rec: dict[str, Any]) -> tuple[str, str]:
    mt = str(rec.get("market_type") or "").upper()
    prop = str(rec.get("proposition") or rec.get("market") or "").upper()
    return mt, prop


def is_game_total_record(rec: dict[str, Any]) -> bool:
    """Full-game market totals only (GAMELINE / TOTAL)."""
    if not is_full_game_total(rec):
        return False
    mt, prop = _record_market_and_prop(rec)
    return mt == "GAMELINE" and prop == "TOTAL"


def is_team_total_record(rec: dict[str, Any]) -> bool:
    """Full-game team totals only (TEAM_PROP / POINTS)."""
    if not is_full_game_total(rec):
        return False
    mt, prop = _record_market_and_prop(rec)
    return mt == "TEAM_PROP" and prop == "POINTS"


def is_eligible_total_record(rec: dict[str, Any], *, kind: str | None = None) -> bool:
    """Eligibility for the combined totals projection board.

    kind=None matches either stream (legacy). Prefer is_game_total_record /
    is_team_total_record for new call sites.
    """
    if kind == TOTAL_KIND_GAME:
        return is_game_total_record(rec)
    if kind == TOTAL_KIND_TEAM:
        return is_team_total_record(rec)
    return is_game_total_record(rec) or is_team_total_record(rec)


def logical_market_key(rec: dict[str, Any]) -> str:
    """One board market per event × kind × team × period × OT flag.

    Docs (games_section_api_map): display/grouping keys use
    ``(proposition, period_label, include_overtime)``; row grain stays outcome_id.
    """
    event_id = str(rec.get("event_id") or "").strip()
    mt = str(rec.get("market_type") or "").upper()
    total_kind = "team" if mt == "TEAM_PROP" else "game"
    prop = str(rec.get("proposition") or rec.get("market") or "TOTAL").upper()
    team = str(rec.get("team") or rec.get("team_raw") or "").strip().lower()
    period = period_identity(rec)
    ot = rec.get("include_overtime")
    ot_token = "ot" if ot is True else ("no_ot" if ot is False else "ot_unk")
    if total_kind == "team":
        return f"{event_id}|{total_kind}|{prop}|{team}|{period}|{ot_token}"
    return f"{event_id}|{total_kind}|{prop}|{period}|{ot_token}"


def _pick_representative_market_id(records: list[dict[str, Any]], logical_key: str) -> str:
    """Prefer the raw market_id with the most book quotes; fall back to logical key."""
    scores: dict[str, int] = {}
    for rec in records:
        mid = str(rec.get("market_id") or "").strip()
        if not mid:
            continue
        scores[mid] = scores.get(mid, 0) + len(rec.get("books") or [])
    if not scores:
        return logical_key
    return max(scores.items(), key=lambda item: (item[1], item[0]))[0]


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
    refs, _accepted = build_consensus_operator_refs(
        [
            (book, over_odds, under_books[book])
            for book, over_odds in over_books.items()
            if book in under_books
        ]
    )
    probs = [pair[0] for pair in refs.values()]
    if not probs:
        return None, 0, ["NO_VALID_CONSENSUS"]
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


def _index_candidates_by_market(
    rows: list[dict[str, Any]], *, kind: str
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_candidate_total(row, kind=kind):
            continue
        mid = str(row.get("market_id") or "")
        if mid and mid not in out:
            out[mid] = row
    return out


def _group_records_by_logical_market(
    records: list[dict[str, Any]], *, kind: str
) -> dict[str, list[dict[str, Any]]]:
    """Collapse raw API market_ids into one ladder per logical total."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not is_eligible_total_record(rec, kind=kind):
            continue
        if not str(rec.get("event_id") or "").strip():
            continue
        key = logical_market_key(rec)
        grouped.setdefault(key, []).append(rec)
    return grouped


def _resolve_candidate(
    cand_by_market: dict[str, dict[str, Any]],
    market_records: list[dict[str, Any]],
    representative_market_id: str,
) -> dict[str, Any]:
    cand = cand_by_market.get(representative_market_id)
    if cand:
        return cand
    for rec in market_records:
        mid = str(rec.get("market_id") or "")
        if mid and mid in cand_by_market:
            return cand_by_market[mid]
    return {}


def build_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    kind: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for one totals stream (game or team)."""
    if kind not in (TOTAL_KIND_GAME, TOTAL_KIND_TEAM):
        raise ValueError(f"unsupported totals kind: {kind!r}")
    records = (games_norm or {}).get("records") or []
    by_market = _group_records_by_logical_market(records, kind=kind)
    cand_by_market = _index_candidates_by_market(candidate_rows, kind=kind)
    now = now or datetime.now().astimezone()
    output: list[dict[str, Any]] = []
    freshness = _props_freshness(
        games_norm or {}, now=now, max_age_hours=6.0, max_future_hours=5.0 / 60.0
    )
    source_flags: list[str] = []
    if freshness.is_stale:
        source_flags.append("STALE_DATA")
    if (games_norm or {}).get("fetch_errors"):
        source_flags.append("SOURCE_FETCH_ERRORS")
    from outlier_scrapers import pack as pack_module

    for logical_key, market_records in by_market.items():
        market_id = _pick_representative_market_id(market_records, logical_key)
        rep_records = [
            r for r in market_records if str(r.get("market_id") or "").strip() == market_id
        ]
        identity = rep_records[0] if rep_records else market_records[0]
        event_id = str(identity.get("event_id") or "")
        prop = str(identity.get("proposition") or identity.get("market") or "")
        scope = period_identity(identity) or "full_game"
        total_kind = kind
        team = identity.get("team") or identity.get("team_raw") or ""
        matchup = identity.get("matchup") or identity.get("matchup_raw") or ""

        flags: list[str] = list(source_flags)
        cand = _resolve_candidate(cand_by_market, market_records, market_id)

        context_event = (
            ((games_norm or {}).get("context") or {}).get("events") or {}
        ).get(event_id, {})
        event_start = (
            identity.get("event_starts_at")
            or (context_event.get("starts_at") if isinstance(context_event, dict) else None)
            or cand.get("_event_starts_at")
        )
        _pregame, locked = pack_module.drop_locked_events(
            [{"event_id": event_id, "_event_starts_at": event_start}], now=now
        )
        if locked:
            flags.append("LOCKED_OR_UNVERIFIED_EVENT")
        if identity.get("is_active") is False:
            flags.append("MARKET_INACTIVE")
        if cand.get("data_quality_flags"):
            flags.append("SOURCE_INTEGRITY_FLAG")

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
            line_p_over, _bc, lf = aggregate_line_p_over(sides.get("over", {}), sides.get("under", {}))
            if lf:
                line_flags[line] = lf
            if line_p_over is not None:
                ladder_p[line] = line_p_over

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
        p_side = (
            p_over_headline
            if best_side == "OVER"
            else (1.0 - p_over_headline if p_over_headline is not None else None)
        )
        decimal_price = _american_to_decimal(best_price)
        implied_prob = implied_probability(best_price)

        push_prob: float | str = ""
        sizing_flags = ""
        push_blocked = _is_integer_line(headline_line)
        if push_blocked:
            # F3 Option 2 — push-aware *display* edge only. Integer lines stay
            # non-actionable; we only replace the misleading two-way edge_pct.
            # Reuses sizing.compute_sizing (same helper pack.build_row uses),
            # which nets push via p_lose = 1 - p_win - push_prob (F7-guarded).
            derived = derive_push_prob(headline_line, ladder_p)
            if derived is not None:
                push_prob = round(derived, 4)
                if decimal_price is not None and p_side is not None:
                    sizing = compute_sizing(
                        decimal_price=decimal_price,
                        model_prob=p_side,
                        push_prob=float(push_prob),
                    )
                    edge_pct = (
                        round(sizing.edge_pct, 4) if sizing.edge_pct is not None else None
                    )
                else:
                    edge_pct = None
            else:
                push_prob = ""
                sizing_flags = "push_capable_no_prob"
                # No honest push mass → blank the push-contaminated two-way edge.
                edge_pct = None

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
                and not flags
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
        totals_id = _totals_id(market_id, headline_line, side_for_selection)

        row: dict[str, Any] = {k: "" for k in GAME_TOTALS_HEADER}
        row.update(
            {
                "totals_id": totals_id,
                "sport": sport,
                "event_id": event_id,
                "market_id": market_id,
                "outcome_id": totals_id,
                "total_kind": total_kind,
                "team": team,
                "selection": selection,
                "line": headline_line,
                "price": best_price,
                "decimal_price": decimal_price,
                "book": cand.get("book") or "",
                "best_side": best_side,
                "best_price": best_price,
                "projected_over_prob": round(p_over_headline, 4) if p_over_headline is not None else "",
                "projected_under_prob": round(p_under, 4) if p_under is not None else "",
                "market_consensus_prob": round(p_side, 4) if p_side is not None else "",
                "independent_model_prob": "",
                "final_blended_prob": round(p_side, 4) if p_side is not None else "",
                "fair_total": fair_total if fair_total is not None else "",
                "edge_pct": edge_pct if edge_pct is not None else "",
                "implied_prob": implied_prob if implied_prob is not None else "",
                "actionable": actionable,
                "quality_flags": quality_flags or ("INSUFFICIENT_DATA" if p_over_headline is None else ""),
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


def build_game_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for game totals only (GAMELINE / TOTAL)."""
    return build_totals(
        candidate_rows, games_norm, sport=sport, kind=TOTAL_KIND_GAME, now=now
    )


def build_team_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for team totals only (TEAM_PROP / POINTS)."""
    return build_totals(
        candidate_rows, games_norm, sport=sport, kind=TOTAL_KIND_TEAM, now=now
    )


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
    row: dict[str, Any] = {k: "" for k in GAME_TOTALS_HEADER}
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
