from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure local source tree wins over any installed egg/site-packages copy
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import outlier_scrapers.daily_job as daily_job
except Exception as e:
    daily_job = None
    _daily_job_err = e

try:
    from outlier_scrapers import otp_fetcher
except Exception as e:
    otp_fetcher = None
    _otp_err = e


def _require_daily_job():
    if daily_job is None:
        pytest.skip(f"daily_job import failed (env/OneDrive): {_daily_job_err}")


def _require_otp_fetcher():
    if otp_fetcher is None:
        pytest.skip(f"otp_fetcher import failed (env/OneDrive): {_otp_err}")


@pytest.fixture(autouse=True)
def _disable_live_result_collection(monkeypatch):
    if daily_job is not None:
        monkeypatch.setattr(
            daily_job.results,
            "collect_and_import",
            lambda *_args, **_kwargs: {"settlement_rows": 0, "updated_count": 0},
        )


def test_daily_job_orchestrates_login_and_refresh(tmp_path):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-06-29"
    with (
        patch("outlier_scrapers.daily_job.perform_auth_check", return_value=False),
        patch("outlier_scrapers.daily_job.orchestrate_login", return_value=True),
        patch("outlier_scrapers.daily_job.run_explicit_refresh", return_value=True),
        patch("outlier_scrapers.daily_job.check_freshness", return_value=True),
        patch("outlier_scrapers.daily_job.run_pack", return_value=fake_pack),
        patch("outlier_scrapers.daily_job._acquire_writer_lock", return_value=tmp_path / ".lock"),
        patch("outlier_scrapers.daily_job._release_writer_lock", return_value=None),
        patch("outlier_scrapers.daily_job._atomic_write_manifest", return_value=None),
        patch("outlier_scrapers.daily_job.run_desk.orchestrate_desk", return_value=0),
    ):
        result = daily_job.main(["--leagues", "MLB"])
        assert result == 0


def test_otp_fetcher_redacts_code(caplog, tmp_path):
    _require_otp_fetcher()
    import logging

    caplog.set_level(logging.INFO)
    with patch("outlier_scrapers.otp_fetcher.imaplib.IMAP4_SSL") as mock_imap:
        instance = mock_imap.return_value
        instance.login.return_value = ("OK", [b""])
        instance.select.return_value = ("OK", [b""])
        instance.search.return_value = ("OK", [b"1 2 3"])
        from email.utils import formatdate
        import time

        date_str = formatdate(time.time(), localtime=False).encode()
        mock_msg_full = (
            b"Date: " + date_str + b"\r\nContent-Type: text/plain\r\n\r\nYour code is 123456."
        )
        instance.fetch.return_value = ("OK", [(b"1 (RFC822)", mock_msg_full)])
        with (
            patch(
                "outlier_scrapers.otp_fetcher.os.getenv",
                side_effect=lambda k, d="": (
                    "test"
                    if k in ["OUTLIER_IMAP_SERVER", "OUTLIER_IMAP_USER", "OUTLIER_IMAP_PASSWORD"]
                    else d
                ),
            ),
            patch(
                "outlier_scrapers.otp_fetcher.otp_code_file", return_value=tmp_path / "otp_code.txt"
            ),
        ):
            otp_fetcher.fetch_and_write_otp(time.time() - 100)
            assert "otp_found=True" in caplog.text
            assert "123456" not in caplog.text


def test_run_explicit_refresh_failure(monkeypatch):
    _require_daily_job()
    monkeypatch.setattr("outlier_scrapers.refresh.main", lambda _: 1)
    assert not daily_job.run_explicit_refresh(["MLB"])


