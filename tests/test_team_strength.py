from __future__ import annotations

import json

import pytest

from outlier_scrapers import team_strength


def _game(home: str, away: str, home_score: float, away_score: float, event_date: str = "2026-07-01") -> dict:
    return {
        "home": home, "away": away,
        "home_score": home_score, "away_score": away_score,
        "event_date": event_date,
    }


class _FinalEventLike:
    """Duck-typed stand-in for form_source.FinalEvent (attributes, not dict)."""

    def __init__(self, home: str, away: str, home_score: float, away_score: float, event_date: str):
        self.home = home
        self.away = away
        self.home_score = home_score
        self.away_score = away_score
        self.event_date = event_date


def _round_robin_games(teams: list[str], games_per_pair: int, *, base_home: float = 4.0) -> list[dict]:
    """Deterministic, roughly-balanced schedule so the fit doesn't degenerate."""
    games: list[dict] = []
    n = len(teams)
    for _ in range(games_per_pair):
        for i in range(n):
            home = teams[i]
            away = teams[(i + 1) % n]
            games.append(_game(home, away, base_home, base_home - 1.0))
    return games


def test_fit_reports_insufficient_history_below_min_games():
    games = _round_robin_games(["AAA", "BBB", "CCC"], games_per_pair=2)
    artifact = team_strength.fit_team_strength(games, min_games=100)
    assert artifact["status"] == "insufficient_history"
    assert artifact["teams"] == {}
    assert artifact["n_games"] == len(games)


def test_fit_becomes_active_once_min_games_met():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    assert artifact["status"] == "active"
    assert artifact["n_games"] == len(games)
    assert set(artifact["teams"]) == {"AAA", "BBB", "CCC", "DDD"}
    assert artifact["league_avg_runs"] is not None
    assert artifact["home_field_advantage"] is not None
    assert artifact["model_version"].startswith("team-strength-ridge-v1-")


def test_fit_recovers_known_scoring_skew_against_a_common_opponent():
    # Each team's own scoring rate is independent of its opponent here (no
    # defense signal by design), so the fitted offense/defense split should
    # still recover the ordering AAA > BBB > CCC > DDD when predicting each
    # team's mean runs against the same held-fixed opponent.
    skill = {"AAA": 8.0, "BBB": 6.0, "CCC": 4.0, "DDD": 2.0}
    teams = list(skill)
    games = []
    for _ in range(15):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                games.append(_game(home, away, skill[home], skill[away]))
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    assert artifact["status"] == "active"
    means = [
        team_strength.predict_team_run_mean(artifact, team, "DDD", is_home=True)
        for team in ("AAA", "BBB", "CCC")
    ]
    assert means[0] > means[1] > means[2]


def test_fit_accepts_attribute_style_games_not_just_dicts():
    games = [
        _FinalEventLike("AAA", "BBB", 5.0, 4.0, "2026-07-01"),
        _FinalEventLike("BBB", "AAA", 4.0, 5.0, "2026-07-02"),
        _FinalEventLike("AAA", "BBB", 5.0, 4.0, "2026-07-03"),
    ]
    artifact = team_strength.fit_team_strength(games, min_games=3, ridge_lambda=5.0)
    assert artifact["status"] == "active"
    assert artifact["trained_through"] == "2026-07-03"


def test_fit_skips_incomplete_games():
    games = [
        _game("AAA", "BBB", 5.0, 4.0),
        {"home": "AAA", "away": "BBB", "home_score": None, "away_score": 4.0, "event_date": "x"},
        {"home": "", "away": "BBB", "home_score": 5.0, "away_score": 4.0, "event_date": "x"},
    ]
    artifact = team_strength.fit_team_strength(games, min_games=1, ridge_lambda=5.0)
    assert artifact["n_games"] == 1


