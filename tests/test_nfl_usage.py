"""Player usage signals: vacated targets/carries and efficiency regression."""

from __future__ import annotations

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