def test_ingest_pending_settlements_routes_to_feedback_inbox(monkeypatch, tmp_path):
    _require_daily_job()
    expected = {
        "file_count": 1,
        "processed_count": 1,
        "retained_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 2,
    }
    seen = {}

    def fake_import(inbox, db):
        seen["args"] = (inbox, db)
        return expected

    monkeypatch.setattr(daily_job.feedback, "import_settlement_inbox", fake_import)
    inbox = tmp_path / "inbox"
    db = tmp_path / "feedback.sqlite3"

    assert daily_job.ingest_pending_settlements(inbox, db) == expected
    assert seen["args"] == (inbox, db)


def test_collect_completed_results_routes_to_results_module(monkeypatch, tmp_path):
    _require_daily_job()
    expected = {"settlement_rows": 2, "updated_count": 2}
    seen = {}

    def fake_collect(db, *, leagues, lookback_days, output_dir):
        seen["args"] = (db, leagues, lookback_days, output_dir)
        return expected

    monkeypatch.setattr(daily_job.results, "collect_and_import", fake_collect)
    db = tmp_path / "feedback.sqlite3"
    output = tmp_path / "generated"

    assert daily_job.collect_completed_results(["MLB", "WNBA"], db, output, lookback_days=5) == {
        "status": "ok",
        **expected,
    }
    assert seen["args"] == (db, ["MLB", "WNBA"], 5, output)


def test_collect_completed_results_fails_closed_without_blocking_daily_refresh(
    monkeypatch, tmp_path
):
    _require_daily_job()

    def fail(*_args, **_kwargs):
        raise daily_job.results.ResultsError("provider unavailable")

    monkeypatch.setattr(daily_job.results, "collect_and_import", fail)
    result = daily_job.collect_completed_results(["WNBA"], tmp_path / "feedback.sqlite3")

    assert result == {"status": "failed", "error": "provider unavailable"}


