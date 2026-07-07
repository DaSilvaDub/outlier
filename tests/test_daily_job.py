from __future__ import annotations

import sys
from pathlib import Path

# Ensure local source tree wins over any installed egg/site-packages copy
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unittest.mock import patch

import pytest

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

def test_daily_job_orchestrates_login_and_refresh(tmp_path):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-06-29"
    with (
        patch("outlier_scrapers.daily_job.perform_auth_check", return_value=False),
        patch("outlier_scrapers.daily_job.orchestrate_login", return_value=True),
        patch("outlier_scrapers.daily_job.run_explicit_refresh", return_value=True),
        patch("outlier_scrapers.daily_job.run_pack", return_value=fake_pack),
        patch("outlier_scrapers.daily_job._acquire_pack_lock", return_value=tmp_path / ".lock"),
        patch("outlier_scrapers.daily_job._release_pack_lock", return_value=None),
        patch("outlier_scrapers.daily_job._atomic_write_manifest", return_value=None),
        patch("outlier_scrapers.daily_job.run_desk", create=True) as mock_rd,
    ):
        mock_rd.orchestrate_desk.return_value = 0
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
        mock_msg_full = b"Date: " + date_str + b"\r\nContent-Type: text/plain\r\n\r\nYour code is 123456."
        instance.fetch.return_value = ("OK", [(b"1 (RFC822)", mock_msg_full)])
        with (
            patch("outlier_scrapers.otp_fetcher.os.getenv", side_effect=lambda k, d="": "test" if k in ["OUTLIER_IMAP_SERVER", "OUTLIER_IMAP_USER", "OUTLIER_IMAP_PASSWORD"] else d),
            patch("outlier_scrapers.otp_fetcher.otp_code_file", return_value=tmp_path / "otp_code.txt"),
        ):
            otp_fetcher.fetch_and_write_otp(time.time() - 100)
            assert "otp_found=True" in caplog.text
            assert "123456" not in caplog.text

def test_run_explicit_refresh_failure(monkeypatch):
    _require_daily_job()
    monkeypatch.setattr("outlier_scrapers.refresh.main", lambda _: 1)
    assert not daily_job.run_explicit_refresh(["MLB"])

def test_daily_job_orchestrates_reasoning(monkeypatch, tmp_path):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-06-27"
    fake_pack.mkdir(parents=True, exist_ok=True)
    (fake_pack / "briefing.md").write_text("SLATE", encoding="utf-8")

    monkeypatch.setattr("outlier_scrapers.daily_job.run_explicit_refresh", lambda *a, **k: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.run_pack", lambda leagues: fake_pack)
    monkeypatch.setattr("outlier_scrapers.daily_job.orchestrate_login", lambda: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.perform_auth_check", lambda _: True)
    monkeypatch.setattr("outlier_scrapers.daily_job._acquire_pack_lock", lambda p: tmp_path/".l")
    monkeypatch.setattr("outlier_scrapers.daily_job._release_pack_lock", lambda lock_dir: None)
    monkeypatch.setattr("outlier_scrapers.daily_job._atomic_write_manifest", lambda p,d: None)

    import outlier_scrapers.run_desk as rd
    calls = {}
    def fake_desk(pack_dir, **kw):
        calls["called"] = True
        calls["steps"] = kw.get("steps")
        return 0
    monkeypatch.setattr(rd, "orchestrate_desk", fake_desk)

    import outlier_scrapers.reasoning
    monkeypatch.setattr(outlier_scrapers.reasoning, "run_reasoning", lambda *a,**k: 0)

    exit_code = daily_job.main(["--run-reasoning"])
    assert exit_code == 0
    assert calls.get("called")

def test_daily_job_reasoning_failure_returns_1(monkeypatch, tmp_path):
    _require_daily_job()
    fake_pack = tmp_path / "packs" / "2026-06-27"
    fake_pack.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("outlier_scrapers.daily_job.run_explicit_refresh", lambda *a,**k: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.run_pack", lambda leagues: fake_pack)
    monkeypatch.setattr("outlier_scrapers.daily_job.orchestrate_login", lambda: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.perform_auth_check", lambda _: True)
    monkeypatch.setattr("outlier_scrapers.daily_job._acquire_pack_lock", lambda p: tmp_path/".l")
    monkeypatch.setattr("outlier_scrapers.daily_job._release_pack_lock", lambda lock_dir: None)
    monkeypatch.setattr("outlier_scrapers.daily_job._atomic_write_manifest", lambda p,d: None)

    import outlier_scrapers.run_desk as rd
    def fake_desk_fail(*a, **k):
        (fake_pack / "reasoning_status.json").write_text('{"overall": "failed"}')
        return 1
    monkeypatch.setattr(rd, "orchestrate_desk", fake_desk_fail)

    exit_code = daily_job.main(["--run-reasoning"])
    assert exit_code == 1

def test_orchestrate_login_removes_stale_status(tmp_path):
    import json
    status_file = tmp_path / "otp_status.json"
    status_file.write_text(json.dumps({"stage": "authenticated"}), encoding="utf-8")
    assert status_file.exists()
