"""Empirical adversarial stress-test suite for outlier_nfl Milestone 1.

Stress-tests:
1. Schema validation gates against truncated, missing-field, and type-violated payloads.
2. Atomic file operations (safe_write_json, _replace_with_retry) under real Windows lock contention.
3. Domain models immutability, hashability, and null/empty odds handling.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import threading
import time
from typing import Any
import pytest

from outlier_nfl.models import (
    BookPrice,
    NflGameLine,
    NflPlayerProp,
)
from outlier_nfl.schema import (
    validate_event_markets_payload,
    validate_game_line_record,
    validate_normalized_dataset,
    validate_player_prop_record,
    validate_player_props_payload,
    validate_schedule_payload,
)
from outlier_nfl.utils import (
    _replace_with_retry,
    format_signed_line,
    safe_read_json,
    safe_write_json,
    to_eastern_date,
)


# =============================================================================
# PART 1: SCHEMA VALIDATION GATES STRESS-TESTS
# =============================================================================


class TestScheduleValidationGate:
    """Stress-test validate_schedule_payload."""

    @pytest.mark.parametrize("bad_payload", [None, 123, "string", [], True, 3.14])
    def test_rejects_non_dict_payload(self, bad_payload: Any):
        errs = validate_schedule_payload(bad_payload)
        assert len(errs) == 1
        assert "Schedule payload must be a JSON object" in errs[0]

    def test_missing_events_field(self):
        errs = validate_schedule_payload({})
        assert len(errs) == 1
        assert "missing 'events' field" in errs[0]

    @pytest.mark.parametrize("bad_events", ["not_a_list", 123, {}, True])
    def test_events_not_a_list(self, bad_events: Any):
        errs = validate_schedule_payload({"events": bad_events})
        assert len(errs) == 1
        assert "'events' field must be a list" in errs[0]

    def test_empty_events_list_is_valid(self):
        # An empty slate (e.g. offseason) should be valid and return zero errors
        errs = validate_schedule_payload({"events": []})
        assert errs == []

    def test_corrupted_event_items(self):
        payload = {"events": [None, 123, "not_dict", []]}
        errs = validate_schedule_payload(payload)
        assert len(errs) == 4
        for idx in range(4):
            assert f"Event at index {idx} is not an object" in errs[idx]

    def test_missing_event_id(self):
        payload = {"events": [{"home": {"name": "KC"}, "away": {"name": "BAL"}}]}
        errs = validate_schedule_payload(payload)
        assert len(errs) == 1
        assert "missing 'eventId' or 'id'" in errs[0]

    def test_missing_or_invalid_team_objects(self):
        # Test missing home, away, non-dict home, and empty dict home
        payload = {
            "events": [
                {"eventId": "e1", "home": None, "away": {"name": "BAL"}},
                {"eventId": "e2", "home": "Kansas City", "away": 123},
                {"eventId": "e3", "home": {}, "away": {}},
            ]
        }
        errs = validate_schedule_payload(payload)
        assert len(errs) == 5
        assert any("e1 missing valid 'home'" in e for e in errs)
        assert any("e2 missing valid 'home'" in e for e in errs)
        assert any("e2 missing valid 'away'" in e for e in errs)
        assert any("e3 missing valid 'home'" in e for e in errs)
        assert any("e3 missing valid 'away'" in e for e in errs)

    def test_valid_schedule_payload(self):
        payload = {
            "events": [
                {
                    "eventId": "event_100",
                    "home": {"name": "Kansas City Chiefs", "teamId": "KC"},
                    "away": {"name": "Baltimore Ravens", "teamId": "BAL"},
                    "startTime": "2026-09-10T20:20:00Z",
                }
            ]
        }
        errs = validate_schedule_payload(payload)
        assert errs == []


class TestEventMarketsValidationGate:
    """Stress-test validate_event_markets_payload."""

    @pytest.mark.parametrize("bad_payload", [None, 123, "bad", [], False])
    def test_rejects_non_dict_payload(self, bad_payload: Any):
        errs = validate_event_markets_payload(bad_payload)
        assert len(errs) == 1
        assert "must be a JSON object" in errs[0]

    def test_missing_markets_field(self):
        errs = validate_event_markets_payload({})
        assert len(errs) == 1
        assert "missing 'markets' field" in errs[0]

    def test_markets_not_a_list(self):
        errs = validate_event_markets_payload({"markets": "invalid"})
        assert len(errs) == 1
        assert "'markets' field must be a list" in errs[0]

    def test_empty_markets_list_is_valid(self):
        errs = validate_event_markets_payload({"markets": []})
        assert errs == []

    def test_corrupted_market_items(self):
        payload = {
            "markets": [
                None,
                {"outcomes": []},  # missing marketId
                {"marketId": "m1"},  # missing outcomes
                {"marketId": "m2", "outcomes": "not_a_list"},
            ]
        }
        errs = validate_event_markets_payload(payload)
        assert len(errs) == 4
        assert "Market at index 0 is not an object" in errs[0]
        assert "missing 'marketId'" in errs[1]
        assert "missing or invalid 'outcomes' list" in errs[2]
        assert "missing or invalid 'outcomes' list" in errs[3]

    def test_valid_markets_payload(self):
        payload = {
            "markets": [
                {
                    "marketId": "m_spread_1",
                    "proposition": "SPREAD",
                    "outcomes": [{"id": "o1", "position": "HOME", "line": -3.5}],
                }
            ]
        }
        errs = validate_event_markets_payload(payload)
        assert errs == []


class TestPlayerPropsValidationGate:
    """Stress-test validate_player_props_payload."""

    @pytest.mark.parametrize("bad_payload", [None, 999, "abc", []])
    def test_rejects_non_dict_payload(self, bad_payload: Any):
        errs = validate_player_props_payload(bad_payload)
        assert len(errs) == 1
        assert "must be a JSON object" in errs[0]

    def test_missing_props_field(self):
        errs = validate_player_props_payload({})
        assert len(errs) == 1
        assert "missing 'props' field" in errs[0]

    def test_props_not_a_list(self):
        errs = validate_player_props_payload({"props": {"item": 1}})
        assert len(errs) == 1
        assert "'props' field must be a list" in errs[0]

    def test_empty_props_list_is_valid(self):
        errs = validate_player_props_payload({"props": []})
        assert errs == []

    def test_corrupted_prop_items(self):
        payload = {
            "props": [
                None,
                {},  # missing outcome
                {"outcome": "not_a_dict"},
                {"outcome": {"position": "OVER"}},  # missing eventId
                {"outcome": {"eventId": "e1"}},  # missing position
            ]
        }
        errs = validate_player_props_payload(payload)
        assert len(errs) == 5
        assert "Player prop item at index 0 is not an object" in errs[0]
        assert "missing valid 'outcome' object" in errs[1]
        assert "missing valid 'outcome' object" in errs[2]
        assert "missing outcome 'eventId'" in errs[3]
        assert "missing outcome 'position'" in errs[4]

    def test_valid_props_payload(self):
        payload = {
            "props": [
                {
                    "outcome": {
                        "eventId": "e_100",
                        "position": "OVER",
                        "proposition": "PASSING_YARDS",
                    }
                }
            ]
        }
        assert validate_player_props_payload(payload) == []


class TestGameLineRecordValidationGate:
    """Stress-test validate_game_line_record."""

    @pytest.fixture
    def valid_record_dict(self) -> dict[str, Any]:
        return {
            "event_id": "event_1",
            "event_starts_at": "2026-09-10T20:20:00Z",
            "matchup": "BAL @ KC",
            "home_team": "KC",
            "away_team": "BAL",
            "market_type": "GAMELINE",
            "market": "SPREAD",
            "proposition": "SPREAD",
            "position": "HOME",
            "line": -3.5,
            "signed_line": "-3.5",
            "selection": "KC -3.5",
            "team": "KC",
            "books": [{"book": "DraftKings", "odds": -110, "odds_raw": "-110"}],
            "best_odds": -110,
            "implied_probability": 52.381,
            "scope": "full_game",
        }

    @pytest.mark.parametrize("bad_record", [None, "bad", 123, []])
    def test_rejects_non_dict_record(self, bad_record: Any):
        errs = validate_game_line_record(bad_record)
        assert len(errs) == 1
        assert "must be a dict or NflGameLine instance" in errs[0]

    @pytest.mark.parametrize(
        "missing_field",
        [
            "event_id",
            "matchup",
            "home_team",
            "away_team",
            "market_type",
            "market",
            "position",
            "books",
        ],
    )
    def test_missing_required_fields(self, valid_record_dict: dict[str, Any], missing_field: str):
        record = dict(valid_record_dict)
        del record[missing_field]
        errs = validate_game_line_record(record)
        assert any(f"missing required field '{missing_field}'" in e for e in errs)

    def test_invalid_market_type(self, valid_record_dict: dict[str, Any]):
        record = dict(valid_record_dict, market_type="PLAYER_PROP")
        errs = validate_game_line_record(record)
        assert any("Invalid market_type 'PLAYER_PROP'" in e for e in errs)

    def test_invalid_position(self, valid_record_dict: dict[str, Any]):
        record = dict(valid_record_dict, position="YES")
        errs = validate_game_line_record(record)
        assert any("Invalid position 'YES'" in e for e in errs)

    def test_invalid_line_type(self, valid_record_dict: dict[str, Any]):
        record = dict(valid_record_dict, line="not_a_number")
        errs = validate_game_line_record(record)
        assert any("must be numeric or None" in e for e in errs)

    @pytest.mark.parametrize("bad_ip", [-5.0, 105.0, "50%"])
    def test_invalid_implied_probability(self, valid_record_dict: dict[str, Any], bad_ip: Any):
        record = dict(valid_record_dict, implied_probability=bad_ip)
        errs = validate_game_line_record(record)
        assert any("implied_probability" in e and "percentage between 0 and 100" in e for e in errs)

    @pytest.mark.parametrize("valid_ip", [0.0, 50.0, 100.0, None])
    def test_valid_implied_probability_boundaries(self, valid_record_dict: dict[str, Any], valid_ip: Any):
        record = dict(valid_record_dict, implied_probability=valid_ip)
        assert validate_game_line_record(record) == []

    def test_books_not_a_list(self, valid_record_dict: dict[str, Any]):
        record = dict(valid_record_dict, books="DraftKings -110")
        errs = validate_game_line_record(record)
        assert any("Field 'books' must be a list" in e for e in errs)

    def test_book_entry_missing_fields(self, valid_record_dict: dict[str, Any]):
        record = dict(valid_record_dict, books=[{"book": "DK"}])  # missing odds
        errs = validate_game_line_record(record)
        assert any("missing 'book' or 'odds'" in e for e in errs)

    def test_adversarial_non_dict_book_entry_behavior(self, valid_record_dict: dict[str, Any]):
        """Adversarial check: what happens if books contains non-dict items without to_dict?

        Healed: validate_game_line_record cleanly handles non-dict items and returns validation errors.
        """
        record = dict(valid_record_dict, books=["DraftKings -110"])
        errs = validate_game_line_record(record)
        assert len(errs) > 0
        assert any("not a valid dict or BookPrice" in e for e in errs)

    def test_valid_nfl_game_line_instance(self, valid_record_dict: dict[str, Any]):
        book = BookPrice(book="DraftKings", odds=-110, odds_raw="-110", decimal=1.909)
        line = NflGameLine(
            event_id="e1",
            event_starts_at="2026-09-10T20:20:00Z",
            matchup="BAL @ KC",
            home_team="KC",
            away_team="BAL",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=-3.5,
            signed_line="-3.5",
            selection="KC -3.5",
            team="KC",
            books=[book],
            best_odds=-110,
            implied_probability=52.381,
        )
        errs = validate_game_line_record(line)
        assert errs == []


class TestPlayerPropRecordValidationGate:
    """Stress-test validate_player_prop_record."""

    @pytest.fixture
    def valid_prop_dict(self) -> dict[str, Any]:
        return {
            "event_id": "event_1",
            "event_starts_at": "2026-09-10T20:20:00Z",
            "matchup": "BAL @ KC",
            "team": "KC",
            "opponent": "BAL",
            "player_name": "Patrick Mahomes",
            "player_id": "mahomes_1",
            "market": "PASS_YDS",
            "market_raw": "PASSING_YARDS",
            "position": "OVER",
            "line": 265.5,
            "books": [{"book": "FanDuel", "odds": -115, "odds_raw": "-115"}],
            "best_odds": -115,
            "implied_probability": 53.488,
        }

    @pytest.mark.parametrize("bad_record", [None, "bad", 123])
    def test_rejects_non_dict_record(self, bad_record: Any):
        errs = validate_player_prop_record(bad_record)
        assert len(errs) == 1
        assert "must be a dict or NflPlayerProp instance" in errs[0]

    @pytest.mark.parametrize(
        "missing_field",
        [
            "event_id",
            "matchup",
            "player_name",
            "market",
            "position",
            "line",
            "books",
        ],
    )
    def test_missing_required_fields(self, valid_prop_dict: dict[str, Any], missing_field: str):
        record = dict(valid_prop_dict)
        del record[missing_field]
        errs = validate_player_prop_record(record)
        assert any(f"missing required field '{missing_field}'" in e for e in errs)

    def test_invalid_position(self, valid_prop_dict: dict[str, Any]):
        record = dict(valid_prop_dict, position="HOME")
        errs = validate_player_prop_record(record)
        assert any("Invalid position 'HOME' for player prop" in e for e in errs)

    def test_invalid_line_type(self, valid_prop_dict: dict[str, Any]):
        record = dict(valid_prop_dict, line="not_a_float")
        errs = validate_player_prop_record(record)
        assert any("must be numeric" in e for e in errs)

    def test_adversarial_non_dict_book_entry_behavior(self, valid_prop_dict: dict[str, Any]):
        """Adversarial check: what happens if books contains non-dict items without to_dict?

        Healed: validate_player_prop_record cleanly handles non-dict items and returns validation errors.
        """
        record = dict(valid_prop_dict, books=[None])
        errs = validate_player_prop_record(record)
        assert len(errs) > 0
        assert any("not a valid dict or BookPrice" in e for e in errs)

    def test_valid_nfl_player_prop_instance(self, valid_prop_dict: dict[str, Any]):
        book = BookPrice(book="FanDuel", odds=-115, odds_raw="-115", decimal=1.87)
        prop = NflPlayerProp(
            event_id="e1",
            event_starts_at="2026-09-10T20:20:00Z",
            matchup="BAL @ KC",
            team="KC",
            opponent="BAL",
            player_name="Patrick Mahomes",
            player_id="mahomes_1",
            market="PASS_YDS",
            market_raw="PASSING_YARDS",
            position="OVER",
            line=265.5,
            books=[book],
            best_odds=-115,
            implied_probability=53.488,
        )
        assert validate_player_prop_record(prop) == []


class TestDatasetValidationGate:
    """Stress-test validate_normalized_dataset."""

    def test_rejects_non_list_dataset(self):
        errs = validate_normalized_dataset({"record": 1})  # type: ignore
        assert len(errs) == 1
        assert "Dataset must be a list of records" in errs[0]

    def test_empty_dataset_is_valid(self):
        assert validate_normalized_dataset([], dataset_type="games") == []
        assert validate_normalized_dataset([], dataset_type="props") == []

    def test_mixed_dataset_reports_indexed_errors(self):
        records = [
            {"event_id": "e1"},  # missing many fields
            {"event_id": "e2"},  # missing many fields
        ]
        errs = validate_normalized_dataset(records, dataset_type="games")
        assert any(e.startswith("Record 0:") for e in errs)
        assert any(e.startswith("Record 1:") for e in errs)


# =============================================================================
# PART 2: ATOMIC FILE OPERATIONS & WINDOWS LOCK MITIGATION
# =============================================================================


class TestAtomicFileOperations:
    """Stress-test _replace_with_retry and safe_write_json under lock contention."""

    def test_replace_with_retry_succeeds_under_transient_lock(self, tmp_path: Path):
        """Simulate a transient Windows file lock (WinError 32) released during retries."""
        src = tmp_path / "test_src.json"
        dst = tmp_path / "test_dst.json"

        src.write_text('{"version": 2}', encoding="utf-8")
        dst.write_text('{"version": 1}', encoding="utf-8")

        # Open dst in read mode to lock it on Windows
        lock_holder = open(dst, "r", encoding="utf-8")
        lock_released = threading.Event()

        def release_lock_delayed():
            time.sleep(0.3)
            lock_holder.close()
            lock_released.set()

        t = threading.Thread(target=release_lock_delayed)
        t.start()

        # _replace_with_retry should encounter lock, retry, and succeed after release
        start_time = time.time()
        _replace_with_retry(src, dst, retries=5, delay=0.15)
        elapsed = time.time() - start_time

        t.join()
        assert lock_released.is_set()
        assert elapsed >= 0.25
        assert not src.exists()
        assert dst.exists()
        assert json.loads(dst.read_text(encoding="utf-8")) == {"version": 2}

    def test_replace_with_retry_fails_when_lock_held_permanently(self, tmp_path: Path):
        """Verify that permanent lock cleanly raises OSError without deleting src."""
        src = tmp_path / "test_src_perm.json"
        dst = tmp_path / "test_dst_perm.json"

        src.write_text('{"version": 2}', encoding="utf-8")
        dst.write_text('{"version": 1}', encoding="utf-8")

        # Hold lock permanently throughout all retries
        with open(dst, "r", encoding="utf-8"):
            with pytest.raises(OSError) as exc_info:
                _replace_with_retry(src, dst, retries=3, delay=0.05)
            assert exc_info.type in (PermissionError, OSError)

        # src must still exist (no data loss)
        assert src.exists()
        assert dst.exists()
        assert json.loads(dst.read_text(encoding="utf-8")) == {"version": 1}

    def test_safe_write_json_under_lock_and_cleanup(self, tmp_path: Path):
        """Verify safe_write_json writes atomically, retries on lock, and cleans .tmp."""
        target = tmp_path / "safe_target.json"
        target.write_text('{"count": 0}', encoding="utf-8")

        lock = open(target, "r", encoding="utf-8")

        def delayed_unlock():
            time.sleep(0.25)
            lock.close()

        threading.Thread(target=delayed_unlock).start()

        safe_write_json(target, {"count": 100}, retries=5, delay=0.1)

        # Target should be updated
        data = safe_read_json(target)
        assert data == {"count": 100}

        # Sibling temp files must be completely cleaned up
        temp_files = list(tmp_path.glob(".*.tmp"))
        assert temp_files == []

    def test_safe_write_json_concurrent_stress(self, tmp_path: Path):
        """Stress test concurrent writes to distinct files and verify persistence."""
        def write_worker(idx: int):
            path = tmp_path / f"worker_{idx}.json"
            payload = {"worker": idx, "timestamp": time.time()}
            safe_write_json(path, payload, retries=8, delay=0.05)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(write_worker, i) for i in range(16)]
            for fut in futures:
                fut.result()

        # Check all 16 files written cleanly and readable
        for i in range(16):
            res = safe_read_json(tmp_path / f"worker_{i}.json")
            assert res is not None and res["worker"] == i

        # Check no dangling temp files
        temp_files = list(tmp_path.glob(".*.tmp"))
        assert temp_files == []

    def test_safe_read_json_unescaped_control_characters(self, tmp_path: Path):
        """Test safe_read_json tolerates unescaped control chars (strict=False)."""
        file_path = tmp_path / "control_chars.json"
        # Write raw bytes with unescaped tab and newline inside a JSON string value
        raw_content = '{"notes": "Line 1\x09Tabbed\x0aLine 2"}'
        file_path.write_text(raw_content, encoding="utf-8")

        # standard json.loads would fail with strict=True
        data = safe_read_json(file_path)
        assert data is not None
        assert "notes" in data

    def test_safe_read_json_non_existent_and_corrupt(self, tmp_path: Path):
        assert safe_read_json(tmp_path / "non_existent.json", default={"fallback": 1}) == {"fallback": 1}

        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("NOT VALID JSON", encoding="utf-8")
        assert safe_read_json(corrupt, default=None) is None

    def test_to_eastern_date_night_games(self):
        """NFL night games kicking off at 00:15-01:15 UTC must resolve to previous Eastern date."""
        # TNF 8:15 PM ET on 2026-09-10 is 2026-09-11 00:15 UTC
        assert to_eastern_date("2026-09-11T00:15:00Z") == "2026-09-10"
        # Late night West Coast game at 03:30 UTC is still 2026-09-10 Eastern
        assert to_eastern_date("2026-09-11T03:30:00Z") == "2026-09-10"
        # Sunday 1:00 PM ET on 2026-09-13 is 2026-09-13 17:00 UTC
        assert to_eastern_date("2026-09-13T17:00:00Z") == "2026-09-13"

        # Edge cases
        assert to_eastern_date(None) is None
        assert to_eastern_date("") is None
        assert to_eastern_date("invalid-timestamp") is None

    def test_format_signed_line(self):
        assert format_signed_line(-3.5) == "-3.5"
        assert format_signed_line(3.5) == "+3.5"
        assert format_signed_line(0.0) == "PK"
        assert format_signed_line(-0.0) == "PK"
        assert format_signed_line(None) is None


# =============================================================================
# PART 3: DOMAIN MODELS IMMUTABILITY, HASHABILITY & NULL ODDS
# =============================================================================


class TestDomainModelsSafety:
    """Stress-test domain models for immutability, safety with nulls, and hashability."""

    def test_book_price_immutability(self):
        bp = BookPrice(book="FanDuel", odds=-110, odds_raw="-110", decimal=1.909)
        with pytest.raises(FrozenInstanceError):
            bp.odds = -105  # type: ignore

    def test_book_price_hashability(self):
        bp1 = BookPrice(book="FanDuel", odds=-110, odds_raw="-110")
        bp2 = BookPrice(book="FanDuel", odds=-110, odds_raw="-110")
        assert hash(bp1) == hash(bp2)
        price_set = {bp1, bp2}
        assert len(price_set) == 1

    def test_nfl_game_line_immutability(self):
        gl = NflGameLine(
            event_id="e1",
            event_starts_at="2026-09-10T20:20:00Z",
            matchup="BAL @ KC",
            home_team="KC",
            away_team="BAL",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=-3.5,
            signed_line="-3.5",
            selection="KC -3.5",
            team="KC",
            books=[],
            best_odds=-110,
            implied_probability=52.381,
        )
        with pytest.raises(FrozenInstanceError):
            gl.selection = "KC -4.0"  # type: ignore
        with pytest.raises(FrozenInstanceError):
            gl.line = -4.0  # type: ignore

    def test_nfl_player_prop_immutability(self):
        pp = NflPlayerProp(
            event_id="e1",
            event_starts_at="2026-09-10T20:20:00Z",
            matchup="BAL @ KC",
            team="KC",
            opponent="BAL",
            player_name="Patrick Mahomes",
            player_id="mahomes_1",
            market="PASS_YDS",
            market_raw="PASSING_YARDS",
            position="OVER",
            line=265.5,
            books=[],
            best_odds=-115,
            implied_probability=53.488,
        )
        with pytest.raises(FrozenInstanceError):
            pp.line = 270.5  # type: ignore
        with pytest.raises(FrozenInstanceError):
            pp.player_name = "P. Mahomes"  # type: ignore

    def test_models_handle_null_and_empty_odds_safely(self):
        """Ensure models can represent unpriced or off-the-board markets cleanly."""
        gl = NflGameLine(
            event_id="e1",
            event_starts_at=None,
            matchup="BAL @ KC",
            home_team="KC",
            away_team="BAL",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=None,
            signed_line=None,
            selection="KC",
            team="KC",
            books=[],
            best_odds=None,
            implied_probability=None,
        )
        d = gl.to_dict()
        assert d["books"] == []
        assert d["best_odds"] is None
        assert d["implied_probability"] is None
        # Schema validation should accept off-the-board / empty books record
        errs = validate_game_line_record(gl)
        assert errs == []

        pp = NflPlayerProp(
            event_id="e1",
            event_starts_at=None,
            matchup="BAL @ KC",
            team="KC",
            opponent="BAL",
            player_name="Isiah Pacheco",
            player_id=None,
            market="RUSH_YDS",
            market_raw="RUSHING_YARDS",
            position="OVER",
            line=62.5,
            books=[],
            best_odds=None,
            implied_probability=None,
        )
        pp_d = pp.to_dict()
        assert pp_d["books"] == []
        assert pp_d["best_odds"] is None
        assert validate_player_prop_record(pp) == []

    def test_adversarial_dataclass_hashability_with_list(self):
        """Verify that NflGameLine is cleanly hashable with tuple books."""
        gl = NflGameLine(
            event_id="e1",
            event_starts_at=None,
            matchup="BAL @ KC",
            home_team="KC",
            away_team="BAL",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=-3.5,
            signed_line="-3.5",
            selection="KC -3.5",
            team="KC",
            books=[BookPrice("DK", -110, "-110")],
            best_odds=-110,
            implied_probability=52.381,
        )
        h = hash(gl)
        assert isinstance(h, int)
        assert gl in {gl}

    def test_dataclass_serialization_roundtrip(self, tmp_path: Path):
        """Verify full round-trip from dataclass to json on disk and back."""
        bp = BookPrice(book="DraftKings", odds=-110, odds_raw="-110", decimal=1.909)
        original = NflGameLine(
            event_id="e1",
            event_starts_at="2026-09-10T20:20:00Z",
            matchup="BAL @ KC",
            home_team="KC",
            away_team="BAL",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=-3.5,
            signed_line="-3.5",
            selection="KC -3.5",
            team="KC",
            books=[bp],
            best_odds=-110,
            implied_probability=52.381,
        )
        file_path = tmp_path / "game_line.json"
        safe_write_json(file_path, original.to_dict())

        loaded = safe_read_json(file_path)
        assert loaded["event_id"] == "e1"
        assert loaded["books"][0]["book"] == "DraftKings"
        assert loaded["books"][0]["odds"] == -110

        # Validate loaded dict through schema gate
        assert validate_game_line_record(loaded) == []

    def test_adversarial_models_list_mutation(self):
        """Verify that books is coerced to an immutable tuple, preventing in-place mutations."""
        gl = NflGameLine(
            event_id="e1",
            event_starts_at=None,
            matchup="BAL @ KC",
            home_team="KC",
            away_team="BAL",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=-3.5,
            signed_line="-3.5",
            selection="KC -3.5",
            team="KC",
            books=[BookPrice("DK", -110, "-110")],
            best_odds=-110,
            implied_probability=52.381,
        )
        assert len(gl.books) == 1
        assert isinstance(gl.books, tuple)
        with pytest.raises(AttributeError):
            gl.books.append(BookPrice("FD", -105, "-105"))  # type: ignore[attr-defined]


class TestAdversarialEdgeCases:
    """Explicit tests capturing discovered edge cases and defects."""

    def test_adversarial_schema_accepts_boolean_values(self):
        """Verify that validate_game_line_record rejects booleans in numeric fields."""
        rec = {
            "event_id": "e1",
            "matchup": "BAL @ KC",
            "home_team": "KC",
            "away_team": "BAL",
            "market_type": "GAMELINE",
            "market": "SPREAD",
            "position": "HOME",
            "line": True,  # should be rejected as non-numeric
            "implied_probability": True,  # should be rejected as non-numeric
            "books": [],
        }
        errs = validate_game_line_record(rec)
        assert any("Line 'True' must be numeric or None" in e for e in errs)
        assert any("implied_probability 'True' must be a percentage between 0 and 100" in e for e in errs)

    def test_adversarial_schema_accepts_nan_line(self):
        """Verify that line=float('nan') is rejected by schema validation."""
        rec = {
            "event_id": "e1",
            "matchup": "BAL @ KC",
            "home_team": "KC",
            "away_team": "BAL",
            "market_type": "GAMELINE",
            "market": "SPREAD",
            "position": "HOME",
            "line": float("nan"),
            "books": [],
        }
        errs = validate_game_line_record(rec)
        assert any("must be numeric" in e for e in errs)

    def test_schema_rejects_inf_and_nan(self):
        """Verify that line and implied_probability reject non-finite numbers (NaN, Inf, -Inf)."""
        for bad_val in (float("inf"), float("-inf"), float("nan")):
            rec = {
                "event_id": "e1",
                "matchup": "BAL @ KC",
                "home_team": "KC",
                "away_team": "BAL",
                "market_type": "GAMELINE",
                "market": "SPREAD",
                "position": "HOME",
                "line": bad_val,
                "implied_probability": 50.0,
                "books": [],
            }
            errs = validate_game_line_record(rec)
            assert any("must be numeric" in e for e in errs)

            rec_bad_ip = {
                "event_id": "e1",
                "matchup": "BAL @ KC",
                "home_team": "KC",
                "away_team": "BAL",
                "market_type": "GAMELINE",
                "market": "SPREAD",
                "position": "HOME",
                "line": -3.5,
                "implied_probability": bad_val,
                "books": [],
            }
            errs_ip = validate_game_line_record(rec_bad_ip)
            assert any("implied_probability" in e for e in errs_ip)

    def test_safe_write_json_concurrent_same_file(self, tmp_path: Path):
        """Run 30 threads simultaneously writing to the exact same file path."""
        target = tmp_path / "concurrent_target.json"
        errors: list[Exception] = []

        def worker(thread_idx: int) -> None:
            try:
                for i in range(5):
                    safe_write_json(target, {"thread": thread_idx, "iteration": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Encountered concurrency errors: {errors}"
        assert target.exists()
        loaded = safe_read_json(target)
        assert isinstance(loaded, dict)
        assert "thread" in loaded

    def test_safe_read_json_retry_under_lock(self, tmp_path: Path):
        """Hold a lock for 0.15s while invoking safe_read_json on another thread."""
        target = tmp_path / "locked_read.json"
        safe_write_json(target, {"status": "ok", "val": 42})

        lock_holder = open(target, "r", encoding="utf-8")

        def delayed_release():
            time.sleep(0.15)
            lock_holder.close()

        t = threading.Thread(target=delayed_release)
        t.start()

        loaded = safe_read_json(target, retries=5, delay=0.08)
        t.join()
        assert loaded == {"status": "ok", "val": 42}

