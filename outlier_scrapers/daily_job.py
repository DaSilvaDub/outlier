from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .api import OutlierApiClient, AuthRequiredError, OutlierApiError
from .environment import load_environment
from .paths import otp_status_file, PROJECT_ROOT
from .otp_fetcher import fetch_and_write_otp
from . import refresh
from . import pack

logger = logging.getLogger(__name__)

def perform_auth_check(leagues: list[str]) -> bool:
    try:
        client = OutlierApiClient()
        league = leagues[0] if leagues else "MLB"
        client.fetch_schedule(league)
        return True
    except (AuthRequiredError, OutlierApiError, FileNotFoundError, ValueError) as exc:
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
                    time.sleep(5)
                    success = fetch_and_write_otp(attempt_timestamp)
                    if success:
                        otp_fetched = True
                    else:
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
    login_proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
    success = tail_otp_status_and_fetch(attempt_timestamp)
    login_proc.wait()
    if login_proc.returncode != 0:
        logger.error(f"Login process exited with code {login_proc.returncode}")
        success = False
    return success

def run_explicit_refresh(leagues: list[str]) -> bool:
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

def run_pack(leagues: list[str]) -> Path | None:
    logger.info("Building pack...")
    args = ["--leagues", ",".join(leagues)]
    try:
        return pack.main(args)
    except Exception as e:
        logger.error(f"Failed to build pack: {e}")
        return None

def _acquire_pack_lock(pack_dir: Path) -> Path | None:
    lock_dir = pack_dir / ".pack_lock"
    pack_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir(exist_ok=False)
        return lock_dir
    except FileExistsError:
        return None

def _release_pack_lock(lock_dir: Path | None) -> None:
    if lock_dir and lock_dir.exists():
        try:
            lock_dir.rmdir()
        except Exception:
            pass

def _atomic_write_manifest(pack_dir: Path, data: dict) -> None:
    mpath = pack_dir / "manifest.json"
    tmp = mpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, mpath)
    logger.info("Wrote %s", mpath)

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Daily Outlier Orchestration Job")
    parser.add_argument("--leagues", default="MLB,WNBA", help="Comma-separated leagues")
    parser.add_argument(
        "--analysis-profile",
        default="local",
        choices=["local", "openai", "full"],
        help="local: pack + deterministic local report only (no external). openai: +Prompt A. full: A+B+D+E (with fallbacks).",
    )
    parser.add_argument(
        "--run-reasoning", action="store_true", help="(deprecated) alias for --analysis-profile openai"
    )
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

    pack_dir = run_pack(leagues)
    if pack_dir is None:
        logger.error("Pack generation failed. Aborting.")
        return 1

    lock_dir = _acquire_pack_lock(pack_dir)
    if lock_dir is None:
        logger.error(f"Writer lock conflict for {pack_dir} (another daily job running).")
        return 2

    try:
        profile = args.analysis_profile
        if args.run_reasoning and profile == "local":
            profile = "openai"

        run_steps = []
        if profile == "openai":
            run_steps = ["A"]
        elif profile == "full":
            run_steps = ["A", "B", "D", "E"]

        if run_steps or profile == "local":
            logger.info("Running analysis desk (profile=%s)...", profile)
            try:
                from . import run_desk
                if profile == "local":
                    desk_code = run_desk.orchestrate_desk(pack_dir, steps=["E"], force=False, allow_local_synth=True)
                else:
                    desk_code = run_desk.orchestrate_desk(pack_dir, steps=run_steps, force=False, allow_local_synth=True)
                if desk_code != 0:
                    logger.warning("Desk completed with non-zero (may be partial/degraded).")
            except Exception as e:
                logger.error("Desk orchestration error: %s", e)

        status_path = pack_dir / "reasoning_status.json"
        overall = "ok"
        if status_path.exists():
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
                overall = st.get("overall", "ok")
            except Exception:
                overall = "degraded"
        elif (pack_dir / "briefing.md").exists() or (pack_dir / "candidates.csv").exists():
            overall = "degraded"

        manifest = {
            "run_id": f"{pack_dir.name}-{int(time.time())}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "leagues": leagues,
            "profile": profile,
            "overall": overall,
            "pack_rows": None,
            "status_file": str(status_path) if status_path.exists() else None,
        }
        try:
            cand = pack_dir / "candidates.csv"
            if cand.exists():
                with open(cand, encoding="utf-8") as f:
                    manifest["pack_rows"] = max(0, len(f.readlines()) - 1)
        except Exception:
            pass
        _atomic_write_manifest(pack_dir, manifest)

        final_code = 0 if overall in ("ok", "degraded", "partial") else 1
        logger.info("Daily job completed (exit=%s, profile=%s, overall=%s).", final_code, profile, overall)
        return final_code
    finally:
        _release_pack_lock(lock_dir)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
