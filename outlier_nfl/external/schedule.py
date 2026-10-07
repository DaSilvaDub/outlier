"""Schedule / game-environment adapter (nflverse ``schedules`` release).

One record per regular-season game: kickoff, final score, closing
spread/total/moneylines, roof, surface, temperature, wind, rest days, referee,
and starting QBs. ``spread_line`` is nflverse's convention: positive means the
home team is favored.

Upcoming games stay in the output (venue and kickoff are pregame facts), but
for every game not surely over by the run's ``as_of_utc`` (kickoff +
``GAME_END_BUFFER``) the postgame fields (``POSTGAME_FIELDS``: score, closing
prices, observed weather) are blanked, so a replay cannot read results or
closes it would not have had (F01). A game in progress at ``as_of`` is not over.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from outlier_nfl.config import normalize_team
from outlier_nfl.run_context import game_finished_by, schedule_kickoff_utc
from outlier_nfl.tape_nflverse import SCHEDULES_URL

from .common import Client, num

SCHEDULE_URL = SCHEDULES_URL

NUMERIC_FIELDS = (
    "home_score", "away_score", "spread_line", "total_line", "home_moneyline",
    "away_moneyline", "home_spread_odds", "away_spread_odds", "over_odds", "under_odds",
    "temp", "wind", "home_rest", "away_rest",
)
# Known only once the game is played (or at close): blanked unless the game is over by as_of.
POSTGAME_FIELDS = (
    "home_score", "away_score", "spread_line", "total_line", "home_moneyline",
    "away_moneyline", "home_spread_odds", "away_spread_odds", "over_odds", "under_odds",
    "temp", "wind",
)
TEXT_FIELDS = (
    "game_id", "gameday", "gametime", "weekday", "location", "roof", "surface", "stadium",
    "referee", "home_qb_name", "away_qb_name", "home_coach", "away_coach",
)


def fetch(client: Client, season: int, as_of_utc: datetime) -> dict[str, Any]:
    """Regular-season game environment records for ``season`` as known at ``as_of_utc``."""
    records: list[dict[str, Any]] = []
    for row in client.fetch_csv(SCHEDULE_URL):
        if str(row.get("season")) != str(season) or row.get("game_type") != "REG":
            continue
        week = int(num(row.get("week")) or 0)
        if week < 1:
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
        kickoff = schedule_kickoff_utc(row.get("gameday"), row.get("gametime"))
        record["kickoff_utc"] = kickoff.isoformat() if kickoff else None
        if not game_finished_by(kickoff, as_of_utc):
            for field in POSTGAME_FIELDS:
                record[field] = None
        records.append(record)
    return {"records": records}
