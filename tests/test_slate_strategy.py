import json
from datetime import date
import logging
from types import SimpleNamespace

import pytest
from outlier_scrapers.form_source import FinalEvent
from outlier_scrapers import slate_strategy
from outlier_scrapers.slate_strategy import (
    MatchupContext,
    build_event_strategy,
    export_slate_strategy_for_league,
    window_player_form,
)


def _event(
    event_id: str,
    away: str,
    home: str,
    away_score: float,
    home_score: float,
    players: dict[str, dict[str, float]],
) -> FinalEvent:
    return FinalEvent(
        provider_event_id=event_id,
        sport="WNBA",
        event_date=date(2099, 8, 8),
        away=away,
        home=home,
        away_score=away_score,
        home_score=home_score,
        players=players,
    )

def test_window_player_form():
    recent = [
        _event("e1", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}, "p2": {"PTS": 20}}),
        _event("e2", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
    ]
    # Test safe variance calculation for single game
    form_p2 = window_player_form(recent, "p2")
    assert form_p2["gp"] == 1
    assert form_p2["std_pts"] == 0.0

    # Test identical scores don't trigger zero division in ceiling calculation
    form_p1 = window_player_form(recent, "p1")
    assert form_p1["gp"] == 2
    assert form_p1["mean_pts"] == 10.0

def test_build_event_strategy_defense_clamp():
    # Home team allows very few points
    recent = [
        _event(f"e{i}", "AWAY", "HOME", 80, 100, {}) for i in range(5)
    ]
    ctx = MatchupContext("event", "AWAY", "HOME", recent, set(), set())
    out = build_event_strategy(ctx)
    
    # HOME team is clamping, so AWAY team gets PTS UNDER downgrade
    assert any(t["id"] == "defense_clamp" and t["team"] == "HOME" for t in out.trends)
    d = next(d for d in out.directions if d.side == "UNDER" and d.market_family == "PTS")
    assert d.team == "AWAY"

def test_build_event_strategy_ceiling():
    # p1 has 3 normal games (10 pts) and 1 ceiling game (30 pts)
    recent = [
        _event("e1", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
        _event("e2", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
        _event("e3", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
        _event("e4", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 30}}),
    ]
    ctx = MatchupContext("event", "AWAY", "HOME", recent, {"p1"}, set())
    out = build_event_strategy(ctx)
    
    assert any(t["id"] == "ceiling_game" and t["text"].startswith("p1") for t in out.trends)
    d = next(d for d in out.directions if d.player_key == "p1")
    assert d.side == "OVER"
    assert d.market_family == "PTS"

def test_build_event_strategy_leakage():
    # A player p2 from another game shouldn't be processed if not in context.away_players or home_players
    recent = [
        _event("e1", "OTHER", "HOME", 100, 90, {"p2": {"PTS": 50, "AST": 10}})
    ]
    ctx = MatchupContext("event", "AWAY", "HOME", recent, {"p1"}, set())
    out = build_event_strategy(ctx)
    
    # No directions for p2
    assert not any(d.player_key == "p2" for d in out.directions)


def test_export_logs_exception_with_traceback_on_recent_finals_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    games_file = tmp_path / "games.json"
    games_file.write_text(
        '{"events":[{"event_id":"e1","away_team":"AWAY","home_team":"HOME","injuries":[]}]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        slate_strategy,
        "league_paths",
        lambda _league: SimpleNamespace(
            games_normalized_latest=lambda: games_file,
            ensure=lambda: SimpleNamespace(reports=tmp_path),
        ),
    )

    def _boom(_league: str, _teams: set[str], _limit: int):
        raise RuntimeError("boom")

    monkeypatch.setattr(slate_strategy, "iter_recent_finals", _boom)
    caplog.set_level(logging.ERROR)

    with pytest.raises(RuntimeError, match="boom"):
        slate_strategy.export_slate_strategy_for_league("WNBA")

    assert any(
        record.getMessage() == "Failed to fetch recent finals: boom" and record.exc_info
        for record in caplog.records
    )

def test_out_injury_nested_schema_filters_playmaker():
    recent = [
        _event(f"e{i}", "AWAY", "HOME", 100, 90, {"PLAYMAKER": {"PTS": 8, "AST": 6}})
        for i in range(4)
    ]
    ctx = MatchupContext("event", "AWAY", "HOME", recent, {"PLAYMAKER"}, set())
    injuries = [
        {
            "firstName": "Play",
            "lastName": "Maker",
            "teamId": "t1",
            "injury": {"status": "Out"},
        }
    ]
    out = build_event_strategy(ctx, injuries=injuries)
    assert not any(d.player_key == "PLAYMAKER" for d in out.directions)


def _write_normalized_games(data_dir, league, *, records, events, teams):
    out = data_dir / league / "normalized"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{league.lower()}_games_latest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-13T00:00:00Z",
                "league": league,
                "primary_record_array": "records",
                "records": records,
                "context": {"events": events, "teams": teams, "insights": {}},
            }
        ),
        encoding="utf-8",
    )


def test_export_reads_normalize_games_contract(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers import slate_strategy as strat

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_normalized_games(
        tmp_path / "data",
        "WNBA",
        records=[{"event_id": "e1", "matchup": "LVA @ SEA", "team": "LVA"}],
        events={"e1": {"away_team_id": "a1", "home_team_id": "h1", "lineups": {}}},
        teams={
            "a1": {
                "injuries": [
                    {
                        "firstName": "Play",
                        "lastName": "Maker",
                        "injury": {"status": "Out"},
                    }
                ]
            }
        },
    )
    recent = [
        _event(f"g{i}", "LVA", "SEA", 88, 80, {"PLAYMAKER": {"PTS": 8, "AST": 6}})
        for i in range(4)
    ]
    monkeypatch.setattr(strat, "iter_recent_finals", lambda *a, **k: recent)

    result = export_slate_strategy_for_league("WNBA")

    assert result["status"] == "ok"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["event_id"] == "e1"
    assert event["matchup"] == "LVA @ SEA"
    assert event["team_form"]["away"]["gp"] == 4
    assert not any(d["player_key"] == "PLAYMAKER" for d in event["directions"])
    reports = paths_mod.league_paths("WNBA").reports
    assert (reports / "slate_strategy_latest.json").exists()
    assert "LVA @ SEA" in (reports / "slate_strategy_latest.md").read_text(encoding="utf-8")


def test_export_rejects_legacy_events_array(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    out = tmp_path / "data" / "WNBA" / "normalized"
    out.mkdir(parents=True, exist_ok=True)
    (out / "wnba_games_latest.json").write_text(
        json.dumps({"events": [{"event_id": "e1", "away_team": "LVA", "home_team": "SEA"}]}),
        encoding="utf-8",
    )
    result = export_slate_strategy_for_league("WNBA")
    assert result["status"] == "error"
    assert result["reason"] == "unrecognized_games_contract"


def test_export_skips_mlb():
    result = export_slate_strategy_for_league("MLB")
    assert result["status"] == "skipped"
    assert result["events"] == []
