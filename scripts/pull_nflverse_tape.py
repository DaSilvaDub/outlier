#!/usr/bin/env python3
"""Rebuild the NFL matchup tape (data/NFL/tape/prior_week.json) from nflverse box scores.

Usage:
    python scripts/pull_nflverse_tape.py --before 2026-09-27
    python scripts/pull_nflverse_tape.py --season 2026 --last-n 2 --dry-run

``--before`` excludes games on/after the slate date so a slate never sees its own
results. Role fields (qb/rb1/te/wr_slot/wr_deep) are preserved from the existing tape.
The previous file is kept as prior_week.prev.json.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from outlier_nfl.tape_nflverse import (  # noqa: E402
    build_tape_payload,
    load_existing_roles,
    write_tape,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", type=date.fromisoformat, default=None,
                        help="Only games before this date (YYYY-MM-DD). Default: all completed.")
    parser.add_argument("--season", type=int, default=None,
                        help="Season year. Default: derived from --before, else today.")
    parser.add_argument("--last-n", type=int, default=None,
                        help="Average only each team's last N games. Default: all.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"),
                        help="Data root; the tape is written to <data-dir>/NFL/tape/prior_week.json.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the payload instead of writing it.")
    args = parser.parse_args()

    ref = args.before or date.today()
    season = args.season or (ref.year if ref.month >= 3 else ref.year - 1)
    path = args.data_dir / "NFL" / "tape" / "prior_week.json"

    payload = build_tape_payload(
        season, before=args.before, last_n=args.last_n, roles=load_existing_roles(path)
    )
    if not payload["teams"]:
        print(f"No completed {season} regular-season games found; tape not written.")
        return 1
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0
    backup = write_tape(path, payload)
    print(f"Wrote {path} ({len(payload['teams'])} teams, weeks {payload['week']})")
    if backup:
        print(f"Previous tape kept at {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
