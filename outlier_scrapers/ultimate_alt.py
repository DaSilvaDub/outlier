"""Shadow-only unified alternate-market ranking and parlay construction.

The legacy alternate exports remain authoritative inputs.  This module adds a
single decision surface that compares alternate spreads, totals, and player
props on the same conservative probability/price basis.  It never changes a
live recommendation: promotion requires measured, settled shadow history.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Iterable

from outlier_scrapers.parlay_legs import encode_legs
from outlier_scrapers.utils import _american_to_decimal, _decimal_to_american


MIN_RECENT_HIT_PCT = 80.0
MIN_PLAYER_SEASON_PCT = 70.0
MIN_EDGE_PCT = 1.5
MIN_LEG_EV_PCT = 1.5
MIN_PARLAY_EV_PCT = 3.0
PRIOR_STRENGTH = 20.0
WILSON_Z = 1.2815515655446004  # one-sided 90% lower confidence bound
SHADOW_UNITS = 0.5
MAX_SHORTLIST = 8
MAX_PARLAYS = 5


ULTIMATE_ALT_HEADER = [
    "sport",
    "league",
    "event_id",
    "event_starts_at",
    "matchup",
    "alt_type",
    "market_type",
    "market",
    "selection",
    "headline_side",
    "team",
    "player",
    "player_id",
    "market_id",
    "outcome_id",
    "line",
    "book",
    "price",
    "decimal_price",
    "implied_prob",
    "l5_pct",
    "l10_pct",
    "season_pct",
    "estimated_prob",
    "conservative_prob",
    "edge_pct",
    "ev_pct",
    "confidence_score",
    "shadow_status",
    "rejection_reasons",
    "recommended_units_pre_news",
    "portfolio_shadow_units",
    "stable_wager_id",
    "cap_reasons",
    "actionable",
    "board",
    "scope",
    "as_of",
]


ULTIMATE_ALT_PARLAYS_HEADER = [
    "rank",
    "num_legs",
    "legs",
    # Machine-readable leg identity; "legs" above is display text only.
    "legs_json",
    "alt_types",
    "event_ids",
    "combined_decimal",
    "combined_american",
    "combined_implied_prob",
    "combined_conservative_prob",
    "ev_pct",
    "quality_flags",
]


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(str(value).replace("−", "-").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _probability(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    return number if 0.0 <= number <= 1.0 else None


def _wilson_lower(probability: float, sample_size: float) -> float:
    n = max(1.0, sample_size)
    z2 = WILSON_Z * WILSON_Z
    centre = probability + z2 / (2.0 * n)
    margin = WILSON_Z * math.sqrt(probability * (1.0 - probability) / n + z2 / (4.0 * n * n))
    return max(0.0, (centre - margin) / (1.0 + z2 / n))


def _estimate_probability(row: dict[str, Any], alt_type: str) -> tuple[float, float] | None:
    l10 = _probability(row.get("l10_pct"))
    if l10 is None:
        return None
    recent_n = _number(row.get("l10_total")) or 10.0
    if alt_type == "PLAYER_PROP":
        season = _probability(row.get("season_pct"))
        raw = 0.65 * l10 + 0.35 * season if season is not None else l10
    else:
        raw = l10
    # Shrink toward market-implied probability (not 0.5) via empirical Bayes:
    # p_hat = (k + alpha * p_mkt) / (n + alpha)
    implied = _probability(row.get("implied_prob"))
    p_mkt = implied if implied is not None else 0.5
    k = raw * recent_n
    shrunk = (k + p_mkt * PRIOR_STRENGTH) / (recent_n + PRIOR_STRENGTH)
    return shrunk, _wilson_lower(shrunk, recent_n + PRIOR_STRENGTH)


def _base_row(row: dict[str, Any], *, alt_type: str) -> dict[str, Any]:
    league = str(row.get("league") or row.get("sport") or "").upper()
    if alt_type == "PLAYER_PROP":
        market = str(row.get("market") or "").upper()
        side = str(row.get("position") or "").upper()
        player = str(row.get("player") or "")
        selection = f"{player} - {market} {side} {row.get('line', '')}".strip()
        price = row.get("best_odds")
        book = row.get("best_book")
        market_type = "PLAYER_PROP"
    elif alt_type == "SPREAD":
        market = "SPREAD"
        side = str(row.get("position") or "").upper()
        player = ""
        selection = str(row.get("selection") or "")
        price = row.get("best_price")
        book = row.get("best_book")
        market_type = "GAMELINE"
    else:
        market = str(row.get("proposition") or row.get("market") or "TOTAL").upper()
        side = str(row.get("position") or "OVER").upper()
        player = ""
        team = str(row.get("team") or "")
        subject = team or str(row.get("matchup") or "Game")
        price = row.get("best_price")
        book = row.get("best_book")
        market_type = str(row.get("market_type") or "TEAM_PROP").upper()
        label = "Team Total" if market_type == "TEAM_PROP" and team else "Total O/U"
        selection = f"{subject} {label} {side} {row.get('line', '')}".strip()
    decimal = _number(row.get("decimal_price")) or _american_to_decimal(price)
    implied = _probability(row.get("implied_prob"))
    if implied is None and decimal:
        implied = 1.0 / decimal
    return {
        "sport": league,
        "league": league,
        "event_id": str(row.get("event_id") or ""),
        "event_starts_at": row.get("event_starts_at") or row.get("_event_starts_at") or "",
        "matchup": row.get("matchup") or "",
        "alt_type": alt_type,
        "market_type": market_type,
        "market": market,
        "selection": selection,
        "headline_side": side,
        "team": row.get("team") or "",
        "player": player,
        "player_id": row.get("player_id") or "",
        "market_id": str(row.get("market_id") or ""),
        "outcome_id": str(row.get("outcome_id") or ""),
        "line": row.get("line") if row.get("line") is not None else "",
        "book": book or "",
        "price": price if price is not None else "",
        "decimal_price": round(decimal, 6) if decimal else "",
        "implied_prob": round(implied, 6) if implied is not None else "",
        "l5_pct": row.get("l5_pct") or "",
        "l10_pct": row.get("l10_pct") or "",
        "season_pct": row.get("season_pct") or "",
        "scope": row.get("scope") or "full_game",
        "as_of": row.get("as_of") or "",
    }


def build_ultimate_alt_board(
    *,
    spread_rows: Iterable[dict[str, Any]],
    total_rows: Iterable[dict[str, Any]],
    player_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return every unified candidate with explicit shadow pass/fail reasons."""
    inputs = [
        *((dict(row), "SPREAD") for row in spread_rows),
        *((dict(row), "TOTAL") for row in total_rows),
        *((dict(row), "PLAYER_PROP") for row in player_rows),
    ]
    deduped: dict[tuple[str, str, str, str, str], tuple[dict[str, Any], str]] = {}
    for row, alt_type in inputs:
        key = (
            str(row.get("event_id") or ""),
            str(row.get("market_id") or ""),
            str(row.get("outcome_id") or ""),
            str(row.get("line") or ""),
            alt_type,
        )
        current = deduped.get(key)
        current_price = (
            _number(current[0].get("best_price") or current[0].get("best_odds"))
            if current
            else None
        )
        new_price = _number(row.get("best_price") or row.get("best_odds"))
        if current is None or (
            new_price is not None and (current_price is None or new_price > current_price)
        ):
            deduped[key] = (row, alt_type)

    board: list[dict[str, Any]] = []
    for source, alt_type in deduped.values():
        row = _base_row(source, alt_type=alt_type)
        reasons: list[str] = []
        required_identity = ("event_id", "market_id", "outcome_id", "book")
        if any(not str(row.get(field) or "").strip() for field in required_identity):
            reasons.append("MISSING_IDENTITY")
        l5 = _probability(row.get("l5_pct"))
        l10 = _probability(row.get("l10_pct"))
        season = _probability(row.get("season_pct"))
        if l10 is None or l10 * 100.0 < MIN_RECENT_HIT_PCT:
            reasons.append("RECENT_HIT_RATE_BELOW_80")
        if l5 is not None and l5 * 100.0 < MIN_RECENT_HIT_PCT:
            reasons.append("L5_HIT_RATE_BELOW_80")
        if alt_type == "PLAYER_PROP" and (season is None or season * 100.0 < MIN_PLAYER_SEASON_PCT):
            reasons.append("PLAYER_SEASON_RATE_BELOW_70")
        quality_flags = str(source.get("quality_flags") or "").upper()
        if "SHORT_SAMPLE" in quality_flags:
            reasons.append("SHORT_SAMPLE")
        if "INTEGER_LINE_PUSH_RISK" in quality_flags:
            reasons.append("INTEGER_LINE_PUSH_RISK")

        estimate = _estimate_probability(source, alt_type)
        implied = _probability(row.get("implied_prob"))
        decimal = _number(row.get("decimal_price"))
        if estimate is None or implied is None or decimal is None:
            reasons.append("MISSING_PRICE_OR_PROBABILITY")
            estimated = conservative = edge_pct = ev_pct = None
        else:
            estimated, conservative = estimate
            edge_pct = 100.0 * (conservative - implied)
            ev_pct = 100.0 * (conservative * decimal - 1.0)
            if edge_pct < MIN_EDGE_PCT:
                reasons.append("CONSERVATIVE_EDGE_BELOW_1_5")
            if ev_pct < MIN_LEG_EV_PCT:
                reasons.append("CONSERVATIVE_EV_BELOW_1_5")

        recent = (
            min(value for value in (l5, l10) if value is not None)
            if any(value is not None for value in (l5, l10))
            else 0.0
        )
        baseline = season if season is not None else (l10 or 0.0)
        confidence = 100.0 * (0.60 * (conservative or 0.0) + 0.25 * recent + 0.15 * baseline)
        qualified = not reasons
        row.update(
            {
                "estimated_prob": round(estimated, 6) if estimated is not None else "",
                "conservative_prob": round(conservative, 6) if conservative is not None else "",
                "edge_pct": round(edge_pct, 3) if edge_pct is not None else "",
                "ev_pct": round(ev_pct, 3) if ev_pct is not None else "",
                "confidence_score": round(confidence, 3),
                "shadow_status": "QUALIFIED" if qualified else "REJECTED",
                "rejection_reasons": ";".join(dict.fromkeys(reasons)),
                # Shadow rows are never actionable. Keep the executable stake
                # field blank and let the portfolio pass publish only to the
                # dedicated shadow field.
                "recommended_units_pre_news": "",
                "portfolio_shadow_units": "",
                "actionable": "false",
                "board": "ALT_SHADOW_QUALIFIED" if qualified else "ALT_SHADOW_REJECTED",
            }
        )
        board.append(row)

    board.sort(
        key=lambda row: (
            row["shadow_status"] == "QUALIFIED",
            _number(row.get("ev_pct")) or -999.0,
            _number(row.get("confidence_score")) or 0.0,
            str(row.get("event_id")),
        ),
        reverse=True,
    )
    qualified_seen = 0
    seen_events: set[str] = set()
    seen_teams: set[str] = set()
    seen_players: set[str] = set()
    for row in board:
        if row["shadow_status"] != "QUALIFIED":
            continue
        event_id = str(row.get("event_id") or "")
        team = str(row.get("team") or "").casefold()
        player = str(row.get("player_id") or row.get("player") or "").casefold()
        cap_reason = ""
        if event_id in seen_events:
            cap_reason = "EVENT_EXPOSURE_CAP"
        elif team and team in seen_teams:
            cap_reason = "TEAM_EXPOSURE_CAP"
        elif player and player in seen_players:
            cap_reason = "PLAYER_EXPOSURE_CAP"
        elif qualified_seen >= MAX_SHORTLIST:
            cap_reason = "SHORTLIST_CAP"
        if cap_reason:
            row["shadow_status"] = "REJECTED"
            row["rejection_reasons"] = cap_reason
            row["recommended_units_pre_news"] = ""
            row["board"] = "ALT_SHADOW_REJECTED"
            continue
        qualified_seen += 1
        seen_events.add(event_id)
        if team:
            seen_teams.add(team)
        if player:
            seen_players.add(player)
    return board


