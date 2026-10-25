"""Price team markets from one set of simulated draws (plan M11, team layer).

Moneyline, spread, game total, team totals and every alternate line read the same
``SimDraws``, so the prices are mutually consistent by construction. Win / push / loss
are separate: an integer line has a real push probability and the EV treats a push as a
refund. A draw set with NaNs or too few draws raises :class:`PricingError`; there is no
default probability.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from outlier_nba.sim.engine import SimDraws

MIN_PRICING_DRAWS = 200
Side = Literal["HOME", "AWAY", "OVER", "UNDER"]


class PricingError(ValueError):
    """The draws cannot support a probability."""


@dataclass(frozen=True)
class Priced:
    p_win: float
    p_push: float
    p_loss: float
    se: float
    n_draws: int

    @property
    def p_win_given_no_push(self) -> float:
        """Win probability among non-push outcomes: what a two-way devigged price implies."""
        live = self.p_win + self.p_loss
        return self.p_win / live if live > 0 else math.nan

    @property
    def fair_decimal(self) -> float:
        return 1.0 + self.p_loss / self.p_win if self.p_win > 0 else math.inf

    def ev(self, decimal: float) -> float:
        """Expected profit per unit staked at ``decimal`` odds; a push returns the stake."""
        return self.p_win * (decimal - 1.0) - self.p_loss


def _priced(win: NDArray[np.bool_], push: NDArray[np.bool_]) -> Priced:
    n = int(win.size)
    p_win, p_push = float(win.mean()), float(push.mean())
    p_loss = 1.0 - p_win - p_push
    p_loss = max(p_loss, 0.0)
    return Priced(
        p_win=p_win,
        p_push=p_push,
        p_loss=p_loss,
        se=math.sqrt(p_win * (1.0 - p_win) / n),
        n_draws=n,
    )


def _game(draws: SimDraws, g: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    home = np.asarray(draws.home[g], dtype=np.float64)
    away = np.asarray(draws.away[g], dtype=np.float64)
    if home.size < MIN_PRICING_DRAWS:
        raise PricingError(f"need at least {MIN_PRICING_DRAWS} draws, got {home.size}")
    if not (np.isfinite(home).all() and np.isfinite(away).all()):
        raise PricingError("simulated draws contain a non-finite value")
    return home, away


def _vs(values: NDArray[np.float64], line: float) -> dict[str, Priced]:
    over = _priced(values > line, values == line)
    under = _priced(values < line, values == line)
    return {"OVER": over, "UNDER": under}


def price_moneyline(draws: SimDraws, g: int = 0) -> dict[str, Priced]:
    home, away = _game(draws, g)
    margin = home - away  # overtime always produces a winner, so this never ties
    zero = np.zeros(margin.shape, dtype=bool)
    return {"HOME": _priced(margin > 0, zero), "AWAY": _priced(margin < 0, zero)}


def price_spread(draws: SimDraws, home_line: float, g: int = 0) -> dict[str, Priced]:
    """``home_line`` is the home team's handicap as quoted (-5.5: home gives 5.5)."""
    home, away = _game(draws, g)
    adjusted = (home - away) + home_line
    push = adjusted == 0
    return {"HOME": _priced(adjusted > 0, push), "AWAY": _priced(adjusted < 0, push)}


def price_total(draws: SimDraws, line: float, g: int = 0) -> dict[str, Priced]:
    home, away = _game(draws, g)
    return _vs(home + away, line)


def price_team_total(
    draws: SimDraws, team: Literal["HOME", "AWAY"], line: float, g: int = 0
) -> dict[str, Priced]:
    home, away = _game(draws, g)
    return _vs(home if team == "HOME" else away, line)


def price_ladder(
    draws: SimDraws,
    kind: Literal["TOTAL", "SPREAD", "HOME_TOTAL", "AWAY_TOTAL"],
    lines: Sequence[float],
    g: int = 0,
) -> dict[float, dict[str, Priced]]:
    """Alternate lines: the same draws priced at every rung."""
    out: dict[float, dict[str, Priced]] = {}
    for line in lines:
        if kind == "TOTAL":
            out[line] = price_total(draws, line, g)
        elif kind == "SPREAD":
            out[line] = price_spread(draws, line, g)
        elif kind == "HOME_TOTAL":
            out[line] = price_team_total(draws, "HOME", line, g)
        else:
            out[line] = price_team_total(draws, "AWAY", line, g)
    return out
