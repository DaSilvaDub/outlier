from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .api import OutlierApiClient, AuthRequiredError, OutlierApiError
from .environment import load_environment
from .paths import otp_status_file, PROJECT_ROOT
from .otp_fetcher import fetch_and_write_otp
from . import pack
from . import feedback
from . import probability_blend
from . import feed_health
from . import pack_manifest
from . import refresh_plan
from . import results
from . import run_desk
from . import run_state
from . import runner_common as rc
from . import t30_reprice
from . import verdict_store

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
    results = refresh_plan.execute_refresh(leagues, target_date=target_date)
    run_explicit_refresh.last_results = results  # type: ignore[attr-defined]
    return refresh_plan.all_required_ok(results)


run_explicit_refresh.last_results = []  # type: ignore[attr-defined]


def check_freshness(leagues: list[str], *, now: datetime | None = None) -> bool:
    logger.info("Checking unified feed health before building pack...")
    reference_now = now or datetime.now(timezone.utc)
    task_results: list[refresh_plan.RefreshTaskResult] = (
        getattr(run_explicit_refresh, "last_results", []) or []
    )
    all_safe = True
    for league in leagues:
        try:
            if task_results:
                health = feed_health.build_feed_health(
                    league,
                    now=now,
                    write=True,
                    task_results=task_results,
                )
            else:
                health = feed_health.build_feed_health(league, now=now, write=True)
            safe, reasons = feed_health.validate_feed_health(health)
        except Exception as exc:
            logger.error("%s feed-health evaluation failed: %s", league, exc)
            all_safe = False
            continue
        if not safe:
            # An out-of-season league is unsafe by this gate's own math every
            # single day: games.py preserves the last real games snapshot on
            # a zero-event day instead of overwriting it with an empty one,
            # so that artifact's age -- and everything derived from it --
            # never clears on its own. The producer's own fresh status is the
            # authoritative "nothing scheduled today" signal; only that, not
            # the stale-artifact symptom, makes this non-fatal.
            if feed_health.league_has_confirmed_empty_slate(league, now=reference_now):
                logger.info(
                    "%s feed health flagged unsafe (%s) but the league has no scheduled "
                    "games today; treating as non-fatal.",
                    league,
                    "; ".join(reasons),
                )
                continue
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


def run_pack(
    leagues: list[str], target_date: str | None = None, feedback_db: Path | None = None
) -> Path | None:
    logger.info("Building pack...")
    args = ["--leagues", ",".join(leagues)]
    if target_date:
        args.extend(["--date", target_date])
    if feedback_db:
        args.extend(["--feedback-db", str(feedback_db)])
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


def refit_blend_weights(
    db_path: Path | None = None,
    weights_path: Path | None = None,
) -> dict:
    """Refit market/model blend weights from settled ledger rows before the pack.

    The fitted artifact only improves as settlements accumulate, so it is
    refitted every run instead of whenever someone remembers to call
    ``feedback fit-blend`` by hand. Failure is non-fatal: the pack falls back to
    the previous artifact (or to market-only) rather than skipping a slate.
    """

    db = db_path or feedback.DEFAULT_DB_PATH
    output = weights_path or probability_blend.DEFAULT_WEIGHTS_PATH
    if not Path(db).exists():
        logger.warning("Blend refit skipped: no feedback ledger at %s", db)
        return {"status": "skipped", "reason": "missing_ledger"}
    try:
        artifact = feedback.fit_blend_weights(Path(db), Path(output))
    except (OSError, sqlite3.Error, feedback.FeedbackError, ValueError) as exc:
        logger.error("Blend refit failed (keeping previous weights): %s", exc)
        return {"status": "failed", "error": str(exc)[:300]}
    promotion = probability_blend.promotion_status(artifact)
    logger.info(
        "Blend refit: status=%s eligible_samples=%s model_version=%s drives_sizing=%s",
        artifact.get("status"),
        artifact.get("eligible_samples"),
        artifact.get("model_version"),
        promotion["drives_sizing"],
    )
    if not promotion["drives_sizing"]:
        logger.info("Blend stays audit-only: %s", "; ".join(promotion["reasons"]))
    return {
        "status": "ok",
        "artifact_status": artifact.get("status"),
        "eligible_samples": artifact.get("eligible_samples"),
        "model_version": artifact.get("model_version"),
        "drives_sizing": promotion["drives_sizing"],
        "promotion_reasons": promotion["reasons"],
    }


