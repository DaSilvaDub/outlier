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


def _depth(dt: str, team: str, pos: str, slot: str, rank: int, name: str, gsis: str = "") -> dict[str, str]:
    return {"dt": dt, "team": team, "pos_abb": pos, "pos_slot": slot, "pos_rank": str(rank),
            "player_name": name, "gsis_id": gsis}


DT_OLD, DT_NEW, DT_AFTER = "2026-09-20T12:00:00Z", "2026-09-27T12:56:55Z", "2026-09-28T06:00:00Z"
DEPTH = [
    _depth(DT_OLD, "LA", "TE", "10", 1, "Tyler Higbee"),
    _depth(DT_NEW, "LA", "QB", "9", 1, "Matthew Stafford"),
    _depth(DT_NEW, "LA", "RB", "11", 1, "Kyren Williams"),
    _depth(DT_NEW, "LA", "TE", "10", 1, "Colby Parkinson"),
    _depth(DT_NEW, "LA", "TE", "10", 4, "Tyler Higbee"),
    _depth(DT_NEW, "LA", "WR", "1", 1, "Puka Nacua", "00-NACUA"),
    _depth(DT_NEW, "LA", "WR", "2", 2, "Davante Adams"),
    _depth(DT_NEW, "LA", "WR", "8", 3, "Jordan Whittington"),
    _depth(DT_AFTER, "LA", "QB", "9", 1, "Future Snapshot QB"),
]
INJURIES = [
    {"season_type": "REG", "week": "3", "team": "LA", "full_name": "Puka Nacua",
     "gsis_id": "00-NACUA", "report_status": "Doubtful", "position": "WR"},
    {"season_type": "REG", "week": "3", "team": "LA", "full_name": "Kyren Williams",
     "gsis_id": "", "report_status": "Questionable", "position": "RB"},
    {"season_type": "REG", "week": "2", "team": "LA", "full_name": "Blake Corum",
     "gsis_id": "", "report_status": "Out", "position": "RB"},
]


def test_inactives_use_slate_week_and_out_doubtful_only() -> None:
    assert tape.slate_week(GAMES, 2026, date(2026, 9, 27)) == 3
    out = tape.inactive_players(INJURIES, 3)
    assert [p["name"] for p in out["LAR"]] == ["Puka Nacua"]  # Questionable plays; Wk 2 ignored


def test_depth_roles_skip_inactive_and_ignore_future_snapshots() -> None:
    roles = tape.depth_chart_roles(DEPTH, tape.inactive_players(INJURIES, 3), date(2026, 9, 27))
    assert roles["LAR"] == {
        "qb": "Matthew Stafford", "rb1": "Kyren Williams", "te": "Colby Parkinson",
        "wr_deep": "Davante Adams", "wr_slot": "Jordan Whittington",
    }
    healthy = tape.depth_chart_roles(DEPTH, {}, date(2026, 9, 27))
    assert healthy["LAR"]["wr_deep"] == "Puka Nacua"
    # name fallback when the injury row has no gsis id
    by_name = tape.depth_chart_roles(
        DEPTH, {"LAR": [{"name": "Puka Nacua", "gsis_id": ""}]}, date(2026, 9, 27)
    )
    assert by_name["LAR"]["wr_deep"] == "Davante Adams"


def test_auto_roles_override_stale_tape_roles_and_fill_gaps() -> None:
    stale = {"LAR": {"te": "Tyler Higbee", "wr_deep": "Puka Nacua"}, "SF": {"qb": "Brock Purdy"}}
    roles = tape.depth_chart_roles(DEPTH, tape.inactive_players(INJURIES, 3), date(2026, 9, 27))
    payload = tape.build_tape_payload(
        2026, roles=stale, team_rows=TEAM_ROWS, game_rows=GAMES,
        depth_roles=roles, inactive=tape.inactive_players(INJURIES, 3),
    )
    assert payload["teams"]["LAR"]["te"] == "Colby Parkinson"
    assert payload["teams"]["LAR"]["wr_deep"] == "Davante Adams"
    assert payload["teams"]["SF"]["qb"] == "Brock Purdy"  # no depth data -> tape role kept


def test_auto_roles_fetch_failure_keeps_existing_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, timeout: float = 60.0) -> list[dict[str, str]]:
        raise OSError("blocked")

    monkeypatch.setattr(tape, "fetch_csv", boom)
    payload = tape.build_tape_payload(
        2026, roles={"LAR": {"qb": "Manual QB"}}, team_rows=TEAM_ROWS, game_rows=GAMES
    )
    assert payload["teams"]["LAR"]["qb"] == "Manual QB"
    assert payload["inactive"] == {} and payload["roles_source"] == "existing tape"


def test_pipeline_maps_inactives_to_both_teams_of_each_event(tmp_path: Path) -> None:
    (tmp_path / "tape").mkdir()
    (tmp_path / "tape" / "prior_week.json").write_text(
        json.dumps({"inactive": {"LA": ["Puka Nacua"], "DEN": []}, "teams": {}})
    )
    inactive = tape.load_tape_inactives(tmp_path)
    assert inactive == {"LAR": ["Puka Nacua"], "DEN": []}
    events = [{"eventId": "e1", "home": {"alias": "DEN"}, "away": {"alias": "LAR"}}]
    mapped = nfl_pipeline.injuries_by_event(inactive, [], events)
    assert mapped == {"e1": ["Puka Nacua"]}
    assert nfl_pipeline.injuries_by_event({}, [], events) == {}


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
        2026, roles=tape.load_existing_roles(path), team_rows=TEAM_ROWS, game_rows=GAMES,
        auto_roles=False,
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
        if "stats_team_week_2026" in url:
            return TEAM_ROWS
        if "injuries_2026" in url:
            return INJURIES
        if "depth_charts_2026" in url:
            return DEPTH
        return GAMES

    monkeypatch.setattr(tape, "fetch_csv", fake_fetch)
    out = tape.refresh_prior_week_tape(tmp_path, 2026, before=date(2026, 9, 27))
    assert out == tmp_path / "tape" / "prior_week.json"
    written = json.loads(out.read_text())
    assert written["teams"]["LAR"]["games"] == 2
    assert written["teams"]["LAR"]["wr_deep"] == "Davante Adams"  # Nacua Doubtful
    assert written["inactive"] == {"LAR": ["Puka Nacua"]}
    assert written["roles_source"].startswith("nflverse")
    assert len(calls) == 4

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
