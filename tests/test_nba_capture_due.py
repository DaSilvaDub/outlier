import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import outlier_nba.cli as cli
from outlier_nba.track.due import capture_due, read_last_attempt, record_attempt

NOW = datetime(2026, 10, 21, 22, 10, tzinfo=UTC)


def _ledger(tmp_path, rows):
    db = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE market_snapshots (sport TEXT, captured_at TEXT, event_starts_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO market_snapshots VALUES (?, ?, ?)",
            [
                (sport, at.isoformat(), tip if isinstance(tip, str | None) else tip.isoformat())
                for sport, at, tip in rows
            ],
        )
    conn.close()
    return db


def test_missing_ledger_is_due(tmp_path):
    result = capture_due(tmp_path / "absent.sqlite3", NOW)
    assert result.due and result.last_capture is None


def test_ledger_without_the_snapshot_table_is_due(tmp_path):
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    assert capture_due(db, NOW).due


def test_ledger_without_nba_rows_is_due(tmp_path):
    db = _ledger(tmp_path, [("MLB", NOW - timedelta(hours=1), NOW + timedelta(minutes=20))])
    assert capture_due(db, NOW).due


def test_tip_inside_the_lead_without_a_verified_capture_is_due(tmp_path):
    tip = NOW + timedelta(minutes=25)
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), tip)])
    result = capture_due(db, NOW)
    assert result.due and result.next_tip == tip
    assert "no capture" in result.reason


def test_tip_exactly_at_the_lead_is_due(tmp_path):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), NOW + timedelta(minutes=30))])
    assert capture_due(db, NOW).due


def test_tip_beyond_the_lead_waits_even_inside_the_window(tmp_path):
    tip = NOW + timedelta(minutes=31)
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), tip)])
    result = capture_due(db, NOW)
    assert not result.due and result.next_tip == tip


def test_capture_exactly_one_window_before_tip_still_covers_it(tmp_path):
    # clv.verified_close accepts tip - at == 60 minutes, so the gate must too
    tip = NOW + timedelta(minutes=20)
    db = _ledger(tmp_path, [("NBA", tip - timedelta(minutes=60), tip)])
    assert not capture_due(db, NOW).due


def test_capture_just_outside_the_window_does_not_cover(tmp_path):
    tip = NOW + timedelta(minutes=20)
    db = _ledger(tmp_path, [("NBA", tip - timedelta(minutes=61), tip)])
    assert capture_due(db, NOW).due


def test_started_games_do_not_trigger_a_capture(tmp_path):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), NOW - timedelta(minutes=5))])
    assert not capture_due(db, NOW).due


def test_tips_come_from_the_latest_capture_only(tmp_path):
    # an older capture still lists a tip the latest capture no longer carries (rescheduled)
    db = _ledger(
        tmp_path,
        [
            ("NBA", NOW - timedelta(hours=5), NOW + timedelta(minutes=20)),
            ("NBA", NOW - timedelta(hours=2), NOW + timedelta(hours=3)),
        ],
    )
    assert not capture_due(db, NOW).due


def test_unparseable_and_null_tips_are_ignored(tmp_path):
    at = NOW - timedelta(hours=2)
    tip = NOW + timedelta(minutes=20)
    db = _ledger(tmp_path, [("NBA", at, None), ("NBA", at, "not-a-time"), ("NBA", at, tip)])
    result = capture_due(db, NOW)
    assert result.due and result.next_tip == tip


def test_z_suffixed_timestamps_parse(tmp_path):
    tip = "2026-10-21T22:30:00Z"
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), tip)])
    assert capture_due(db, NOW).next_tip == datetime(2026, 10, 21, 22, 30, tzinfo=UTC)


def test_stale_slate_is_due_even_with_no_tip_soon(tmp_path):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=13), NOW + timedelta(days=2))])
    result = capture_due(db, NOW)
    assert result.due and "stale" in result.reason


def test_empty_slate_refresh_is_throttled_by_the_last_attempt(tmp_path):
    # an off day: the last attempt found nothing to write, so rows stay 13h old
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=13), NOW + timedelta(days=2))])
    assert not capture_due(db, NOW, last_attempt=NOW - timedelta(hours=1)).due
    assert capture_due(db, NOW, last_attempt=NOW - timedelta(hours=12)).due


def test_no_prior_capture_is_throttled_by_the_last_attempt(tmp_path):
    db = tmp_path / "absent.sqlite3"
    assert not capture_due(db, NOW, last_attempt=NOW - timedelta(minutes=15)).due


def test_uncovered_tip_is_not_throttled_by_a_recent_failed_attempt(tmp_path):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), NOW + timedelta(minutes=15))])
    assert capture_due(db, NOW, last_attempt=NOW - timedelta(minutes=15)).due


def test_a_corrupt_ledger_fails_loudly(tmp_path):
    db = tmp_path / "corrupt.sqlite3"
    db.write_bytes(b"not a sqlite database at all" * 200)
    with pytest.raises(sqlite3.DatabaseError):
        capture_due(db, NOW)


def test_attempt_marker_round_trips(tmp_path):
    db = tmp_path / "nested" / "ledger.sqlite3"
    assert read_last_attempt(db) is None
    record_attempt(db, NOW)
    assert read_last_attempt(db) == NOW


def test_cli_only_if_due_skips_without_touching_auth(tmp_path, monkeypatch, capsys):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=1), NOW + timedelta(hours=4))])
    monkeypatch.setattr(
        "outlier_scrapers.api.OutlierApiClient", lambda: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    assert cli.main(["capture", "--db", str(db), "--only-if-due"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True and out["next_tip"] == (NOW + timedelta(hours=4)).isoformat()
    assert read_last_attempt(db) is None


def test_cli_only_if_due_captures_and_records_the_attempt(tmp_path, monkeypatch):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), NOW + timedelta(minutes=20))])
    ran = []
    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        cli,
        "run_capture",
        lambda client, **kw: ran.append(kw) or cli.PipelineResult("r", "OK", 0, True, None, []),
    )
    assert cli.main(["capture", "--db", str(db), "--only-if-due"]) == 0
    assert ran and read_last_attempt(db) == NOW
