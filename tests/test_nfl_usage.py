"""Player usage signals: vacated targets/carries and efficiency regression."""

from __future__ import annotations

from dataclasses import replace

import pytest

from outlier_nfl import usage
from outlier_nfl.matchup import MatchupScript


def _pw(pid: str, name: str, team: str, pos: str, week: int, *, tgt_share: float = 0.0,
        targets: int = 0, carries: int = 0, rec: int = 0, rush: int = 0) -> dict[str, str]:
    return {"season_type": "REG", "player_id": pid, "player_display_name": name, "team": team,
            "position": pos, "week": str(week), "target_share": str(tgt_share),
            "targets": str(targets), "carries": str(carries), "receiving_yards": str(rec),
            "rushing_yards": str(rush)}


def _ep(pid: str, week: int, rec_exp: float = 0.0, rush_exp: float = 0.0) -> dict[str, str]:
    return {"player_id": pid, "week": str(week), "rec_yards_gained_exp": str(rec_exp),
            "rush_yards_gained_exp": str(rush_exp)}


ROWS = [
    # LA (nflverse code) pass catchers, weeks 1-2
    *[_pw("wr1", "Star Receiver", "LA", "WR", w, tgt_share=0.30, targets=10, rec=120) for w in (1, 2)],
    *[_pw("wr2", "Second Receiver", "LA", "WR", w, tgt_share=0.15, targets=5, rec=40) for w in (1, 2)],
    *[_pw("te1", "Depth Tight End", "LA", "TE", w, tgt_share=0.05, targets=2, rec=10) for w in (1, 2)],
    # Hurt earlier: played week 1 only, so week-2 shares already reflect his absence
    _pw("wr3", "Old Injury", "LA", "WR", 1, tgt_share=0.25, targets=8, rec=60),
    _pw("wr3", "Old Injury", "LA", "WR", 0, tgt_share=0.25, targets=8, rec=60),
    # DEN backfield
    *[_pw("rb1", "Lead Back", "DEN", "RB", w, carries=18, rush=80) for w in (1, 2)],
    *[_pw("rb2", "Backup Back", "DEN", "RB", w, carries=6, rush=20) for w in (1, 2)],
    _pw("rb1", "Lead Back", "DEN", "RB", 3, carries=20, rush=90),  # after cutoff
    {**_pw("x", "Preseason Guy", "DEN", "RB", 1, carries=30), "season_type": "PRE"},
    _pw("one", "One Game", "DEN", "WR", 2, tgt_share=0.4, targets=9),
]
EXPECTED = [
    *[_ep("wr1", w, rec_exp=80) for w in (1, 2)],   # 120 vs 80 -> +50% hot
    *[_ep("wr2", w, rec_exp=60) for w in (1, 2)],   # 40 vs 60 -> -33% cold
    *[_ep("te1", w, rec_exp=5) for w in (1, 2)],    # below expected floor
    *[_ep("rb1", w, rush_exp=78) for w in (1, 2)],  # within band
]
EVENTS = {"LAR": "e1", "DEN": "e1"}


@pytest.fixture
def profiles() -> dict[str, usage.PlayerUsage]:
    return usage.build_profiles(ROWS, EXPECTED, before_week=3)


def test_profiles_average_before_cutoff_with_carry_share(profiles: dict[str, usage.PlayerUsage]) -> None:
    assert set(profiles) == {"wr1", "wr2", "te1", "wr3", "rb1", "rb2"}  # 1-game & PRE dropped
    rb1 = profiles["rb1"]
    assert rb1.games == 2 and rb1.carries_pg == 18.0 and rb1.carry_share == 0.75
    assert rb1.team_last_week == 2 and rb1.last_week == 2  # week-3 row excluded
    wr1 = profiles["wr1"]
    assert wr1.team == "LAR" and wr1.rec_yds_exp_pg == 80.0 and wr1.target_share == 0.3
    assert profiles["wr3"].last_week == 1 and profiles["wr3"].team_last_week == 2


def _by(sigs: list, tag: str) -> set[tuple[str | None, str]]:
    return {(s.player_name, s.market) for s in sigs if s.tag == tag}


def test_vacated_targets_only_for_newly_absent_teammates(profiles: dict[str, usage.PlayerUsage]) -> None:
    sigs = usage.usage_signals(profiles, {"LAR": ["Star Receiver"]}, EVENTS)
    vac = _by(sigs, "VACATED_TARGETS")
    assert ("Second Receiver", "REC_YDS") in vac and ("Second Receiver", "RECEIVING_TARGETS") in vac
    assert not any(n == "Depth Tight End" for n, _ in vac)  # below 8% share
    assert not any(n == "Star Receiver" for n, _ in vac)
    stale = usage.usage_signals(profiles, {"LAR": ["Old Injury"]}, EVENTS)
    assert _by(stale, "VACATED_TARGETS") == set()  # also missed last game -> already priced in