def build_ultimate_alt_parlays(board: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    qualified = [row for row in board if row.get("shadow_status") == "QUALIFIED"]
    parlays: list[dict[str, Any]] = []
    for size in (2, 3):
        for legs in combinations(qualified, size):
            event_ids = [str(leg.get("event_id") or "") for leg in legs]
            if len(set(event_ids)) != size:
                continue
            teams = [str(leg.get("team") or "").casefold() for leg in legs if leg.get("team")]
            players = [
                str(leg.get("player_id") or leg.get("player") or "").casefold()
                for leg in legs
                if leg.get("player_id") or leg.get("player")
            ]
            if len(set(teams)) != len(teams) or len(set(players)) != len(players):
                continue
            alt_types = {str(leg.get("alt_type")) for leg in legs}
            if len(alt_types) < 2:
                continue
            decimals = [_number(leg.get("decimal_price")) for leg in legs]
            probabilities = [_probability(leg.get("conservative_prob")) for leg in legs]
            if any(value is None for value in decimals + probabilities):
                continue
            combined_decimal = math.prod(value for value in decimals if value is not None)
            combined_probability = math.prod(value for value in probabilities if value is not None)
            if not 1.4 <= combined_decimal <= 4.0:
                continue
            combined_implied = 1.0 / combined_decimal
            ev_pct = 100.0 * (combined_probability * combined_decimal - 1.0)
            if ev_pct < MIN_PARLAY_EV_PCT:
                continue
            parlays.append(
                {
                    "rank": "",
                    "num_legs": size,
                    "legs": " | ".join(str(leg.get("selection")) for leg in legs),
                    "legs_json": encode_legs(legs),
                    "alt_types": ",".join(sorted(alt_types)),
                    "event_ids": ",".join(event_ids),
                    "combined_decimal": round(combined_decimal, 4),
                    "combined_american": _decimal_to_american(combined_decimal),
                    "combined_implied_prob": round(combined_implied, 6),
                    "combined_conservative_prob": round(combined_probability, 6),
                    "ev_pct": round(ev_pct, 3),
                    "quality_flags": "CROSS_EVENT;SHADOW_ONLY",
                }
            )
    parlays.sort(key=lambda row: (row["ev_pct"], -row["num_legs"]), reverse=True)
    output = parlays[:MAX_PARLAYS]
    for rank, row in enumerate(output, 1):
        row["rank"] = rank
    return output


def format_ultimate_alt_md(
    board: Iterable[dict[str, Any]], parlays: Iterable[dict[str, Any]]
) -> str:
    rows = list(board)
    qualified = [row for row in rows if row.get("shadow_status") == "QUALIFIED"]
    rejected = [row for row in rows if row.get("shadow_status") == "REJECTED"]
    lines = [
        "# Ultimate Alt table (shadow only)",
        "",
        "Unified alternate spreads, totals, and player props. No row changes live betting output until the shadow release gate passes.",
        "",
        f"- Qualified: {len(qualified)}",
        f"- Rejected: {len(rejected)}",
        "",
        "## Qualified legs",
    ]
    if not qualified:
        lines.append("_No legs passed the conservative price and correlation filters._")
    for row in qualified:
        lines.append(
            f"- **{row['selection']}** ({row['price']} {row['book']}) | "
            f"{row['alt_type']} | conservative p={100 * float(row['conservative_prob']):.1f}% | "
            f"EV={row['ev_pct']}%"
        )
    lines += ["", "## Best diversified parlays"]
    parlay_rows = list(parlays)
    if not parlay_rows:
        lines.append("_No cross-event, multi-type parlay passed the shadow EV gate._")
    for row in parlay_rows:
        lines.append(
            f"{row['rank']}. **{row['legs']}** | {row['combined_american']} | "
            f"conservative EV={row['ev_pct']}%"
        )
    return "\n".join(lines) + "\n"
