"""F17: entity joins use provider IDs, event membership and player ID (#226)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl import boxscore_nflverse, projection
from outlier_nfl.boxscore import NflBoxScoreEvent, parse_simplified_events, resolve_player_stats
from outlier_nfl.normalizer import build_schedule_index
from outlier_nfl.props import extract_player_props

SCHED = {"events": [{"eventId": "ev-buf-kc", "id": "ev-buf-kc",
                     "scheduledTime": "2026-09-13T17:00:00+00:00",
                     "home": {"teamId": 12, "alias": "KC", "name": "Kansas City Chiefs"},
                     "away": {"teamId": 2, "alias": "BUF", "name": "Buffalo Bills"}}]}


def _prop(**over: Any) -> dict[str, Any]:
    o: dict[str, Any] = {"eventId": "ev-buf-kc", "marketId": "m1", "outcomeId": "o1",
                         "proposition": "RECEIVING_YARDS", "position": "OVER", "line": 60.5,
                         "playerName": "Test Player", "playerId": "p1", "bestOdds": -110}
    o.update(over)
    return {"outcome": {k: v for k, v in o.items() if v is not None}}


def _one(**over: Any) -> list[tuple[str | None, str | None]]:
    rows = extract_player_props({"props": [_prop(**over)]}, build_schedule_index(SCHED))
    return [(r.team, r.opponent) for r in rows]


def test_foreign_team_prop_is_dropped_not_given_an_opponent() -> None:
    assert _one(teamId="MIA") == []


@pytest.mark.parametrize("team_ref", ["12", 12, {"teamId": 12}, {"teamId": "12"}, "KC"])
def test_team_ids_resolve_as_strings_and_id_only_objects(team_ref: Any) -> None:
    over: dict[str, Any] = {"teamId": None, "team": team_ref} if isinstance(team_ref, dict) \
        else {"teamId": team_ref}
    assert _one(**over) == [("KC", "BUF")]


def test_outcome_id_is_not_an_event_id() -> None:
    assert _one(eventId=None, id="ev-buf-kc") == []


def test_unresolved_team_gets_team_from_a_member_opponent() -> None:
    assert _one(teamId="nope", oppTeamId=2) == [("KC", "BUF")]
    assert _one(teamId=None, oppTeamId="MIA") == [(None, None)]


def _csv(path: Path, rows: list[dict[str, str]]) -> Path:
    cols = list(dict.fromkeys(c for r in rows for c in r))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


GAME = {"game_id": "2026_01_BUF_KC", "season": "2026", "week": "1", "gameday": "2026-09-13",
        "away_team": "BUF", "home_team": "KC", "away_score": "20", "home_score": "27"}


def _stat(name: str, pid: str, team: str, yds: str, week: str = "1") -> dict[str, str]:
    return {"game_id": GAME["game_id"] if week == "1" else f"2026_0{week}_X", "season": "2026",
            "week": week, "season_type": "REG", "player_id": pid, "player_display_name": name,
            "team": team, "receiving_yards": yds, "receptions": "4"}


def test_same_name_players_stay_separate_in_the_boxscore(tmp_path: Path) -> None:
    ev = boxscore_nflverse.load_nflverse_events(
        season=2026, week=1, cache_dir=tmp_path, games_csv=_csv(tmp_path / "g.csv", [GAME]),
        stats_csv=_csv(tmp_path / "s.csv", [_stat("Josh Allen", "00-A1", "BUF", "80"),
                                            _stat("Josh Allen", "00-A2", "KC", "30")]))[0]
    assert sorted(ev.players) == ["JOSHALLEN#BUF#00A1", "JOSHALLEN#KC#00A2"]
    assert resolve_player_stats(ev, "Josh Allen") == (None, "ambiguous_player_match")
    buf, reason = resolve_player_stats(ev, "Josh Allen", "BUF")
    assert reason is None and buf is not None and buf["RECEIVING:YDS"] == 80.0
    kc, _ = resolve_player_stats(ev, "Josh Allen", "KC")
    assert kc is not None and kc["RECEIVING:YDS"] == 30.0
    assert resolve_player_stats(ev, "Josh Allen", "MIA") == (None, "ambiguous_player_match")
    # The keys survive the simplified-payload round trip settle uses.
    again = parse_simplified_events(boxscore_nflverse.events_to_simplified_payload([ev]))[0]
    assert sorted(again.players) == sorted(ev.players)


def test_unique_name_still_resolves_by_name_only() -> None:
    ev = NflBoxScoreEvent(provider_event_id="g", event_date=date(2026, 9, 13), away="BUF",
                          home="KC", away_score=20, home_score=27,
                          players={"TRAVISKELCE": {"RECEIVING:YDS": 70.0}})
    assert resolve_player_stats(ev, "Travis Kelce", "KC")[0] == {"RECEIVING:YDS": 70.0}


def test_projection_rows_belong_to_one_player_id(tmp_path: Path) -> None:
    path = _csv(tmp_path / "w.csv", [
        _stat("Josh Allen", "00-A1", "BUF", "80"), _stat("Josh Allen", "00-A2", "KC", "30"),
        _stat("Josh Allen", "00-A1", "BUF", "70", week="2"),
        _stat("Traded Guy", "00-T1", "NYJ", "40"), _stat("Traded Guy", "00-T1", "KC", "55", "2")])
    idx = projection.load_week_stats_index(path, before_week=3)
    assert len(idx["JOSHALLEN"]) == 3
    assert [r["receiving_yards"] for r in projection.player_rows(idx, "Josh Allen", "BUF")] \
        == ["80", "70"]
    assert [r["receiving_yards"] for r in projection.player_rows(idx, "Josh Allen", "KC")] == ["30"]
    assert projection.player_rows(idx, "Josh Allen", None) == []
    assert len(projection.player_rows(idx, "Traded Guy", "KC")) == 2  # one ID: both teams
    rec: dict[str, Any] = {"player_name": "Josh Allen", "team": "MIA", "market": "REC_YDS",
                           "line": 50.5, "position": "OVER"}
    assert projection.attach_projection_model_p_record(rec, week_index=idx, overwrite=True) \
        .get("model_p") is None
