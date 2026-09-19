"""Extraction and normalization of NFL player props.

Extracts and validates player prop outcomes from bulk paginated Outlier feeds
into normalized NflPlayerProp instances.
"""

from __future__ import annotations

import logging
from typing import Any

from outlier_nfl.config import (
    detect_scope,
    normalize_market,
    normalize_team,
)
from outlier_nfl.games import american_to_implied_probability, extract_book_prices
from outlier_nfl.models import NflPlayerProp
from outlier_nfl.utils import coerce_float, coerce_odds

logger = logging.getLogger("outlier_nfl.props")


def extract_player_props(
    player_props_payload: dict[str, Any] | list[Any],
    schedule_index: dict[str, dict[str, Any]],
) -> list[NflPlayerProp]:
    """Normalize raw bulk player props payload into strongly typed NflPlayerProp objects."""
    results: list[NflPlayerProp] = []

    if isinstance(player_props_payload, dict):
        props = player_props_payload.get("props", [])
    elif isinstance(player_props_payload, list):
        props = player_props_payload
    else:
        props = []

    for item in props:
        if not isinstance(item, dict):
            continue

        outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else item
        if not isinstance(outcome, dict):
            continue
        event_id = str(outcome.get("eventId") or outcome.get("id") or "")
        event_info = schedule_index.get(event_id, {})

        event_starts_at = event_info.get("start_time") or outcome.get("eventStartsAt")
        matchup = event_info.get("matchup") or outcome.get("matchup") or ""
        player_name = str(outcome.get("playerName") or outcome.get("player_name") or "").strip()
        if not player_name and outcome.get("marketLabel"):
            label = str(outcome["marketLabel"])
            if " - " in label:
                player_name = label.split(" - ", 1)[0].strip()
            else:
                player_name = label.strip()

        if not player_name:
            continue

        player_id = outcome.get("playerId") or outcome.get("player_id")
        raw_prop = outcome.get("proposition") or outcome.get("market") or ""
        market = normalize_market(raw_prop) or str(raw_prop)
        pos = str(outcome.get("position", "")).strip().upper()

        line_val = outcome.get("line")
        try:
            line_float = float(line_val) if line_val is not None else None
        except (ValueError, TypeError):
            continue

        if line_float is None:
            continue

        # Team & Opponent resolution
        raw_team = outcome.get("teamId") or outcome.get("team")
        team = normalize_team(raw_team)
        if not team and event_info:
            home_tid = event_info.get("home_team_id")
            away_tid = event_info.get("away_team_id")
            if raw_team and raw_team == home_tid:
                team = event_info.get("home_team")
            elif raw_team and raw_team == away_tid:
                team = event_info.get("away_team")

        home_code = event_info.get("home_team")
        away_code = event_info.get("away_team")
        if team and home_code and away_code:
            opponent = away_code if team == home_code else home_code
        else:
            raw_opp = outcome.get("oppTeamId") or outcome.get("opponent")
            opponent = normalize_team(raw_opp)
            if not opponent and event_info:
                home_tid = event_info.get("home_team_id")
                away_tid = event_info.get("away_team_id")
                if raw_opp and raw_opp == home_tid:
                    opponent = event_info.get("home_team")
                elif raw_opp and raw_opp == away_tid:
                    opponent = event_info.get("away_team")

        # Books and best odds
        books = extract_book_prices(outcome)
        best_odds = (
            max((b.odds for b in books), default=None)
            if books
            else None
        )
        if best_odds is None:
            best_odds = coerce_odds(outcome.get("bestOdds"))
        implied_prob = american_to_implied_probability(best_odds)

        # Hit rate statistics
        raw_stats = item.get("stats")
        stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        l5 = coerce_float(stats.get("l5"))
        l10 = coerce_float(stats.get("l10"))
        l20 = coerce_float(stats.get("l20"))
        season_val = stats.get("curSeason") if stats.get("curSeason") is not None else stats.get("season")
        season = coerce_float(season_val)

        scope = detect_scope(outcome.get("periodLabel"), outcome.get("marketLabel"))
        market_id = outcome.get("marketId") or outcome.get("id")
        outcome_id = outcome.get("outcomeId") or outcome.get("id")

        results.append(
            NflPlayerProp(
                event_id=event_id,
                event_starts_at=event_starts_at,
                matchup=matchup,
                team=team,
                opponent=opponent,
                player_name=player_name,
                player_id=str(player_id) if player_id else None,
                market=market,
                market_raw=str(raw_prop),
                position=pos,
                line=line_float,
                books=books,
                best_odds=best_odds,
                implied_probability=implied_prob,
                l5_hit_rate=l5,
                l10_hit_rate=l10,
                l20_hit_rate=l20,
                season_hit_rate=season,
                scope=scope,
                market_id=str(market_id) if market_id else None,
                outcome_id=str(outcome_id) if outcome_id else None,
            )
        )

    return results
