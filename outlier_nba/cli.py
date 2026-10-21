"""NBA CLI (Phase 1 shadow capture and settlement; Phase 2 team model).

    python -m outlier_nba.cli capture [--db LEDGER] [--nba-dir DIR] [--fixture]
    python -m outlier_nba.cli settle  [--db LEDGER] [--lookback-days N]
    python -m outlier_nba.cli backfill --stage scoreboard|summary|build [--seasons A:B]
    python -m outlier_nba.cli ratings [--date D] [--no-refresh]   # refresh results, fit as of D
    python -m outlier_nba.cli run     [--date D] [--nba-dir DIR]  # price the slate, SHADOW only
    python -m outlier_nba.cli backtest [--seasons A:B] [--tune-season S]

Exit codes: 0 OK, 1 auth/IO failure, 3 a required stage did not pass (publication
withheld; the bundle is kept). No reasoning-model calls anywhere in this CLI.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from outlier_nba.ingest import outlier as ingest
from outlier_nba.pipeline import EXIT_OK, NbaPipeline, PipelineResult, Stage
from outlier_nba.receipts import StageReceipt, failing, receipt
from outlier_nba.run_context import AsOfReader, RunContext, make_run_context
from outlier_nba.run_writer import RunWriter
from outlier_nba.track import capture as capture_mod
from outlier_scrapers import paths

logger = structlog.get_logger("outlier_nba.cli")

DEFAULT_NBA_DIR = paths.PROJECT_ROOT / "data" / "NBA"
DEFAULT_STORE_PATH = DEFAULT_NBA_DIR / "store" / "nba_games.sqlite3"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _slate_date(slate: ingest.Slate, as_of: datetime) -> str:
    """Eastern date of the first open tip (NBA slates are named by US evening date)."""
    from zoneinfo import ZoneInfo

    tips = sorted(r.tip for r in slate.records) or [as_of]
    return tips[0].astimezone(ZoneInfo("America/New_York")).date().isoformat()


def run_capture(
    client: ingest.NbaClient,
    *,
    db_path: Path,
    nba_dir: Path,
    published_dir: Path | None = None,
    clock: Callable[[], datetime] = _utc_now,
    fixture: bool = False,
    method: str = "shin",
) -> PipelineResult:
    """Fetch the live NBA slate and record it as SHADOW rows, gated by stage receipts."""
    bundle = ingest.fetch_raw(client, clock=clock)
    as_of = bundle.fetch_finished
    slate = ingest.normalize_slate(bundle, as_of)
    ctx = make_run_context(
        _slate_date(slate, as_of),
        as_of,
        fixture=fixture,
        slate_events=slate.events,
        run_started_utc=as_of,
    )
    pipe = NbaPipeline(nba_dir=nba_dir, published_dir=published_dir or nba_dir / "published")
    stages = _capture_stages(bundle, slate, db_path, method)
    result = pipe.run(ctx, stages)
    logger.info(
        "nba_capture",
        run_id=result.run_id,
        status=result.status,
        published=result.published,
        exit_code=result.exit_code,
        withheld=result.withheld_reason,
    )
    return result


def _capture_stages(
    bundle: ingest.RawBundle, slate: ingest.Slate, db_path: Path, method: str
) -> list[Stage]:
    def feeds(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> list[StageReceipt]:
        writer.stage_json(
            "raw/outlier_nba_raw.json",
            {
                "schedule": bundle.schedule,
                "player_props": bundle.props,
                "event_markets": bundle.event_markets,
                "event_errors": bundle.event_errors,
                "fetch_started": bundle.fetch_started,
                "fetch_finished": bundle.fetch_finished,
            },
        )
        return ingest.raw_receipts(bundle, slate)

    def normalize(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        # Every record is stamped with the fetch start and must precede both the run's
        # cutoff and its own tip; the reader drops (and counts) anything that does not.
        reader = AsOfReader(ctx)
        stamped = [
            {"rec": r, "captured_at": bundle.fetch_started.isoformat(), "tip": r.tip.isoformat()}
            for r in slate.records
        ]
        admitted = [
            row["rec"] for row in reader.admit(stamped, stamp_key="captured_at", tip_key="tip")
        ]
        state["records"] = admitted
        counts = ingest.count_by_kind(admitted)
        writer.stage_json(
            "slate_summary.json",
            {
                "counts": counts,
                "skipped": slate.skipped,
                "rejected_post_cutoff": reader.rejected_post_cutoff,
            },
        )
        return receipt(
            "normalize",
            "OK" if admitted else "EMPTY",
            received=len(admitted),
            reason=None if admitted else "no open NBA outcomes on the slate",
        )

    def shadow_rows(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        cap = capture_mod.build_shadow_capture(
            state["records"], captured_at=bundle.fetch_started, method=method
        )
        state["capture"] = cap
        writer.stage_text("shadow_candidates.csv", capture_mod.rows_to_csv(cap.rows))
        writer.stage_text(
            "capture_summary.json", capture_mod.capture_summary_json(cap, {"run_id": ctx.run_id})
        )
        return receipt("shadow_rows", "OK" if cap.rows else "EMPTY", received=len(cap.rows))

    def ledger(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        prior = failing(state["receipts"])
        if prior:
            return receipt(
                "ledger_capture", "EMPTY", reason=f"skipped: {len(prior)} required stage(s) not OK"
            )
        if not ctx.publishes:
            return receipt(
                "ledger_capture", "EMPTY", reason=f"skipped: {ctx.mode} run is bundle-only"
            )
        cap = state["capture"]
        if not cap.rows:
            return receipt("ledger_capture", "EMPTY", reason="no rows to record")
        with tempfile.TemporaryDirectory(prefix="nba-capture-") as tmp:
            stats = capture_mod.write_to_ledger(
                cap.rows,
                cap.method_probs,
                db_path=db_path,
                work_dir=Path(tmp),
                recorded_path=writer.run_dir,
            )
        return receipt("ledger_capture", "OK", received=stats["snapshots"])

    return [
        Stage("feeds", True, feeds),
        Stage("normalize", True, normalize),
        Stage("shadow_rows", True, shadow_rows),
        Stage("ledger_capture", True, ledger),
    ]


def run_settle(db_path: Path, *, lookback_days: int = 3) -> dict[str, Any]:
    from outlier_nba.track.settle import settle_nba

    return settle_nba(db_path, lookback_days=lookback_days)


def _today_et() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def run_ratings(args: argparse.Namespace) -> int:
    """Refresh recent results, rebuild the store, fit the team model as of ``--date``."""
    from datetime import date

    from outlier_nba import team_run
    from outlier_nba.ingest import espn

    day = args.date or _today_et()
    if not args.no_refresh:
        cache = espn.EspnCache(args.cache_dir or espn.default_cache_dir())
        try:
            refreshed = espn.refresh_recent(cache, date.fromisoformat(day))
        except espn.BlockedError as exc:
            print(f"blocked: {exc} (stopped; the existing store is used as is)")
            refreshed = {}
        with tempfile.TemporaryDirectory(prefix="nba-store-") as tmp:
            built = Path(tmp) / "nba_games.sqlite3"
            espn.build_store(cache, built)
            try:
                espn.install_store(built, args.store)
            except espn.StoreRegressionError as exc:
                # a cleared or partial cache must never shrink the installed history
                print(f"store_not_replaced: {exc}")
        logger.info("nba_ratings_refreshed", **refreshed)
    params, source = team_run.load_params()
    try:
        state = team_run.compute_team_state(
            team_run.load_store_games(args.store), day, params, source
        )
    except team_run.TeamStateError as exc:
        print(f"team_state_unavailable: {exc}")
        return 3
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    table = team_run.ratings_rows(state)
    (out_dir / f"team_ratings_{day}.csv").write_text(
        team_run.rows_to_csv(table, list(table[0])), encoding="utf-8"
    )
    summary = team_run.state_summary(state)
    (out_dir / f"team_model_state_{day}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True))
    return EXIT_OK


def run_team(args: argparse.Namespace) -> int:
    from outlier_nba import team_run

    from outlier_scrapers.api import OutlierApiClient

    try:
        client = OutlierApiClient()
    except Exception as exc:
        print(f"auth_required: {exc}")
        return 1
    result = team_run.run_team_shadow(
        client,
        games_loader=lambda: team_run.load_store_games(args.store),
        nba_dir=args.nba_dir,
        fixture=args.fixture,
        slate_date=args.date,
        n_draws=args.draws,
    )
    print(
        json.dumps(
            {**dataclasses.asdict(result), "receipts": [r.to_dict() for r in result.receipts]},
            default=str,
            sort_keys=True,
        )
    )
    return result.exit_code


def run_backtest_cmd(args: argparse.Namespace) -> int:
    from outlier_nba import team_run
    from outlier_nba.ingest import espn
    from outlier_nba.ratings.model import TeamParams
    from outlier_nba.track import backtest

    first, _, last = str(args.seasons).partition(":")
    games = team_run.load_store_games(args.store)
    report = backtest.run_backtest(
        games,
        first_scored=first,
        last_scored=last or first,
        tune_season=args.tune_season,
        n_draws=args.draws,
        tune_params=not args.no_tune,
        start_params=TeamParams.from_dict(team_run.load_params()[0].to_dict()),
    )
    report["coverage"] = espn.coverage_report(args.store)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    if args.write_config and not args.no_tune:
        team_run.MODEL_CONFIG_PATH.write_text(
            json.dumps(
                {
                    "params": report["params"],
                    "tuned_on_through": args.tune_season,
                    "scored_window": report["scored_seasons"],
                },
                indent=2,
                sort_keys=True,
            )  # fmt: skip
            + "\n",
            encoding="utf-8",
        )
    print(f"wrote {args.out}")
    return EXIT_OK


def run_backfill(args: argparse.Namespace) -> int:
    """Network stages fill the cache; ``build`` builds in local temp, then installs + verifies."""
    from outlier_nba.ingest import espn

    cache = espn.EspnCache(args.cache_dir or espn.default_cache_dir())
    first, _, last = str(args.seasons).partition(":")
    seasons = espn.seasons_between(first, last or first)
    if args.stage in ("scoreboard", "summary"):
        try:
            print(json.dumps(espn.run_fetch(args.stage, seasons, cache), sort_keys=True))
        except espn.BlockedError as exc:
            print(f"blocked: {exc} (stopped; rerun later, the cache keeps what was fetched)")
            return 4
        return EXIT_OK
    with tempfile.TemporaryDirectory(prefix="nba-store-") as tmp:
        built = Path(tmp) / "nba_games.sqlite3"
        espn.build_store(cache, built)
        try:
            final = espn.install_store(built, args.store)
        except espn.StoreRegressionError as exc:
            print(f"store_not_replaced: {exc}")
            return 5
    print(
        json.dumps(
            {"installed": str(args.store), **final, **espn.coverage_report(args.store)},
            sort_keys=True,
            default=str,
        )
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NBA shadow capture and settlement")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "settle"):
        p = sub.add_parser(name)
        p.add_argument(
            "--db",
            type=Path,
            default=paths.FEEDBACK_DB_PATH,
            help="feedback ledger (default: the shared ledger, D3)",
        )
    sub.choices["capture"].add_argument("--nba-dir", type=Path, default=DEFAULT_NBA_DIR)
    sub.choices["capture"].add_argument(
        "--fixture", action="store_true", help="treat the run as a fixture run (publishes)"
    )
    sub.choices["capture"].add_argument(
        "--only-if-due",
        action="store_true",
        help="skip unless a tip within 60 min lacks a verified-window capture (scheduler use)",
    )
    sub.choices["settle"].add_argument("--lookback-days", type=int, default=3)
    bf = sub.add_parser("backfill", help="cache + store historical NBA results (ESPN public JSON)")
    bf.add_argument("--seasons", default="2016-17:2025-26", help="FIRST:LAST season labels")
    bf.add_argument("--stage", choices=["scoreboard", "summary", "build"], required=True)
    bf.add_argument("--cache-dir", type=Path, default=None)
    bf.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    rt = sub.add_parser("ratings", help="refresh results and fit team ratings as of a date")
    rt.add_argument("--date", default=None, help="slate date YYYY-MM-DD (default: today, ET)")
    rt.add_argument("--no-refresh", action="store_true", help="skip the ESPN refresh")
    rt.add_argument("--cache-dir", type=Path, default=None)
    rt.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    rt.add_argument("--out-dir", type=Path, default=DEFAULT_NBA_DIR / "model")
    rn = sub.add_parser("run", help="price the slate from the team model (SHADOW rows only)")
    rn.add_argument("--date", default=None, help="slate date YYYY-MM-DD (default: first tip, ET)")
    rn.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    rn.add_argument("--nba-dir", type=Path, default=DEFAULT_NBA_DIR)
    rn.add_argument("--draws", type=int, default=20_000)
    rn.add_argument("--fixture", action="store_true")
    bk = sub.add_parser("backtest", help="walk-forward backtest of the team model")
    bk.add_argument("--seasons", default="2018-19:2025-26", help="scoring window FIRST:LAST")
    bk.add_argument("--tune-season", default="2017-18", help="last season used for tuning")
    bk.add_argument("--no-tune", action="store_true")
    bk.add_argument("--write-config", action="store_true", help="save tuned params to config/")
    bk.add_argument("--draws", type=int, default=4000)
    bk.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    bk.add_argument(
        "--out", type=Path, default=DEFAULT_NBA_DIR / "backtest" / "phase2_backtest.json"
    )
    args = parser.parse_args(argv)

    if args.command == "backfill":
        return run_backfill(args)
    if args.command == "ratings":
        return run_ratings(args)
    if args.command == "run":
        return run_team(args)
    if args.command == "backtest":
        return run_backtest_cmd(args)

    if args.command == "settle":
        print(json.dumps(run_settle(args.db, lookback_days=args.lookback_days), sort_keys=True))
        return EXIT_OK

    if args.fixture and args.db == paths.FEEDBACK_DB_PATH:
        # fixture runs publish, so they must never write the shared ledger
        print("--fixture requires an explicit non-default --db")
        return 2

    if args.only_if_due:
        from outlier_nba.track import due as due_mod

        now = _utc_now()
        due = due_mod.capture_due(args.db, now, last_attempt=due_mod.read_last_attempt(args.db))
        if not due.due:
            print(
                json.dumps(
                    {"skipped": True, **dataclasses.asdict(due)}, default=_iso, sort_keys=True
                )
            )
            return EXIT_OK
        logger.info("nba_capture_due", reason=due.reason)
        due_mod.record_attempt(args.db, now)

    from outlier_scrapers.api import OutlierApiClient

    try:
        client = OutlierApiClient()
    except Exception as exc:
        print(f"auth_required: {exc}")
        return 1
    result = run_capture(client, db_path=args.db, nba_dir=args.nba_dir, fixture=args.fixture)
    print(
        json.dumps(
            {**dataclasses.asdict(result), "receipts": [r.to_dict() for r in result.receipts]},
            default=str,
            sort_keys=True,
        )
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