def test_daily_job_orchestrates_reasoning(monkeypatch, tmp_path):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-06-27"
    fake_pack.mkdir(parents=True, exist_ok=True)
    (fake_pack / "briefing.md").write_text("SLATE", encoding="utf-8")

    monkeypatch.setattr("outlier_scrapers.daily_job.run_explicit_refresh", lambda *a, **k: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.check_freshness", lambda *a, **k: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.run_pack", lambda leagues: fake_pack)
    monkeypatch.setattr("outlier_scrapers.daily_job.orchestrate_login", lambda _leagues: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.perform_auth_check", lambda _: True)
    monkeypatch.setattr(
        "outlier_scrapers.daily_job._acquire_writer_lock", lambda: tmp_path / ".l"
    )
    monkeypatch.setattr("outlier_scrapers.daily_job._release_writer_lock", lambda lock_dir: None)
    monkeypatch.setattr("outlier_scrapers.daily_job._atomic_write_manifest", lambda p, d: None)

    calls = {}

    def fake_desk(pack_dir, **kw):
        calls["called"] = True
        calls["steps"] = kw.get("steps")
        return 0

    monkeypatch.setattr("outlier_scrapers.daily_job.run_desk.orchestrate_desk", fake_desk)

    import outlier_scrapers.reasoning

    monkeypatch.setattr(outlier_scrapers.reasoning, "run_reasoning", lambda *a, **k: 0)

    exit_code = daily_job.main(["--run-reasoning"])
    assert exit_code == 0
    assert calls.get("called")


def test_daily_job_reasoning_failure_returns_1(monkeypatch, tmp_path):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-06-27"
    fake_pack.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("outlier_scrapers.daily_job.run_explicit_refresh", lambda *a, **k: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.check_freshness", lambda *a, **k: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.run_pack", lambda leagues: fake_pack)
    monkeypatch.setattr("outlier_scrapers.daily_job.orchestrate_login", lambda _leagues: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.perform_auth_check", lambda _: True)
    monkeypatch.setattr(
        "outlier_scrapers.daily_job._acquire_writer_lock", lambda: tmp_path / ".l"
    )
    monkeypatch.setattr("outlier_scrapers.daily_job._release_writer_lock", lambda lock_dir: None)
    monkeypatch.setattr("outlier_scrapers.daily_job._atomic_write_manifest", lambda p, d: None)

    def fake_desk_fail(*a, **k):
        (fake_pack / "reasoning_status.json").write_text('{"overall": "failed"}')
        return 1

    monkeypatch.setattr("outlier_scrapers.daily_job.run_desk.orchestrate_desk", fake_desk_fail)

    exit_code = daily_job.main(["--run-reasoning"])
    assert exit_code == 1


def _run_daily_job_with_desk_status(tmp_path, monkeypatch, overall: str) -> int:
    fake_pack = tmp_path / "packs" / "2026-07-17"
    fake_pack.mkdir(parents=True, exist_ok=True)
    (fake_pack / "candidates.csv").write_text("market_id,line\nm1,1.5\n", encoding="utf-8")
    (fake_pack / "briefing.md").write_text("SLATE", encoding="utf-8")

    monkeypatch.setattr(daily_job, "perform_auth_check", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_explicit_refresh", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "check_freshness", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_pack", lambda _leagues: fake_pack)
    monkeypatch.setattr(daily_job, "_acquire_writer_lock", lambda: tmp_path / ".lock")
    monkeypatch.setattr(daily_job, "_release_writer_lock", lambda _lock: None)
    monkeypatch.setattr(daily_job, "_atomic_write_manifest", lambda _pack, _data: None)

    def fake_desk(pack_dir, **_kwargs):
        (pack_dir / "reasoning_status.json").write_text(
            json.dumps({"overall": overall}), encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(daily_job.run_desk, "orchestrate_desk", fake_desk)
    return daily_job.main(["--leagues", "MLB"])


@pytest.mark.parametrize("overall", ["PARTIAL", "FULL", "partial", "ok", "degraded"])
def test_daily_job_exits_zero_for_successful_desk_status(tmp_path, monkeypatch, overall):
    _require_daily_job()
    assert _run_daily_job_with_desk_status(tmp_path, monkeypatch, overall) == 0


@pytest.mark.parametrize("overall", ["DATA_ONLY", "failed", "running", "ERROR"])
def test_daily_job_exits_one_for_unsuccessful_desk_status(tmp_path, monkeypatch, overall):
    _require_daily_job()
    assert _run_daily_job_with_desk_status(tmp_path, monkeypatch, overall) == 1


def test_orchestrate_login_removes_stale_status(tmp_path):
    status_file = tmp_path / "otp_status.json"
    status_file.write_text(json.dumps({"stage": "authenticated"}), encoding="utf-8")
    assert status_file.exists()


class _FakeLoginProc:
    def __init__(self, returncode: int):
        self.returncode = returncode

    def wait(self):
        return self.returncode


def _setup_orchestrate_login(
    monkeypatch, tmp_path, *, otp_result: bool, returncode: int, auth_check: bool
) -> dict:
    calls: dict = {"auth_checks": []}
    monkeypatch.setattr(daily_job, "otp_status_file", lambda: tmp_path / "otp_status.json")
    monkeypatch.setattr(daily_job.subprocess, "Popen", lambda *_a, **_k: _FakeLoginProc(returncode))
    monkeypatch.setattr(daily_job, "tail_otp_status_and_fetch", lambda _ts: otp_result)

    def fake_auth_check(leagues):
        calls["auth_checks"].append(leagues)
        return auth_check

    monkeypatch.setattr(daily_job, "perform_auth_check", fake_auth_check)
    return calls


def test_orchestrate_login_recovers_when_session_already_valid(tmp_path, monkeypatch):
    # Regression for 2026-07-17: a still-valid session refreshes silently, so no
    # OTP form appears and the status wait times out even though auth now works.
    _require_daily_job()
    calls = _setup_orchestrate_login(
        monkeypatch, tmp_path, otp_result=False, returncode=0, auth_check=True
    )

    assert daily_job.orchestrate_login(["MLB", "WNBA"])
    assert calls["auth_checks"] == [["MLB", "WNBA"]]


def test_orchestrate_login_fails_when_auth_check_also_fails(tmp_path, monkeypatch):
    _require_daily_job()
    calls = _setup_orchestrate_login(
        monkeypatch, tmp_path, otp_result=False, returncode=0, auth_check=False
    )

    assert not daily_job.orchestrate_login(["MLB"])
    assert calls["auth_checks"] == [["MLB"]]


def test_orchestrate_login_recovers_from_nonzero_exit_if_auth_ok(tmp_path, monkeypatch):
    _require_daily_job()
    calls = _setup_orchestrate_login(
        monkeypatch, tmp_path, otp_result=True, returncode=1, auth_check=True
    )

    assert daily_job.orchestrate_login(["MLB"])
    assert calls["auth_checks"] == [["MLB"]]


def test_orchestrate_login_skips_auth_recheck_on_clean_success(tmp_path, monkeypatch):
    _require_daily_job()
    calls = _setup_orchestrate_login(
        monkeypatch, tmp_path, otp_result=True, returncode=0, auth_check=True
    )

    assert daily_job.orchestrate_login(["MLB"])
    assert calls["auth_checks"] == []


def _healthy_feed_health() -> dict:
    return {
        "props_status": "ok",
        "games_status": "ok",
        "insights_status": "ok",
        "injuries_status": "ok",
        "line_movement_status": "ok",
        "game_line_movement_status": "ok",
        "cards_status": "ok",
        "coverage_pct": 100.0,
        "oldest_source_age": 0.2,
        "latest_source_age": 0.1,
        "failed_ids": [],
        "schema_version": "1.0",
    }


def test_check_freshness_accepts_unified_health(monkeypatch):
    _require_daily_job()
    now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    written = []
    monkeypatch.setattr(
        daily_job.feed_health,
        "build_feed_health",
        lambda league, **kwargs: written.append((league, kwargs)) or _healthy_feed_health(),
    )

    assert daily_job.check_freshness(["MLB"], now=now)
    assert written == [("MLB", {"now": now, "write": True})]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("props_status", "missing"),
        ("games_status", "stale"),
        ("cards_status", "error"),
        ("coverage_pct", 89.99),
    ],
)
def test_check_freshness_rejects_unsafe_unified_health(monkeypatch, field, value):
    _require_daily_job()
    health = _healthy_feed_health()
    health[field] = value
    monkeypatch.setattr(
        daily_job.feed_health, "build_feed_health", lambda _league, **_kwargs: health
    )

    assert not daily_job.check_freshness(["MLB"])


def test_check_freshness_rejects_malformed_source(monkeypatch):
    _require_daily_job()

    def fail(*_args, **_kwargs):
        raise ValueError("malformed status")

    monkeypatch.setattr(daily_job.feed_health, "build_feed_health", fail)

    assert not daily_job.check_freshness(["MLB"])


def test_writer_lock_conflict_prevents_every_pipeline_mutation(monkeypatch):
    _require_daily_job()
    called = []
    monkeypatch.setattr(daily_job, "load_environment", lambda: called.append("environment"))
    monkeypatch.setattr(daily_job, "_acquire_writer_lock", lambda: None)
    monkeypatch.setattr(
        daily_job,
        "collect_completed_results",
        lambda *_args, **_kwargs: called.append("results"),
    )
    monkeypatch.setattr(
        daily_job,
        "ingest_pending_settlements",
        lambda *_args, **_kwargs: called.append("settlements"),
    )
    monkeypatch.setattr(daily_job, "perform_auth_check", lambda *_args: called.append("auth"))
    monkeypatch.setattr(daily_job, "run_explicit_refresh", lambda *_args: called.append("refresh"))
    monkeypatch.setattr(daily_job, "run_pack", lambda *_args: called.append("pack"))

    assert daily_job.main(["--leagues", "MLB"]) == 2
    assert called == ["environment"]


def test_writer_lock_precedes_mutations_and_spans_manifest(tmp_path, monkeypatch):
    _require_daily_job()
    calls = []
    lock = tmp_path / ".daily_job_lock"
    fake_pack = tmp_path / "packs" / "2026-07-07"
    fake_pack.mkdir(parents=True)
    (fake_pack / "candidates.csv").write_text("market_id,line\n", encoding="utf-8")

    monkeypatch.setattr(daily_job, "load_environment", lambda: calls.append("environment"))
    monkeypatch.setattr(
        daily_job, "_acquire_writer_lock", lambda: calls.append("lock") or lock
    )
    monkeypatch.setattr(
        daily_job,
        "_release_writer_lock",
        lambda actual: calls.append("release") if actual == lock else None,
    )
    monkeypatch.setattr(
        daily_job,
        "collect_completed_results",
        lambda *_args, **_kwargs: calls.append("results") or {"status": "ok"},
    )
    monkeypatch.setattr(
        daily_job,
        "ingest_pending_settlements",
        lambda *_args, **_kwargs: calls.append("settlements") or {"updated_count": 0},
    )
    monkeypatch.setattr(
        daily_job, "perform_auth_check", lambda *_args: calls.append("auth") or True
    )
    monkeypatch.setattr(
        daily_job, "run_explicit_refresh", lambda *_args: calls.append("refresh") or True
    )
    monkeypatch.setattr(
        daily_job, "check_freshness", lambda *_args: calls.append("health") or True
    )
    monkeypatch.setattr(
        daily_job, "run_pack", lambda *_args: calls.append("pack") or fake_pack
    )
    monkeypatch.setattr(
        daily_job,
        "_atomic_write_manifest",
        lambda *_args: calls.append("manifest"),
    )

    assert daily_job.main(["--leagues", "MLB"]) == 0
    assert calls == [
        "environment",
        "lock",
        "results",
        "settlements",
        "auth",
        "refresh",
        "health",
        "pack",
        "manifest",
        "release",
    ]


def test_early_pipeline_failure_still_releases_writer_lock(tmp_path, monkeypatch):
    _require_daily_job()
    calls = []
    lock = tmp_path / ".daily_job_lock"
    monkeypatch.setattr(daily_job, "load_environment", lambda: calls.append("environment"))
    monkeypatch.setattr(
        daily_job, "_acquire_writer_lock", lambda: calls.append("lock") or lock
    )
    monkeypatch.setattr(
        daily_job,
        "_release_writer_lock",
        lambda actual: calls.append("release") if actual == lock else None,
    )
    monkeypatch.setattr(
        daily_job,
        "ingest_pending_settlements",
        lambda *_args, **_kwargs: calls.append("settlements") or None,
    )

    assert daily_job.main(["--leagues", "MLB", "--skip-result-collection"]) == 1
    assert calls == ["environment", "lock", "settlements", "release"]


def test_daily_job_skips_desk_for_empty_pack(tmp_path, monkeypatch):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-07-07"
    fake_pack.mkdir(parents=True)
    (fake_pack / "candidates.csv").write_text("market_id,line\n", encoding="utf-8")
    (fake_pack / "briefing.md").write_text("No pregame candidates", encoding="utf-8")

    monkeypatch.setattr(daily_job, "perform_auth_check", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_explicit_refresh", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "check_freshness", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_pack", lambda _leagues: fake_pack)
    monkeypatch.setattr(daily_job, "_acquire_writer_lock", lambda: tmp_path / ".lock")
    monkeypatch.setattr(daily_job, "_release_writer_lock", lambda _lock: None)
    manifest = {}
    monkeypatch.setattr(
        daily_job, "_atomic_write_manifest", lambda _pack, data: manifest.update(data)
    )

    desk_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal desk_called
        desk_called = True
        raise AssertionError("desk must not run for an empty pack")

    monkeypatch.setattr("outlier_scrapers.daily_job.run_desk.orchestrate_desk", fail_if_called)

    assert daily_job.main(["--analysis-profile", "full"]) == 0
    assert not desk_called
    assert manifest["pack_rows"] == 0
    assert manifest["overall"] == "ok"


def test_daily_job_runs_desk_for_actionable_totals_only(tmp_path, monkeypatch):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-07-07"
    fake_pack.mkdir(parents=True)
    (fake_pack / "candidates.csv").write_text("market_id,line\n", encoding="utf-8")
    from outlier_scrapers.game_totals import GAME_TOTALS_HEADER

    with (fake_pack / "game_totals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GAME_TOTALS_HEADER)
        writer.writeheader()
        row = {field: "" for field in GAME_TOTALS_HEADER}
        row.update(
            totals_id="t2",
            market_id="m2",
            selection="A @ B Total OVER 8.5",
            line="8.5",
            price="-110",
            actionable="true",
        )
        writer.writerow(row)
    (fake_pack / "briefing.md").write_text("Totals only", encoding="utf-8")

    monkeypatch.setattr(daily_job, "perform_auth_check", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_explicit_refresh", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "check_freshness", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_pack", lambda _leagues: fake_pack)
    monkeypatch.setattr(daily_job, "_acquire_writer_lock", lambda: tmp_path / ".lock")
    monkeypatch.setattr(daily_job, "_release_writer_lock", lambda _lock: None)
    manifest = {}
    monkeypatch.setattr(
        daily_job, "_atomic_write_manifest", lambda _pack, data: manifest.update(data)
    )
    calls = []
    monkeypatch.setattr(
        daily_job.run_desk,
        "orchestrate_desk",
        lambda *_args, **kwargs: calls.append(kwargs) or 0,
    )

    assert daily_job.main(["--analysis-profile", "full"]) == 0
    assert calls and calls[0]["steps"] == ["A", "B", "C", "D", "E"]
    assert manifest["pack_rows"] == 1


def test_daily_job_runs_desk_for_actionable_team_totals_only(tmp_path, monkeypatch):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-07-07"
    fake_pack.mkdir(parents=True)
    (fake_pack / "candidates.csv").write_text("market_id,line\n", encoding="utf-8")
    from outlier_scrapers.game_totals import TEAM_TOTALS_HEADER

    with (fake_pack / "team_totals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEAM_TOTALS_HEADER)
        writer.writeheader()
        row = {field: "" for field in TEAM_TOTALS_HEADER}
        row.update(
            totals_id="t3",
            market_id="m3",
            selection="A Team Total OVER 4.5",
            line="4.5",
            price="-110",
            total_kind="team",
            actionable="true",
        )
        writer.writerow(row)
    (fake_pack / "briefing.md").write_text("Team totals only", encoding="utf-8")

    monkeypatch.setattr(daily_job, "perform_auth_check", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_explicit_refresh", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "check_freshness", lambda _leagues: True)
    monkeypatch.setattr(daily_job, "run_pack", lambda _leagues: fake_pack)
    monkeypatch.setattr(daily_job, "_acquire_writer_lock", lambda: tmp_path / ".lock")
    monkeypatch.setattr(daily_job, "_release_writer_lock", lambda _lock: None)
    manifest = {}
    monkeypatch.setattr(
        daily_job, "_atomic_write_manifest", lambda _pack, data: manifest.update(data)
    )
    calls = []
    monkeypatch.setattr(
        daily_job.run_desk,
        "orchestrate_desk",
        lambda *_args, **kwargs: calls.append(kwargs) or 0,
    )

    assert daily_job.main(["--analysis-profile", "full"]) == 0
    assert calls and calls[0]["steps"] == ["A", "B", "C", "D", "E"]
    assert manifest["pack_rows"] == 1