def test_vacated_carries_are_high_confidence(profiles: dict[str, usage.PlayerUsage]) -> None:
    sigs = usage.usage_signals(profiles, {"DEN": ["Lead Back"]}, EVENTS)
    carries = [s for s in sigs if s.tag == "VACATED_CARRIES"]
    assert {(s.player_name, s.market) for s in carries} == {
        ("Backup Back", "RUSH_ATT"), ("Backup Back", "RUSH_YDS")}
    assert all(s.confidence == "HIGH" and s.volume_adjustment == 0.25 for s in carries)


def test_efficiency_regression_both_directions_with_floor(profiles: dict[str, usage.PlayerUsage]) -> None:
    sigs = usage.usage_signals(profiles, {}, EVENTS)
    hot = [s for s in sigs if s.tag == "EFFICIENCY_HOT"]
    cold = [s for s in sigs if s.tag == "EFFICIENCY_COLD"]
    assert [(s.player_name, s.market, s.side, s.volume_adjustment) for s in hot] == [
        ("Star Receiver", "REC_YDS", "UNDER", -0.10)]
    assert [(s.player_name, s.side, s.volume_adjustment) for s in cold] == [
        ("Second Receiver", "OVER", 0.08)]
    assert not any(s.player_name == "Depth Tight End" for s in sigs)  # expected < 15 yds
    assert usage.usage_signals(profiles, {}, {"NYG": "e9"}) == []  # teams not on slate


def test_append_signals_adds_note_only_where_signals_exist(profiles: dict[str, usage.PlayerUsage]) -> None:
    def script(eid: str) -> MatchupScript:
        return MatchupScript(eid, "A @ B", "DEN", "LAR", "COMPETITIVE", "AWAY", "UNDER",
                             21, 24, 2.5, 44.5, (), ())

    sigs = usage.usage_signals(profiles, {"DEN": ["Lead Back"]}, EVENTS)
    out = usage.append_signals([script("e1"), script("e2")], sigs)
    assert len(out[0].prop_signals) == len(sigs) and out[0].notes[-1].startswith("Usage:")
    assert out[1].prop_signals == () and out[1].notes == ()


def test_load_usage_survives_missing_expected_stats() -> None:
    def fetch(url: str) -> list[dict[str, str]]:
        if "ffopportunity" in url:
            raise OSError("blocked")
        return ROWS

    prof = usage.load_usage(2026, 3, fetch)
    assert prof["wr1"].rec_yds_exp_pg is None  # regression signals drop out, profiles remain
    assert usage.usage_signals(prof, {}, EVENTS) == []


def _sig(tag: str, side: str, adj: float, conf: str = "MEDIUM", market: str = "RUSH_YDS"):
    from outlier_nfl.matchup import PropSignal

    return PropSignal("e1", "Kyren Williams", "LAR", market, side, tag, "r", conf, adj)


def _kyren_prop(market: str = "RUSH_YDS", position: str = "OVER"):
    from outlier_nfl.models import BookPrice, NflPlayerProp

    return NflPlayerProp(
        event_id="e1", event_starts_at=None, matchup="LAR @ DEN", team="LAR", opponent="DEN",
        player_name="Kyren Williams", player_id=None, market=market, market_raw=market,
        position=position, line=64.5,
        books=(BookPrice(book="DK", odds=-110, odds_raw="-110", decimal=1.91),),
        best_odds=-110, implied_probability=52.4,
    )


def _apply(signals: list) -> object:
    from outlier_nfl.matchup import apply_matchup_signals

    script = MatchupScript("e1", "LAR @ DEN", "DEN", "LAR", "COMPETITIVE", "AWAY", "UNDER",
                           21, 24, 2.5, 44.5, (), tuple(signals))
    return apply_matchup_signals([_kyren_prop()], [script])[0]


def test_regression_wins_conflict_and_blocks_tier_bump() -> None:
    prop = _apply([
        _sig("MATCHUP_RUSH_MISMATCH", "OVER", 0.20, "HIGH"),
        _sig("EFFICIENCY_HOT", "UNDER", -0.10),
    ])
    assert prop.calibrated_volume_adjustment == -0.10
    assert "EFFICIENCY_HOT" in prop.calibration_tags and "MATCHUP_FADE" in prop.calibration_tags
    assert "OVERRIDDEN_MATCHUP_RUSH_MISMATCH" in prop.calibration_tags
    assert "MATCHUP_RUSH_MISMATCH" not in prop.calibration_tags
    assert prop.confidence_tier in {None, "", "STANDARD"}  # HIGH over was dropped


