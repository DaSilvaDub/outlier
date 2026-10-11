"""Record NFL slate files in the archive manifest, or check it for completeness.

    python -m scripts.nfl_archive record --nfl-dir data/NFL [--nflverse-cache DIR --season 2025]

    python -m scripts.nfl_archive check  --nfl-dir data/NFL [--slate 2026-10-04 --season 2025]

``check`` is read-only. It lists every expected slate (each dated run pointer
plus any ``--slate``) whose roles are missing, every archived file that is gone
or whose sha256/size no longer matches, and any missing nflverse box-score
input for each ``--season``. Exit codes: 0 complete, 1 gaps found, 2 the
manifest has invalid lines.

``record`` only reads the archived files and appends to
``<nfl-dir>/archive/manifest.jsonl``. Exit codes: 0 when nothing conflicted,
1 when an entry or file conflicted (the existing entry is kept).
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from outlier_nfl import archive as ar


def _record(args: argparse.Namespace) -> int:
    nfl_dir = Path(args.nfl_dir)
    if args.run_id:
        result = ar.record_run(nfl_dir, args.run_id, args.slate)
    else:
        result = ar.record_pointed_runs(nfl_dir)
    if args.nflverse_cache:
        nv = ar.record_nflverse(nfl_dir, args.nflverse_cache, args.season or [])
        result.recorded += nv.recorded
        result.already += nv.already
        result.conflicts += nv.conflicts
        result.missing += nv.missing
    print(f"recorded {len(result.recorded)}, already archived {len(result.already)}, "
          f"conflicts {len(result.conflicts)}, missing {len(result.missing)}")
    for line in result.conflicts:
        print(f"CONFLICT {line}")
    for line in result.missing:
        print(f"MISSING  {line}")
    for line in result.notes:
        print(f"NOTE     {line}")
    return 1 if result.conflicts else 0


def _check(args: argparse.Namespace) -> int:
    report = ar.check_archive(Path(args.nfl_dir), args.slate, args.season)
    if args.json:
        print(json.dumps({"slates": report.slates, "complete": report.complete_slates,
                          "seasons": report.seasons, "entries": report.entries,
                          "gaps": [asdict(g) for g in report.gaps]}, indent=2))
    else:
        for g in report.gaps:
            where = g.slate or (g.role or "manifest")
            print(f"GAP {where:<22} {g.kind:<25} {g.detail}")
        print(f"slates {len(report.slates)}, complete {len(report.complete_slates)}, "
              f"entries {report.entries}, gaps {len(report.gaps)}")
    if any(g.kind == "invalid_line" for g in report.gaps):
        return 2
    return 1 if report.gaps else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nfl_archive", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="append entries for committed runs / nflverse inputs")
    rec.add_argument("--nfl-dir", default="data/NFL")
    rec.add_argument("--run-id", help="record one run (default: every dated run pointer)")
    rec.add_argument("--slate", help="slate key for --run-id (default: its context.slate_date)")
    rec.add_argument("--nflverse-cache", help="folder holding stats_player_week_<season>.csv")
    rec.add_argument("--season", type=int, action="append", help="nflverse season (repeatable)")
    rec.set_defaults(func=_record)
    chk = sub.add_parser("check", help="read-only completeness check")
    chk.add_argument("--nfl-dir", default="data/NFL")
    chk.add_argument("--slate", action="append", help="also expect this slate (repeatable)")
    chk.add_argument("--season", type=int, action="append",
                     help="require nflverse box-score inputs for this season (repeatable)")
    chk.add_argument("--json", action="store_true")
    chk.set_defaults(func=_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "record" and args.nflverse_cache and not args.season:
        print("--nflverse-cache needs at least one --season", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
