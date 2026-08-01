"""Alt player props extraction for bankroll parlays.

Pulls low-risk alternate lines (e.g., -500 to -200) with the highest 
historical hit probabilities, prioritizing diverse markets within each game.
"""

from __future__ import annotations

from typing import Any
from collections import defaultdict
from itertools import combinations
from datetime import datetime

from outlier_scrapers.utils import _local_date, _price_text


ALT_PLAYER_PROPS_HEADER = [
    "league",
    "event_id",
    "event_starts_at",
    "player",
    "team",
    "market",
    "line",
    "best_odds",
    "l10_pct",
    "season_pct",
    "outcome_id",
]

ALT_PLAYER_PROPS_PARLAYS_HEADER = [
    "league",
    "event_starts_at",
    "type",
    "leg_1_player",
    "leg_1_market",
    "leg_1_line",
    "leg_1_odds",
    "leg_2_player",
    "leg_2_market",
    "leg_2_line",
    "leg_2_odds",
    "parlay_odds",
]

WNBA_TARGET_MARKETS = frozenset({"AST", "REB", "PTS", "PRA", "PA", "PR", "RA"})
MLB_TARGET_MARKETS = frozenset({"SO", "TB", "OUTS", "HRR", "ER", "BB", "H", "2B"})


def _american_to_decimal(american: float | int | None) -> float | None:
    if american is None:
        return None
    val = float(american)
    if val > 0:
        return (val / 100.0) + 1.0
    if val < 0:
        return (100.0 / abs(val)) + 1.0
    return 2.0


def _decimal_to_american(decimal: float) -> str:
    if decimal < 1.01:
        return "N/A"
    if decimal >= 2.0:
        val = (decimal - 1.0) * 100.0
        return f"+{int(round(val))}"
    else:
        val = 100.0 / (decimal - 1.0)
        return f"-{int(round(val))}"


def build_alt_player_props_board(
    rows: list[dict[str, Any]], league: str = "WNBA"
) -> list[dict[str, Any]]:
    """Extracts up to 4 top alternate player prop lines per match.
    
    Rules:
    - Side must be OVER.
    - Market must be in TARGET_MARKETS.
    - Odds must be between -500 and -200 inclusive.
    - Sorted by l10_pct (descending), season_pct (descending), best_odds (ascending).
    - Max 1 selection per (player, market) to ensure diversity.
    """
    eligible = []
    
    for row in rows:
        if row.get("league") != league:
            continue
        market = str(row.get("market") or "")
        
        if league == "WNBA":
            if market not in WNBA_TARGET_MARKETS:
                continue
            if str(row.get("side")).upper() != "OVER":
                continue
        elif league == "MLB":
            if market not in MLB_TARGET_MARKETS:
                continue
            side = str(row.get("side")).upper()
            if market == "2B" and side != "UNDER":
                continue
            if market != "2B" and side != "OVER":
                continue
        else:
            continue
            
        odds = row.get("best_odds")
        if odds is None:
            continue
            
        try:
            odds_val = int(odds)
        except (ValueError, TypeError):
            continue
            
        if not (-500 <= odds_val <= -200):
            continue
            
        l10 = float(row.get("l10_pct") or 0.0)
        season = float(row.get("season_pct") or 0.0)
        
        eligible.append(
            {
                "league": league,
                "event_id": row.get("event_id"),
                "event_starts_at": (row.get("sport_context") or {}).get("event_starts_at"),
                "player": row.get("player"),
                "player_id": row.get("player_id"),
                "team": row.get("team"),
                "market": market,
                "line": row.get("line"),
                "best_odds": odds_val,
                "l10_pct": l10,
                "season_pct": season,
                "outcome_id": row.get("outcome_id"),
            }
        )

    # Sort globally by our priority
    # Note: lower negative odds (e.g. -500) represents a higher probability,
    # so we sort best_odds ascending (-500 before -200)
    eligible.sort(key=lambda x: (x["l10_pct"], x["season_pct"], -x["best_odds"]), reverse=True)

    # Group by event_id and enforce rules
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        by_event[str(r["event_id"])].append(r)

    selected: list[dict[str, Any]] = []
    for event_id, event_rows in by_event.items():
        seen_combos = set()
        picked = 0
        for r in event_rows:
            combo = (r["player_id"], r["market"])
            if combo in seen_combos:
                continue
                
            selected.append(r)
            seen_combos.add(combo)
            picked += 1
            
            if picked >= 4:
                break
                
    return selected