def test_regression_overrides_weather_and_stacks_with_same_direction() -> None:
    prop = _apply([
        _sig("EFFICIENCY_COLD", "OVER", 0.08),
        _sig("VACATED_CARRIES", "OVER", 0.25, "HIGH"),
        _sig("WEATHER_WIND_HIGH", "UNDER", -0.10),
    ])
    assert prop.calibrated_volume_adjustment == 0.33  # cold + vacated stack
    assert "OVERRIDDEN_WEATHER_WIND_HIGH" in prop.calibration_tags
    assert prop.confidence_tier == "TIER_2_STRONG"  # surviving HIGH over still promotes


def test_without_regression_conflicting_signals_still_net_out() -> None:
    prop = _apply([
        _sig("MATCHUP_RUSH_MISMATCH", "OVER", 0.20, "HIGH"),
        _sig("WEATHER_WIND_HIGH", "UNDER", -0.10),
    ])
    assert prop.calibrated_volume_adjustment == 0.10
    assert not any(t.startswith("OVERRIDDEN_") for t in prop.calibration_tags)


def test_fetch_player_weeks_validates_season() -> None:
    urls: list[str] = []
    usage.fetch_player_weeks(2026, lambda url: urls.append(url) or [])
    assert urls[0].endswith("/stats_player/stats_player_week_2026.csv")
    for bad in (1900, 3000):
        with pytest.raises(ValueError):
            usage.fetch_player_weeks(bad, lambda url: [])


# --- F22: paired actual/expected, current-team samples (#229) ---------------

def _pw(pid, week, team, rec, targets=5, season=2026, game=None):
    return {"player_id": pid, "player_display_name": pid, "position": "WR",
            "season": str(season), "week": str(week), "season_type": "REG",
            "game_id": game or f"{season}_{week:02d}_{team}", "team": team,
            "receiving_yards": str(rec), "targets": str(targets),
            "target_share": "0.25", "carries": "0", "rushing_yards": "0"}


def _ep(pid, week, team, exp, season=2026):
    # ffopportunity ep_weekly has no season_type; keys are season/week/game_id/player_id.
    return {"season": str(season), "posteam": team, "week": str(week),
            "game_id": f"{season}_{week:02d}_{team}", "player_id": pid,
            "rec_yards_gained_exp": str(exp), "rush_yards_gained_exp": "0"}


def test_partial_expected_compares_the_same_games_only():
    from outlier_nfl.usage import build_profiles, usage_signals

    p = build_profiles(
        [_pw("P1", 1, "KC", 100), _pw("P1", 2, "KC", 100), _pw("P1", 3, "KC", 0)],
        [_ep("P1", 1, "KC", 100), _ep("P1", 2, "KC", 100)], before_week=4)["P1"]
    assert p.rec_yds_pg == 66.7  # full history kept
    assert (p.rec_yds_paired_pg, p.rec_yds_exp_pg) == (100.0, 100.0)
    assert p.expected_games == 2 and p.expected_coverage == 0.6667
    assert usage_signals({"P1": p}, {}, {"KC": "e"}) == []


def test_expected_rows_from_another_season_do_not_pair():
    from outlier_nfl.usage import build_profiles

    p = build_profiles(
        [_pw("P2", 1, "KC", 60), _pw("P2", 2, "KC", 60)],
        [_ep("P2", 1, "KC", 120, season=2025), _ep("P2", 2, "KC", 120, season=2025)],
        before_week=4)["P2"]
    assert p.rec_yds_exp_pg is None and p.expected_games == 0


def test_duplicate_player_game_counts_once_and_conflicting_expected_drops():
    from outlier_nfl.usage import build_profiles

    p = build_profiles(
        [_pw("P4", 1, "KC", 50), _pw("P4", 2, "KC", 50), _pw("P4", 2, "KC", 50),
         _pw("P4", 3, "KC", 50)],
        [_ep("P4", 1, "KC", 50), _ep("P4", 2, "KC", 50), _ep("P4", 3, "KC", 50),
         _ep("P4", 3, "KC", 90)], before_week=4)["P4"]
    assert p.games == 3 and p.expected_games == 2


def test_traded_player_role_is_current_team_only():
    from outlier_nfl.usage import build_profiles, usage_signals

    rows = [_pw("P3", 1, "NYJ", 40, targets=10), _pw("P3", 2, "NYJ", 40, targets=10),
            _pw("P3", 3, "KC", 40, targets=2)]
    p = build_profiles(rows, before_week=4)["P3"]
    assert (p.team, p.team_games, p.targets_pg, p.targets_pg_all) == ("KC", 1, 2.0, 7.33)
    # One KC game is not a KC role: not a vacated-volume source.
    star = replace(p, player="Star", target_share=0.3, team_games=1)
    mate = replace(p, player="Mate", player_id="M", target_share=0.2, team_games=5)
    assert usage_signals({"s": star, "m": mate}, {"KC": ["Star"]}, {"KC": "e"}) == []
    star_ok = replace(star, team_games=2)
    assert usage_signals({"s": star_ok, "m": mate}, {"KC": ["Star"]}, {"KC": "e"})