def test_fit_rejects_invalid_parameters():
    with pytest.raises(ValueError, match="ridge_lambda"):
        team_strength.fit_team_strength([], ridge_lambda=-1.0)
    with pytest.raises(ValueError, match="min_games"):
        team_strength.fit_team_strength([], min_games=0)


def test_predict_team_run_mean_and_game_total():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    home_mean = team_strength.predict_team_run_mean(artifact, "AAA", "BBB", is_home=True)
    away_mean = team_strength.predict_team_run_mean(artifact, "BBB", "AAA", is_home=False)
    assert home_mean is not None and home_mean >= 0
    assert away_mean is not None and away_mean >= 0
    total = team_strength.predict_game_total_mean(artifact, "AAA", "BBB")
    assert total == pytest.approx(home_mean + away_mean)


def test_predict_returns_none_for_unrated_team_or_inactive_artifact():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    assert team_strength.predict_team_run_mean(artifact, "ZZZ", "AAA", is_home=True) is None
    assert team_strength.predict_game_total_mean(artifact, "ZZZ", "AAA") is None
    inactive = team_strength.fit_team_strength(games, min_games=10_000)
    assert team_strength.predict_team_run_mean(inactive, "AAA", "BBB", is_home=True) is None
    assert team_strength.predict_team_run_mean(None, "AAA", "BBB", is_home=True) is None


def test_artifact_round_trip(tmp_path):
    games = _round_robin_games(["AAA", "BBB", "CCC"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    output = team_strength.write_artifact(artifact, tmp_path / "team_strength.json")
    loaded = team_strength.load_artifact(output)
    assert loaded == json.loads(output.read_text())
    assert loaded["model_version"] == artifact["model_version"]


def test_load_artifact_missing_or_malformed(tmp_path):
    assert team_strength.load_artifact(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert team_strength.load_artifact(bad) is None
    wrong_schema = tmp_path / "wrong.json"
    wrong_schema.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    assert team_strength.load_artifact(wrong_schema) is None


def test_promotion_policy_defaults_to_shadow_when_missing(tmp_path):
    policy = team_strength.load_promotion_policy(tmp_path / "missing.json")
    assert policy["mode"] == "shadow"
    assert policy["min_eligible_games"] == team_strength.DEFAULT_PROMOTION_MIN_GAMES


def test_promotion_policy_rejects_malformed_mode(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"mode": "definitely-live", "min_eligible_games": 10}), encoding="utf-8")
    policy = team_strength.load_promotion_policy(path)
    assert policy["mode"] == "shadow"
    assert policy["min_eligible_games"] == 10


def test_promotion_status_blocks_by_default_even_when_active():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=200)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    assert artifact["status"] == "active"
    status = team_strength.promotion_status(artifact, {"mode": "shadow", "min_eligible_games": 10})
    assert status["drives_sizing"] is False
    assert "policy_shadow" in status["reasons"]


def test_promotion_status_requires_min_games_even_in_live_mode():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    status = team_strength.promotion_status(
        artifact, {"mode": "live", "min_eligible_games": 10_000}
    )
    assert status["drives_sizing"] is False
    assert any(reason.startswith("insufficient_games:") for reason in status["reasons"])


def test_promotion_status_passes_when_live_and_sample_and_version_match():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    status = team_strength.promotion_status(
        artifact,
        {"mode": "live", "min_eligible_games": 1, "model_version": artifact["model_version"]},
    )
    assert status["drives_sizing"] is True
    assert status["reasons"] == []


def test_promotion_status_blocks_on_model_version_mismatch():
    games = _round_robin_games(["AAA", "BBB", "CCC", "DDD"], games_per_pair=10)
    artifact = team_strength.fit_team_strength(games, min_games=5, ridge_lambda=5.0)
    status = team_strength.promotion_status(
        artifact, {"mode": "live", "min_eligible_games": 1, "model_version": "stale-version"}
    )
    assert status["drives_sizing"] is False
    assert "model_version_mismatch" in status["reasons"]
