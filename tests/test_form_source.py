from datetime import date, timedelta

import pytest

from outlier_scrapers.form_source import (
    ResultsError,
    UnsupportedSport,
    _boxscore_players,
    _scoreboard_events,
    canon_team,
    iter_recent_finals,
)


def test_scoreboard_events_extracts_completed_event() -> None:
    payload = {
        "events": [
            {
                "id": "12345",
                "status": {"type": {"completed": True}},
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"name": "Las Vegas Aces"},
                                "score": "100",
                            },
                            {
                                "homeAway": "away",
                                "team": {"name": "New York Liberty"},
                                "score": "90",
                            },
                        ]
                    }
                ],
            }
        ]
    }

    events = _scoreboard_events(payload, "WNBA", date(2099, 8, 8))

    assert len(events) == 1
    event = events[0]
    assert event.provider_event_id == "12345"
    assert event.sport == "WNBA"
    assert event.event_date == date(2099, 8, 8)
    assert event.home == "Las Vegas Aces"
    assert event.away == "New York Liberty"
    assert event.home_score == 100
    assert event.away_score == 90
    assert event.players == {}


def test_scoreboard_events_requires_events_list() -> None:
    with pytest.raises(ResultsError, match="events list"):
        _scoreboard_events({}, "WNBA", date(2099, 8, 8))


def test_boxscore_players_extracts_named_stats() -> None:
    payload = {
        "boxscore": {
            "players": [
                {
                    "statistics": [
                        {
                            "names": ["PTS", "AST"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "A'ja Wilson"},
                                    "stats": ["30", "5"],
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }

    players = _boxscore_players(payload)

    assert "AJAWILSON" in players
    assert players["AJAWILSON"]["PTS"] == 30.0
    assert players["AJAWILSON"]["AST"] == 5.0


def test_boxscore_players_handles_missing_structure() -> None:
    assert _boxscore_players({}) == {}


def test_canon_team_maps_espn_lv_to_outlier_lva() -> None:
    assert canon_team("WNBA", "LV") == "LVA"
    assert canon_team("WNBA", "LVA") == "LVA"
    assert canon_team("WNBA", "Las Vegas Aces") == "LVA"


def test_iter_recent_finals_matches_lva_against_espn_lv(monkeypatch) -> None:
    as_of = date(2026, 8, 13)
    day = as_of - timedelta(days=1)

    def fake_fetch(url: str, *, timeout: int = 30) -> dict:
        if "/summary" in url:
            return {
                "boxscore": {
                    "players": [
                        {
                            "team": {"abbreviation": "LV"},
                            "statistics": [
                                {
                                    "names": ["PTS"],
                                    "athletes": [
                                        {
                                            "athlete": {"displayName": "A'ja Wilson"},
                                            "stats": ["20"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        if f"dates={day.strftime('%Y%m%d')}" in url:
            return {
                "events": [
                    {
                        "id": "espn-1",
                        "status": {"type": {"completed": True}},
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "score": "88",
                                        "team": {"abbreviation": "LV"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "score": "80",
                                        "team": {"abbreviation": "SEA"},
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }
        return {"events": []}

    monkeypatch.setattr("outlier_scrapers.form_source._fetch_json", fake_fetch)
    events = iter_recent_finals("WNBA", {"LVA"}, 5, as_of=as_of)
    assert len(events) == 1
    assert events[0].provider_event_id == "espn-1"
    assert events[0].players["AJAWILSON"]["PTS"] == 20.0


def test_iter_recent_finals_mlb_is_unsupported() -> None:
    with pytest.raises(UnsupportedSport):
        iter_recent_finals("MLB", {"NYY"}, 5)
