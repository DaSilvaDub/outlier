import json
import sqlite3
from datetime import UTC, datetime, timedelta

import outlier_nba.cli as cli
from outlier_nba.track.due import capture_due

NOW = datetime(2026, 10, 21, 22, 10, tzinfo=UTC)


def _ledger(tmp_path, rows):
    db = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE market_snapshots (sport TEXT, captured_at TEXT, event_starts_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO market_snapshots VALUES (?, ?, ?)",
            [(sport, at.isoformat(), tip.isoformat()) for sport, at, tip in rows],
        )
    conn.close()
    return db


def test_missing_ledger_is_due(tmp_path):
    result = capture_due(tmp_path / "absent.sqlite3", NOW)
    assert result.due and result.last_capture is None


def test_ledger_without_nba_rows_is_due(tmp_path):
    db = _ledger(tmp_path, [("MLB", NOW - timedelta(hours=1), NOW + timedelta(minutes=30))])
    assert capture_due(db, NOW).due


def test_tip_inside_the_window_without_a_verified_capture_is_due(tmp_path):
    tip = NOW + timedelta(minutes=50)
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), tip)])
    result = capture_due(db, NOW)
    assert result.due and result.next_tip == tip
    assert "no capture" in result.reason


def test_tip_already_covered_by_a_capture_in_its_window_is_not_due(tmp_path):
    tip = NOW + timedelta(minutes=50)
    db = _ledger(tmp_path, [("NBA", tip - timedelta(minutes=60), tip)])
    assert not capture_due(db, NOW).due


def test_tip_beyond_the_window_waits(tmp_path):
    tip = NOW + timedelta(minutes=61)
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), tip)])
    result = capture_due(db, NOW)
    assert not result.due and result.next_tip == tip


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


def test_stale_slate_is_due_even_with_no_tip_soon(tmp_path):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=13), NOW + timedelta(days=2))])
    result = capture_due(db, NOW)
    assert result.due and "stale" in result.reason


def test_cli_only_if_due_skips_without_touching_auth(tmp_path, monkeypatch, capsys):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=1), NOW + timedelta(hours=4))])
    monkeypatch.setattr(
        "outlier_scrapers.api.OutlierApiClient", lambda: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    assert cli.main(["capture", "--db", str(db), "--only-if-due"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True and out["next_tip"] == (NOW + timedelta(hours=4)).isoformat()


def test_cli_only_if_due_captures_when_due(tmp_path, monkeypatch):
    db = _ledger(tmp_path, [("NBA", NOW - timedelta(hours=3), NOW + timedelta(minutes=30))])
    ran = []
    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        cli,
        "run_capture",
        lambda client, **kw: ran.append(kw) or cli.PipelineResult("r", "OK", 0, True, None, []),
    )
    assert cli.main(["capture", "--db", str(db), "--only-if-due"]) == 0
    assert ran
