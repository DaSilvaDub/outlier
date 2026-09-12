"""Empirical Challenger 2 Adversarial Stress Suite for Milestone 1 Iteration 2.

Exhaustively verifies:
1. Safe book entry validation without unhandled AttributeError on non-dict objects.
2. Rejection of bool, NaN, and Inf in numeric fields.
3. Concurrent file write safety without temp path collisions (multi-thread and multi-process).
4. safe_read_json retry under Windows file locks.
5. Dataclass hashability, immutability, and set/dict deduplication with tuple books.
"""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
import math
import multiprocessing
import os
from pathlib import Path
import threading
import time
from typing import Any
import pytest

from outlier_nfl.models import BookPrice, NflEvent, NflGameLine, NflPlayerProp
from outlier_nfl.schema import (
    _validate_book_entry,
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


# Helper class for testing custom objects with .to_dict()
class CustomPriceWithToDict:
    def __init__(self, book: str, odds: int, decimal: float | None = None):
        self.book = book
        self.odds = odds
        self.decimal = decimal

    def to_dict(self) -> dict[str, Any]:
        return {"book": self.book, "odds": self.odds, "decimal": self.decimal}


class ExplodingToDict:
    def to_dict(self):
        raise ZeroDivisionError("simulated crash inside to_dict()")


class NonDictReturningToDict:
    def to_dict(self):
        return ["not", "a", "dict"]


class PropertyExplodingClass:
    @property
    def to_dict(self):
        raise RuntimeError("property access boom")


# =============================================================================
# SUITE 1: ADVERSARIAL BOOK ENTRY & SCHEMA GATES VALIDATION
# =============================================================================

class TestAdversarialBookValidation:
    """Stress-test _validate_book_entry and schema gates against non-dict objects."""

    @pytest.mark.parametrize(
        "non_dict_book",
        [
            None,
            "DraftKings",
            12345,
            3.1415,
            True,
            False,
            [],
            [1, 2, 3],
            ("tuple", "entry"),
            object(),
        ],
    )
    def test_validate_book_entry_rejects_non_dict_gracefully(self, non_dict_book: Any):
        errors = _validate_book_entry(non_dict_book, 0)
        assert len(errors) > 0
        assert "not a valid dict or BookPrice instance" in errors[0]

    def test_validate_book_entry_with_valid_to_dict(self):
        obj = CustomPriceWithToDict("FanDuel", -110, 1.91)
        assert _validate_book_entry(obj, 0) == []

    def test_validate_book_entry_with_exploding_to_dict(self):
        obj = ExplodingToDict()
        errors = _validate_book_entry(obj, 0)
        assert len(errors) == 1
        assert "to_dict() raised exception" in errors[0]

    def test_validate_book_entry_with_non_dict_return(self):
        obj = NonDictReturningToDict()
        errors = _validate_book_entry(obj, 0)
        assert len(errors) == 1
        assert "did not produce a dictionary" in errors[0]

    def test_validate_book_entry_property_boom(self):
        obj = PropertyExplodingClass()
        # In Python, hasattr catches Exception (AttributeError/RuntimeError etc) and returns False
        errors = _validate_book_entry(obj, 0)
        assert len(errors) == 1
        assert "not a valid dict or BookPrice instance" in errors[0]

    @pytest.mark.parametrize(
        "bad_book_dict, expected_substr",
        [
            ({"book": "", "odds": -110}, "missing 'book' or 'odds'"),
            ({"book": "   ", "odds": -110}, "missing 'book' or 'odds'"),
            ({"book": None, "odds": -110}, "missing 'book' or 'odds'"),
            ({"book": 123, "odds": -110}, "missing 'book' or 'odds'"),
            ({"book": "DK"}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": None}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": True}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": False}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": "110"}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": 110.0}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": float("nan")}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": float("inf")}, "missing 'book' or 'odds'"),
            ({"book": "DK", "odds": -110, "decimal": True}, "invalid 'decimal' odds"),
            ({"book": "DK", "odds": -110, "decimal": float("nan")}, "invalid 'decimal' odds"),
            ({"book": "DK", "odds": -110, "decimal": float("inf")}, "invalid 'decimal' odds"),
            ({"book": "DK", "odds": -110, "decimal": -1.5}, "invalid 'decimal' odds"),
            ({"book": "DK", "odds": -110, "decimal": 0.0}, "invalid 'decimal' odds"),
            ({"book": "DK", "odds": -110, "decimal": "1.91"}, "invalid 'decimal' odds"),
        ],
    )
    def test_validate_book_entry_corrupt_fields(self, bad_book_dict: dict, expected_substr: str):
        errors = _validate_book_entry(bad_book_dict, 0)
        assert any(expected_substr in e for e in errors)


