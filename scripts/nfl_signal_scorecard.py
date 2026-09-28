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
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
# Fixed locations: no file path in this script comes from the command line.
NORMALIZED_DIR = REPO_ROOT / "data" / "NFL" / "normalized"
LEDGER_PATH = REPO_ROOT / "data" / "NFL" / "scorecard" / "ledger.jsonl"
REPORTS_DIR = REPO_ROOT / "reports" / "NFL"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from outlier_nfl.scorecard import (  # noqa: E402
    direction_baselines,
    grade_signals,
    load_slate_records,
    render_markdown,
    summarize,
    update_ledger,
    write_report,
)
from outlier_nfl.tape_nflverse import SCHEDULES_URL, fetch_csv  # noqa: E402
from outlier_nfl.usage import fetch_player_weeks  # noqa: E402


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
    parser.add_argument("--date", required=True, type=date_cls.fromisoformat,
                        help="Slate date (YYYY-MM-DD, Eastern).")
    parser.add_argument("--week", type=int, default=None, help="Slate week (default: from schedule).")
    parser.add_argument("--season", type=int, default=None, help="Season (default: from date).")
    args = parser.parse_args()

    slate: date_cls = args.date
    slate_iso = slate.isoformat()
    season = args.season or (slate.year if slate.month >= 3 else slate.year - 1)
    scripts = load_slate_records(NORMALIZED_DIR, "matchup_scripts", slate)
    if scripts is None:
        print(f"Missing matchup scripts for {slate_iso}; run the pipeline for that date first.")
        return 1
    props = load_slate_records(NORMALIZED_DIR, "calibrated_props", slate) or []

    week = args.week or _slate_week(season, slate_iso)
    player_rows = fetch_player_weeks(season)
    if not any(r.get("week") == str(week) for r in player_rows):
        print(f"nflverse has no week {week} box scores yet; try again after the games are posted.")
        return 1

    graded, skipped = grade_signals(slate_iso, week, scripts, player_rows, props)
    ledger = update_ledger(LEDGER_PATH, graded, slate_iso)
    slate_summary = summarize([g.__dict__ for g in graded])
    cumulative = summarize(ledger)
    baselines = direction_baselines(week, player_rows, {g.market for g in graded})

    report = render_markdown(slate_iso, week, slate_summary, cumulative, baselines, graded, skipped)
    out = write_report(REPORTS_DIR, slate, report)

    print(f"Week {week}: graded {len(graded)} signals, {len(skipped)} ungraded -> {out}")
    for label, summary in (("SLATE", slate_summary), ("CUMULATIVE", cumulative)):
        print(label)
        for s in summary:
            print(f"  {s['tag']:24} vs avg {s['vs_avg']:>7}  vs line {s['vs_line']:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
