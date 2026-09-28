"""Schedule / game-environment adapter (nflverse ``schedules`` release).

One record per regular-season game in weeks 1..``through_week``: kickoff, final
score (None until played), closing spread/total/moneylines, roof, surface,
temperature, wind, rest days, referee, and starting QBs. ``spread_line`` is
nflverse's convention: positive means the home team is favored.
"""

from __future__ import annotations

from typing import Any

from outlier_nfl.config import normalize_team

from .common import NFLVERSE_RELEASES, Client, num

SCHEDULE_URL = NFLVERSE_RELEASES + "/schedules/games.csv"

NUMERIC_FIELDS = (
    "home_score", "away_score", "spread_line", "total_line", "home_moneyline",
    "away_moneyline", "home_spread_odds", "away_spread_odds", "over_odds", "under_odds",
    "temp", "wind", "home_rest", "away_rest",
)
TEXT_FIELDS = (
    "game_id", "gameday", "gametime", "weekday", "location", "roof", "surface", "stadium",
    "referee", "home_qb_name", "away_qb_name", "home_coach", "away_coach",
)


def fetch(client: Client, season: int, through_week: int = 22) -> dict[str, Any]:
    """Regular-season game environment records for ``season``."""
    records: list[dict[str, Any]] = []
    for row in client.fetch_csv(SCHEDULE_URL):
        if str(row.get("season")) != str(season) or row.get("game_type") != "REG":
            continue
        week = int(num(row.get("week")) or 0)
        if week < 1 or week > through_week:
            continue
        home, away = str(row.get("home_team") or ""), str(row.get("away_team") or "")
        record: dict[str, Any] = {
            "source": "schedule",
            "kind": "game",
            "season": season,
            "week": week,
            "home_team": normalize_team(home) or home,
            "away_team": normalize_team(away) or away,
            "div_game": row.get("div_game") == "1",
        }
        for field in TEXT_FIELDS:
            record[field] = row.get(field) or None
        for field in NUMERIC_FIELDS:
            record[field] = num(row.get(field))
        records.append(record)
    return {"records": records}
