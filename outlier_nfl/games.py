"""Extraction and normalization of NFL game lines and team props.

Processes raw Outlier event markets payloads into normalized NflGameLine
records for point spreads, game totals, moneylines, and team totals.
"""

from __future__ import annotations

import logging
from typing import Any

from outlier_nfl.config import (
    NFL_TEAMS,
    detect_scope,
    get_team_display_name,
    is_game_total,
    is_moneyline,
    is_spread,
    is_team_total,
    normalize_market,
    normalize_team,
    PROP_SPREAD,
    PROP_TOTAL,
    PROP_MONEYLINE,
    PROP_TEAM_TOTAL_POINTS,
)
from outlier_nfl.constants import (
    MARKET_TYPE_GAMELINE,
    MARKET_TYPE_TEAM_PROP,
)
from outlier_nfl.models import BookPrice, NflGameLine
from outlier_nfl.utils import format_signed_line

logger = logging.getLogger("outlier_nfl.games")


def extract_book_prices(outcome: dict[str, Any]) -> tuple[BookPrice, ...]:
    """Extract and deduplicate BookPrice instances from an outcome object."""
    prices: list[BookPrice] = []

    # 1. Check 'odds' list
    raw_odds = outcome.get("odds")
    if isinstance(raw_odds, list):
        for o in raw_odds:
            if not isinstance(o, dict):
                continue
            book = o.get("book")
            val = o.get("american") if o.get("american") is not None else o.get("odds")
            if not book or val is None:
                continue
            try:
                int_odds = int(val)
                dec = o.get("decimal")
                dec_float = float(dec) if dec is not None else None
                prices.append(
                    BookPrice(
                        book=str(book),
                        odds=int_odds,
                        odds_raw=str(val),
                        decimal=dec_float,
                    )
                )
            except (ValueError, TypeError):
                continue

    # 2. Check 'bookOdds' dict or list
    book_odds = outcome.get("bookOdds")
    if isinstance(book_odds, dict):
        for book, o in book_odds.items():
            if isinstance(o, dict):
                val = o.get("american") if o.get("american") is not None else o.get("odds")
                dec = o.get("decimal")
            else:
                val = o
                dec = None
            if val is None:
                continue
            try:
                int_odds = int(val)
                dec_float = float(dec) if dec is not None else None
                prices.append(
                    BookPrice(
                        book=str(book),
                        odds=int_odds,
                        odds_raw=str(val),
                        decimal=dec_float,
                    )
                )
            except (ValueError, TypeError):
                continue
    elif isinstance(book_odds, list):
        for o in book_odds:
            if not isinstance(o, dict):
                continue
            book = o.get("book")
            val = o.get("american") if o.get("american") is not None else o.get("odds")
            if not book or val is None:
                continue
            try:
                int_odds = int(val)
                dec = o.get("decimal")
                dec_float = float(dec) if dec is not None else None
                prices.append(
                    BookPrice(
                        book=str(book),
                        odds=int_odds,
                        odds_raw=str(val),
                        decimal=dec_float,
                    )
                )
            except (ValueError, TypeError):
                continue

    # Deduplicate while preserving first occurrence
    seen_books: set[str] = set()
    deduped: list[BookPrice] = []
    for p in prices:
        if p.book not in seen_books:
            seen_books.add(p.book)
            deduped.append(p)
    return tuple(deduped)


def american_to_implied_probability(odds: int | float | str | None) -> float | None:
    """Convert American odds to implied win probability percentage [0.0, 100.0]."""
    if odds is None or odds == "":
        return None

    if isinstance(odds, str):
        odds_str = odds.strip().upper()
        if not odds_str or odds_str == "INVALID":
            return None
        if odds_str in ("EVEN", "EV", "+100", "-100", "100"):
            return 50.0
        try:
            val = float(odds_str)
        except ValueError:
            return None
    elif isinstance(odds, (int, float)):
        if isinstance(odds, bool):
            return None
        val = float(odds)
    else:
        return None

    import math
    if not math.isfinite(val):
        return None

    if val == 0:
        return 50.0
    elif val > 0:
        p = (100.0 / (val + 100.0)) * 100.0
    else:
        abs_val = abs(val)
        p = (abs_val / (abs_val + 100.0)) * 100.0

    return round(p, 3)


