"""Market, team, and probability normalization for outlier_nfl.

Provides taxonomy normalization, team registry lookups, push probability
adjustments, and orchestrates extraction of game lines and player propositions.
"""

from __future__ import annotations

import logging
from typing import Any

from outlier_nfl.config import (
    get_team_display_name,
    normalize_team,
)
from outlier_nfl.games import (
    american_to_implied_probability,
    extract_game_lines,
)
from outlier_nfl.models import NflGameLine, NflPlayerProp
from outlier_nfl.props import extract_player_props

logger = logging.getLogger("outlier_nfl.normalizer")

__all__ = [
    "adjust_push_probability",
    "american_to_implied_probability",
    "build_schedule_index",
    "build_team_index",
    "normalize_game_markets",
    "normalize_player_props",
]


def adjust_push_probability(conditional_prob: float, push_prob: float) -> float:
    """Adjust conditional win probability for push chance on integer handicap or total lines.

    Absolute Win Prob = (1.0 - push_prob) * conditional_win_prob
    """
    if push_prob <= 0.0:
        return conditional_prob
    return (1.0 - push_prob) * conditional_prob


def build_team_index(schedule_payload: dict[str, Any]) -> dict[str, str]:
    """Build a mapping of team IDs, aliases, and names to canonical team codes."""
    index: dict[str, str] = {}
    events = schedule_payload.get("events", []) if isinstance(schedule_payload, dict) else []

    for event in events:
        if not isinstance(event, dict):
            continue
        for side in ("home", "away"):
            team_obj = event.get(side)
            if isinstance(team_obj, dict):
                alias = team_obj.get("alias")
                name = team_obj.get("name")
                team_id = team_obj.get("teamId") or team_obj.get("id")

                canonical = normalize_team(alias) or normalize_team(name)
                if canonical:
                    if team_id:
                        index[str(team_id)] = canonical
                        index[str(team_id).lower()] = canonical
                    if alias:
                        index[str(alias)] = canonical
                        index[str(alias).upper()] = canonical
                    if name:
                        index[str(name)] = canonical
                        index[str(name).lower()] = canonical

    return index


def build_schedule_index(schedule_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build index of events keyed by eventId with normalized team metadata and matchups."""
    index: dict[str, dict[str, Any]] = {}
    events = schedule_payload.get("events", []) if isinstance(schedule_payload, dict) else []

    for event in events:
        if not isinstance(event, dict):
            continue

        event_id = event.get("eventId") or event.get("id")
        if not event_id:
            continue

        home = event.get("home", {}) if isinstance(event.get("home"), dict) else {}
        away = event.get("away", {}) if isinstance(event.get("away"), dict) else {}

        home_alias = home.get("alias")
        home_name = home.get("name")
        away_alias = away.get("alias")
        away_name = away.get("name")

        home_code = normalize_team(home_alias) or normalize_team(home_name) or str(home_alias or "")
        away_code = normalize_team(away_alias) or normalize_team(away_name) or str(away_alias or "")

        start_time = event.get("scheduledTime") or event.get("startTime")

        matchup = event.get("matchup")
        if not matchup:
            matchup = f"{away_code} @ {home_code}" if (away_code and home_code) else f"{away_name} @ {home_name}"

        event_info = {
            "event_id": str(event_id),
            "start_time": start_time,
            "home_team": home_code,
            "away_team": away_code,
            "home_name": home_name or (get_team_display_name(home_code) if home_code else ""),
            "away_name": away_name or (get_team_display_name(away_code) if away_code else ""),
            "home_team_id": home.get("teamId"),
            "away_team_id": away.get("teamId"),
            "matchup": matchup,
            "venue": event.get("venue"),
            "status": event.get("status"),
            "season": event.get("season"),
            "week": event.get("week"),
        }

        index[str(event_id)] = event_info
        id_alt = event.get("id")
        if id_alt:
            index[str(id_alt)] = event_info

    return index


def normalize_game_markets(
    event: dict[str, Any],
    event_markets_payload: dict[str, Any] | list[Any],
    team_index: dict[str, str],
) -> list[NflGameLine]:
    """Normalize raw game markets payload into NflGameLine records."""
    return extract_game_lines(event, event_markets_payload, team_index)


def normalize_player_props(
    player_props_payload: dict[str, Any] | list[Any],
    schedule_index: dict[str, dict[str, Any]],
) -> list[NflPlayerProp]:
    """Normalize raw bulk player props payload into NflPlayerProp records."""
    return extract_player_props(player_props_payload, schedule_index)
