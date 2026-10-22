"""Team model: season-chained as-of fits plus the out-of-sample noise tracker (plan M4/M5).

A season's fit starts from the previous season's *final* fit (shrunk by ``carry``) and then
learns from that season's games before the prediction day, decayed by ``tau``. Nothing
here looks at a game on or after the day being predicted: callers pass ``day`` and the
mask is built from ``ds.day < day``.

The simulator's noise parameters (per-100 efficiency spread, the correlation between the
two teams' efficiency noise, pace spread, the overtime ratio) are measured on *predictions
made before each game*, never on in-sample residuals, and accumulate over all earlier
history (an expanding window, so there is no half-life to invent).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from outlier_nba.pace import PaceFit, PacePrior, fit_pace, tie_density
from outlier_nba.ratings.possessions import Dataset
from outlier_nba.ratings.ridge import RatingFit, RatingPrior, fit_ratings

MIN_BIAS_N = 200  # games of residual history before the level correction is applied
MIN_NOISE_N = 300  # games of out-of-sample history before the simulator may be used


@dataclass(frozen=True)
class TeamParams:
    """Starting values; ``tune`` replaces them with NBA walk-forward estimates."""

    lam: float = 60.0
    tau: float = 120.0
    carry: float = 0.6
    lam_mu: float = 5.0
    lam_h: float = 50.0
    lam_po: float = 100.0
    p_lam: float = 40.0
    p_tau: float = 150.0
    p_carry: float = 0.6
    p_lam_mu: float = 5.0
    p_lam_po: float = 100.0
    elo_k: float = 20.0
    elo_hfa: float = 100.0
    elo_carry: float = 0.75
    elo_mov_exp: float = 0.8
    noise_halflife: float = 2000.0
    bias_weight: float = 1.0  # 0 turns the out-of-sample level correction off
    bias_halflife: float = 200.0  # games; tuned on 2016-17 to 2017-18 (the level drifts)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamParams:
        known = {k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class NoiseTracker:
    """Exponentially weighted out-of-sample residual moments (``halflife`` in games)."""

    halflife: float = 1e12  # effectively an expanding window
    n_obs: int = 0
    n: float = 0.0  # effective sample size after decay
    s_eh2: float = 0.0
    s_ea2: float = 0.0
    s_eha: float = 0.0
    s_p2: float = 0.0
    s_m2: float = 0.0
    s_t2: float = 0.0
    n_ot: float = 0.0
    s_density: float = 0.0
    bias_halflife: float = 1e12
    s_bias: float = 0.0
    bias_n: float = 0.0
    bias_obs: int = 0  # raw count; readiness cannot depend on the decayed sample size
    s_pe: float = 0.0

    @property
    def ready(self) -> bool:
        return self.n_obs >= MIN_NOISE_N

    @property
    def sigma_e(self) -> float:
        return math.sqrt((self.s_eh2 + self.s_ea2) / (2 * self.n))

    @property
    def rho(self) -> float:
        denom = math.sqrt(self.s_eh2 * self.s_ea2)
        return self.s_eha / denom if denom > 0 else 0.0

    @property
    def sigma_p(self) -> float:
        return math.sqrt(self.s_p2 / self.n)

    @property
    def sigma_margin(self) -> float:
        return math.sqrt(self.s_m2 / self.n)

    @property
    def sigma_total(self) -> float:
        return math.sqrt(self.s_t2 / self.n)

    @property
    def level_bias(self) -> float:
        """Mean out-of-sample per-100 residual of the *uncorrected* model (pts/100, both teams).

        The ratings are fitted on decayed history, so on a league whose scoring level keeps
        moving they run behind it; this is the average amount by which they have been behind.
        Zero until MIN_BIAS_N games of history exist.
        """
        return self.s_bias / self.bias_n if self.bias_obs >= MIN_BIAS_N else 0.0

    @property
    def beta_pe(self) -> float:
        """Per-100 efficiency change per unit of log-possession surprise (negative in the NBA:
        games that run faster than expected are less efficient per possession)."""
        return self.s_pe / self.s_p2 if self.ready and self.s_p2 > 0 else 0.0

    @property
    def ot_ratio(self) -> float:
        return self.n_ot / self.s_density if self.s_density > 0 else float("nan")

    def update(
        self,
        e_h: float,
        e_a: float,
        pace_resid: float,
        exp_margin: float,
        m_res: float,
        t_res: float,
        ot: bool,
        raw_eff_resid: float | None = None,
    ) -> None:
        """Fold in one finished game. Residuals are actual minus the pre-game prediction.

        ``e_h``/``e_a`` are residuals of the *corrected* forecast (they feed the spread
        estimates); ``raw_eff_resid`` is the mean residual of the uncorrected one (it feeds
        the level correction).
        """
        d = 0.5 ** (1.0 / self.halflife)
        density = None
        if self.n_obs >= 30:  # the tie density needs a spread; use the one measured so far
            density = float(tie_density(np.array([exp_margin]), self.sigma_margin)[0])
        for name in (
            "n", "s_eh2", "s_ea2", "s_eha", "s_p2", "s_m2", "s_t2", "n_ot", "s_density", "s_pe",
        ):  # fmt: skip
            setattr(self, name, getattr(self, name) * d)
        db = 0.5 ** (1.0 / self.bias_halflife)
        self.s_bias *= db
        self.bias_n *= db
        if raw_eff_resid is not None:
            self.s_bias += raw_eff_resid
            self.bias_n += 1.0
            self.bias_obs += 1
        self.s_pe += pace_resid * 0.5 * (e_h + e_a)
        if density is not None:
            self.s_density += density
            self.n_ot += float(ot)  # counted only where a density was accumulated
        self.n_obs += 1
        self.n += 1.0
        self.s_eh2 += e_h * e_h
        self.s_ea2 += e_a * e_a
        self.s_eha += e_h * e_a
        self.s_p2 += pace_resid * pace_resid
        self.s_m2 += m_res * m_res
        self.s_t2 += t_res * t_res

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NoiseTracker:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class AsOf:
    season: str
    day: int
    rating: RatingFit
    pace: PaceFit


@dataclass(frozen=True)
class TeamPrediction:
    e_home: float  # expected points per 100 possessions
    e_away: float
    log_poss: float  # mean of log(48-minute possessions)
    poss: float
    exp_margin: float  # home minus away, regulation-pace scoring
    exp_total: float
    cold: bool = field(default=False)  # fitted on no games of this season yet


class TeamModel:
    def __init__(
        self,
        ds: Dataset,
        params: TeamParams,
        *,
        use_ratings: bool = True,
        use_pace: bool = True,
        extra_season: str | None = None,
    ) -> None:
        self.ds = ds
        self.params = params
        # tuning one component need not pay for fitting the other
        self.use_ratings = use_ratings
        self.use_pace = use_pace
        order: list[str] = []
        for s in ds.season:
            if not order or order[-1] != s:
                if s not in order:
                    order.append(s)
        if extra_season is not None and extra_season not in order:
            order.append(extra_season)  # a new season with no games yet still inherits a prior
        self.seasons = order
        self._season_arr = np.array(ds.season)
        self._final: dict[str, tuple[RatingFit, PaceFit]] = {}

    def _priors(self, season: str) -> tuple[RatingPrior, PacePrior]:
        i = self.seasons.index(season)
        if i == 0:
            return RatingPrior(), PacePrior()
        rating, pace = self.final_fit(self.seasons[i - 1])
        return rating.to_prior(self.params.carry), pace.to_prior(self.params.p_carry)

    def _fit(self, season: str, day: int, mask: np.ndarray) -> AsOf:
        p = self.params
        rp, pp = self._priors(season)
        rating = (
            fit_ratings(
                self.ds,
                mask,
                now_day=day,
                lam=p.lam,
                tau=p.tau,
                lam_mu=p.lam_mu,
                lam_h=p.lam_h,
                lam_po=p.lam_po,
                prior=rp,
            )  # fmt: skip
            if self.use_ratings
            else RatingFit(0.0, 0.0, 0.0, 0.0, rp.off, rp.deff, 0)
        )
        pace = (
            fit_pace(
                self.ds,
                mask,
                now_day=day,
                lam=p.p_lam,
                tau=p.p_tau,
                lam_mu=p.p_lam_mu,
                lam_po=p.p_lam_po,
                prior=pp,
            )  # fmt: skip
            if self.use_pace
            else PaceFit(pp.mu, pp.playoff, pp.team, 0)
        )
        return AsOf(season, day, rating, pace)

    def has_prior(self, season: str) -> bool:
        return self.seasons.index(season) > 0

    def final_fit(self, season: str) -> tuple[RatingFit, PaceFit]:
        if season not in self._final:
            mask = self._season_arr == season
            last = int(self.ds.day[mask].max()) + 1
            asof = self._fit(season, last, mask)
            self._final[season] = (asof.rating, asof.pace)
        return self._final[season]

    def fit_asof(self, season: str, day: int) -> AsOf:
        """Fit using this season's games strictly before ``day`` (an ET date ordinal)."""
        mask = (self._season_arr == season) & (self.ds.day < day)
        return self._fit(season, day, mask)


def predict(
    asof: AsOf,
    home: int,
    away: int,
    *,
    neutral: bool = False,
    playoff: bool = False,
    sigma_p: float = 0.0,
) -> TeamPrediction:
    e_h, e_a = asof.rating.per100(home, away, neutral=neutral, playoff=playoff)
    log_poss = asof.pace.mean_log(home, away, playoff=playoff)
    poss = math.exp(log_poss + 0.5 * sigma_p * sigma_p)  # lognormal mean
    return TeamPrediction(
        e_home=e_h,
        e_away=e_a,
        log_poss=log_poss,
        poss=poss,
        exp_margin=(e_h - e_a) * poss / 100.0,
        exp_total=(e_h + e_a) * poss / 100.0,
        cold=asof.rating.n_games == 0,
    )