def extract_game_lines(
    event: dict[str, Any],
    event_markets_payload: dict[str, Any] | list[Any],
    team_index: dict[str, str],
) -> list[NflGameLine]:
    """Extract and normalize all game lines and team props for an NFL event."""
    results: list[NflGameLine] = []

    event_id = str(event.get("eventId") or event.get("id") or "")
    event_starts_at = event.get("scheduledTime") or event.get("startTime")

    home_obj = event.get("home", {}) if isinstance(event.get("home"), dict) else {}
    away_obj = event.get("away", {}) if isinstance(event.get("away"), dict) else {}

    home_code = (
        team_index.get(str(home_obj.get("teamId", "")))
        or normalize_team(home_obj.get("alias"))
        or normalize_team(home_obj.get("name"))
        or str(home_obj.get("alias") or "")
    )
    away_code = (
        team_index.get(str(away_obj.get("teamId", "")))
        or normalize_team(away_obj.get("alias"))
        or normalize_team(away_obj.get("name"))
        or str(away_obj.get("alias") or "")
    )

    home_name = home_obj.get("name") or get_team_display_name(home_code)
    away_name = away_obj.get("name") or get_team_display_name(away_code)

    matchup = event.get("matchup")
    if not matchup:
        matchup = f"{away_code} @ {home_code}" if (away_code and home_code) else f"{away_name} @ {home_name}"

    if isinstance(event_markets_payload, dict):
        markets = event_markets_payload.get("markets", [])
    elif isinstance(event_markets_payload, list):
        markets = event_markets_payload
    else:
        markets = []

    for market in markets:
        if not isinstance(market, dict):
            continue

        # Optional eventId guard if market payload is heterogeneous
        m_event_id = market.get("eventId") or market.get("id")
        if m_event_id and event_id and str(m_event_id) != event_id:
            continue

        market_id = market.get("marketId") or market.get("id")
        raw_market_type = market.get("marketType") or market.get("market_type") or MARKET_TYPE_GAMELINE
        raw_prop = market.get("proposition") or market.get("market") or market.get("label") or ""
        scope = detect_scope(market.get("periodLabel"), market.get("label"))

        outcomes = market.get("outcomes", [])
        if not isinstance(outcomes, list):
            continue

        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue

            outcome_id = outcome.get("outcomeId") or outcome.get("id")
            raw_pos = str(outcome.get("position", "")).strip().upper()
            line_val = outcome.get("line")
            try:
                line_float = float(line_val) if line_val is not None else None
            except (ValueError, TypeError):
                line_float = None

            books = extract_book_prices(outcome)
            best_odds = (
                max((b.odds for b in books), default=None)
                if books
                else (int(outcome["bestOdds"]) if outcome.get("bestOdds") is not None else None)
            )
            implied_prob = american_to_implied_probability(best_odds)

            team: str | None = None

            # -------------------------------------------------------------
            # 1. Point Spread
            # -------------------------------------------------------------
            if is_spread(raw_prop) or (
                raw_market_type == MARKET_TYPE_GAMELINE and normalize_market(raw_prop) == PROP_SPREAD
            ):
                signed = format_signed_line(line_float)
                # Position resolution: HOME / AWAY
                if raw_pos in ("HOME", "H"):
                    pos = "HOME"
                    team = home_code
                    sel = f"{home_name} {signed}" if signed else str(home_name)
                elif raw_pos in ("AWAY", "A"):
                    pos = "AWAY"
                    team = away_code
                    sel = f"{away_name} {signed}" if signed else str(away_name)
                else:
                    norm_pos_team = normalize_team(raw_pos)
                    if norm_pos_team == home_code:
                        pos = "HOME"
                        team = home_code
                        sel = f"{home_name} {signed}" if signed else str(home_name)
                    elif norm_pos_team == away_code:
                        pos = "AWAY"
                        team = away_code
                        sel = f"{away_name} {signed}" if signed else str(away_name)
                    else:
                        pos = raw_pos
                        team = norm_pos_team
                        sel = f"{raw_pos} {signed}" if signed else str(raw_pos)

                results.append(
                    NflGameLine(
                        event_id=event_id,
                        event_starts_at=event_starts_at,
                        matchup=matchup,
                        home_team=home_code,
                        away_team=away_code,
                        market_type=MARKET_TYPE_GAMELINE,
                        market=PROP_SPREAD,
                        proposition=str(raw_prop),
                        position=pos,
                        line=line_float,
                        signed_line=signed,
                        selection=sel,
                        team=team,
                        books=books,
                        best_odds=best_odds,
                        implied_probability=implied_prob,
                        scope=scope,
                        market_id=str(market_id) if market_id else None,
                        outcome_id=str(outcome_id) if outcome_id else None,
                    )
                )

            # -------------------------------------------------------------
            # 2. Game Total
            # -------------------------------------------------------------
            elif is_game_total(raw_prop) or (
                raw_market_type == MARKET_TYPE_GAMELINE and normalize_market(raw_prop) == PROP_TOTAL
            ):
                pos = "OVER" if "OVER" in raw_pos else ("UNDER" if "UNDER" in raw_pos else raw_pos)
                sel = f"Over {line_float}" if pos == "OVER" else (f"Under {line_float}" if pos == "UNDER" else f"{pos} {line_float}")

                results.append(
                    NflGameLine(
                        event_id=event_id,
                        event_starts_at=event_starts_at,
                        matchup=matchup,
                        home_team=home_code,
                        away_team=away_code,
                        market_type=MARKET_TYPE_GAMELINE,
                        market=PROP_TOTAL,
                        proposition=str(raw_prop),
                        position=pos,
                        line=line_float,
                        signed_line=None,
                        selection=sel,
                        team=None,
                        books=books,
                        best_odds=best_odds,
                        implied_probability=implied_prob,
                        scope=scope,
                        market_id=str(market_id) if market_id else None,
                        outcome_id=str(outcome_id) if outcome_id else None,
                    )
                )

            # -------------------------------------------------------------
            # 3. Team Total (TEAM_PROP, POINTS)
            # -------------------------------------------------------------
            elif is_team_total(raw_prop) or raw_market_type == MARKET_TYPE_TEAM_PROP:
                pos = "OVER" if "OVER" in raw_pos else ("UNDER" if "UNDER" in raw_pos else raw_pos)

                # Attribute team
                outcome_team_id = outcome.get("teamId") or outcome.get("team")
                team_resolved: str | None = None
                if outcome_team_id:
                    team_resolved = team_index.get(str(outcome_team_id)) or normalize_team(outcome_team_id)

                if not team_resolved:
                    market_team_id = market.get("teamId")
                    if market_team_id:
                        team_resolved = team_index.get(str(market_team_id)) or normalize_team(market_team_id)

                if not team_resolved:
                    # Infer team from market label e.g. "Kansas City Chiefs - Total Points"
                    label = str(market.get("label") or "")
                    if home_name and home_name.lower() in label.lower():
                        team_resolved = home_code
                    elif away_name and away_name.lower() in label.lower():
                        team_resolved = away_code
                    else:
                        for code, info in NFL_TEAMS.items():
                            if info.name.lower() in label.lower() or info.nickname.lower() in label.lower():
                                team_resolved = code
                                break

                team_display = get_team_display_name(team_resolved) if team_resolved else "Team"
                sel = f"{team_display} {pos.capitalize()} {line_float}"

                results.append(
                    NflGameLine(
                        event_id=event_id,
                        event_starts_at=event_starts_at,
                        matchup=matchup,
                        home_team=home_code,
                        away_team=away_code,
                        market_type=MARKET_TYPE_TEAM_PROP,
                        market=PROP_TEAM_TOTAL_POINTS,
                        proposition=str(raw_prop),
                        position=pos,
                        line=line_float,
                        signed_line=None,
                        selection=sel,
                        team=team_resolved,
                        books=books,
                        best_odds=best_odds,
                        implied_probability=implied_prob,
                        scope=scope,
                        market_id=str(market_id) if market_id else None,
                        outcome_id=str(outcome_id) if outcome_id else None,
                    )
                )

            # -------------------------------------------------------------
            # 4. Moneyline
            # -------------------------------------------------------------
            elif is_moneyline(raw_prop):
                if raw_pos in ("HOME", "H"):
                    pos = "HOME"
                    team = home_code
                    sel = f"{home_name} ML"
                elif raw_pos in ("AWAY", "A"):
                    pos = "AWAY"
                    team = away_code
                    sel = f"{away_name} ML"
                else:
                    norm_pos_team = normalize_team(raw_pos)
                    if norm_pos_team == home_code:
                        pos = "HOME"
                        team = home_code
                        sel = f"{home_name} ML"
                    elif norm_pos_team == away_code:
                        pos = "AWAY"
                        team = away_code
                        sel = f"{away_name} ML"
                    else:
                        pos = raw_pos
                        team = norm_pos_team
                        sel = f"{raw_pos} ML"

                results.append(
                    NflGameLine(
                        event_id=event_id,
                        event_starts_at=event_starts_at,
                        matchup=matchup,
                        home_team=home_code,
                        away_team=away_code,
                        market_type=MARKET_TYPE_GAMELINE,
                        market=PROP_MONEYLINE,
                        proposition=str(raw_prop),
                        position=pos,
                        line=line_float or 0.0,
                        signed_line=None,
                        selection=sel,
                        team=team,
                        books=books,
                        best_odds=best_odds,
                        implied_probability=implied_prob,
                        scope=scope,
                        market_id=str(market_id) if market_id else None,
                        outcome_id=str(outcome_id) if outcome_id else None,
                    )
                )

    return results