def build_alt_player_props_parlays(
    board: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build 2-leg parlays from the best alt player props per day."""
    # Group by start day (local timezone proxy)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in board:
        day = _local_date(row.get("event_starts_at"))
        by_day[day].append(row)

    parlays = []
    for day, day_rows in by_day.items():
        # Generate 2-leg combos
        for leg1, leg2 in combinations(day_rows, 2):
            if leg1["player_id"] == leg2["player_id"]:
                # Don't parlay the exact same player
                continue

            dec1 = _american_to_decimal(leg1["best_odds"])
            dec2 = _american_to_decimal(leg2["best_odds"])
            if not dec1 or not dec2:
                continue
            parlay_dec = dec1 * dec2
            
            p_type = "SGP" if leg1["event_id"] == leg2["event_id"] else "Cross-Game"
            
            parlays.append(
                {
                    "league": leg1["league"],
                    "event_starts_at": day,
                    "type": p_type,
                    "leg_1_player": leg1["player"],
                    "leg_1_market": leg1["market"],
                    "leg_1_line": leg1["line"],
                    "leg_1_odds": leg1["best_odds"],
                    "leg_2_player": leg2["player"],
                    "leg_2_market": leg2["market"],
                    "leg_2_line": leg2["line"],
                    "leg_2_odds": leg2["best_odds"],
                    "parlay_odds": _decimal_to_american(parlay_dec),
                    "_sort_dec": parlay_dec,
                    "_l10_avg": (leg1["l10_pct"] + leg2["l10_pct"]) / 2.0,
                }
            )

    # Sort parlays: highest l10 average, then lowest odds (most likely)
    parlays.sort(key=lambda p: (p["_l10_avg"], -p["_sort_dec"]), reverse=True)
    
    # Clean up sort keys
    for p in parlays:
        p.pop("_sort_dec", None)
        p.pop("_l10_avg", None)

    return parlays


def format_alt_player_props_md(
    rows: list[dict[str, Any]], parlays: list[dict[str, Any]]
) -> str:
    lines = [
        "## Alt Player Props (Bankroll Builders)",
        "",
        "The top alt-line props (-500 to -200) mathematically backed by recent game trends.",
        "Limited to the highest probability 4 props per game to ensure diversity.",
        "",
    ]
    
    if not rows:
        lines.append("*No qualifying alt lines found in current odds.*")
        return "\n".join(lines) + "\n"

    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_event[str(r.get("event_id"))].append(r)

    # Output individual props by event
    for event_id, event_rows in by_event.items():
        if not event_rows:
            continue
            
        lines.append(f"### Event: {event_id}")
        for r in event_rows:
            odds = _price_text(r['best_odds'])
            l10 = f"{r['l10_pct']:.0f}%" if r.get('l10_pct') else "N/A"
            szn = f"{r['season_pct']:.0f}%" if r.get('season_pct') else "N/A"
            # Get the side for the markdown display. We didn't save side in eligible!
            # Let's just hardcode the display logic based on market for MLB/WNBA
            side_display = "U" if r["league"] == "MLB" and r["market"] == "2B" else "O"
            lines.append(
                f"- **{r['player']}** ({side_display} {r['line']} {r['market']}) at {odds} "
                f"[L10: {l10} | SZN: {szn}]"
            )
        lines.append("")
        
    lines.append("### Recommended 2-Leg Parlays")
    if not parlays:
        lines.append("*Not enough qualifying legs for parlays.*")
    else:
        # Just show top 5 parlays to avoid exploding the markdown
        for idx, p in enumerate(parlays[:5], 1):
            lines.append(f"{idx}. **{p['type']} Parlay ({p['parlay_odds']})**")
            side1 = "U" if p['league'] == "MLB" and p['leg_1_market'] == "2B" else "O"
            side2 = "U" if p['league'] == "MLB" and p['leg_2_market'] == "2B" else "O"
            lines.append(f"   - {p['leg_1_player']} {side1} {p['leg_1_line']} {p['leg_1_market']} ({_price_text(p['leg_1_odds'])})")
            lines.append(f"   - {p['leg_2_player']} {side2} {p['leg_2_line']} {p['leg_2_market']} ({_price_text(p['leg_2_odds'])})")
        
    return "\n".join(lines) + "\n"