# =============================================================================
# SUITE 2: ADVERSARIAL NUMERIC BOUNDS & TYPE EXCLUSIONS
# =============================================================================

class TestAdversarialNumericBounds:
    """Stress-test line and implied_probability validation across all gate functions."""

    @pytest.fixture
    def base_game_record(self) -> dict[str, Any]:
        return {
            "event_id": "event_adv_1",
            "matchup": "KC @ BAL",
            "home_team": "BAL",
            "away_team": "KC",
            "market_type": "GAMELINE",
            "market": "SPREAD",
            "position": "HOME",
            "line": -3.5,
            "implied_probability": 52.38,
            "books": [{"book": "DK", "odds": -110}],
        }

    @pytest.fixture
    def base_prop_record(self) -> dict[str, Any]:
        return {
            "event_id": "event_adv_1",
            "matchup": "KC @ BAL",
            "player_name": "Lamar Jackson",
            "market": "RUSH_YDS",
            "position": "OVER",
            "line": 55.5,
            "implied_probability": 54.0,
            "books": [{"book": "FD", "odds": -115}],
        }

    @pytest.mark.parametrize(
        "bad_num",
        [True, False, float("nan"), float("inf"), float("-inf"), "NaN", "3.5", [3.5]],
    )
    def test_game_line_rejects_non_finite_and_bool_line(self, base_game_record: dict, bad_num: Any):
        rec = dict(base_game_record, line=bad_num)
        errors = validate_game_line_record(rec)
        assert any("must be numeric or None" in e for e in errors)

    @pytest.mark.parametrize(
        "bad_ip",
        [True, False, float("nan"), float("inf"), float("-inf"), -0.01, 100.01, -100.0, 999.0, "50.0"],
    )
    def test_game_line_rejects_invalid_implied_probability(self, base_game_record: dict, bad_ip: Any):
        rec = dict(base_game_record, implied_probability=bad_ip)
        errors = validate_game_line_record(rec)
        assert any("percentage between 0 and 100" in e for e in errors)

    @pytest.mark.parametrize("valid_ip", [0.0, 0.001, 50.0, 99.999, 100.0, None])
    def test_game_line_accepts_valid_implied_probability(self, base_game_record: dict, valid_ip: Any):
        rec = dict(base_game_record, implied_probability=valid_ip)
        assert validate_game_line_record(rec) == []

    @pytest.mark.parametrize(
        "bad_prop_line",
        [None, True, False, float("nan"), float("inf"), float("-inf"), "not_a_number"],
    )
    def test_prop_record_rejects_bad_line(self, base_prop_record: dict, bad_prop_line: Any):
        rec = dict(base_prop_record, line=bad_prop_line)
        errors = validate_player_prop_record(rec)
        assert any("Player prop line" in e and "must be numeric" in e for e in errors)

    @pytest.mark.parametrize(
        "bad_prop_ip",
        [True, False, float("nan"), float("inf"), float("-inf"), -1.0, 101.0],
    )
    def test_prop_record_rejects_bad_implied_probability(self, base_prop_record: dict, bad_prop_ip: Any):
        rec = dict(base_prop_record, implied_probability=bad_prop_ip)
        errors = validate_player_prop_record(rec)
        assert any("percentage between 0 and 100" in e for e in errors)


