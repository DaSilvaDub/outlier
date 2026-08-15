from datetime import date

import pytest

from outlier_scrapers.form_source import ResultsError, _boxscore_players, _scoreboard_events


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
