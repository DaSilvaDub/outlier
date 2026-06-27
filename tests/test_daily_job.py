from __future__ import annotations

from unittest.mock import patch


from outlier_scrapers import daily_job
from outlier_scrapers import otp_fetcher


def test_daily_job_orchestrates_login_and_refresh(tmp_path):
    # Mocking external calls so we don't actually hit APIs or spawn processes
    with (
        patch("outlier_scrapers.daily_job.perform_auth_check", return_value=False),
        patch("outlier_scrapers.daily_job.orchestrate_login", return_value=True),
        patch("outlier_scrapers.daily_job.run_explicit_refresh", return_value=True),
        patch("outlier_scrapers.daily_job.run_pack", return_value=True),
    ):
        result = daily_job.main(["--leagues", "MLB"])
        assert result == 0


def test_otp_fetcher_redacts_code(caplog, tmp_path):
    import logging

    caplog.set_level(logging.INFO)
    # We want to ensure the code itself is never logged
    # We will mock the imaplib and simulate finding an email
    with patch("outlier_scrapers.otp_fetcher.imaplib.IMAP4_SSL") as mock_imap:
        instance = mock_imap.return_value
        instance.login.return_value = ("OK", [b""])
        instance.select.return_value = ("OK", [b""])
        instance.search.return_value = ("OK", [b"1 2 3"])

        mock_msg = b"Content-Type: text/plain\r\n\r\nYour code is 123456."
        # We need a valid date for parsedate_tz
        from email.utils import formatdate
        import time

        date_str = formatdate(time.time(), localtime=False).encode()
        mock_msg_full = b"Date: " + date_str + b"\r\n" + mock_msg

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
            # Try to fetch, setting attempt timestamp slightly in the past
            otp_fetcher.fetch_and_write_otp(time.time() - 100)

            # Check logs
            log_text = caplog.text
            assert "otp_found=True" in log_text
            assert "123456" not in log_text


def test_run_explicit_refresh_failure(monkeypatch):
    monkeypatch.setattr("outlier_scrapers.refresh.main", lambda _: 1)

    assert not daily_job.run_explicit_refresh(["MLB"])


def test_daily_job_orchestrates_reasoning(monkeypatch, tmp_path):
    from pathlib import Path

    mock_run_pack_called = False
    mock_reasoning_called = False
    mock_reasoning_args = []

    def mock_run_pack(leagues):
        nonlocal mock_run_pack_called
        mock_run_pack_called = True
        return Path("/mock/pack/2026-06-27")

    def mock_run_reasoning(pack_dir, *, force=False, refresh_if_stale=False, client=None):
        nonlocal mock_reasoning_called, mock_reasoning_args
        mock_reasoning_called = True
        mock_reasoning_args = {"pack_dir": pack_dir, "refresh_if_stale": refresh_if_stale}
        return 0

    monkeypatch.setattr(
        "outlier_scrapers.daily_job.run_explicit_refresh",
        lambda leagues, cards_only=False, game_cards_only=False: True,
    )
    monkeypatch.setattr("outlier_scrapers.daily_job.run_pack", mock_run_pack)
    monkeypatch.setattr("outlier_scrapers.daily_job.orchestrate_login", lambda: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.perform_auth_check", lambda _: True)

    import outlier_scrapers.reasoning

    monkeypatch.setattr(outlier_scrapers.reasoning, "run_reasoning", mock_run_reasoning)

    exit_code = daily_job.main(["--run-reasoning"])

    assert exit_code == 0
    assert mock_run_pack_called
    assert mock_reasoning_called
    assert mock_reasoning_args["pack_dir"].name == "2026-06-27"
    assert mock_reasoning_args["refresh_if_stale"] is True


def test_daily_job_reasoning_failure_returns_1(monkeypatch):
    from pathlib import Path

    def mock_run_pack(leagues):
        return Path("/mock/pack/2026-06-27")

    def mock_run_reasoning(*args, **kwargs):
        return 1

    monkeypatch.setattr(
        "outlier_scrapers.daily_job.run_explicit_refresh",
        lambda leagues, cards_only=False, game_cards_only=False: True,
    )
    monkeypatch.setattr("outlier_scrapers.daily_job.run_pack", mock_run_pack)
    monkeypatch.setattr("outlier_scrapers.daily_job.orchestrate_login", lambda: True)
    monkeypatch.setattr("outlier_scrapers.daily_job.perform_auth_check", lambda _: True)

    import outlier_scrapers.reasoning

    monkeypatch.setattr(outlier_scrapers.reasoning, "run_reasoning", mock_run_reasoning)

    exit_code = daily_job.main(["--run-reasoning"])

    assert exit_code == 1


def test_orchestrate_login_removes_stale_status(tmp_path):
    """Verify that a pre-existing otp_status.json is deleted before we spawn login."""
    import json

    status_file = tmp_path / "otp_status.json"
    status_file.write_text(json.dumps({"stage": "authenticated"}), encoding="utf-8")

    assert status_file.exists()

    def assert_status_deleted(*args, **kwargs):
        assert not status_file.exists(), "Status file should be deleted before Popen is called"
        # We need to return a mock process from Popen
        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("outlier_scrapers.daily_job.otp_status_file", return_value=status_file),
        patch("outlier_scrapers.daily_job.subprocess.Popen", side_effect=assert_status_deleted),
        patch("outlier_scrapers.daily_job.tail_otp_status_and_fetch", return_value=True),
    ):
        success = daily_job.orchestrate_login()

        assert success is True
        assert not status_file.exists()
