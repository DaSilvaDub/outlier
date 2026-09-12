import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from outlier_nfl.schema import (
    validate_schedule_payload,
    validate_event_markets_payload,
    validate_player_props_payload,
    validate_game_line_record,
    validate_player_prop_record,
    validate_normalized_dataset,
)
from outlier_nfl.utils import safe_write_json, parse_iso_datetime

try:
    from outlier_nfl.pipeline import NflPipeline
    HAS_PIPELINE = True
except ImportError:
    HAS_PIPELINE = False


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"


@pytest.fixture
def mock_api_client():
    client = MagicMock()
    with open(FIXTURES_DIR / "schedule.json", "r", encoding="utf-8") as f:
        sched_data = json.load(f)
    with open(FIXTURES_DIR / "event_markets.json", "r", encoding="utf-8") as f:
        mkts_data = json.load(f)
    with open(FIXTURES_DIR / "player_props.json", "r", encoding="utf-8") as f:
        props_data = json.load(f)

    client.fetch_schedule.return_value = sched_data
    client.fetch_event_markets.return_value = mkts_data
    client.fetch_player_props.return_value = props_data
    return client


def test_schema_validation_gates():
    with open(FIXTURES_DIR / "schedule.json", "r", encoding="utf-8") as f:
        sched = json.load(f)
    with open(FIXTURES_DIR / "event_markets.json", "r", encoding="utf-8") as f:
        mkts = json.load(f)
    with open(FIXTURES_DIR / "player_props.json", "r", encoding="utf-8") as f:
        props = json.load(f)

    # Valid payloads must return 0 validation errors
    assert validate_schedule_payload(sched) == []
    assert validate_event_markets_payload(mkts) == []
    assert validate_player_props_payload(props) == []

    # Invalid schedule payload
    assert len(validate_schedule_payload({})) > 0
    assert len(validate_schedule_payload({"events": [{"id": "bad"}]})) > 0

    # Single record validators
    valid_game_line = {
        "event_id": "evt-1",
        "matchup": "KC @ BAL",
        "home_team": "KC",
        "away_team": "BAL",
        "market_type": "GAMELINE",
        "market": "SPREAD",
        "position": "HOME",
        "line": -3.5,
        "books": [{"book": "DraftKings", "odds": -110}],
    }
    assert validate_game_line_record(valid_game_line) == []

    invalid_game_line = dict(valid_game_line)
    invalid_game_line["position"] = "INVALID_POS"
    assert len(validate_game_line_record(invalid_game_line)) > 0

    valid_player_prop = {
        "event_id": "evt-1",
        "matchup": "KC @ BAL",
        "player_name": "Patrick Mahomes",
        "market": "PASS_YDS",
        "position": "OVER",
        "line": 268.5,
        "books": [{"book": "DraftKings", "odds": -110}],
    }
    assert validate_player_prop_record(valid_player_prop) == []

    invalid_player_prop = dict(valid_player_prop)
    del invalid_player_prop["player_name"]
    assert len(validate_player_prop_record(invalid_player_prop)) > 0

    # Invalid normalized dataset
    invalid_record = [{"league": "NFL", "market": "TOTAL"}]  # Missing required keys
    assert len(validate_normalized_dataset(invalid_record, dataset_type="games")) > 0


def test_iso_datetime_parser():
    dt = parse_iso_datetime("2026-09-13T17:00:00+00:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 13
    assert dt.hour == 17

    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None
    assert parse_iso_datetime("invalid-date") is None


def test_safe_write_json_and_winerror_retry(tmp_path: Path):
    target = tmp_path / "test_out.json"
    data = {"status": "ok", "items": [1, 2, 3]}

    safe_write_json(target, data)
    assert target.exists()

    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data

    # Test retry on simulated WinError 32
    attempt_counter = 0
    real_replace = os.replace

    def mock_replace(src, dst):
        nonlocal attempt_counter
        attempt_counter += 1
        if attempt_counter == 1:
            err = OSError("File lock collision")
            err.winerror = 32
            raise err
        # Succeed on 2nd attempt using the real underlying replace
        real_replace(src, dst)

    with patch("os.replace", side_effect=mock_replace):
        safe_write_json(target, {"retry_success": True}, delay=0.01)
        with open(target, "r", encoding="utf-8") as f:
            updated = json.load(f)
        assert updated == {"retry_success": True}
        assert attempt_counter == 2


@pytest.mark.skipif(not HAS_PIPELINE, reason="outlier_nfl.pipeline not yet implemented in M1")
def test_pipeline_run_end_to_end(tmp_path: Path, mock_api_client):
    pipeline = NflPipeline(client=mock_api_client, data_dir=tmp_path)
    summary = pipeline.run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR)

    assert summary["status"] == "OK"
    assert summary["game_lines_count"] > 0
    assert summary["player_props_count"] > 0
    assert summary["spreads_count"] > 0
    assert summary["totals_count"] > 0
    assert summary["team_totals_count"] > 0
    assert summary["props_count"] > 0

    # Verify persisted files
    games_file = tmp_path / "NFL" / "normalized" / "nfl_games_latest.json"
    props_file = tmp_path / "NFL" / "normalized" / "nfl_props_latest.json"

    assert games_file.exists(), f"Expected {games_file} to exist"
    assert props_file.exists(), f"Expected {props_file} to exist"

    with open(games_file, "r", encoding="utf-8") as f:
        games_payload = json.load(f)
    with open(props_file, "r", encoding="utf-8") as f:
        props_payload = json.load(f)

    assert "records" in games_payload
    assert "records" in props_payload

    game_records = games_payload["records"]
    prop_records = props_payload["records"]

    # Verify market coverage inside persisted files
    assert any(r.get("market") == "SPREAD" for r in game_records)
    assert any(r.get("market") == "TOTAL" for r in game_records)
    assert any(r.get("market_type") == "TEAM_PROP" for r in game_records)

    assert any(r.get("market") == "PASS_YDS" for r in prop_records)
    assert any(r.get("market") == "RUSH_YDS" for r in prop_records)
    assert any(r.get("market") == "REC_YDS" for r in prop_records)


@pytest.mark.skipif(not HAS_PIPELINE, reason="outlier_nfl.pipeline not yet implemented in M1")
def test_pipeline_empty_schedule_graceful_degradation(tmp_path: Path, mock_api_client):
    mock_api_client.fetch_schedule.return_value = {"events": []}
    pipeline = NflPipeline(client=mock_api_client, data_dir=tmp_path)

    summary = pipeline.run(date="2026-09-13")
    assert summary["status"] == "OK"
    assert summary["game_lines_count"] == 0
    assert summary["player_props_count"] == 0
