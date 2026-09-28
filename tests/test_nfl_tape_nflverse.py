"""nflverse -> matchup tape builder (offline; network fetch is monkeypatched)."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl import tape_nflverse as tape
from outlier_nfl.matchup import _unit_score, load_prior_week_tape


def _team_row(game_id: str, week: int, team: str, opp: str, rush: int, pas: int, sacks: int,
              season_type: str = "REG") -> dict[str, str]:
    return {
        "season": "2026", "week": str(week), "team": team, "season_type": season_type,
        "game_id": game_id, "opponent_team": opp, "rushing_yards": str(rush),
        "passing_yards": str(pas), "def_sacks": str(sacks),
    }


def _game(game_id: str, week: int, day: str, away: str, home: str,
          away_score: str, home_score: str, game_type: str = "REG") -> dict[str, str]:
    return {
        "game_id": game_id, "season": "2026", "game_type": game_type, "week": str(week),
        "gameday": day, "away_team": away, "home_team": home,
        "away_score": away_score, "home_score": home_score,
    }


GAMES = [
    _game("g1", 1, "2026-09-13", "SF", "LA", "27", "7"),
    _game("g2", 2, "2026-09-21", "NYG", "LA", "6", "28"),
    _game("g3", 3, "2026-09-27", "LA", "DEN", "", ""),  # not played yet
    _game("p1", 0, "2026-08-20", "LA", "LAC", "10", "3", game_type="PRE"),
]
TEAM_ROWS = [
    _team_row("g1", 1, "LA", "SF", 122, 168, 0),
    _team_row("g1", 1, "SF", "LA", 174, 205, 3),
    _team_row("g2", 2, "LA", "NYG", 163, 327, 2),
    _team_row("g2", 2, "NYG", "LA", 52, 131, 1),
    _team_row("p1", 0, "LA", "LAC", 90, 150, 1, season_type="PRE"),
]


def test_per_game_joins_opponent_and_score_and_normalizes_team() -> None:
    per = tape.build_per_game(TEAM_ROWS, GAMES, 2026)
    assert set(per) == {"LAR", "SF", "NYG"}  # nflverse "LA" -> canonical "LAR"
    w1 = per["LAR"][0]
    assert w1 == {
        "week": 1, "opponent": "SF", "rush_yards": 122.0, "pass_yards": 168.0,
        "points": 7.0, "opp_rush_yards_allowed": 174.0, "opp_pass_yards_allowed": 205.0,
        "opp_points_allowed": 27.0, "sacks": 0.0,
    }
    assert [g["week"] for g in per["LAR"]] == [1, 2]  # unplayed and preseason dropped


def test_before_cutoff_excludes_games_on_or_after_slate_date() -> None:
    per = tape.build_per_game(TEAM_ROWS, GAMES, 2026, before=date(2026, 9, 21))
    assert [g["week"] for g in per["LAR"]] == [1]
    assert "NYG" not in per


def test_aggregate_averages_last_n_and_roles() -> None:
    per = tape.build_per_game(TEAM_ROWS, GAMES, 2026)
    roles = {"LAR": {"qb": "Matthew Stafford", "wr_deep": "Davante Adams", "notes": "x"}}
    teams = tape.aggregate_tape(per, roles=roles)
    lar = teams["LAR"]
    assert lar["rush_yards"] == 142.5 and lar["pass_yards"] == 247.5
    assert lar["points"] == 17.5 and lar["sacks"] == 1.0
    assert lar["games"] == 2 and lar["opponents"] == ["SF", "NYG"]
    assert lar["qb"] == "Matthew Stafford" and "notes" not in lar

    last = tape.aggregate_tape(per, last_n=1)["LAR"]
    assert last["rush_yards"] == 163.0 and last["weeks"] == [2]


def test_write_tape_keeps_backup_and_engine_can_load(tmp_path: Path) -> None:
    path = tmp_path / "tape" / "prior_week.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"teams": {"LAR": {"qb": "Old QB", "rush_yards": 1}}}))

    payload = tape.build_tape_payload(
        2026, roles=tape.load_existing_roles(path), team_rows=TEAM_ROWS, game_rows=GAMES
    )
    backup = tape.write_tape(path, payload)

    assert backup is not None and json.loads(backup.read_text())["teams"]["LAR"]["rush_yards"] == 1
    assert payload["week"] == "1-2"
    loaded = load_prior_week_tape(tmp_path)
    assert loaded["LAR"]["qb"] == "Old QB"  # role survives the refresh
    unit = _unit_score(loaded["LAR"])
    assert unit["rush_offense"] is not None and unit["pass_rush"] is not None


def test_refresh_uses_fetch_and_rejects_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, timeout: float = 60.0) -> list[dict[str, str]]:
        calls.append(url)
        return TEAM_ROWS if "stats_team_week_2026" in url else GAMES

    monkeypatch.setattr(tape, "fetch_csv", fake_fetch)
    out = tape.refresh_prior_week_tape(tmp_path, 2026, before=date(2026, 9, 27))
    assert out == tmp_path / "tape" / "prior_week.json"
    assert json.loads(out.read_text())["teams"]["LAR"]["games"] == 2
    assert len(calls) == 2

    with pytest.raises(ValueError):
        tape.refresh_prior_week_tape(tmp_path, 2026, before=date(2026, 9, 1))


def test_pipeline_refresh_failure_keeps_existing_tape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tape" / "prior_week.json"
    path.parent.mkdir()
    path.write_text('{"teams": {"LAR": {"rush_yards": 99}}}')

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("network down")

    monkeypatch.setattr(nfl_pipeline, "refresh_prior_week_tape", boom)
    nfl_pipeline._refresh_tape(tmp_path, "2026-09-27", None)  # must not raise
    assert json.loads(path.read_text())["teams"]["LAR"]["rush_yards"] == 99


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.com/x.csv", "ftp://x/y.csv"])
def test_fetch_csv_rejects_non_https(url: str) -> None:
    with pytest.raises(ValueError):
        tape.fetch_csv(url)
