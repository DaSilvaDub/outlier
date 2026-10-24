"""Elo with a margin-of-victory multiplier (plan M4).

The *structure* follows the published 538 NBA form (K-factor update scaled by a MOV
multiplier that damps blowouts between mismatched teams, a home-court offset in rating
points, and between-season regression toward the mean). Every constant that can be
fit -- K, home-court points, season carryover, the MOV exponent -- is a parameter that
the walk-forward tuning sets on NBA data; the additive constants inside the multiplier
(+3, 7.5, 0.006) are structural and kept as published.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from outlier_nba.ratings.possessions import TEAMS

BASE = 1500.0
MOV_SHIFT = 3.0
MOV_DENOM = 7.5
MOV_ELO_SLOPE = 0.006


@dataclass(frozen=True)
class EloParams:
    k: float = 20.0
    hfa: float = 100.0
    carry: float = 0.75
    mov_exp: float = 0.8
    use_mov: bool = True


class EloTable:
    def __init__(self, params: EloParams) -> None:
        self.p = params
        self.r: NDArray[np.float64] = np.full(len(TEAMS), BASE, dtype=np.float64)
        self.season: str | None = None

    def snapshot(self) -> tuple[np.ndarray, str | None]:
        return self.r.copy(), self.season

    def _enter_season(self, season: str) -> None:
        if self.season is not None and season != self.season:
            self.r = self.p.carry * self.r + (1.0 - self.p.carry) * BASE
        self.season = season

    def prob_home(
        self, home: int, away: int, *, neutral: bool = False, season: str | None = None
    ) -> float:
        """Pre-game P(home wins). A new season's regression is applied lazily, read-only."""
        r = self.r
        if season is not None and self.season is not None and season != self.season:
            r = self.p.carry * r + (1.0 - self.p.carry) * BASE
        diff = r[home] - r[away] + (0.0 if neutral else self.p.hfa)
        return float(1.0 / (1.0 + 10.0 ** (-diff / 400.0)))

    def update(
        self, home: int, away: int, hp: float, ap: float, *, neutral: bool, season: str
    ) -> None:
        self._enter_season(season)
        diff = self.r[home] - self.r[away] + (0.0 if neutral else self.p.hfa)
        expected = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
        home_won = hp > ap
        margin = abs(hp - ap)
        mult = 1.0
        if self.p.use_mov:
            winner_edge = diff if home_won else -diff
            mult = ((margin + MOV_SHIFT) ** self.p.mov_exp) / (
                MOV_DENOM + MOV_ELO_SLOPE * winner_edge
            )
        delta = self.p.k * mult * ((1.0 if home_won else 0.0) - expected)
        self.r[home] += delta
        self.r[away] -= delta
