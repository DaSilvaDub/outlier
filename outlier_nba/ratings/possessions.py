"""Possession estimate and the numpy dataset the team models fit on (plan M4).

``poss = FGA + 0.44*FTA - OREB + TOV`` per team, averaged over both teams (the two
estimates differ by a few possessions of measurement noise). When a game has no
summary box score, ``poss`` falls back to ``FGA + 0.44*FTA + adj`` where ``adj`` is the
running mean of ``TOV - OREB`` over *earlier* exact games, so the fallback never uses
the future. ``Dataset.exact`` records which games carry the exact estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from outlier_nba.ingest.espn import GameRow
from outlier_scrapers.registry import NBA_TEAM_DISPLAY

FTA_WEIGHT = 0.44  # standard free-throw-trip share; the fit is checked in tests, not assumed
ET = ZoneInfo("America/New_York")
TEAMS: tuple[str, ...] = tuple(sorted(NBA_TEAM_DISPLAY))
TEAM_INDEX = {t: i for i, t in enumerate(TEAMS)}
RESTART_DAY = datetime(2020, 7, 30, tzinfo=ET).date().toordinal()  # bubble restart

FArr = NDArray[np.float64]
IArr = NDArray[np.int64]
BArr = NDArray[np.bool_]


def team_possessions(fga: float, fta: float, oreb: float, tov: float) -> float:
    return fga + FTA_WEIGHT * fta - oreb + tov


def et_day(game: GameRow) -> int:
    """Eastern calendar date as an ordinal: NBA slates are named by US evening date."""
    return game.start_utc.astimezone(ET).date().toordinal()


def is_bubble(game: GameRow) -> bool:
    """Orlando-bubble games after the restart: every one was played on a neutral court.

    ESPN does not set ``neutralSite`` on them (only the three international games of that
    season carry it), so the flag is derived from the date.
    """
    return game.season == "2019-20" and et_day(game) >= RESTART_DAY


def is_stress(game: GameRow) -> bool:
    """Bubble restart (neutral courts) and 2020-21 (limited or no crowds)."""
    return game.season == "2020-21" or is_bubble(game)


@dataclass(frozen=True)
class Dataset:
    games: tuple[GameRow, ...]
    home: IArr
    away: IArr
    hp: FArr
    ap: FArr
    poss: FArr  # whole game, overtime included
    poss48: FArr  # normalised to 48 minutes
    ot: IArr
    neutral: BArr
    stress: BArr
    exact: BArr
    stype: IArr
    day: IArr
    season: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.games)

    def mask_before(self, day: int, season: str | None = None) -> BArr:
        m: BArr = self.day < day
        if season is not None:
            m = m & np.array([s == season for s in self.season], dtype=bool)
        return m


def build_dataset(games: list[GameRow]) -> Dataset:
    ordered = sorted(games, key=lambda g: (g.start_utc, g.game_id))
    n = len(ordered)
    poss = np.full(n, np.nan)
    exact = np.zeros(n, dtype=bool)
    adj_sum, adj_n = 0.0, 0
    for i, g in enumerate(ordered):
        fga_fta = [g.home_fga, g.home_fta, g.away_fga, g.away_fta]
        if any(v is None for v in fga_fta):
            continue
        base_h = (g.home_fga or 0) + FTA_WEIGHT * (g.home_fta or 0)
        base_a = (g.away_fga or 0) + FTA_WEIGHT * (g.away_fta or 0)
        if g.home_oreb is not None and g.home_tov is not None and g.away_oreb is not None:
            tov_a = g.away_tov if g.away_tov is not None else 0
            ph = base_h - g.home_oreb + g.home_tov
            pa = base_a - g.away_oreb + tov_a
            poss[i] = 0.5 * (ph + pa)
            exact[i] = True
            adj_sum += 0.5 * ((g.home_tov - g.home_oreb) + (tov_a - g.away_oreb))
            adj_n += 1
        elif adj_n:
            poss[i] = 0.5 * (base_h + base_a) + adj_sum / adj_n  # causal running mean
    ot = np.array([g.ot_periods for g in ordered], dtype=np.int64)
    return Dataset(
        games=tuple(ordered),
        home=np.array([TEAM_INDEX[g.home] for g in ordered], dtype=np.int64),
        away=np.array([TEAM_INDEX[g.away] for g in ordered], dtype=np.int64),
        hp=np.array([g.home_pts for g in ordered], dtype=np.float64),
        ap=np.array([g.away_pts for g in ordered], dtype=np.float64),
        poss=poss,
        poss48=poss * 48.0 / (48.0 + 5.0 * ot),
        ot=ot,
        neutral=np.array([g.neutral or is_bubble(g) for g in ordered], dtype=bool),
        stress=np.array([is_stress(g) for g in ordered], dtype=bool),
        exact=exact,
        stype=np.array([g.season_type for g in ordered], dtype=np.int64),
        day=np.array([et_day(g) for g in ordered], dtype=np.int64),
        season=tuple(g.season for g in ordered),
    )


def describe(ds: Dataset) -> dict[str, Any]:
    ok = ~np.isnan(ds.poss)
    return {
        "games": len(ds),
        "with_possessions": int(ok.sum()),
        "exact_possessions": int(ds.exact.sum()),
        "mean_poss": float(np.nanmean(ds.poss)) if ok.any() else None,
    }
