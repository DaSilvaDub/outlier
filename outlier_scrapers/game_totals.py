"""Deterministic game/team totals projection board.

Derives fair totals, edges, and actionable flags from normalized games data.
Reasoning agents consume the output; they never recompute probability or edge.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from outlier_scrapers.line_movement import build_consensus_operator_refs, _props_freshness
from outlier_scrapers.normalizer import detect_scope, implied_probability, percent_number
from outlier_scrapers.sizing import compute_sizing
from outlier_scrapers.team_totals import is_team_total_proposition
from outlier_scrapers.utils import (
    _american_to_decimal,
    _summary_stat_for_team,
)


TOTAL_KIND_GAME = "game"
TOTAL_KIND_TEAM = "team"
FAIR_TOTAL_DIRECTION_TOLERANCE = 0.05
TOTALS_MODEL_DIVERGENCE_THRESHOLD = 0.15


def _totals_models_diverge(
    independent_probability: float | None,
    market_probability: float | None,
) -> bool:
    if independent_probability is None or market_probability is None:
        return False
    return abs(independent_probability - market_probability) >= TOTALS_MODEL_DIVERGENCE_THRESHOLD


def _is_candidate_game_total(row: dict[str, Any]) -> bool:
    if row.get("player_id"):
        return False
    mt = row.get("market_type")
    if mt != "GAMELINE":
        return False
    sel = (row.get("selection") or "").lower()
    prop = str(row.get("_proposition") or row.get("market") or "").upper()
    return "total o/u" in sel or prop == "TOTAL"


def _is_candidate_team_total(row: dict[str, Any], *, sport: str | None = None) -> bool:
    if row.get("player_id"):
        return False
    mt = str(row.get("market_type") or "").upper()
    if mt != "TEAM_PROP":
        return False
    sel = (row.get("selection") or "").lower()
    prop = row.get("_proposition") or row.get("market") or row.get("market_label")
    row_sport = sport or row.get("sport") or row.get("league")
    return "team total" in sel or is_team_total_proposition(prop, sport=row_sport)


def _is_candidate_total(
    row: dict[str, Any], *, kind: str | None = None, sport: str | None = None
) -> bool:
    """Match candidate rows that correspond to totals markets.

    kind=None matches either game or team totals (legacy helpers).
    """
    if kind == TOTAL_KIND_GAME:
        return _is_candidate_game_total(row)
    if kind == TOTAL_KIND_TEAM:
        return _is_candidate_team_total(row, sport=sport)
    return _is_candidate_game_total(row) or _is_candidate_team_total(row, sport=sport)


def _research_leverage(prop: str, scope: str, sport: str) -> str:
    token = (prop or "").upper()
    scope_l = (scope or "").lower()
    if sport.upper() == "MLB":
        if (
            token == "TOTAL"
            or scope_l in ("first_5_innings", "first_3_innings")
            or "nrfi" in scope_l
        ):
            return "HIGH"
        if token in ("SPREAD", "MONEYLINE", "RUN_LINE", "GAMELINE"):
            return "LOW"
    return "MED"


MIN_EDGE_TOTALS = 0.03
SHADOW_MIN_EDGE_TOTALS = 0.04
FULL_GAME_SCOPES = frozenset({"", "full_game", "game", "full"})

# Flags that indicate a genuine signal disagreement (recent-form vs. market, or
# an extreme independent probability) rather than corrupted/self-contradictory
# data. Unlike FAIR_TOTAL_SIDE_CONFLICT-style flags — which mean the pack's own
# math disagrees with the side it picked, a correctness problem — these mean a
# second signal disagrees with the market, which is lower conviction but not
# necessarily wrong. They no longer hard-block ``actionable``; instead they
# still surface in ``quality_flags`` for visibility and apply a sizing haircut.
SOFT_QUALITY_FLAGS = frozenset({"totals_model_divergence", "MODEL_SATURATED"})
SOFT_FLAG_UNITS_DISCOUNT = 0.5

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
    "recency_hit_prob",
    "final_blended_prob",
    "fair_total",
    "edge_pct",
    "implied_prob",
    "actionable",
    "shadow_actionable_4pct",
    "shadow_recommended_units",
    "shadow_gate_reasons",
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
    "starter_flags",
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


# Weight given to a full 10-game L10 sample when blending with the market
# consensus; ten games is a high-variance signal, so the market stays dominant.
BASE_INDEPENDENT_WEIGHT = 0.25  # legacy; retained for reference
_FULL_SAMPLE_GAMES = 10.0
EB_PRIOR_STRENGTH = 20.0  # alpha in p_hat = (k + alpha * p_mkt) / (n + alpha)


def _l10_over_for_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Side-specific L10 signal for one OVER outcome at its line.

    Team totals use the matching team's summary blob; game totals combine the
    home and away samples (each team's last 10 games grade the same line).
    Returns {"hits", "total", "pct"} with pct as a 0-1 fraction, or None.
    """
    stats = rec.get("stats")
    if not isinstance(stats, dict) or not stats:
        return None
    if str(rec.get("market_type") or "").upper() == "TEAM_PROP":
        blob, _flag = _summary_stat_for_team(rec)
        blobs = [blob] if isinstance(blob, dict) else []
    else:
        blobs = [
            b
            for b in (stats.get("homeSummaryStat"), stats.get("awaySummaryStat"))
            if isinstance(b, dict)
        ]
    if not blobs:
        return None

    hits = 0
    total = 0
    fractions: list[float] = []
    all_have_results = True
    for blob in blobs:
        results = blob.get("l10Results")
        if isinstance(results, list) and results:
            blob_hits = sum(bool(v) for v in results)
            hits += blob_hits
            total += len(results)
            fractions.append(blob_hits / len(results))
        else:
            all_have_results = False
            pct = percent_number(blob.get("l10"))
            if pct is not None:
                fractions.append(float(pct) / 100.0)
    if not fractions:
        return None
    if all_have_results:
        return {"hits": hits, "total": total, "pct": hits / total}
    return {"hits": None, "total": None, "pct": sum(fractions) / len(fractions)}


