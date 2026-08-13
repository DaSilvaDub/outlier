import pytest
from outlier_scrapers.form_source import FinalEvent, extract_event, _safe_players

def test_extract_event_espn():
    espn_game = {
        "id": "12345",
        "competitions": [
            {
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"displayName": "Las Vegas Aces"},
                        "score": "100"
                    },
                    {
                        "homeAway": "away",
                        "team": {"displayName": "New York Liberty"},
                        "score": "90"
                    }
                ],
                "boxscore": {
                    "players": [
                        {
                            "team": {"displayName": "Las Vegas Aces"},
                            "statistics": [
                                {
                                    "names": ["points", "assists"],
                                    "athletes": [
                                        {
                                            "athlete": {"displayName": "A'ja Wilson"},
                                            "stats": ["30", "5"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }
    
    event = extract_event(espn_game)
    assert event.provider_event_id == "12345"
    assert event.home == "Las Vegas Aces"
    assert event.away == "New York Liberty"
    assert event.home_score == 100
    assert event.away_score == 90
    
    # Check player parsing (the key is normalized via _token in form_source)
    # The normalizer _token for "A'ja Wilson" will be "AJAWILSON"
    assert "AJAWILSON" in event.players
    assert event.players["AJAWILSON"]["PTS"] == 30.0
    assert event.players["AJAWILSON"]["AST"] == 5.0

def test_extract_event_missing_data():
    espn_game = {
        "id": "12345",
        "competitions": []
    }
    with pytest.raises(IndexError):
        extract_event(espn_game)

def test_safe_players_empty():
    assert _safe_players({}, []) == {}
