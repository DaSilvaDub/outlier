"""Strict alternate player-prop board for bankroll parlays.

Consumes normalized player-prop feeds directly so book-specific prices and
recent hit rates are not lost through the ranked candidate stream.  The board
is pregame-only, full-game-only, Hard Rock-only, and restricted to the
configured high-probability odds band.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from itertools import combinations
from typing import Any

from outlier_scrapers.game_totals import FULL_GAME_SCOPES
from outlier_scrapers.normalizer import ALLOWED_MLB_PLAYER_PROPS, percent_number
from outlier_scrapers.utils import (
    _american_to_decimal,
    _local_date,
    _price_text,
    drop_locked_events,
)

MIN_AMERICAN_ODDS = -1000
MAX_AMERICAN_ODDS = -200
MIN_L5_HIT_PCT = 75.0
MIN_L10_HIT_PCT = 75.0

ALT_PLAYER_PROPS_HEADER = [
    "league",
    "event_id",
    "event_starts_at",
    "matchup",
    "player",
    "player_id",
    "team",
    "market",
    "position",
    "line",
    "best_book",
    "best_odds",
    "l5_pct",
    "l10_pct",
    "season_pct",
    "market_id",
    "outcome_id",
]

ALT_PLAYER_PROPS_PARLAYS_HEADER = [
    "league",
    "event_starts_at",
    "type",
    "leg_1_player",
    "leg_1_market",
    "leg_1_position",
    "leg_1_line",
    "leg_1_odds",
    "leg_2_player",
    "leg_2_market",
    "leg_2_position",
    "leg_2_line",
    "leg_2_odds",
    "parlay_odds",
]

WNBA_TARGET_MARKETS = frozenset({"AST", "REB", "PTS", "PRA", "PA", "PR", "RA"})


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("−", "-"))
    except (TypeError, ValueError):
        return None


def _hard_rock_offer(rec: dict[str, Any]) -> tuple[str, int | float] | None:
    """Return the canonical Hard Rock offer, excluding similarly named books."""
    for offer in rec.get("books") or []:
        book = str(offer.get("book") or offer.get("book_raw") or "").strip()
        if "".join(ch for ch in book.lower() if ch.isalnum()) != "hardrock":
            continue
        price = _number(offer.get("odds") if offer.get("odds") is not None else offer.get("odds_raw"))
        if price is None:
            return None
        normalized_price: int | float = int(price) if price.is_integer() else price
        return "Hard Rock", normalized_price
    return None


def _event_started(rec: dict[str, Any], now: datetime) -> bool:
    context = rec.get("sport_context") or {}
    kept, _dropped = drop_locked_events(
        [
            {
                "event_id": rec.get("event_id"),
                "_event_starts_at": rec.get("event_starts_at")
                or context.get("event_starts_at"),
            }
        ],
        now=now,
    )
    return not kept


def _is_allowed_side(league: str, market: str, position: str) -> bool:
    if league == "WNBA":
        return market in WNBA_TARGET_MARKETS and position == "OVER"
    if league != "MLB" or market not in ALLOWED_MLB_PLAYER_PROPS:
        return False
    return position == ("UNDER" if market == "2B" else "OVER")


def build_alt_player_props_board(
    props_norm: dict[str, Any] | None,
    *,
    league: str,
    now: datetime | None = None,
    target_date: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to four strict alternate player props per event."""
    token = league.strip().upper()
    now = now or datetime.now().astimezone()
    eligible: list[dict[str, Any]] = []

    for rec in (props_norm or {}).get("records") or []:
        if str(rec.get("league") or token).strip().upper() != token:
            continue
        if rec.get("is_active") is False:
            continue
        context = rec.get("sport_context") or {}
        if str(context.get("scope") or rec.get("scope") or "").lower() not in FULL_GAME_SCOPES:
            continue
        event_id = str(rec.get("event_id") or "").strip()
        event_starts_at = rec.get("event_starts_at") or context.get("event_starts_at")
        if not event_id or not event_starts_at or _event_started(rec, now):
            continue
        if target_date is not None and _local_date(event_starts_at) != target_date:
            continue

        market = str(rec.get("market") or "").strip().upper()
        position = str(rec.get("position") or rec.get("side") or "").strip().upper()
        if not _is_allowed_side(token, market, position):
            continue

        offer = _hard_rock_offer(rec)
        if offer is None:
            continue
        best_book, best_odds = offer
        odds_value = float(best_odds)
        if not (MIN_AMERICAN_ODDS <= odds_value <= MAX_AMERICAN_ODDS):
            continue

        l5 = percent_number(rec.get("l5_pct"))
        l10 = percent_number(rec.get("l10_pct"))
        if l5 is None or l10 is None:
            continue
        if not (MIN_L5_HIT_PCT <= l5 <= 100.0) or not (MIN_L10_HIT_PCT <= l10 <= 100.0):
            continue
        player = str(rec.get("player") or rec.get("player_raw") or "").strip()
        if not player:
            continue
        player_id = str(rec.get("player_id") or "").strip()
        outcome_id = str(rec.get("outcome_id") or context.get("outcome_id") or "").strip()
        if not outcome_id:
            continue

        eligible.append(
            {
                "league": token,
                "event_id": event_id,
                "event_starts_at": event_starts_at,
                "matchup": rec.get("matchup") or rec.get("matchup_raw") or "",
                "player": player,
                "player_id": player_id,
                "_player_key": player_id or player.casefold(),
                "team": rec.get("team") or rec.get("team_raw") or "",
                "market": market,
                "position": position,
                "line": rec.get("line") if rec.get("line") is not None else "",
                "best_book": best_book,
                "best_odds": best_odds,
                "l5_pct": l5,
                "l10_pct": l10,
                "season_pct": percent_number(rec.get("season_pct")) or 0.0,
                "market_id": rec.get("market_id") or "",
                "outcome_id": outcome_id,
            }
        )

    eligible.sort(
        key=lambda row: (row["l10_pct"], row["season_pct"], -float(row["best_odds"])),
        reverse=True,
    )
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_event[row["event_id"]].append(row)

    selected: list[dict[str, Any]] = []
    for event_rows in by_event.values():
        seen: set[tuple[str, str]] = set()
        for row in event_rows:
            identity = (row["_player_key"], row["market"])
            if identity in seen:
                continue
            seen.add(identity)
            clean = dict(row)
            clean.pop("_player_key", None)
            selected.append(clean)
            if len(seen) >= 4:
                break
    return selected