# =============================================================================
# SUITE 3: CONCURRENT FILE WRITES & ATOMIC SAFETY
# =============================================================================

def _standalone_proc_writer(args: tuple[str, int, int]) -> int:
    """Helper top-level function for multiprocessing test."""
    target_path_str, proc_idx, count = args
    target_path = Path(target_path_str)
    for i in range(count):
        safe_write_json(target_path, {"proc": proc_idx, "iter": i, "ts": time.time()})
    return proc_idx


class TestAdversarialConcurrentWrites:
    """Stress-test safe_write_json under extreme multi-threaded and multi-process write contention."""

    def test_massive_multithread_contention_same_file(self, tmp_path: Path):
        """50 threads concurrently hammering the exact same file 10 times each."""
        target = tmp_path / "hammered_concurrent.json"
        errors: list[Exception] = []

        def worker(thread_idx: int):
            try:
                for i in range(10):
                    safe_write_json(
                        target,
                        {"worker": thread_idx, "seq": i, "data": [1, 2, 3]},
                        retries=10,
                        delay=0.03,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent thread writes raised errors: {errors}"
        assert target.exists()

        # Target must be completely valid JSON and readable
        data = safe_read_json(target)
        assert isinstance(data, dict)
        assert "worker" in data and "seq" in data

        # Ensure no dangling temporary files
        leftover_tmp = list(tmp_path.glob(".*.tmp"))
        assert leftover_tmp == [], f"Found orphaned temp files: {leftover_tmp}"

    def test_multiprocess_contention_same_file(self, tmp_path: Path):
        """Separate processes hammering the exact same file."""
        target = tmp_path / "proc_concurrent.json"
        tasks = [(str(target), p_idx, 5) for p_idx in range(4)]

        # Run across processes
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=4, mp_context=ctx) as executor:
            results = list(executor.map(_standalone_proc_writer, tasks))

        assert len(results) == 4
        assert target.exists()

        data = safe_read_json(target)
        assert isinstance(data, dict)
        assert "proc" in data

        leftover_tmp = list(tmp_path.glob(".*.tmp"))
        assert leftover_tmp == []


# =============================================================================
# SUITE 4: WINDOWS FILE LOCK RETRY UNDER CONTENTION
# =============================================================================

class TestAdversarialWindowsLockContention:
    """Stress-test safe_read_json resilience when files are locked."""

    def test_safe_read_json_waits_out_lock(self, tmp_path: Path):
        """Lock file for 0.25s while safe_read_json polls and recovers."""
        target = tmp_path / "lock_recovery.json"
        payload = {"status": "recovered", "timestamp": time.time()}
        safe_write_json(target, payload)

        lock_handle = open(target, "r", encoding="utf-8")
        unlocked = threading.Event()

        def release_lock():
            time.sleep(0.2)
            lock_handle.close()
            unlocked.set()

        t = threading.Thread(target=release_lock)
        t.start()

        # Read should retry and succeed once release_lock runs
        result = safe_read_json(target, default={"status": "failed"}, retries=8, delay=0.05)
        t.join()

        assert unlocked.is_set()
        assert result == payload

    def test_concurrent_read_write_stream(self, tmp_path: Path):
        """Writer and reader running in parallel without corruption."""
        target = tmp_path / "stream_rw.json"
        safe_write_json(target, {"counter": 0})

        stop_event = threading.Event()
        writer_errors: list[Exception] = []
        reader_errors: list[Exception] = []
        read_successes: list[int] = []

        def continuous_writer():
            counter = 1
            while not stop_event.is_set() and counter < 50:
                try:
                    safe_write_json(target, {"counter": counter})
                    counter += 1
                    time.sleep(0.01)
                except Exception as e:
                    writer_errors.append(e)

        def continuous_reader():
            while not stop_event.is_set():
                try:
                    data = safe_read_json(target, default=None)
                    if data is not None:
                        assert isinstance(data, dict)
                        assert "counter" in data
                        read_successes.append(data["counter"])
                    time.sleep(0.005)
                except Exception as e:
                    reader_errors.append(e)

        w = threading.Thread(target=continuous_writer)
        r = threading.Thread(target=continuous_reader)
        w.start()
        r.start()

        w.join(timeout=5.0)
        stop_event.set()
        r.join(timeout=2.0)

        assert writer_errors == []
        assert reader_errors == []
        assert len(read_successes) > 0


