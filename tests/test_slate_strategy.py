import pytest
from outlier_scrapers.form_source import FinalEvent
from outlier_scrapers.slate_strategy import MatchupContext, build_event_strategy, window_player_form

def test_window_player_form():
    recent = [
        FinalEvent("e1", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}, "p2": {"PTS": 20}}),
        FinalEvent("e2", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
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
        FinalEvent(f"e{i}", "AWAY", "HOME", 80, 100, {}) for i in range(5)
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
        FinalEvent("e1", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
        FinalEvent("e2", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
        FinalEvent("e3", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 10}}),
        FinalEvent("e4", "AWAY", "HOME", 100, 90, {"p1": {"PTS": 30}}),
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
        FinalEvent("e1", "OTHER", "HOME", 100, 90, {"p2": {"PTS": 50, "AST": 10}})
    ]
    ctx = MatchupContext("event", "AWAY", "HOME", recent, {"p1"}, set())
    out = build_event_strategy(ctx)
    
    # No directions for p2
    assert not any(d.player_key == "p2" for d in out.directions)