def maintain_feedback_ledger(
    db_path: Path | None = None,
    *,
    cutoff_days: int = 90,
    recompute_clv: bool = True,
    run_retention: bool = True,
) -> dict:
    """Run bounded ledger maintenance; each operation is independent and non-fatal."""
    db = Path(db_path or feedback.DEFAULT_DB_PATH)
    if not db.exists():
        return {"status": "skipped", "reason": "missing_ledger"}
    result: dict = {"status": "ok", "cutoff_days": cutoff_days}
    failures = 0
    if recompute_clv:
        try:
            result["clv"] = {"status": "ok", **feedback.recompute_settlement_clv(db)}
        except (OSError, sqlite3.Error, feedback.FeedbackError, ValueError) as exc:
            failures += 1
            result["clv"] = {"status": "failed", "error": str(exc)[:300]}
            logger.error("Settlement CLV recompute failed (continuing): %s", exc)
    else:
        result["clv"] = {"status": "skipped", "reason": "disabled"}
    if run_retention:
        try:
            retention = feedback.apply_retention_policy(db, cutoff_days=cutoff_days)
            result["retention"] = {"status": "ok", **asdict(retention)}
        except (OSError, sqlite3.Error, feedback.FeedbackError, ValueError) as exc:
            failures += 1
            result["retention"] = {"status": "failed", "error": str(exc)[:300]}
            logger.error("Feedback retention failed (continuing): %s", exc)
    else:
        result["retention"] = {"status": "skipped", "reason": "disabled"}
    attempted = int(recompute_clv) + int(run_retention)
    result["status"] = (
        "failed" if failures == attempted and attempted else ("partial" if failures else "ok")
    )
    return result


def persist_desk_feedback(pack_dir: Path, db_path: Path | None = None) -> dict:
    db = Path(db_path or feedback.DEFAULT_DB_PATH)
    if not db.exists():
        return {"status": "skipped", "reason": "missing_ledger"}
    if not (pack_dir / "verdicts" / "desk_snapshot.json").exists():
        return {"status": "skipped", "reason": "missing_desk_snapshot"}
    try:
        stats = verdict_store.persist_desk_snapshot(pack_dir, db)
    except (OSError, sqlite3.Error, verdict_store.VerdictPersistenceError, ValueError) as exc:
        logger.error("Desk feedback projection failed closed (continuing): %s", exc)
        return {"status": "failed", "error": str(exc)[:300]}
    return {
        "status": "ok",
        "publications": stats.publications,
        "records": stats.records,
        "decisions_updated": stats.decisions_updated,
    }


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
    from .desk_snapshot import LockBusy, acquire_daily_lock

    packs_dir = PROJECT_ROOT / "packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    try:
        return acquire_daily_lock(packs_dir / "_job")
    except LockBusy:
        return None


def _release_writer_lock(lock_dir: Path | None) -> None:
    from .desk_snapshot import release_daily_lock

    release_daily_lock(lock_dir)


def _atomic_write_manifest(pack_dir: Path, data: dict) -> None:
    pack_manifest.write_manifest(pack_dir, data)


def _run_pre_pack_maintenance(
    args: argparse.Namespace, leagues: list[str]
) -> tuple[int | None, dict, dict, dict, dict]:
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
            return 1, result_collection, settlement_stats, {}, {}
        settlement_stats = imported

    feedback_maintenance = {"status": "skipped", "reason": "disabled"}
    if not args.skip_feedback_maintenance:
        feedback_maintenance = maintain_feedback_ledger(
            args.feedback_db,
            cutoff_days=args.feedback_retention_days,
            recompute_clv=not args.skip_clv_recompute,
            run_retention=not args.skip_feedback_retention,
        )

    blend_refit = {"status": "skipped"}
    if not args.skip_blend_refit:
        blend_refit = refit_blend_weights(args.feedback_db)
    return None, result_collection, settlement_stats, feedback_maintenance, blend_refit


def _ensure_authenticated(leagues: list[str]) -> bool:
    if perform_auth_check(leagues):
        return True
    if orchestrate_login(leagues):
        return True
    logger.error("Authentication failed. Aborting pipeline.")
    return False


def _run_refresh_and_health(leagues: list[str], target_date: str | None) -> bool:
    run_explicit_refresh.last_results = []  # type: ignore[attr-defined]
    if not run_explicit_refresh(leagues, target_date=target_date):
        logger.error("Refresh pipeline failed. Aborting.")
        return False
    if not check_freshness(leagues):
        logger.error("Feed-health check failed. Aborting pipeline before pack build.")
        return False
    return True


def _desk_steps_for_profile(profile: str) -> list[str]:
    if profile == "openai":
        return ["A"]
    if profile == "full":
        return ["A", "B", "C", "D", "E"]
    return ["E"]


