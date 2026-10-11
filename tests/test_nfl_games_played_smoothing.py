"""Laplace n from real games played, not a fixed window size (#229, deferred b).

Outlier ships hit-rate ratios only (stats: l5/l10/l20/h2h/curSeason/prevSeason,
no games count), so the count comes from the nflverse week index.
"""

from __future__ import annotations

import pytest

from outlier_nfl.calibration import (
    attach_empirical_model_p_record,
    compute_shrunk_empirical_model_p,
)
from outlier_nfl.projection import _token, attach_model_p_hierarchy_record, games_played


def _row(week, team="KC", pid="00-1", season="2026"):
    return {"player_id": pid, "player_display_name": "Joe Wr", "season": season,
            "week": str(week), "team": team}


def test_two_games_shrink_as_two_trials():
    p, _ = compute_shrunk_empirical_model_p(l10_hit_rate=1.0, games_played=2)
    assert p == pytest.approx((2 + 2) / (2 + 4))


def test_season_rate_uses_real_games_not_17():
    p, _ = compute_shrunk_empirical_model_p(season_hit_rate=1.0, games_played=3)
    assert p == pytest.approx(5 / 7)


@pytest.mark.parametrize("games", [None, 0, 12])
def test_window_size_when_count_unknown_or_larger(games):
    p, _ = compute_shrunk_empirical_model_p(l10_hit_rate=1.0, games_played=games)
    assert p == pytest.approx(12 / 14)


def test_games_played_counts_distinct_weeks_and_unknown_is_none():
    idx = {_token("Joe Wr"): [_row(1), _row(3), _row(3)]}  # bye/inactive week 2 is not a game
    assert games_played(idx, "Joe Wr", "KC") == 2
    assert games_played(idx, "Someone Else", "KC") is None
    assert games_played(None, "Joe Wr") is None


def test_record_stamps_the_n_used():
    rec = {"player_name": "Joe Wr", "l10_hit_rate": 1.0}
    attach_empirical_model_p_record(rec, method="laplace", games_played=3)
    assert (rec["model_p_n_games"], rec["model_p_n_source"]) == (3, "nflverse_games_played")
    assert rec["model_p"] == pytest.approx(5 / 7, abs=1e-6)


def test_hierarchy_laplace_fallback_uses_index_games():
    # Two prior weeks for the player: too few for a projection, so the hierarchy
    # falls back to Laplace, which should shrink as 2 trials.
    idx = {_token("Joe Wr"): [_row(1), _row(2)]}
    rec = {"player_name": "Joe Wr", "team": "KC", "market": "UNSUPPORTED_MKT",
           "position": "OVER", "line": 0.5, "l10_hit_rate": 1.0}
    attach_model_p_hierarchy_record(rec, week_index=idx)
    assert rec["model_p_source"] == "empirical_hit_rate_laplace"
    assert rec["model_p"] == pytest.approx(4 / 6, abs=1e-6)
    assert rec["model_p_n_games"] == 2
