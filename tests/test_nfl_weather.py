"""Game-day weather calibration: forecast summary, thresholds, venues, and prop signals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest

from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl import weather as wx
from outlier_nfl.config import PROP_LONG_PASS
from outlier_nfl.matchup import MatchupScript, apply_matchup_signals
from outlier_nfl.models import BookPrice, NflPlayerProp

KICKOFF = datetime(2026, 9, 28, 0, 20, tzinfo=timezone.utc)  # SNF 8:20 PM ET
EVENT = {"eventId": "e1", "scheduledTime": "2026-09-28T00:20:00Z",
         "home": {"alias": "DEN"}, "away": {"alias": "LAR"}}
TAPES = {
    "DEN": {"qb": "Bo Nix", "wr_deep": "Jaylen Waddle", "wr_slot": "Marvin Mims Jr.",
            "rb1": "J.K. Dobbins", "te": "Evan Engram"},
    "LAR": {"qb": "Matthew Stafford", "wr_deep": "Davante Adams", "rb1": "Kyren Williams"},
}


def _payload(wind: list[float], prob: float = 10, precip: float = 0.0,
             temp: float = 55.0) -> dict[str, Any]:
    """Hourly Open-Meteo payload from 22:00 UTC; ``wind`` covers 00:00..03:00 UTC."""
    start = datetime(2026, 9, 27, 22, tzinfo=timezone.utc)
    hours = [start + timedelta(hours=i) for i in range(8)]
    winds = [99.0, 99.0] + wind + [99.0] * (6 - len(wind))  # outside-window noise
    return {"hourly": {
        "time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours],
        "wind_speed_10m": winds,
        "wind_gusts_10m": [w + 8 for w in winds],
        "temperature_2m": [temp] * 8,
        "precipitation_probability": [prob] * 8,
        "precipitation": [precip] * 8,
    }}


def test_summary_uses_kickoff_window_only() -> None:
    s = wx.summarize_forecast(_payload([10, 14, 16, 20]), KICKOFF)
    assert s["wind_mph"] == 15.0  # mean of 00-03 UTC, 22/23 UTC noise excluded
    assert s["gust_mph"] == 28.0 and s["temp_f"] == 55.0
    assert s["precip_mm"] == 0.0 and s["precip_prob"] == 10.0


@pytest.mark.parametrize(
    ("cond", "tags", "adj"),
    [
        ({"wind_mph": 11.9}, (), 0.0),
        ({"wind_mph": 12.0}, ("WEATHER_WIND",), -0.06),
        ({"wind_mph": 15.0}, ("WEATHER_WIND_HIGH",), -0.10),
        ({"wind_mph": 16, "precip_prob": 70, "precip_mm": 3}, ("WEATHER_WIND_HIGH", "WEATHER_WET"),
         -0.15),
        ({"wind_mph": 5, "precip_prob": 70, "precip_mm": 0.5}, (), 0.0),  # drizzle chance only
        ({"wind_mph": 5, "temp_f": 20}, ("WEATHER_COLD",), 0.0),
    ],
)
def test_classify_thresholds(cond: dict[str, float], tags: tuple[str, ...], adj: float) -> None:
    assert wx.classify(cond) == (tags, adj)


def test_venue_status_rules() -> None:
    assert wx.venue_status("DEN", {"roof": "outdoors"}) == "outdoor"
    assert wx.venue_status("DET", None) == "indoor"
    assert wx.venue_status("LAR", {"roof": "outdoors"}) == "indoor"  # SoFi fixed roof
    assert wx.venue_status("DAL", None) == "retractable"
    assert wx.venue_status("DAL", {"roof": "open"}) == "outdoor"
    assert wx.venue_status("GB", {"roof": "closed"}) == "indoor"
    assert wx.venue_status("JAX", {"location": "Neutral", "roof": "outdoors"}) == "neutral"


def test_game_weather_fetches_only_open_air() -> None:
    urls: list[str] = []

    def fetch(url: str) -> Mapping[str, Any]:
        urls.append(url)
        return _payload([16, 16, 17, 17])

    w = wx.game_weather(EVENT, {"roof": "outdoors"}, fetch)
    assert w.venue == "outdoor" and w.tags == ("WEATHER_WIND_HIGH",) and w.pass_adjustment == -0.1
    assert "latitude=39.7439" in urls[0] and "start_date=2026-09-28" in urls[0]
    assert "wind_speed_unit=mph" in urls[0]

    dome = {**EVENT, "home": {"alias": "DET"}}
    assert wx.game_weather(dome, None, fetch).venue == "indoor"
    assert len(urls) == 1  # indoor games never call the API


def test_signals_cover_roles_for_both_teams_and_ground_lean_only_in_high_wind() -> None:
    high = wx.GameWeather("e1", "DEN", "LAR", None, "outdoor", wind_mph=17,
                          tags=("WEATHER_WIND_HIGH",), pass_adjustment=-0.1)
    sig = {(s.player_name, s.market): s for s in wx.weather_signals(high, TAPES)}
    assert sig[("Bo Nix", "PASS_YDS")].side == "UNDER"
    assert sig[("Matthew Stafford", "PASS_YDS")].volume_adjustment == -0.1
    assert sig[("Davante Adams", "LONG_REC")].tag == "WEATHER_WIND_HIGH"
    assert sig[("Marvin Mims Jr.", "REC_YDS")].volume_adjustment == -0.05
    assert sig[("J.K. Dobbins", "RUSH_YDS")].side == "OVER"
    assert sig[("Evan Engram", "REC")].confidence == "MEDIUM"
    assert ("Kyren Williams", "RUSH_YDS") in sig

    mild = wx.GameWeather("e1", "DEN", "LAR", None, "outdoor", wind_mph=13,
                          tags=("WEATHER_WIND",), pass_adjustment=-0.06)
    markets = {s.market for s in wx.weather_signals(mild, TAPES)}
    assert "RUSH_YDS" not in markets and "PASS_YDS" in markets
    calm = wx.GameWeather("e1", "DEN", "LAR", None, "outdoor", wind_mph=5)
    assert wx.weather_signals(calm, TAPES) == []


def _script() -> MatchupScript:
    return MatchupScript(
        event_id="e1", matchup="LAR @ DEN", home_team="DEN", away_team="LAR",
        script_type="COMPETITIVE", spread_lean="AWAY", total_lean="UNDER", home_score=21,
        away_score=24, home_spread=2.5, total=44.5, mismatches=(), prop_signals=(),
    )


def _prop(name: str, team: str, market: str, position: str) -> NflPlayerProp:
    return NflPlayerProp(
        event_id="e1", event_starts_at=None, matchup="LAR @ DEN", team=team, opponent=None,
        player_name=name, player_id=None, market=market, market_raw=market, position=position,
        line=220.5, books=(BookPrice(book="DK", odds=-110, odds_raw="-110", decimal=1.91),),
        best_odds=-110, implied_probability=52.4,
    )


def test_weather_flows_through_apply_matchup_signals() -> None:
    high = wx.GameWeather("e1", "DEN", "LAR", None, "outdoor", wind_mph=17, gust_mph=30,
                          temp_f=40, precip_mm=0, precip_prob=10,
                          tags=("WEATHER_WIND_HIGH",), pass_adjustment=-0.1)
    scripts = wx.apply_weather([_script()], {"e1": high}, TAPES)
    assert scripts[0].notes[-1].startswith("Weather: 40F, wind 17 mph")
    props = apply_matchup_signals(
        [_prop("Bo Nix", "DEN", "PASS_YDS", "OVER"), _prop("Bo Nix", "DEN", "PASS_YDS", "UNDER"),
         _prop("Courtland Sutton", "DEN", "REC_YDS", "OVER")],
        scripts,
    )
    over, under, other = props
    assert over.calibrated_volume_adjustment == -0.1 and "WEATHER_WIND_HIGH" in over.calibration_tags
    assert "MATCHUP_FADE" in over.calibration_tags
    assert under.calibrated_volume_adjustment is None and "WEATHER_WIND_HIGH" in under.calibration_tags
    assert other.calibration_tags == ()  # not a tape role -> untouched


def test_qb_longest_completion_signal_uses_the_canonical_market_code() -> None:
    """The longest-completion haircut must join props, which carry ``LONG_PASS``.

    ``apply_matchup_signals`` matches ``signal.market == prop.market`` exactly, so a
    signal emitted under a non-canonical market code is silently dropped.
    """
    high = wx.GameWeather("e1", "DEN", "LAR", None, "outdoor", wind_mph=17,
                          tags=("WEATHER_WIND_HIGH",), pass_adjustment=-0.1)
    markets = {s.market for s in wx.weather_signals(high, TAPES)}
    assert PROP_LONG_PASS in markets and "LONGEST_PASSING_COMPLETION" not in markets

    scripts = wx.apply_weather([_script()], {"e1": high}, TAPES)
    prop = _prop("Bo Nix", "DEN", PROP_LONG_PASS, "OVER")
    (updated,) = apply_matchup_signals([prop], scripts)
    assert "WEATHER_WIND_HIGH" in updated.calibration_tags
    assert updated.calibrated_volume_adjustment == -0.1


def test_load_slate_weather_matches_schedule_by_eastern_date_and_survives_errors() -> None:
    schedule = [{"home_team": "DEN", "away_team": "LAR", "gameday": "2026-09-27", "roof": "outdoors"},
                {"home_team": "DEN", "away_team": "LAR", "gameday": "2025-11-02", "roof": "closed"}]
    got = wx.load_slate_weather([EVENT], schedule, lambda url: _payload([13, 13, 13, 13]))
    assert got["e1"].venue == "outdoor" and got["e1"].tags == ("WEATHER_WIND",)

    def boom(url: str) -> Mapping[str, Any]:
        raise OSError("blocked")

    assert wx.load_slate_weather([EVENT], schedule, boom) == {}


def test_pipeline_fixture_mode_never_forecasts(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: object, **_k: object) -> dict[str, object]:
        raise AssertionError("fixture replay must not fetch forecasts")

    monkeypatch.setattr(nfl_pipeline, "load_slate_weather", forbidden)
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "nfl"
    summary = nfl_pipeline.NflPipeline(data_dir=tmp_path).run(
        date="2026-09-13",
        offline_fixtures_dir=fixtures,
        reports_dir=tmp_path / "reports" / "NFL",
    )
    assert summary["status"] == "OK"