def blend_over_probability(
    p_over_market: float, l10_over: dict[str, Any] | None
) -> tuple[float, bool]:
    """Empirical-Bayes shrinkage of L10 toward market-implied probability.

    p_hat = (k + alpha * p_mkt) / (n + alpha)

    Returns (shrunk p_over, whether the L10 signal was used). A missing or
    empty L10 signal returns the market probability unchanged.
    """
    if not l10_over or l10_over.get("pct") is None:
        return p_over_market, False
    total = _to_float(l10_over.get("total"))
    n = total if total and total > 0 else _FULL_SAMPLE_GAMES
    hits = _to_float(l10_over.get("hits"))
    if hits is not None:
        k = hits
    else:
        k = float(l10_over["pct"]) * n
    alpha = EB_PRIOR_STRENGTH
    shrunk = (k + alpha * p_over_market) / (n + alpha)
    return shrunk, True


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
    prop = str(rec.get("proposition") or rec.get("_proposition") or rec.get("market") or "").upper()
    return mt, prop


def is_game_total_record(rec: dict[str, Any]) -> bool:
    """Full-game market totals only (GAMELINE / TOTAL)."""
    if not is_full_game_total(rec):
        return False
    mt, prop = _record_market_and_prop(rec)
    return mt == "GAMELINE" and prop == "TOTAL"


def is_team_total_record(rec: dict[str, Any], *, sport: str | None = None) -> bool:
    """Full-game TEAM_PROP totals using the sport's scoring propositions."""
    if not is_full_game_total(rec):
        return False
    mt, prop = _record_market_and_prop(rec)
    rec_sport = sport or rec.get("sport") or rec.get("league")
    return mt == "TEAM_PROP" and is_team_total_proposition(prop, sport=rec_sport)


