#!/usr/bin/env python3
"""Grade a slate's NFL matchup/weather/usage signals against nflverse box scores.

Usage:
    python scripts/nfl_signal_scorecard.py --date 2026-09-27
    python scripts/nfl_signal_scorecard.py --date 2026-09-27 --week 3 --season 2026

Reads data/NFL/normalized/nfl_matchup_scripts_<date>.json (and, when present,
nfl_calibrated_props_<date>.json for consensus lines), downloads the season's
nflverse player-week stats, then:

- writes reports/NFL/<date>_Signal_Scorecard.md,
- replaces this date's rows in data/NFL/scorecard/ledger.jsonl (cumulative),
- prints the slate and cumulative per-signal hit rates.

Run it after the slate's games are final and nflverse has posted them
(usually the next morning).
"""

from __future__ import annotations

import argparse
from datetime import date as date_cls
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from outlier_nfl.scorecard import (  # noqa: E402
    direction_baselines,
    grade_signals,
    render_markdown,
    summarize,
    update_ledger,
)
from outlier_nfl.tape_nflverse import SCHEDULES_URL, fetch_csv  # noqa: E402
from outlier_nfl.usage import PLAYER_WEEK_URL  # noqa: E402


def _slate_week(season: int, slate: str) -> int:
    weeks = {
        int(g["week"])
        for g in fetch_csv(SCHEDULES_URL)
        if g.get("season") == str(season) and g.get("game_type") == "REG" and g.get("gameday") == slate
    }
    if not weeks:
        raise SystemExit(f"No {season} regular-season games on {slate}; pass --week explicitly.")
    return min(weeks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", required=True, help="Slate date (YYYY-MM-DD, Eastern).")
    parser.add_argument("--week", type=int, default=None, help="Slate week (default: from schedule).")
    parser.add_argument("--season", type=int, default=None, help="Season (default: from date).")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports/NFL"))
    args = parser.parse_args()

    slate = date_cls.fromisoformat(args.date)
    season = args.season or (slate.year if slate.month >= 3 else slate.year - 1)
    normalized = args.data_dir / "NFL" / "normalized"
    scripts_path = normalized / f"nfl_matchup_scripts_{args.date}.json"
    if not scripts_path.exists():
        print(f"Missing {scripts_path}; run the pipeline for {args.date} first.")
        return 1
    scripts = json.loads(scripts_path.read_text(encoding="utf-8")).get("records", [])
    props_path = normalized / f"nfl_calibrated_props_{args.date}.json"
    props = (
        json.loads(props_path.read_text(encoding="utf-8")).get("records", [])
        if props_path.exists() else []
    )

    week = args.week or _slate_week(season, args.date)
    player_rows = fetch_csv(PLAYER_WEEK_URL.format(season=season))
    if not any(r.get("week") == str(week) for r in player_rows):
        print(f"nflverse has no week {week} box scores yet; try again after the games are posted.")
        return 1

    graded, skipped = grade_signals(args.date, week, scripts, player_rows, props)
    ledger = update_ledger(args.data_dir / "NFL" / "scorecard" / "ledger.jsonl", graded, args.date)
    slate_summary = summarize([g.__dict__ for g in graded])
    cumulative = summarize(ledger)
    baselines = direction_baselines(week, player_rows, {g.market for g in graded})

    report = render_markdown(args.date, week, slate_summary, cumulative, baselines, graded, skipped)
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    out = args.reports_dir / f"{args.date}_Signal_Scorecard.md"
    out.write_text(report, encoding="utf-8")

    print(f"Week {week}: graded {len(graded)} signals, {len(skipped)} ungraded -> {out}")
    for label, summary in (("SLATE", slate_summary), ("CUMULATIVE", cumulative)):
        print(label)
        for s in summary:
            print(f"  {s['tag']:24} vs avg {s['vs_avg']:>7}  vs line {s['vs_line']:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