def _run_pack_and_desk(
    args: argparse.Namespace, leagues: list[str]
) -> tuple[Path | None, str, int | None]:
    pack_dir = run_pack(leagues, target_date=args.date, feedback_db=args.feedback_db)
    if pack_dir is None:
        logger.error("Pack generation failed. Aborting.")
        return None, args.analysis_profile, None

    profile = args.analysis_profile
    if args.run_reasoning and profile == "local":
        profile = "openai"

    pack_rows = _count_pack_rows(pack_dir)
    if pack_rows == 0:
        logger.warning("Pack is empty after safety filters; skipping all analysis desk passes.")
        return pack_dir, profile, pack_rows

    run_steps = _desk_steps_for_profile(profile)
    logger.info("Running analysis desk (profile=%s)...", profile)
    try:
        desk_code = run_desk.orchestrate_desk(
            pack_dir,
            steps=run_steps,
            force=False,
            allow_local_synth=True,
            hold_locks=False,
        )
        if desk_code != 0:
            logger.warning("Desk completed with non-zero (may be partial/degraded).")
    except Exception as exc:
        logger.error("Desk orchestration error: %s", exc)
    return pack_dir, profile, pack_rows


def _log_identity_audit(pack_dir: Path) -> None:
    path = pack_dir / "identity_audit.json"
    if not path.exists():
        logger.info("Pitcher identity audit: identity_audit.json not written for %s", pack_dir.name)
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Pitcher identity audit: failed to read %s (%s)", path, exc)
        return
    logger.info(
        "Pitcher identity audit: status=%s so=%s mismatch=%s unconfirmed=%s",
        payload.get("status"),
        payload.get("so_rows"),
        payload.get("mismatch_count"),
        payload.get("unconfirmed_count"),
    )


def _finalize_run(
    pack_dir: Path,
    args: argparse.Namespace,
    leagues: list[str],
    profile: str,
    pack_rows: int | None,
    settlement_stats: dict,
    result_collection: dict,
    blend_refit: dict,
    feedback_maintenance: dict,
) -> int:
    desk_feedback = persist_desk_feedback(pack_dir, args.feedback_db)
    status_path = pack_dir / "reasoning_status.json"
    overall = "ok" if pack_rows == 0 else "degraded"
    if pack_rows != 0 and status_path.exists():
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            overall = status_payload.get("overall", "ok")
        except Exception:
            overall = "degraded"

    manifest = pack_manifest.build_manifest_v2(
        pack_dir,
        run_id=f"{pack_dir.name}-{int(time.time())}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        leagues=leagues,
        profile=profile,
        overall=overall,
        pack_rows=pack_rows,
        extra={
            "settlement_ingest": settlement_stats,
            "result_collection": result_collection,
            "blend_refit": blend_refit,
            "feedback_maintenance": feedback_maintenance,
            "desk_feedback": desk_feedback,
        },
    )
    _atomic_write_manifest(pack_dir, manifest)
    _log_identity_audit(pack_dir)
    final_code = run_state.exit_code_for_overall(overall)
    logger.info(
        "Daily job completed (exit=%s, profile=%s, overall=%s).", final_code, profile, overall
    )
    return final_code


def _run_locked_pipeline(args: argparse.Namespace, leagues: list[str]) -> int:
    early, result_collection, settlement_stats, feedback_maintenance, blend_refit = (
        _run_pre_pack_maintenance(args, leagues)
    )
    if early is not None:
        return early
    if not _ensure_authenticated(leagues):
        return 1
    if not _run_refresh_and_health(leagues, args.date):
        return 1
    pack_dir, profile, pack_rows = _run_pack_and_desk(args, leagues)
    if pack_dir is None:
        return 1
    return _finalize_run(
        pack_dir,
        args,
        leagues,
        profile,
        pack_rows,
        settlement_stats,
        result_collection,
        blend_refit,
        feedback_maintenance,
    )


def main(argv: list[str] | None = None) -> int:
    from outlier_scrapers.database import init_db

    init_db()
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
        "--skip-blend-refit",
        action="store_true",
        help="Explicit diagnostic escape hatch; normal daily runs refit blend weights.",
    )
    parser.add_argument(
        "--skip-settlement-ingest",
        action="store_true",
        help="Explicit diagnostic escape hatch; normal daily runs import pending results.",
    )
    parser.add_argument(
        "--skip-feedback-maintenance",
        action="store_true",
        help="Skip all automatic feedback-ledger maintenance.",
    )
    parser.add_argument(
        "--skip-clv-recompute",
        action="store_true",
        help="Skip settlement CLV repair during maintenance.",
    )
    parser.add_argument(
        "--skip-feedback-retention",
        action="store_true",
        help="Skip feedback-ledger retention during maintenance.",
    )
    parser.add_argument(
        "--feedback-retention-days",
        type=int,
        default=90,
        help="Retention cutoff in days (default: 90).",
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

    if not args.date:
        args.date = datetime.now().astimezone().date().isoformat()

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
