"""Team-level game simulation (plan M8, steps 1-4).

1. draw possessions (log-normal, M5); 2. draw per-100 efficiencies with correlated noise
(M4); 3. regulation score; 4. overtime. Margin, total and both team totals are read off
the *same* arrays, so a spread, a total and a team-total price cannot contradict each
other. Step 1 of the plan (player availability) and steps 5-7 (minutes, usage, stat lines)
belong to the player phase and are not here.

Every parameter is required and must be finite: a missing input raises
:class:`SimInputError` instead of defaulting, so a caller that lacks a fitted noise
estimate gets no probabilities rather than invented ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FArr = NDArray[np.float64]
IArr = NDArray[np.int64]
MIN_DRAWS = 100
MAX_OT_ROUNDS = 4
OT_FRACTION = 5.0 / 48.0  # one overtime period against regulation


class SimInputError(ValueError):
    """A simulation input is missing, non-finite or out of range."""


@dataclass(frozen=True)
class SimParams:
    """One entry per game (shape ``(G,)``)."""

    log_poss: FArr
    sigma_p: FArr
    e_home: FArr
    e_away: FArr
    sigma_e: FArr
    rho: FArr
    ot_ratio: FArr
    # per-100 efficiency change per unit of log-possession surprise (shared by both teams)
    beta_pe: FArr | None = None

    @classmethod
    def single(cls, **kw: float) -> SimParams:
        return cls(**{k: np.array([v], dtype=np.float64) for k, v in kw.items()})

    def validate(self) -> int:
        arrays = {
            k: np.asarray(v, dtype=np.float64) for k, v in self.__dict__.items() if v is not None
        }
        sizes = {a.shape for a in arrays.values()}
        if len(sizes) != 1 or len(next(iter(sizes))) != 1:
            raise SimInputError(f"parameters must be 1-d arrays of one length, got {sizes}")
        for name, a in arrays.items():
            if not np.isfinite(a).all():
                raise SimInputError(f"{name} contains a non-finite value")
        if (arrays["sigma_e"] <= 0).any() or (arrays["sigma_p"] < 0).any():
            raise SimInputError("sigma_e must be positive and sigma_p non-negative")
        if (np.abs(arrays["rho"]) >= 1).any():
            raise SimInputError("rho must lie strictly inside (-1, 1)")
        if (arrays["ot_ratio"] < 0).any():
            raise SimInputError("ot_ratio must be non-negative")
        return int(next(iter(sizes))[0])


@dataclass(frozen=True)
class SimDraws:
    home: IArr  # final home points, overtime included, shape (G, n)
    away: IArr
    ot: IArr  # overtime periods played
    poss: FArr  # regulation (48-minute) possessions

    @property
    def margin(self) -> IArr:
        return self.home - self.away

    @property
    def total(self) -> IArr:
        return self.home + self.away


def _correlated(rho: FArr, shape: tuple[int, int], rng: np.random.Generator) -> tuple[FArr, FArr]:
    z1 = rng.standard_normal(shape)
    z2 = rho[:, None] * z1 + np.sqrt(1.0 - rho[:, None] ** 2) * rng.standard_normal(shape)
    return z1, z2


def _pull_ties(home: IArr, away: IArr, ratio: FArr, rng: np.random.Generator) -> tuple[IArr, IArr]:
    """Raise the regulation tie rate to ``ratio`` x the simulated Gaussian-margin tie rate.

    Late-game fouling and clock-killing make real NBA margins of +-1 collapse to ties more
    often than a smooth distribution allows. One-point margins are nudged to ties (the
    winner loses a point or the loser gains one, chosen at random so total points are
    unbiased) at the rate that makes the overall tie frequency match the observed ratio.
    """
    margin = home - away
    tie = (margin == 0).mean(axis=1)
    one = (np.abs(margin) == 1).mean(axis=1)
    extra = np.maximum(ratio * tie - tie, 0.0)
    q = np.divide(extra, one, out=np.zeros_like(extra), where=one > 0)
    q = np.minimum(q, 1.0)
    flip = (np.abs(margin) == 1) & (rng.uniform(size=margin.shape) < q[:, None])
    drop_winner = rng.uniform(size=margin.shape) < 0.5
    home_wins = margin > 0
    h = home.copy()
    a = away.copy()
    # winner -1 or loser +1: either way the one-point margin becomes zero
    h = np.where(flip & home_wins & drop_winner, h - 1, h)
    a = np.where(flip & ~home_wins & drop_winner, a - 1, a)
    a = np.where(flip & home_wins & ~drop_winner, a + 1, a)
    h = np.where(flip & ~home_wins & ~drop_winner, h + 1, h)
    return h, a


def simulate(params: SimParams, n: int, rng: np.random.Generator) -> SimDraws:
    if n < MIN_DRAWS:
        raise SimInputError(f"need at least {MIN_DRAWS} draws, got {n}")
    games = params.validate()
    shape = (games, n)
    lp = np.asarray(params.log_poss, dtype=np.float64)[:, None]
    sp = np.asarray(params.sigma_p, dtype=np.float64)[:, None]
    se = np.asarray(params.sigma_e, dtype=np.float64)[:, None]
    rho = np.asarray(params.rho, dtype=np.float64)
    eh = np.asarray(params.e_home, dtype=np.float64)[:, None]
    ea = np.asarray(params.e_away, dtype=np.float64)[:, None]

    z_poss = rng.standard_normal(shape)
    poss = np.exp(lp + sp * z_poss)  # log-normal: positive, no floor
    beta = (
        np.zeros((games, 1))
        if params.beta_pe is None
        else np.asarray(params.beta_pe, dtype=np.float64)[:, None]
    )
    pace_effect = beta * sp * z_poss  # a faster game than expected is less efficient per possession
    zh, za = _correlated(rho, shape, rng)
    home = np.rint(np.maximum(poss * (eh + pace_effect + se * zh) / 100.0, 0.0)).astype(np.int64)
    away = np.rint(np.maximum(poss * (ea + pace_effect + se * za) / 100.0, 0.0)).astype(np.int64)
    home, away = _pull_ties(home, away, np.asarray(params.ot_ratio, dtype=np.float64), rng)

    ot = np.zeros(shape, dtype=np.int64)
    ot_noise = se * np.sqrt(1.0 / OT_FRACTION)  # fewer possessions, so a wider per-100 spread
    for _ in range(MAX_OT_ROUNDS):
        tied = home == away
        if not tied.any():
            break
        ot += tied
        zh, za = _correlated(rho, shape, rng)
        poss_ot = poss * OT_FRACTION
        home += np.where(
            tied, np.rint(np.maximum(poss_ot * (eh + ot_noise * zh) / 100.0, 0.0)), 0
        ).astype(np.int64)
        away += np.where(
            tied, np.rint(np.maximum(poss_ot * (ea + ot_noise * za) / 100.0, 0.0)), 0
        ).astype(np.int64)
    still = home == away  # a fifth overtime is a coin flip, not another period
    coin = rng.uniform(size=shape) < 0.5
    home = np.where(still & coin, home + 1, home)
    away = np.where(still & ~coin, away + 1, away)
    return SimDraws(home=home, away=away, ot=ot, poss=poss)
