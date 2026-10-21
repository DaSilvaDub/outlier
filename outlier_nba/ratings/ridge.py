"""Ridge per-100 team ratings with a home term, time decay and a prior (plan M4).

``pts_per100(T vs O) = mu + h*s + po*playoff + O_T - D_O``, where ``s`` is +1 on the
home team's row, -1 on the away team's and 0 at a neutral site. Each game contributes
two rows (one per offense). Games flagged ``stress`` (bubble, 2020-21 crowds) put their
home sign on a separate ``h_stress`` column, so they do not move the home-court
estimate used to price everything else.

Shrinkage is a penalty toward a prior vector rather than toward zero:
``beta = argmin sum w (y - X beta)^2 + sum lam_j (beta_j - beta0_j)^2``. At the start
of a season ``beta0`` is the previous season's final fit shrunk by ``carry``, which is
the plan's "(n*O_fit + k*O_prior)/(n + k)" with ``lam`` playing the role of ``k``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from outlier_nba.ratings.possessions import TEAMS, Dataset

FArr = NDArray[np.float64]
NT = len(TEAMS)
# column layout: mu | h | h_stress | playoff | O (NT) | D (NT)
C_MU, C_H, C_HS, C_PO = 0, 1, 2, 3
C_O, C_D = 4, 4 + NT
NCOL = 4 + 2 * NT
STRESS_PENALTY = 1e-6  # effectively free: it exists only to absorb stress-game home effects


def solve_weighted_ridge(x: FArr, y: FArr, w: FArr, penalty: FArr, beta0: FArr) -> FArr:
    xw = x * w[:, None]
    a = x.T @ xw + np.diag(penalty)
    b = xw.T @ y + penalty * beta0
    return np.asarray(np.linalg.solve(a, b), dtype=np.float64)


@dataclass(frozen=True)
class RatingPrior:
    mu: float = 0.0
    h: float = 0.0
    playoff: float = 0.0
    off: FArr = field(default_factory=lambda: np.zeros(NT))
    deff: FArr = field(default_factory=lambda: np.zeros(NT))


@dataclass(frozen=True)
class RatingFit:
    mu: float
    h: float
    h_stress: float
    playoff: float
    off: FArr
    deff: FArr
    n_games: int

    def per100(self, home: int, away: int, *, neutral: bool = False, playoff: bool = False):
        """Expected points per 100 possessions ``(home, away)``."""
        s = 0.0 if neutral else 1.0
        po = self.playoff if playoff else 0.0
        e_home = self.mu + self.h * s + po + self.off[home] - self.deff[away]
        e_away = self.mu - self.h * s + po + self.off[away] - self.deff[home]
        return float(e_home), float(e_away)

    def to_prior(self, carry: float) -> RatingPrior:
        """Next season's starting point: team strengths shrink toward the league by ``carry``."""
        return RatingPrior(
            mu=self.mu,
            h=self.h,
            playoff=self.playoff,
            off=carry * self.off,
            deff=carry * self.deff,
        )


def fit_ratings(
    ds: Dataset,
    mask: NDArray[np.bool_],
    *,
    now_day: int,
    lam: float,
    tau: float,
    lam_mu: float,
    lam_h: float,
    lam_po: float,
    prior: RatingPrior,
) -> RatingFit:
    """Fit on the games selected by ``mask`` only. The caller owns the as-of cutoff."""
    ok = mask & ~np.isnan(ds.poss)
    idx = np.flatnonzero(ok)
    n = len(idx)
    beta0 = np.zeros(NCOL)
    beta0[C_MU], beta0[C_H], beta0[C_PO] = prior.mu, prior.h, prior.playoff
    beta0[C_O : C_O + NT], beta0[C_D : C_D + NT] = prior.off, prior.deff
    penalty = np.full(NCOL, float(lam))  # float: an int array would truncate STRESS_PENALTY to 0
    penalty[C_MU], penalty[C_H], penalty[C_HS], penalty[C_PO] = (
        lam_mu,
        lam_h,
        STRESS_PENALTY,
        lam_po,
    )
    if n == 0:
        return RatingFit(
            prior.mu, prior.h, 0.0, prior.playoff, prior.off.copy(), prior.deff.copy(), 0
        )

    rows = 2 * n
    x = np.zeros((rows, NCOL))
    y = np.empty(rows)
    w = np.empty(rows)
    hi, ai = ds.home[idx], ds.away[idx]
    sign = np.where(ds.neutral[idx], 0.0, 1.0)
    stress = ds.stress[idx]
    po = (ds.stype[idx] == 3).astype(np.float64)
    r_home, r_away = np.arange(n), np.arange(n) + n
    x[:, C_MU] = 1.0
    x[r_home, C_H] = np.where(stress, 0.0, sign)
    x[r_away, C_H] = np.where(stress, 0.0, -sign)
    x[r_home, C_HS] = np.where(stress, sign, 0.0)
    x[r_away, C_HS] = np.where(stress, -sign, 0.0)
    x[r_home, C_PO] = po
    x[r_away, C_PO] = po
    x[r_home, C_O + hi] = 1.0
    x[r_away, C_O + ai] = 1.0
    x[r_home, C_D + ai] = -1.0
    x[r_away, C_D + hi] = -1.0
    y[r_home] = ds.hp[idx] / ds.poss[idx] * 100.0
    y[r_away] = ds.ap[idx] / ds.poss[idx] * 100.0
    age = (now_day - ds.day[idx]).astype(np.float64)
    wg = np.exp(-np.maximum(age, 0.0) / tau)
    w[r_home] = wg
    w[r_away] = wg
    beta = solve_weighted_ridge(x, y, w, penalty, beta0)
    return RatingFit(
        mu=float(beta[C_MU]),
        h=float(beta[C_H]),
        h_stress=float(beta[C_HS]),
        playoff=float(beta[C_PO]),
        off=beta[C_O : C_O + NT].copy(),
        deff=beta[C_D : C_D + NT].copy(),
        n_games=n,
    )
