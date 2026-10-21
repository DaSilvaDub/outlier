"""Possessions, dataset and ridge per-100 ratings (offline, synthetic leagues)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from outlier_nba.ingest.espn import GameRow
from outlier_nba.ratings import possessions as P
from outlier_nba.ratings import ridge as R
from tests.nba_synth import make_league, make_season

START = datetime(2018, 10, 20, tzinfo=UTC)
FREE = {"lam": 1.0, "tau": 1e9, "lam_mu": 1e-3, "lam_h": 1e-3, "lam_po": 1.0}


def test_possession_formula() -> None:
    assert P.team_possessions(90, 20, 10, 12) == pytest.approx(90 + 8.8 - 10 + 12)


def _game(**kw) -> GameRow:
    base = {
        "game_id": "g", "season": "2018-19", "season_type": 2, "start_utc": START,
        "home": "BOS", "away": "NYK", "home_pts": 100, "away_pts": 95, "periods": 4,
        "ot_periods": 0, "neutral": False, "attendance": 1, "note": "",
        "home_fga": 90, "home_fta": 20, "home_reb": 45, "home_oreb": 10, "home_tov": 12,
        "away_fga": 88, "away_fta": 22, "away_reb": 40, "away_oreb": 9, "away_tov": 14,
    }  # fmt: skip
    return GameRow(**{**base, **kw})


def test_dataset_averages_both_teams_and_flags_exact() -> None:
    ds = P.build_dataset([_game()])
    ph = 90 + 8.8 - 10 + 12
    pa = 88 + 0.44 * 22 - 9 + 14
    assert ds.poss[0] == pytest.approx((ph + pa) / 2) and ds.exact[0]
    assert ds.home[0] == P.TEAM_INDEX["BOS"] and ds.away[0] == P.TEAM_INDEX["NYK"]


def test_fallback_possessions_use_only_earlier_games() -> None:
    no_box = {"home_oreb": None, "home_tov": None, "away_oreb": None, "away_tov": None}
    a = _game(game_id="a", start_utc=START)
    b = _game(game_id="b", start_utc=START + timedelta(days=1), **no_box)
    c = _game(game_id="c", start_utc=START - timedelta(days=1), **no_box)
    ds = P.build_dataset([a, b, c])
    by_id = {g.game_id: i for i, g in enumerate(ds.games)}
    assert not ds.exact[by_id["b"]] and np.isfinite(ds.poss[by_id["b"]])
    assert np.isnan(ds.poss[by_id["c"]])  # nothing earlier to learn the adjustment from


def test_overtime_possessions_normalise_to_48() -> None:
    ds = P.build_dataset([_game(ot_periods=1, periods=5)])
    assert ds.poss48[0] == pytest.approx(ds.poss[0] * 48 / 53)


def test_stress_and_neutral_flags() -> None:
    # ESPN leaves neutralSite False on bubble games; the dataset derives it from the date
    bubble = _game(
        game_id="b", season="2019-20", start_utc=datetime(2020, 8, 5, 20, tzinfo=UTC), neutral=False
    )
    normal = _game(game_id="n", season="2019-20", start_utc=datetime(2020, 1, 5, 20, tzinfo=UTC))
    crowdless = _game(game_id="c", season="2020-21", start_utc=datetime(2021, 1, 5, 20, tzinfo=UTC))
    ds = P.build_dataset([bubble, normal, crowdless])
    flags = {g.game_id: bool(s) for g, s in zip(ds.games, ds.stress)}
    assert flags == {"n": False, "b": True, "c": True}
    neutral = {g.game_id: bool(v) for g, v in zip(ds.games, ds.neutral)}
    assert neutral == {"n": False, "b": True, "c": False}  # crowdless is not neutral


def _fit(ds, mask=None, now=None, prior=None, **kw):
    params = {**FREE, **kw}
    mask = np.ones(len(ds), dtype=bool) if mask is None else mask
    return R.fit_ratings(
        ds,
        mask,
        now_day=int(ds.day.max()) + 1 if now is None else now,
        prior=prior or R.RatingPrior(),
        **params,
    )


def test_ridge_recovers_known_ratings_and_home_term() -> None:
    league = make_league(seed=3)
    ds = P.build_dataset(make_season(league, "2018-19", START, seed=4))
    fit = _fit(ds)
    # identifiable up to the mu/O/D constant split: compare the net strengths and the league mean
    true_net = league.off + league.deff
    got_net = fit.off + fit.deff
    corr = np.corrcoef(true_net - true_net.mean(), got_net - got_net.mean())[0, 1]
    assert corr > 0.9  # 82 noisy games per team caps the attainable correlation near 0.94
    assert fit.h == pytest.approx(league.h, abs=0.6)
    assert fit.mu + fit.off.mean() - fit.deff.mean() == pytest.approx(
        league.mu + league.off.mean() - league.deff.mean(), abs=0.5
    )


def test_fit_uses_only_masked_games() -> None:
    league = make_league(seed=5)
    ds = P.build_dataset(make_season(league, "2018-19", START, seed=6))
    cut = int(np.median(ds.day))
    early = _fit(ds, ds.mask_before(cut), now=cut)
    # poison the future: a huge scoring change after the cut must not alter the early fit
    poisoned = replace(ds, hp=np.where(ds.day >= cut, ds.hp + 500, ds.hp))
    again = _fit(poisoned, poisoned.mask_before(cut), now=cut)
    assert np.allclose(early.off, again.off) and early.mu == again.mu


def test_prior_pulls_a_data_starved_fit_toward_the_prior() -> None:
    league = make_league(seed=7)
    ds = P.build_dataset(make_season(league, "2018-19", START, seed=8))
    first = ds.mask_before(int(ds.day.min()) + 3)  # a handful of games
    prior = R.RatingPrior(off=np.full(R.NT, 4.0), deff=np.zeros(R.NT))
    anchored = _fit(ds, first, prior=prior, lam=500.0)
    free = _fit(ds, first, prior=R.RatingPrior(), lam=500.0)
    assert anchored.off.mean() > free.off.mean() + 3.0


def test_time_decay_downweights_old_games() -> None:
    league = make_league(seed=9)
    ds = P.build_dataset(make_season(league, "2018-19", START, seed=10))
    half = int(np.median(ds.day))
    shifted = replace(ds, hp=np.where(ds.day < half, ds.hp + 30, ds.hp))
    now = int(ds.day.max()) + 1
    slow = _fit(shifted, now=now, tau=1e9)
    fast = _fit(shifted, now=now, tau=10.0)
    clean = _fit(ds, now=now, tau=10.0)
    assert abs(fast.mu - clean.mu) < abs(slow.mu - clean.mu)


def test_stress_games_do_not_move_the_home_term() -> None:
    league = make_league(seed=11)
    normal = make_season(league, "2018-19", START, seed=12, home_adv=2.0)
    base = _fit(P.build_dataset(normal))
    crowdless = make_season(
        league, "2020-21", datetime(2021, 1, 5, tzinfo=UTC), seed=13, home_adv=-4.0
    )  # home court flipped
    mixed = _fit(P.build_dataset(normal + crowdless))
    assert abs(mixed.h - base.h) < 0.3  # the flipped games were absorbed by h_stress
    assert mixed.h_stress < -2.0


def test_neutral_site_games_contribute_no_home_sign() -> None:
    league = make_league(seed=14)
    games = make_season(league, "2018-19", START, seed=15, neutral_from=START)  # all neutral
    fit = _fit(P.build_dataset(games), lam_h=1e-6)
    assert abs(fit.h) < 0.01  # nothing identifies h: it stays at its prior (0)


def test_to_prior_shrinks_strengths_by_carry() -> None:
    league = make_league(seed=16)
    fit = _fit(P.build_dataset(make_season(league, "2018-19", START, seed=17)))
    prior = fit.to_prior(0.5)
    assert np.allclose(prior.off, 0.5 * fit.off) and prior.mu == fit.mu


def test_empty_mask_returns_the_prior() -> None:
    ds = P.build_dataset(make_season(make_league(1), "2018-19", START, seed=2))
    prior = R.RatingPrior(mu=111.0, h=1.2)
    fit = _fit(ds, np.zeros(len(ds), dtype=bool), prior=prior)
    assert fit.mu == 111.0 and fit.n_games == 0


def test_integer_hyperparameters_do_not_zero_the_stress_penalty() -> None:
    """An int `lam` once built an int penalty vector that truncated 1e-6 to 0 (singular matrix)."""
    ds = P.build_dataset(make_season(make_league(1), "2018-19", START, seed=2))
    few = ds.mask_before(int(ds.day.min()) + 1)
    fit = _fit(ds, few, lam=10, lam_mu=5, lam_h=100, lam_po=100)
    assert np.isfinite(fit.mu) and fit.h_stress == 0.0