def _decimal_to_american(decimal: float) -> str:
    if decimal < 1.01:
        return "N/A"
    if decimal >= 2.0:
        return f"+{int(round((decimal - 1.0) * 100.0))}"
    return f"-{int(round(100.0 / (decimal - 1.0)))}"


def build_alt_player_props_parlays(board: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build two-leg, distinct-player parlays from strict board rows."""
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in board:
        day = _local_date(row.get("event_starts_at"))
        if day is None:
            continue
        by_day[day].append(row)

    parlays: list[dict[str, Any]] = []
    for day, day_rows in by_day.items():
        for leg1, leg2 in combinations(day_rows, 2):
            key1 = leg1.get("player_id") or str(leg1.get("player") or "").casefold()
            key2 = leg2.get("player_id") or str(leg2.get("player") or "").casefold()
            if not key1 or key1 == key2 or leg1.get("league") != leg2.get("league"):
                continue
            dec1 = _american_to_decimal(leg1.get("best_odds"))
            dec2 = _american_to_decimal(leg2.get("best_odds"))
            if dec1 is None or dec2 is None:
                continue
            parlay_decimal = dec1 * dec2
            parlays.append(
                {
                    "league": leg1["league"],
                    "event_starts_at": day,
                    "type": "SGP" if leg1["event_id"] == leg2["event_id"] else "Cross-Game",
                    "leg_1_player": leg1["player"],
                    "leg_1_market": leg1["market"],
                    "leg_1_position": leg1["position"],
                    "leg_1_line": leg1["line"],
                    "leg_1_odds": leg1["best_odds"],
                    "leg_2_player": leg2["player"],
                    "leg_2_market": leg2["market"],
                    "leg_2_position": leg2["position"],
                    "leg_2_line": leg2["line"],
                    "leg_2_odds": leg2["best_odds"],
                    "parlay_odds": _decimal_to_american(parlay_decimal),
                    "_sort_decimal": parlay_decimal,
                    "_l10_avg": (leg1["l10_pct"] + leg2["l10_pct"]) / 2.0,
                }
            )

    parlays.sort(key=lambda row: (row["_l10_avg"], -row["_sort_decimal"]), reverse=True)
    for row in parlays:
        row.pop("_sort_decimal", None)
        row.pop("_l10_avg", None)
    return parlays


def format_alt_player_props_md(
    rows: list[dict[str, Any]], parlays: list[dict[str, Any]]
) -> str:
    lines = [
        "## Alt Player Props (Bankroll Builders)",
        "",
        "Full-game Hard Rock lines from -1000 to -200 with L5>=75% and L10>=75%.",
        "Limited to the highest-probability four diverse props per game.",
        "",
    ]
    if not rows:
        lines.append("*No qualifying alt lines found in current odds.*")
        return "\n".join(lines) + "\n"

    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_event[str(row.get("event_id"))].append(row)
    for event_id, event_rows in by_event.items():
        lines.append(f"### Event: {event_id}")
        for row in event_rows:
            lines.append(
                f"- **{row['player']}** ({row['position'][:1]} {row['line']} {row['market']}) "
                f"at {_price_text(row['best_odds'])} Hard Rock "
                f"[L5: {row['l5_pct']:.0f}% | L10: {row['l10_pct']:.0f}% | "
                f"SZN: {row['season_pct']:.0f}%]"
            )
        lines.append("")

    lines.append("### Recommended 2-Leg Parlays")
    if not parlays:
        lines.append("*Not enough qualifying legs for parlays.*")
    else:
        for index, parlay in enumerate(parlays[:5], 1):
            lines.append(f"{index}. **{parlay['type']} Parlay ({parlay['parlay_odds']})**")
            lines.append(
                f"   - {parlay['leg_1_player']} {parlay['leg_1_position'][:1]} "
                f"{parlay['leg_1_line']} {parlay['leg_1_market']} "
                f"({_price_text(parlay['leg_1_odds'])})"
            )
            lines.append(
                f"   - {parlay['leg_2_player']} {parlay['leg_2_position'][:1]} "
                f"{parlay['leg_2_line']} {parlay['leg_2_market']} "
                f"({_price_text(parlay['leg_2_odds'])})"
            )
    return "\n".join(lines) + "\n"
