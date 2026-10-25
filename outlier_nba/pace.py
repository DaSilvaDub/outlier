"""Pace model: the one place possessions are predicted (plan M5).

``log(poss48_t) = mu + po*playoff + p_home + p_away``, ridge-fit with time decay and a
prior carried from the previous season, exactly like the ratings. Possessions are
log-normal, which keeps them positive without any artificial floor (the old CTG-based
simulation clamped at 90/95 and biased totals upward). Nothing else in the package may
model pace: efficiency ratings are per-100 and carry no tempo term.

Overtime is not modelled here as a flag on a pace draw. :func:`ot_scale` measures how
much more often NBA games actually reach overtime than a Gaussian margin with the
model's own spread would tie; the simulator uses that ratio to reproduce the observed
overtime frequency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from outlier_nba.ratings.possessions import TEAMS, Dataset
from outlier_nba.ratings.ridge import solve_weighted_ridge

FArr = NDArray[np.float64]
NT = len(TEAMS)
P_MU, P_PO, P_T = 0, 1, 2
NCOL = 2 + NT


@dataclass(frozen=True)
class PacePrior:
    mu: float = math.log(98.0)
    playoff: float = 0.0
    team: FArr = field(default_factory=lambda: np.zeros(NT))


@dataclass(frozen=True)
class PaceFit:
    mu: float
    playoff: float
    team: FArr
    n_games: int

    def mean_log(self, home: int, away: int, *, playoff: bool = False) -> float:
        return float(
            self.mu + (self.playoff if playoff else 0.0) + self.team[home] + self.team[away]
        )

    def to_prior(self, carry: float) -> PacePrior:
        return PacePrior(self.mu, self.playoff, carry * self.team)


def fit_pace(
    ds: Dataset,
    mask: NDArray[np.bool_],
    *,
    now_day: int,
    lam: float,
    tau: float,
    lam_mu: float,
    lam_po: float,
    prior: PacePrior,
) -> PaceFit:
    idx = np.flatnonzero(mask & ~np.isnan(ds.poss48))
    n = len(idx)
    if n == 0:
        return PaceFit(prior.mu, prior.playoff, prior.team.copy(), 0)
    x = np.zeros((n, NCOL))
    x[:, P_MU] = 1.0
    x[:, P_PO] = (ds.stype[idx] == 3).astype(np.float64)
    rows = np.arange(n)
    x[rows, P_T + ds.home[idx]] += 1.0
    x[rows, P_T + ds.away[idx]] += 1.0
    y = np.log(ds.poss48[idx])
    w = np.exp(-np.maximum(now_day - ds.day[idx], 0).astype(np.float64) / tau)
    pen = np.full(NCOL, float(lam))
    pen[P_MU], pen[P_PO] = lam_mu, lam_po
    beta0 = np.concatenate([[prior.mu, prior.playoff], prior.team])
    beta = solve_weighted_ridge(x, y, w, pen, beta0)
    return PaceFit(float(beta[P_MU]), float(beta[P_PO]), beta[P_T:].copy(), n)


def _normal_pdf(z: FArr) -> FArr:
    return np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def tie_density(exp_margin: FArr, sd_margin: float | FArr) -> FArr:
    """P(integer margin == 0) under a Gaussian margin: the density at zero."""
    sd = np.asarray(sd_margin, dtype=np.float64)
    return _normal_pdf(np.asarray(exp_margin, dtype=np.float64) / sd) / sd


def ot_scale(exp_margin: FArr, sd_margin: float | FArr, went_to_ot: FArr) -> float:
    """Observed overtimes divided by the overtimes a Gaussian margin would produce.

    NBA games tie in regulation about twice as often as a smooth margin distribution
    implies (the trailing side fouls and the leading side lets the clock run to avoid
    giving up the lead), so the ratio is well above 1. It is estimated, never assumed.
    """
    expected = float(np.sum(tie_density(exp_margin, sd_margin)))
    if expected <= 0.0:
        return float("nan")
    return float(np.sum(went_to_ot)) / expected