def is_eligible_total_record(
    rec: dict[str, Any], *, kind: str | None = None, sport: str | None = None
) -> bool:
    """Eligibility for the combined totals projection board.

    kind=None matches either stream (legacy). Prefer is_game_total_record /
    is_team_total_record for new call sites.
    """
    if kind == TOTAL_KIND_GAME:
        return is_game_total_record(rec)
    if kind == TOTAL_KIND_TEAM:
        return is_team_total_record(rec, sport=sport)
    return is_game_total_record(rec) or is_team_total_record(rec, sport=sport)


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


def pick_best_side(
    p_over: float, over_price: Any, under_price: Any
) -> tuple[str, Any, float | None]:
    over_edge, _ = compute_side_edge("OVER", p_over, over_price)
    under_edge, _ = compute_side_edge("UNDER", p_over, under_price)
    if over_edge is None and under_edge is None:
        return "OVER", over_price, None
    if (under_edge or float("-inf")) > (over_edge or float("-inf")):
        return "UNDER", under_price, under_edge
    return "OVER", over_price, over_edge


def _best_book_offer(
    offers: dict[str, Any], *, fallback_book: Any = "", fallback_price: Any = None
) -> tuple[str, Any]:
    valid = [
        (str(book), price, _american_to_decimal(price))
        for book, price in offers.items()
        if _american_to_decimal(price) is not None
    ]
    if not valid:
        return str(fallback_book or ""), fallback_price
    book, price, _decimal = max(valid, key=lambda offer: offer[2] or 0.0)
    return book, price


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
    rows: list[dict[str, Any]], *, kind: str, sport: str
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_candidate_total(row, kind=kind, sport=sport):
            continue
        mid = str(row.get("market_id") or "")
        if mid and mid not in out:
            out[mid] = row
    return out


