from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .api import OutlierApiClient, AuthRequiredError, OutlierApiError
from .environment import load_environment
from .paths import otp_status_file, PROJECT_ROOT
from .otp_fetcher import fetch_and_write_otp
from . import refresh
from . import pack
from . import feedback
from . import run_desk
from . import runner_common as rc

logger = logging.getLogger(__name__)

FRESHNESS_MAX_AGE = timedelta(hours=6)
FRESHNESS_FUTURE_TOLERANCE = timedelta(minutes=5)

# run_desk writes uppercase overall statuses (FULL/PARTIAL/DATA_ONLY); compare lowercased.
SUCCESS_OVERALL_STATUSES = ("ok", "degraded", "partial", "full")


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


def orchestrate_login(leagues: list[str] | None = None) -> bool:
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
    if not success:
        # A still-valid session refreshes storage_state.json silently: no login
        # form or OTP prompt appears, so the status file never reports success.
        # The direct auth check is the ground truth for whether auth works now.
        logger.info("OTP status did not confirm login; re-checking auth directly...")
        if perform_auth_check(leagues or []):
            logger.info("Auth check passed after login attempt; continuing.")
            return True
        logger.error("Auth check still failing after login attempt.")
    return success


def run_explicit_refresh(leagues: list[str]) -> bool:
    logger.info("Starting explicitly ordered refresh pipeline.")
    for league in leagues:
        args = ["--league", league]
        steps = [
            ("--props", "props"),
            ("--insights", "insights"),
            ("--games", "games"),
            ("--probable-pitchers", "probable pitchers"),
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


def check_freshness(leagues: list[str], *, now: datetime | None = None) -> bool:
    from . import paths

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    logger.info("Checking data freshness before building pack...")
    for lg in leagues:
        reports = paths.league_paths(lg).reports
        for fname in ("games_line_movement_status_latest.json", "line_movement_status_latest.json"):
            status_file = reports / fname
            if not status_file.exists():
                logger.error(f"Missing status file: {status_file}")
                return False
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                gen_at = data.get("generated_at")
                if not gen_at:
                    logger.error(f"Missing generated_at in {status_file}")
                    return False
                dt = datetime.fromisoformat(str(gen_at).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    logger.error(f"Timezone missing from generated_at in {status_file}: {gen_at}")
                    return False
                age = now - dt.astimezone(timezone.utc)
                if age < -FRESHNESS_FUTURE_TOLERANCE:
                    logger.error(f"Future-dated data in {status_file}: {gen_at}")
                    return False
                if age > FRESHNESS_MAX_AGE:
                    logger.error(f"Stale data (>6h old) in {status_file}: {gen_at}")
                    return False
            except Exception as e:
                logger.error(f"Error checking freshness for {status_file}: {e}")
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


def ingest_pending_settlements(
    inbox_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict | None:
    """Import result-feed CSVs before building the next slate."""
    inbox = inbox_dir or PROJECT_ROOT / "calibration" / "settlements" / "inbox"
    db = db_path or feedback.DEFAULT_DB_PATH
    try:
        stats = feedback.import_settlement_inbox(inbox, db)
    except (OSError, csv.Error, feedback.FeedbackError, ValueError) as exc:
        logger.error("Settlement ingestion failed: %s", exc)
        return None
    if stats["file_count"]:
        logger.info("Settlement ingestion: %s", json.dumps(stats, sort_keys=True))
    if stats["retained_count"]:
        logger.warning(
            "%d settlement file(s) retained for identity repair.", stats["retained_count"]
        )
    return stats


def _count_pack_rows(pack_dir: Path) -> int | None:
    candidates = pack_dir / "candidates.csv"
    if not candidates.exists():
        return None
    try:
        with candidates.open(newline="", encoding="utf-8") as handle:
            candidate_count = max(0, sum(1 for _row in csv.reader(handle)) - 1)
        totals_count = 0
        totals = pack_dir / "game_totals.csv"
        if totals.exists():
            totals_count += rc.count_actionable_game_totals(totals.read_bytes())
        team_totals = pack_dir / "team_totals.csv"
        if team_totals.exists():
            totals_count += rc.count_actionable_team_totals(team_totals.read_bytes())
        return candidate_count + totals_count
    except (OSError, csv.Error, rc.RunnerError) as exc:
        logger.error("Could not count pack rows in %s: %s", pack_dir, exc)
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
        "--run-reasoning",
        action="store_true",
        help="(deprecated) alias for --analysis-profile openai",
    )
    parser.add_argument("--settlement-inbox", type=Path)
    parser.add_argument("--feedback-db", type=Path)
    parser.add_argument(
        "--skip-settlement-ingest",
        action="store_true",
        help="Explicit diagnostic escape hatch; normal daily runs import pending results.",
    )
    args = parser.parse_args(argv)

    leagues = [lg.strip().upper() for lg in args.leagues.split(",")]

    load_environment()

    settlement_stats = {
        "file_count": 0,
        "processed_count": 0,
        "retained_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 0,
    }
    if not args.skip_settlement_ingest:
        imported = ingest_pending_settlements(args.settlement_inbox, args.feedback_db)
        if imported is None:
            return 1
        settlement_stats = imported

    if not perform_auth_check(leagues):
        if not orchestrate_login(leagues):
            logger.error("Authentication failed. Aborting pipeline.")
            return 1

    if not run_explicit_refresh(leagues):
        logger.error("Refresh pipeline failed. Aborting.")
        return 1

    if not check_freshness(leagues):
        logger.error("Freshness check failed. Aborting pipeline before pack build.")
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

        pack_rows = _count_pack_rows(pack_dir)
        empty_pack = pack_rows == 0
        if empty_pack:
            logger.warning("Pack is empty after safety filters; skipping all analysis desk passes.")

        run_steps = []
        if profile == "openai":
            run_steps = ["A"]
        elif profile == "full":
            run_steps = ["A", "B", "C", "D", "E"]

        if not empty_pack and (run_steps or profile == "local"):
            logger.info("Running analysis desk (profile=%s)...", profile)
            try:
                if profile == "local":
                    desk_code = run_desk.orchestrate_desk(
                        pack_dir, steps=["E"], force=False, allow_local_synth=True
                    )
                else:
                    desk_code = run_desk.orchestrate_desk(
                        pack_dir, steps=run_steps, force=False, allow_local_synth=True
                    )
                if desk_code != 0:
                    logger.warning("Desk completed with non-zero (may be partial/degraded).")
            except Exception as e:
                logger.error("Desk orchestration error: %s", e)

        status_path = pack_dir / "reasoning_status.json"
        overall = "ok" if empty_pack else "degraded"
        if not empty_pack and status_path.exists():
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
                overall = st.get("overall", "ok")
            except Exception:
                overall = "degraded"

        manifest = {
            "run_id": f"{pack_dir.name}-{int(time.time())}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "leagues": leagues,
            "profile": profile,
            "overall": overall,
            "pack_rows": pack_rows,
            "status_file": str(status_path) if status_path.exists() else None,
            "settlement_ingest": settlement_stats,
        }
        _atomic_write_manifest(pack_dir, manifest)

        final_code = 0 if str(overall).lower() in SUCCESS_OVERALL_STATUSES else 1
        logger.info(
            "Daily job completed (exit=%s, profile=%s, overall=%s).", final_code, profile, overall
        )
        return final_code
    finally:
        _release_pack_lock(lock_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
