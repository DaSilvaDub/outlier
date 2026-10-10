"""Phase 4a (#226): market canonicalization, team-prop family, primary lines and scope."""

from __future__ import annotations

import pytest

from outlier_nfl.config import NFL_MARKET_ALIASES, normalize_market


# F19 -----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("LONGEST_PASSING_COMPLETION", "LONG_PASS"),
        ("Longest Passing Completion", "LONG_PASS"),
        ("PASSING_COMPLETIONS", "PASS_COMP"),
        ("INTERCEPTIONS_THROWN", "INT"),
    ],
)
def test_live_feed_market_names_are_canonicalized(raw: str, canonical: str) -> None:
    assert normalize_market(raw) == canonical


def test_normalize_market_is_idempotent_for_every_canonical_code() -> None:
    for canonical in set(NFL_MARKET_ALIASES.values()):
        assert normalize_market(canonical) == canonical, canonical


def test_parsed_prop_reaches_the_weather_market_code() -> None:
    from outlier_nfl.config import PROP_LONG_PASS
    from outlier_nfl.props import extract_player_props

    payload = {"props": [{"outcome": {
        "eventId": "e1", "playerName": "Patrick Mahomes", "proposition": "LONGEST_PASSING_COMPLETION",
        "position": "OVER", "line": 38.5, "teamId": "KC", "bestOdds": -110,
        "bookOdds": {"DRAFTKINGS": {"american": -110, "decimal": 1.91}}}}]}
    rows = extract_player_props(payload, {})
    assert rows and rows[0].market == PROP_LONG_PASS  # weather emits LONG_PASS signals


# F20 -----------------------------------------------------------------------

from outlier_nfl.calibration import extract_game_script_context  # noqa: E402
from outlier_nfl.models import NflGameLine  # noqa: E402


def _gl(market: str, mtype: str, prop: str, pos: str, line: float, team: str | None,
        ip: float | None = 52.38) -> NflGameLine:
    return NflGameLine(event_id="e1", event_starts_at=None, matchup="BUF @ KC", home_team="KC",
                       away_team="BUF", market_type=mtype, market=market, proposition=prop,
                       position=pos, line=line, signed_line=None, selection="", team=team,
                       books=[], best_odds=-110 if ip is not None else None, implied_probability=ip)


_PRIMARY = [
    _gl("SPREAD", "GAMELINE", "SPREAD", "AWAY", 2.5, "BUF"),
    _gl("SPREAD", "GAMELINE", "SPREAD", "HOME", -2.5, "KC"),
    _gl("POINTS", "TEAM_PROP", "POINTS", "OVER", 24.5, "KC"),
    _gl("POINTS", "TEAM_PROP", "POINTS", "UNDER", 24.5, "KC"),
]


def test_alternates_cannot_move_the_primary_environment() -> None:
    alts = [
        _gl("SPREAD", "GAMELINE", "SPREAD", "AWAY", 10.5, "BUF", ip=20.0),  # one-sided
        _gl("POINTS", "TEAM_PROP", "POINTS", "OVER", 34.5, "KC", ip=None),  # unpriced
        _gl("POINTS", "TEAM_PROP", "POINTS", "OVER", 30.5, "KC", ip=25.0),  # one-sided
    ]
    base = extract_game_script_context(_PRIMARY)["e1"]
    with_alts = extract_game_script_context(_PRIMARY + alts)["e1"]
    assert (base["away_spread"], base["home_team_total"], base["away_deficit_risk"]) == (2.5, 24.5, False)
    assert with_alts == base


def test_primary_pair_is_the_one_closest_to_a_coin_flip() -> None:
    from outlier_nfl.calibration import primary_spread, primary_total

    ladder = [_gl("POINTS", "TEAM_PROP", "POINTS", "OVER", 27.5, "KC", 40.0),
              _gl("POINTS", "TEAM_PROP", "POINTS", "UNDER", 27.5, "KC", 64.0)]
    assert primary_total(_PRIMARY[2:] + ladder) == 24.5
    assert primary_spread(_PRIMARY[:2], "BUF", "KC") == 2.5
    # No two-way quote: the priced one-sided line closest to 50%; unpriced never.
    assert primary_spread(_PRIMARY[:1] + [_gl("SPREAD", "GAMELINE", "SPREAD", "AWAY", 9.5, "BUF", 22.0)],
                          "BUF", "KC") == 2.5
    assert primary_total([_gl("POINTS", "TEAM_PROP", "POINTS", "OVER", 34.5, "KC", None)]) is None


def test_team_rushing_yards_prop_is_not_points() -> None:
    import json
    from pathlib import Path

    from outlier_nfl.games import extract_game_lines

    fixtures = Path(__file__).parent / "fixtures" / "nfl"
    event = json.loads((fixtures / "schedule.json").read_text(encoding="utf-8"))["events"][0]
    payload = {"markets": [{
        "marketId": "m1", "eventId": event.get("eventId") or event.get("id"),
        "marketType": "TEAM_PROP", "proposition": "RUSHING_YARDS", "label": "Team Rushing Yards",
        "periodLabel": None,
        "outcomes": [{"outcomeId": "o1", "position": "OVER", "line": 120.5, "teamId": "kc-chiefs",
                      "odds": [{"book": "DRAFTKINGS", "american": -110, "decimal": 1.91}]}]}]}
    rows = extract_game_lines(event, payload, {})
    assert rows and rows[0].market == "TEAM_RUSH_YDS"


def test_matchup_context_ignores_one_sided_alternates() -> None:
    from outlier_nfl.matchup import _market_context

    alts = [_gl("SPREAD", "GAMELINE", "SPREAD", "AWAY", 10.5, "BUF", ip=80.0),
            _gl("POINTS", "TEAM_PROP", "POINTS", "OVER", 34.5, "KC", ip=20.0)]
    base = _market_context(_PRIMARY, "KC", "BUF")
    assert _market_context(alts + _PRIMARY, "KC", "BUF") == base
    assert (base["home_spread"], base["home_tt"]) == (-2.5, 24.5)