def _group_records_by_logical_market(
    records: list[dict[str, Any]], *, kind: str, sport: str
) -> dict[str, list[dict[str, Any]]]:
    """Collapse raw API market_ids into one ladder per logical total."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not is_eligible_total_record(rec, kind=kind, sport=sport):
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


def _teams_from_matchup(matchup: str) -> tuple[str, str]:
    """Split an ``"AWAY @ HOME"`` matchup string into its two team codes."""
    parts = [p.strip() for p in str(matchup or "").split("@")]
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _starter_flags_for_row(
    *,
    kind: str,
    team: str,
    matchup: str,
    probable_pitchers: dict[str, dict[str, Any]] | None,
) -> str:
    """Return comma-joined ``STARTER_UNCONFIRMED:<TEAM>`` tokens for any side of
    this total whose probable starting pitcher is not yet confirmed.

    Purely informational — it is not folded into ``quality_flags`` and never
    affects ``actionable``, so it doesn't change existing sizing/gating
    behavior. It exists so a reasoning pass can read starter-confirmation
    status directly from the pack instead of re-researching it per game.
    Returns "" when no probable-pitchers lookup was supplied (e.g. league has
    no such source, or the scraper hasn't run yet) — absence of data is never
    treated as "unconfirmed".
    """
    if not probable_pitchers:
        return ""
    teams = (team,) if kind == TOTAL_KIND_TEAM else _teams_from_matchup(matchup)
    tokens: list[str] = []
    for code in teams:
        if not code:
            continue
        entry = probable_pitchers.get(code)
        if entry is None:
            continue
        if not entry.get("confirmed"):
            tokens.append(f"STARTER_UNCONFIRMED:{code}")
    return ",".join(dict.fromkeys(tokens))


def build_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    kind: str,
    now: datetime | None = None,
    probable_pitchers: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for one totals stream (game or team)."""
    if kind not in (TOTAL_KIND_GAME, TOTAL_KIND_TEAM):
        raise ValueError(f"unsupported totals kind: {kind!r}")
    records = (games_norm or {}).get("records") or []
    by_market = _group_records_by_logical_market(records, kind=kind, sport=sport)
    cand_by_market = _index_candidates_by_market(candidate_rows, kind=kind, sport=sport)
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

        context_event = (((games_norm or {}).get("context") or {}).get("events") or {}).get(
            event_id, {}
        )
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
        events_dict = ((games_norm or {}).get("context") or {}).get("events") or {}
        if not events_dict or event_id not in events_dict:
            flags.append("UNINDEXED_SLATE_GAME")
        if cand.get("data_quality_flags"):
            flags.append("SOURCE_INTEGRITY_FLAG")

        ladder = build_market_ladder(market_records)
        if not ladder:
            flags.append("INSUFFICIENT_DATA")
            output.append(
                _empty_row(
                    sport,
                    event_id,
                    market_id,
                    total_kind,
                    team,
                    matchup,
                    scope,
                    flags,
                    cand,
                    identity,
                )
            )
            continue

        ladder_p: dict[float, float] = {}
        line_flags: dict[float, list[str]] = {}
        for line, sides in ladder.items():
            line_p_over, _bc, lf = aggregate_line_p_over(
                sides.get("over", {}), sides.get("under", {})
            )
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

        over_book, over_price = _best_book_offer(
            over_books, fallback_book=cand.get("book"), fallback_price=cand.get("price")
        )
        under_book, under_price = _best_book_offer(under_books)

        # Recent-games signal: the OVER outcome at the headline line carries the
        # per-line L10 hit rate; blend it into the market devig before picking a
        # side so a strong recent OVER trend can surface the OVER play.
        l10_over = None
        for rec in market_records:
            if str(rec.get("position") or "").upper() != "OVER":
                continue
            if _to_float(rec.get("line")) != headline_line:
                continue
            l10_over = _l10_over_for_record(rec)
            if l10_over is not None:
                break
        blended_over, used_l10 = (
            blend_over_probability(p_over_headline, l10_over)
            if p_over_headline is not None
            else (None, False)
        )

        best_side, best_price, edge_pct = (
            pick_best_side(p_over_headline, over_price, under_price)
            if p_over_headline is not None
            else ("OVER", over_price, None)
        )
        cand_side = str(cand.get("headline_side") or cand.get("best_side") or "").strip().upper()
        if cand_side and best_side and cand_side != best_side:
            flags.append("SIDE_RESOLUTION_CONFLICT")
            flags.append("SOURCE_INTEGRITY_FLAG")
        best_book = under_book if best_side == "UNDER" else over_book
        try:
            longshot_price = float(str(best_price).replace("+", ""))
        except (TypeError, ValueError):
            longshot_price = None
        if longshot_price is not None and longshot_price >= 150:
            flags.append("LONGSHOT_PRICE")

        if fair_total is not None:
            if best_side == "UNDER" and fair_total > headline_line + FAIR_TOTAL_DIRECTION_TOLERANCE:
                flags.append("FAIR_TOTAL_DIVERGENCE")
                flags.append("FAIR_TOTAL_SIDE_CONFLICT")
                flags.append("SOURCE_INTEGRITY_FLAG")
            elif (
                best_side == "OVER" and fair_total < headline_line - FAIR_TOTAL_DIRECTION_TOLERANCE
            ):
                flags.append("FAIR_TOTAL_DIVERGENCE")
                flags.append("FAIR_TOTAL_SIDE_CONFLICT")
                flags.append("SOURCE_INTEGRITY_FLAG")

        p_under = (1.0 - p_over_headline) if p_over_headline is not None else None
        p_side_market = (
            p_over_headline
            if best_side == "OVER"
            else (1.0 - p_over_headline if p_over_headline is not None else None)
        )
        recency_hit_prob = None
        if used_l10 and l10_over is not None:
            recency_hit_prob = (
                float(l10_over["pct"]) if best_side == "OVER" else 1.0 - float(l10_over["pct"])
            )
        # Use EB-shrunk probability for Kelly when L10 data is available;
        # fall back to pure market consensus otherwise.
        if blended_over is not None and used_l10:
            model_win_prob = blended_over if best_side == "OVER" else (1.0 - blended_over)
        else:
            model_win_prob = p_side_market
        consensus_win_prob = p_side_market
        independent_win_prob = None
        if _totals_models_diverge(recency_hit_prob, p_side_market):
            flags.append("totals_model_divergence")

        if recency_hit_prob is not None and (recency_hit_prob >= 0.98 or recency_hit_prob <= 0.02):
            flags.append("MODEL_SATURATED")
            flags.append("SOURCE_INTEGRITY_FLAG")
        decimal_price = _american_to_decimal(best_price)
        _implied_pct_val = implied_probability(best_price)
        implied_prob = round(_implied_pct_val / 100.0, 5) if _implied_pct_val is not None else None

        push_blocked = _is_integer_line(headline_line)
        push_prob: float | str = "" if push_blocked else 0.0
        sizing_flags = ""
        sizing = None
        if push_blocked:
            # F3 Option 2 — push-aware *display* edge only. Integer lines stay
            # non-actionable; we only replace the misleading two-way edge_pct.
            # Reuses sizing.compute_sizing (same helper pack.build_row uses),
            # which nets push via p_lose = 1 - p_win - push_prob (F7-guarded).
            derived = derive_push_prob(headline_line, ladder_p)
            if derived is not None:
                push_prob = round(derived, 4)
                no_push = 1.0 - float(push_prob)
                model_win_prob = p_side_market * no_push if p_side_market is not None else None
                consensus_win_prob = p_side_market * no_push if p_side_market is not None else None
                if recency_hit_prob is not None:
                    recency_hit_prob = recency_hit_prob * no_push
                independent_win_prob = None
                if decimal_price is not None and model_win_prob is not None:
                    sizing = compute_sizing(
                        decimal_price=decimal_price,
                        model_prob=model_win_prob,
                        push_prob=float(push_prob),
                    )
                    edge_pct = round(sizing.edge_pct, 4) if sizing.edge_pct is not None else None
                else:
                    edge_pct = None
            else:
                push_prob = ""
                model_win_prob = None
                consensus_win_prob = None
                independent_win_prob = None
                sizing_flags = "push_capable_no_prob"
                # No honest push mass → blank the push-contaminated two-way edge.
                edge_pct = None
        elif decimal_price is not None and model_win_prob is not None:
            sizing = compute_sizing(
                decimal_price=decimal_price,
                model_prob=model_win_prob,
                push_prob=0.0,
            )
            edge_pct = sizing.edge_pct

        quality_flags = ",".join(dict.fromkeys(flags)) if flags else ""
        devig_source = (
            "book_median" if book_count >= 2 else ("single_book" if book_count == 1 else "")
        )

        hard_flags = [f for f in flags if f not in SOFT_QUALITY_FLAGS]
        has_soft_flag = any(f in SOFT_QUALITY_FLAGS for f in flags)
        actionable = (
            "true"
            if (
                p_over_headline is not None
                and edge_pct is not None
                and edge_pct >= MIN_EDGE_TOTALS
                and book_count >= 2
                and "MISSING_SIDE" not in headline_flags
                and "SINGLE_BOOK" not in headline_flags
                and not hard_flags
                and not push_blocked
                and fair_total is not None
            )
            else "false"
        )
        shadow_gate_reasons: list[str] = []
        if edge_pct is None or edge_pct < SHADOW_MIN_EDGE_TOTALS:
            shadow_gate_reasons.append("EDGE_BELOW_4PCT")
        if has_soft_flag:
            # Not a data-integrity verdict: SOFT_QUALITY_FLAGS (above) are an
            # explicit lower-conviction signal, not corrupted data. This name
            # only says the row failed the stricter shadow-sizing gate, same
            # as EDGE_BELOW_4PCT below — reasoning agents must not treat it as
            # grounds to override actionable=true on the main board.
            shadow_gate_reasons.append("MODEL_DIVERGENCE_SHADOW_GATE")
        if actionable != "true" and not shadow_gate_reasons:
            shadow_gate_reasons.append("LEGACY_INTEGRITY_GATE")
        shadow_actionable = "true" if actionable == "true" and not shadow_gate_reasons else "false"
        if not quality_flags and actionable == "false" and p_over_headline is not None:
            if edge_pct is not None and edge_pct < MIN_EDGE_TOTALS:
                quality_flags = "BELOW_MIN_EDGE"
            elif fair_total is None:
                quality_flags = "NON_BRACKETING_LADDER"

        recommended_units: float | str = ""
        if actionable == "true" and sizing is not None:
            recommended_units = sizing.recommended_units_pre_news or 0.0
            if has_soft_flag and recommended_units:
                recommended_units = round(recommended_units * SOFT_FLAG_UNITS_DISCOUNT, 4)
        shadow_recommended_units: float | str = ""
        if shadow_actionable == "true" and sizing is not None:
            shadow_recommended_units = sizing.recommended_units_pre_news or 0.0

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
                "book": best_book,
                "best_side": best_side,
                "best_price": best_price,
                "projected_over_prob": round(p_over_headline, 4)
                if p_over_headline is not None
                else "",
                "projected_under_prob": round(p_under, 4) if p_under is not None else "",
                "market_consensus_prob": round(consensus_win_prob, 4)
                if consensus_win_prob is not None
                else "",
                "independent_model_prob": round(independent_win_prob, 4)
                if independent_win_prob is not None
                else "",
                "recency_hit_prob": round(recency_hit_prob, 4)
                if recency_hit_prob is not None
                else "",
                "final_blended_prob": round(model_win_prob, 4)
                if model_win_prob is not None
                else "",
                "fair_total": fair_total if fair_total is not None else "",
                "edge_pct": edge_pct if edge_pct is not None else "",
                "implied_prob": implied_prob if implied_prob is not None else "",
                "actionable": actionable,
                "shadow_actionable_4pct": shadow_actionable,
                "shadow_recommended_units": shadow_recommended_units,
                "shadow_gate_reasons": ";".join(shadow_gate_reasons),
                "quality_flags": quality_flags
                or ("INSUFFICIENT_DATA" if p_over_headline is None else ""),
                "devig_source": devig_source,
                "recommended_units_pre_news": recommended_units,
                "sizing_flags": sizing_flags,
                "push_prob": push_prob,
                "line_open": cand.get("line_open") or "",
                "line_now": cand.get("line_now") or "",
                "public_money_pct": cand.get("public_money_pct") or "",
                "money_pct": cand.get("money_pct") or "",
                "injury_flags": cand.get("injury_flags") or "",
                "starter_flags": _starter_flags_for_row(
                    kind=kind, team=team, matchup=matchup, probable_pitchers=probable_pitchers
                ),
                "research_leverage": cand.get("research_leverage")
                or _research_leverage(prop, scope, sport),
                "scope": scope,
                "as_of": cand.get("as_of") or (games_norm or {}).get("generated_at") or "",
                "source_timestamps": cand.get("source_timestamps") or "",
            }
        )
        output.append(row)

    output.sort(
        key=lambda r: (
            -(float(r["edge_pct"]) if r.get("edge_pct") not in (None, "") else -1.0),
            r.get("market_id", ""),
        )
    )
    return output


def build_game_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
    probable_pitchers: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for game totals only (GAMELINE / TOTAL)."""
    return build_totals(
        candidate_rows,
        games_norm,
        sport=sport,
        kind=TOTAL_KIND_GAME,
        now=now,
        probable_pitchers=probable_pitchers,
    )


def build_team_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
    probable_pitchers: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for sport-aware full-game TEAM_PROP totals."""
    return build_totals(
        candidate_rows,
        games_norm,
        sport=sport,
        kind=TOTAL_KIND_TEAM,
        now=now,
        probable_pitchers=probable_pitchers,
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