# =============================================================================
# SUITE 5: DATACLASS HASHABILITY, IMMUTABILITY & DEDUPLICATION
# =============================================================================

class TestAdversarialDataclassHashability:
    """Stress-test domain models hashability, immutability, and set operations."""

    def test_game_line_hashability_and_deduplication(self):
        b1 = BookPrice(book="FanDuel", odds=-110, odds_raw="-110", decimal=1.91)
        b2 = BookPrice(book="DraftKings", odds=-105, odds_raw="-105", decimal=1.95)

        gl1 = NflGameLine(
            event_id="e100",
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
            books=[b1, b2],  # Passed as list
            best_odds=-105,
            implied_probability=52.38,
        )

        gl2 = NflGameLine(
            event_id="e100",
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
            books=(b1, b2),  # Passed as tuple
            best_odds=-105,
            implied_probability=52.38,
        )

        # 1. Type coercion to tuple
        assert isinstance(gl1.books, tuple)
        assert isinstance(gl2.books, tuple)

        # 2. Hash equality and object equality
        assert hash(gl1) == hash(gl2)
        assert gl1 == gl2

        # 3. Set deduplication
        s = {gl1, gl2}
        assert len(s) == 1

        # 4. Dict mapping
        d = {gl1: "active_line"}
        assert d[gl2] == "active_line"

        # 5. Immutability
        with pytest.raises(FrozenInstanceError):
            gl1.line = -4.0  # type: ignore
        with pytest.raises(FrozenInstanceError):
            gl1.books = ()  # type: ignore
        with pytest.raises(AttributeError):
            gl1.books.append(BookPrice("BetMGM", -110, "-110"))  # type: ignore

    def test_player_prop_hashability_and_deduplication(self):
        b1 = BookPrice(book="FanDuel", odds=-115, odds_raw="-115", decimal=1.87)
        pp1 = NflPlayerProp(
            event_id="e100",
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
            books=[b1],
            best_odds=-115,
            implied_probability=53.49,
        )

        pp2 = NflPlayerProp(
            event_id="e100",
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
            books=(b1,),
            best_odds=-115,
            implied_probability=53.49,
        )

        assert isinstance(pp1.books, tuple)
        assert hash(pp1) == hash(pp2)
        assert pp1 == pp2
        assert len({pp1, pp2}) == 1

        with pytest.raises(FrozenInstanceError):
            pp1.player_name = "P. Mahomes"  # type: ignore

    def test_dataclass_roundtrip_to_dict_and_back(self):
        b1 = BookPrice(book="FanDuel", odds=-110, odds_raw="-110", decimal=1.91)
        gl = NflGameLine(
            event_id="e1",
            event_starts_at=None,
            matchup="KC @ BAL",
            home_team="BAL",
            away_team="KC",
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position="HOME",
            line=-3.5,
            signed_line="-3.5",
            selection="BAL -3.5",
            team="BAL",
            books=[b1],
            best_odds=-110,
            implied_probability=52.38,
        )

        d = gl.to_dict()
        assert isinstance(d["books"], list)
        assert isinstance(d["books"][0], dict)
        assert d["books"][0]["book"] == "FanDuel"

        # Validate with schema gate
        assert validate_game_line_record(d) == []
        assert validate_game_line_record(gl) == []
