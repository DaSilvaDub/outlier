"""Synthetic NBA seasons with known ratings, for recovering/leakage tests (no network)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from outlier_nba.ingest.espn import GameRow
from outlier_nba.ratings.possessions import TEAMS


@dataclass
class League:
    off: np.ndarray
    deff: np.ndarray
    pace: np.ndarray
    mu: float = 112.0
    h: float = 1.5
    noise: float = 11.0
    pace_mu: float = 98.0
    pace_sd: float = 0.04


def make_league(seed: int = 0, spread: float = 3.0) -> League:
    rng = np.random.default_rng(seed)
    n = len(TEAMS)
    return League(
        off=rng.normal(0, spread, n),
        deff=rng.normal(0, spread, n),
        pace=rng.normal(0, 0.02, n),
    )


def make_season(
    league: League,
    label: str,
    start: datetime,
    *,
    games_per_team: int = 82,
    seed: int = 1,
    home_adv: float | None = None,
    neutral_from: datetime | None = None,
) -> list[GameRow]:
    rng = np.random.default_rng(seed)
    n = len(TEAMS)
    h = league.h if home_adv is None else home_adv
    total = n * games_per_team // 2
    days = games_per_team * 2
    rows: list[GameRow] = []
    for g in range(total):
        i, j = rng.choice(n, 2, replace=False)
        day = start + timedelta(days=int(g * days / total))
        tip = day.replace(hour=0, minute=0) + timedelta(hours=int(rng.integers(23, 25)))
        neutral = neutral_from is not None and tip >= neutral_from
        s = 0.0 if neutral else 1.0
        poss = float(
            np.exp(
                np.log(league.pace_mu)
                + league.pace[i]
                + league.pace[j]
                + rng.normal(0, league.pace_sd)
            )
        )
        e_h = league.mu + h * s + league.off[i] - league.deff[j]
        e_a = league.mu - h * s + league.off[j] - league.deff[i]
        hp = int(round(poss * (e_h + rng.normal(0, league.noise)) / 100))
        ap = int(round(poss * (e_a + rng.normal(0, league.noise)) / 100))
        if hp == ap:
            hp += 1
        p = int(round(poss))
        fga = int(round(p * 0.85))
        rows.append(
            GameRow(
                game_id=f"{label}-{g:05d}",
                season=label,
                season_type=2,
                start_utc=tip.astimezone(UTC),
                home=TEAMS[i],
                away=TEAMS[j],
                home_pts=hp,
                away_pts=ap,
                periods=4,
                ot_periods=0,
                neutral=neutral,
                attendance=15000,
                note="",
                home_fga=fga,
                home_fta=0,
                home_reb=40,
                home_oreb=0,
                home_tov=p - fga,
                away_fga=fga,
                away_fta=0,
                away_reb=40,
                away_oreb=0,
                away_tov=p - fga,
            )
        )
    return rows


def synth_history(seed: int = 41) -> list[GameRow]:
    """Two compact seasons plus a short one that ends the day before the 2026-10-21 slate."""
    league = make_league(seed=seed, spread=3.5)
    out: list[GameRow] = []
    for label, start, gpt, s in [
        ("2024-25", datetime(2024, 10, 22, tzinfo=UTC), 30, 1),
        ("2025-26", datetime(2025, 10, 21, tzinfo=UTC), 30, 2),
        ("2026-27", datetime(2026, 10, 7, tzinfo=UTC), 7, 3),
    ]:
        out += make_season(league, label, start, games_per_team=gpt, seed=s)
    return out
