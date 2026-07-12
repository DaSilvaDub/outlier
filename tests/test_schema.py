import pytest
from pathlib import Path

from outlier_scrapers.schema import (
    ValidationError,
    validate_raw_schedule,
    validate_raw_player_props,
    validate_raw_games,
    validate_raw_line_movement,
    validate_normalized_props,
    validate_normalized_games,
    validate_candidate_row,
)
from outlier_scrapers.normalizer import (
    build_normalized_payload,
    normalize_games,
)
from outlier_scrapers.pack import write_pack, CANDIDATES_HEADER
from outlier_scrapers.registry import get_sport_config


def test_validate_raw_schedule():
    # Happy path
    valid = {
        "events": [
            {
                "eventId": "event-1",
                "away": {"alias": "NYY"},
                "home": {"alias": "BOS"},
                "startTime": "2026-07-12T12:00:00Z",
            }
        ]
    }
    assert not validate_raw_schedule(valid)

    # Missing events key
    assert "Schedule payload is missing 'events' key" in validate_raw_schedule({})

    # Events not a list
    assert "'events' must be a list" in validate_raw_schedule({"events": "not-a-list"})[0]

    # Event missing ID
    invalid_event = {
        "events": [
            {
                "away": {"alias": "NYY"},
                "startTime": "2026-07-12T12:00:00Z",
            }
        ]
    }
    assert any("missing 'eventId' or 'id'" in err for err in validate_raw_schedule(invalid_event))

    # Event side not dict
    invalid_side = {
        "events": [
            {
                "eventId": "evt-1",
                "away": "NYY",
                "startTime": "2026-07-12T12:00:00Z",
            }
        ]
    }
    assert any("side 'away' is not a dictionary" in err for err in validate_raw_schedule(invalid_side))

    # Event missing start time
    missing_time = {
        "events": [
            {
                "eventId": "evt-1",
                "away": {"alias": "NYY"},
            }
        ]
    }
    assert any("missing all schedule/start time keys" in err for err in validate_raw_schedule(missing_time))


def test_validate_raw_player_props():
    # Happy path
    valid = {
        "props": [
            {
                "outcome": {
                    "eventId": "evt-1",
                    "marketId": "m-1",
                    "outcomeId": "o-1",
                    "position": "OVER",
                    "line": 1.5,
                }
            }
        ]
    }
    assert not validate_raw_player_props(valid)

    # Missing props key
    assert "Player props payload is missing 'props' key" in validate_raw_player_props({})

    # Props not list
    assert "'props' must be a list" in validate_raw_player_props({"props": "not-a-list"})[0]

    # Missing outcome
    no_outcome = {"props": [{}]}
    assert any("missing 'outcome' sub-object" in err for err in validate_raw_player_props(no_outcome))

    # Outcome missing critical fields
    missing_critical = {
        "props": [
            {
                "outcome": {
                    "marketId": "m-1",
                    "outcomeId": "o-1",
                }
            }
        ]
    }
    assert any("missing 'eventId'" in err for err in validate_raw_player_props(missing_critical))

    # Wrong types
    wrong_types = {
        "props": [
            {
                "outcome": {
                    "eventId": "evt-1",
                    "marketId": "m-1",
                    "outcomeId": "o-1",
                    "position": 123,
                    "line": {},
                }
            }
        ]
    }
    errors = validate_raw_player_props(wrong_types)
    assert any("position' must be a string" in err for err in errors)
    assert any("line' must be a number or string" in err for err in errors)


def test_validate_raw_games():
    # Happy path
    valid = {
        "events": [
            {
                "eventId": "evt-1",
                "markets": [
                    {
                        "marketId": "m-1",
                        "outcomes": [
                            {"outcomeId": "o-1"}
                        ]
                    }
                ]
            }
        ]
    }
    assert not validate_raw_games(valid)

    # Missing events key
    assert "Games payload is missing 'events' key" in validate_raw_games({})

    # Event missing ID
    missing_id = {"events": [{}]}
    assert any("missing 'eventId' or 'id'" in err for err in validate_raw_games(missing_id))


def test_validate_raw_line_movement():
    # Happy path
    valid = {
        "ev_records": [
            {
                "market_id": "m-1",
                "outcome_id": "o-1",
            }
        ]
    }
    assert not validate_raw_line_movement(valid)

    # ev_records not list
    assert "'ev_records' must be a list" in validate_raw_line_movement({"ev_records": "not-a-list"})[0]

    # EV record missing market reference
    missing_market = {
        "ev_records": [{}]
    }
    assert any("missing both 'market_id' and 'outcome_id'" in err for err in validate_raw_line_movement(missing_market))


def test_validate_normalized_props_and_games():
    # Normalized props check missing keys
    invalid_prop = [{}]
    assert any("missing key(s)" in err for err in validate_normalized_props(invalid_prop))

    # Normalized games check missing keys
    invalid_game = [{}]
    assert any("missing key(s)" in err for err in validate_normalized_games(invalid_game))


def test_validate_candidate_row():
    # Happy path
    valid_row = {
        "sport": "MLB",
        "selection": "test-selection",
        "decimal_price": 2.5,
        "edge_pct": 0.05,
    }
    assert not validate_candidate_row(valid_row, CANDIDATES_HEADER)

    # Unexpected column
    unexpected = {
        "sport": "MLB",
        "selection": "test-selection",
        "invalid_col": "val",
    }
    assert any("unexpected column" in err for err in validate_candidate_row(unexpected, CANDIDATES_HEADER))

    # Type mismatch (non-numeric price)
    type_mismatch = {
        "sport": "MLB",
        "selection": "test-selection",
        "decimal_price": "not-a-number",
    }
    assert any("must be numeric" in err for err in validate_candidate_row(type_mismatch, CANDIDATES_HEADER))

    # Missing critical field
    missing_critical = {
        "sport": "MLB",
    }
    assert any("Critical field 'selection' is empty" in err for err in validate_candidate_row(missing_critical, CANDIDATES_HEADER))


def test_build_normalized_payload_compatibility_gate():
    # If props_payload is completely invalid structure, raise ValidationError
    with pytest.raises(ValidationError, match="Critical player props schema violation"):
        build_normalized_payload(
            config=get_sport_config("MLB"),
            props_payload={},
            schedule_payload={"events": []},
            source_url="http://test.com",
        )


def test_normalize_games_compatibility_gate():
    # If schedule_payload is invalid structure, raise ValidationError
    with pytest.raises(ValidationError, match="Critical schedule schema violation"):
        normalize_games(
            config=get_sport_config("MLB"),
            schedule_payload={},
            events_payloads=[],
            source_url="http://test.com",
        )


def test_write_pack_compatibility_gate(tmp_path):
    # Invalid candidate row with missing selection
    invalid_rows = [
        {
            "sport": "MLB",
        }
    ]
    with pytest.raises(ValidationError, match="Critical schema compatibility violation"):
        write_pack(
            rows=invalid_rows,
            out_dir=tmp_path,
        )
