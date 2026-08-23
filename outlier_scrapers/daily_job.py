from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
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
from . import feedback
from . import feed_health
from . import results
from . import run_desk
from . import runner_common as rc
from . import t30_reprice

logger = logging.getLogger(__name__)

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


def run_explicit_refresh(leagues: list[str], target_date: str | None = None) -> bool:
    logger.info("Starting explicitly ordered refresh pipeline.")
    for league in leagues:
        args = ["--league", league]
        if target_date:
            args.extend(["--date", target_date])
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
    logger.info("Checking unified feed health before building pack...")
    all_safe = True
    for league in leagues:
        try:
            health = feed_health.build_feed_health(league, now=now, write=True)
            safe, reasons = feed_health.validate_feed_health(health)
        except Exception as exc:
            logger.error("%s feed-health evaluation failed: %s", league, exc)
            all_safe = False
            continue
        if not safe:
            logger.error("%s feed health is unsafe: %s", league, "; ".join(reasons))
            all_safe = False
        else:
            logger.info(
                "%s feed health accepted (coverage=%.2f%%, failed_ids=%d)",
                league,
                float(health.get("coverage_pct") or 0.0),
                len(health.get("failed_ids") or []),
            )
    return all_safe


def run_pack(leagues: list[str], target_date: str | None = None) -> Path | None:
    logger.info("Building pack...")
    args = ["--leagues", ",".join(leagues)]
    if target_date:
        args.extend(["--date", target_date])
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


def collect_completed_results(
    leagues: list[str],
    db_path: Path | None = None,
    output_dir: Path | None = None,
    *,
    lookback_days: int = 3,
) -> dict:
    """Grade completed ledger rows without blocking a new-slate refresh on feed downtime."""
    db = db_path or feedback.DEFAULT_DB_PATH
    output = output_dir or results.DEFAULT_OUTPUT_DIR
    try:
        stats = results.collect_and_import(
            db,
            leagues=leagues,
            lookback_days=lookback_days,
            output_dir=output,
        )
    except (
        OSError,
        sqlite3.Error,
        results.ResultsError,
        feedback.FeedbackError,
        ValueError,
    ) as exc:
        logger.error("Automatic result collection failed closed: %s", exc)
        return {"status": "failed", "error": str(exc)[:300]}
    logger.info("Automatic result collection: %s", json.dumps(stats, sort_keys=True))
    return {"status": "ok", **stats}


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


def _acquire_writer_lock() -> Path | None:
    packs_dir = PROJECT_ROOT / "packs"
    lock_dir = packs_dir / ".daily_job_lock"
    packs_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir(exist_ok=False)
        return lock_dir
    except FileExistsError:
        return None


def _release_writer_lock(lock_dir: Path | None) -> None:
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


def _run_locked_pipeline(args: argparse.Namespace, leagues: list[str]) -> int:
    result_collection = {"status": "skipped"}
    if not args.skip_result_collection:
        result_collection = collect_completed_results(
            leagues,
            args.feedback_db,
            args.results_output,
            lookback_days=args.results_lookback_days,
        )

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
        logger.error("Feed-health check failed. Aborting pipeline before pack build.")
        return 1

    pack_dir = run_pack(leagues)
    if pack_dir is None:
        logger.error("Pack generation failed. Aborting.")
        return 1

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
        except Exception as exc:
            logger.error("Desk orchestration error: %s", exc)

    status_path = pack_dir / "reasoning_status.json"
    overall = "ok" if empty_pack else "degraded"
    if not empty_pack and status_path.exists():
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            overall = status_payload.get("overall", "ok")
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
        "result_collection": result_collection,
    }
    _atomic_write_manifest(pack_dir, manifest)

    final_code = 0 if str(overall).lower() in SUCCESS_OVERALL_STATUSES else 1
    logger.info(
        "Daily job completed (exit=%s, profile=%s, overall=%s).", final_code, profile, overall
    )
    return final_code


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Daily Outlier Orchestration Job")
    parser.add_argument("--date", help="Specific target date (YYYY-MM-DD)")
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
    parser.add_argument("--results-output", type=Path)
    parser.add_argument("--results-lookback-days", type=int, default=3)
    parser.add_argument(
        "--skip-result-collection",
        action="store_true",
        help="Explicit diagnostic escape hatch; normal daily runs grade completed events.",
    )
    parser.add_argument(
        "--skip-settlement-ingest",
        action="store_true",
        help="Explicit diagnostic escape hatch; normal daily runs import pending results.",
    )
    parser.add_argument(
        "--t30-reprice-only",
        action="store_true",
        help="Run the due/idempotent T-30 pass under the normal daily writer lock.",
    )
    parser.add_argument(
        "--t30-pack-dir",
        type=Path,
        help="Pack to reprice; defaults to packs/<local-today>.",
    )
    parser.add_argument(
        "--force-t30",
        action="store_true",
        help="Bypass the T-30 timing and already-completed gates.",
    )
    args = parser.parse_args(argv)

    leagues = [lg.strip().upper() for lg in args.leagues.split(",")]

    load_environment()
    lock_dir = _acquire_writer_lock()
    if lock_dir is None:
        logger.error("Writer lock conflict at packs/.daily_job_lock (another daily job running).")
        return 2

    try:
        if args.t30_reprice_only:
            t30_pack_dir = args.t30_pack_dir or (
                PROJECT_ROOT / "packs" / datetime.now().astimezone().date().isoformat()
            )
            try:
                t30_result = t30_reprice.run_t30_reprice(
                    t30_pack_dir,
                    feedback_db=args.feedback_db or feedback.DEFAULT_DB_PATH,
                    force=args.force_t30,
                )
            except Exception as exc:
                logger.error("T-30 repricing failed: %s", exc)
                return 1
            logger.info(
                "T-30 repricing %s (%s).",
                "completed" if t30_result.completed else "skipped",
                t30_result.reason,
            )
            return 0
        return _run_locked_pipeline(args, leagues)
    finally:
        _release_writer_lock(lock_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
