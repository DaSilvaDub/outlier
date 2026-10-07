"""The nflverse-data ``schedules/games.csv`` release asset returns 404.

Every schedule consumer (shadow-settle box scores, tape, external schedule)
must read the nfldata copy instead, from one shared constant.
"""

from __future__ import annotations

from outlier_nfl import boxscore_nflverse, tape_nflverse
from outlier_nfl.external import schedule

NFLDATA_GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


def test_schedule_url_points_at_nfldata() -> None:
    assert tape_nflverse.SCHEDULES_URL == NFLDATA_GAMES_URL


def test_all_schedule_consumers_share_the_url() -> None:
    assert boxscore_nflverse.NFLVERSE_GAMES_URL == tape_nflverse.SCHEDULES_URL
    assert schedule.SCHEDULE_URL == tape_nflverse.SCHEDULES_URL


def test_no_consumer_uses_dead_release_asset() -> None:
    for url in (
        tape_nflverse.SCHEDULES_URL,
        boxscore_nflverse.NFLVERSE_GAMES_URL,
        schedule.SCHEDULE_URL,
    ):
        assert "releases/download/schedules" not in url
