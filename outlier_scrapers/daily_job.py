from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time

import os

from .api import OutlierApiClient, AuthRequiredError
from .paths import otp_status_file, PROJECT_ROOT
from .otp_fetcher import fetch_and_write_otp
from . import refresh
from . import pack

logger = logging.getLogger(__name__)


def load_environment():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def perform_auth_check(leagues: list[str]) -> bool:
    try:
        # Client loads storage state on init
        client = OutlierApiClient()
        league = leagues[0] if leagues else "MLB"
        # Make a lightweight request to verify
        client.fetch_schedule(league)
        return True
    except (AuthRequiredError, FileNotFoundError, ValueError) as exc:
        logger.info(f"Auth check determined reauth is needed: {exc}")
        return False
    except Exception as exc:
        logger.error(f"Unexpected error during auth check: {exc}")
        raise


def tail_otp_status_and_fetch(attempt_timestamp: float, timeout: int = 150) -> bool:
    deadline = time.time() + timeout
    status_file = otp_status_file()

    otp_fetched = False

    while time.time() < deadline:
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                stage = data.get("stage")

                if stage == "waiting_for_code" and not otp_fetched:
                    logger.info("OTP requested by login process. Fetching via IMAP...")
                    # Give the email a moment to arrive
                    time.sleep(5)
                    success = fetch_and_write_otp(attempt_timestamp)
                    if success:
                        otp_fetched = True
                    else:
                        # Will retry on next loop iteration
                        time.sleep(10)

                elif stage == "authenticated":
                    logger.info("Session authenticated successfully.")
                    return True
                elif stage == "failed":
                    logger.error("Login process reported failure.")
                    return False
            except Exception as e:
                logger.warning(f"Error reading otp_status: {e}")

        time.sleep(2)

    logger.error("Timeout waiting for authentication.")
    return False


def orchestrate_login() -> bool:
    attempt_timestamp = time.time()

    status_file = otp_status_file()
    if status_file.exists():
        try:
            status_file.unlink()
        except OSError as e:
            logger.warning(f"Could not remove stale otp_status.json: {e}")

    logger.info("Starting headless login process...")
    cmd = [sys.executable, "-m", "outlier_scrapers.login", "--headless", "--timeout", "150"]

    # We spawn login and let it run
    # while we monitor otp_status.json in the current process
    login_proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))

    success = tail_otp_status_and_fetch(attempt_timestamp)

    login_proc.wait()
    if login_proc.returncode != 0:
        logger.error(f"Login process exited with code {login_proc.returncode}")
        success = False

    return success


def run_explicit_refresh(leagues: list[str]) -> bool:
    # Explicitly ordered pipeline: props, insights, games, props LM, game LM, rebuild cards / game cards
    logger.info("Starting explicitly ordered refresh pipeline.")

    for league in leagues:
        args = ["--league", league]

        steps = [
            ("--props", "props"),
            ("--insights", "insights"),
            ("--games", "games"),
            ("--line-movement", "props line-movement"),
            ("--game-line-movement", "game line-movement"),
            ("--cards", "cards"),
            ("--game-cards", "game cards"),
        ]

        for flag, name in steps:
            logger.info(f"Running {name} for {league}...")
            exit_code = refresh.main(args + [flag])
            if exit_code != 0:
                logger.error(f"Failed at step: {name} for {league}")
                return False

    return True


def run_pack(leagues: list[str]) -> bool:
    logger.info("Building pack...")
    args = ["--leagues", ",".join(leagues)]
    try:
        pack.main(args)
        return True
    except Exception as e:
        logger.error(f"Failed to build pack: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Daily Outlier Orchestration Job")
    parser.add_argument("--leagues", default="MLB,WNBA", help="Comma-separated leagues")
    args = parser.parse_args(argv)

    leagues = [lg.strip().upper() for lg in args.leagues.split(",")]

    load_environment()

    if not perform_auth_check(leagues):
        if not orchestrate_login():
            logger.error("Authentication failed. Aborting pipeline.")
            return 1

    if not run_explicit_refresh(leagues):
        logger.error("Refresh pipeline failed. Aborting.")
        return 1

    if not run_pack(leagues):
        logger.error("Pack generation failed. Aborting.")
        return 1

    logger.info("Daily job completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
