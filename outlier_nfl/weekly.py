"""Weekly NFL refresh: run every remaining slate date of the betting week.

Scheduled (see ``scripts/run_nfl_weekly_snapshots.ps1``) on Tuesday 10:00 and
18:00 ET for the week's opening baseline, then on Thursday 15:00, Sunday 10:30
and Monday 15:00 ET as game-day refreshes. Each run:

1. refreshes the nflverse tape (roles + current injury report) once,
2. runs ``NflPipeline`` for every date in the week that still has games
   (Tuesday through Monday, today onward), appending one price snapshot each,
3. merges the per-date traced best bets into one weekly card.

Only picks whose game is today can be VALIDATED; later games stay PROVISIONAL
(``injury_weather: STALE``) until their own game-day run. No reasoning models.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from outlier_nfl.best_bets import merge_payloads, render_best_bets_markdown
from outlier_nfl.pipeline import NflPipeline, _refresh_tape
from outlier_nfl.snapshots import week_start
from outlier_nfl.utils import safe_read_json, safe_write_json, to_eastern_date, to_eastern_datetime

logger = logging.getLogger("outlier_nfl.weekly")


def remaining_week_dates(events: Iterable[Mapping[str, Any]], today: date) -> list[str]:
    """Eastern slate dates with games from ``today`` through the week's Monday."""
    start = week_start(today)
    end = start + timedelta(days=6)
    dates: set[str] = set()
    for event in events:
        day = to_eastern_date(event.get("scheduledTime") or event.get("startTime"))
        if not day:
            continue
        d = date.fromisoformat(day)
        if today <= d <= end:
            dates.add(day)
    return sorted(dates)


def run_week(
    pipeline: NflPipeline,
    today: date,
    *,
    events: list[Mapping[str, Any]] | None = None,
    offline_fixtures_dir: Path | None = None,
    reports_dir: Path = Path("reports/NFL"),
    run_stamp: str | None = None,
) -> dict[str, Any]:
    """Run each remaining slate date and write the merged weekly card."""
    if events is None:
        schedule = pipeline._get_client().fetch_schedule()
        events = schedule.get("events", []) if isinstance(schedule, dict) else []
    dates = remaining_week_dates(events or [], today)
    logger.info("Week of %s: slate dates %s", week_start(today), dates or "none")

    payloads: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for slate in dates:
        summary = pipeline.run(
            date=slate,
            offline_fixtures_dir=offline_fixtures_dir,
            write_latest=False,
            # Forward the caller's reports dir: the per-slate run writes reports
            # of its own (alt floors, game script), and leaving it unset sent
            # those to ./reports/NFL while the weekly card below honoured
            # --reports-dir.
            reports_dir=reports_dir,
        )
        if summary.get("best_bets_error"):
            raise RuntimeError(f"{slate}: {summary['best_bets_error']}")
        summaries[slate] = summary.get("best_bets_counts", {})
        payload = safe_read_json(pipeline.normalized_dir / f"nfl_best_bets_{slate}.json")
        # Merge only the card this run wrote; an older file must never pass as a refresh.
        if not isinstance(payload, dict) or payload.get("updated_at") != summary.get("timestamp_utc"):
            raise RuntimeError(f"{slate}: best-bets card missing or not from this run")
        payloads.append(payload)

    merged = merge_payloads(payloads, run_date=today.isoformat())
    merged.update({"week_start": week_start(today).isoformat(), "slate_dates": dates})
    safe_write_json(
        pipeline.normalized_dir / f"nfl_best_bets_week_{week_start(today).isoformat()}.json", merged
    )
    stamp = run_stamp or datetime.now(timezone.utc).strftime("%H%M")
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = reports_dir / f"{today.isoformat()}_{stamp}_Weekly_Best_Bets.md"
    report.write_text(
        render_best_bets_markdown(
            merged, title=f"NFL Weekly Traced Best Bets - run {today.isoformat()} {stamp}"
        ),
        encoding="utf-8",
    )
    return {"dates": dates, "per_date": summaries, "counts": merged["counts"], "report": str(report)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh every remaining NFL slate this week.")
    parser.add_argument("--today", default=None, help="Override today's Eastern date (YYYY-MM-DD).")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports/NFL"))
    parser.add_argument("--no-refresh-tape", action="store_true",
                        help="Skip the nflverse tape + injury-report refresh.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    now_et = to_eastern_datetime(datetime.now(timezone.utc))
    today = date.fromisoformat(args.today) if args.today else now_et.date()  # type: ignore[union-attr]
    if not args.no_refresh_tape:
        _refresh_tape(args.data_dir / "NFL", today.isoformat(), None)
    result = run_week(
        NflPipeline(data_dir=args.data_dir),
        today,
        reports_dir=args.reports_dir,
        run_stamp=now_et.strftime("%H%M") + "ET" if now_et else None,
    )
    print("=" * 60)
    print(f"NFL WEEKLY REFRESH - {today.isoformat()}")
    print(f"Slate dates: {', '.join(result['dates']) or 'none'}")
    for slate, counts in result["per_date"].items():
        print(f"  {slate}: {counts}")
    print(f"Week counts: {result['counts']}")
    print(f"Report: {result['report']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
