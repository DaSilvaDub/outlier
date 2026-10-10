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
