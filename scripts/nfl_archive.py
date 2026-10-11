"""Record NFL slate files in the archive manifest, or check it for completeness.

    python -m scripts.nfl_archive record --nfl-dir data/NFL [--nflverse-cache DIR --season 2025]

    python -m scripts.nfl_archive record --nfl-dir data/NFL --legacy [--reports-dir reports/NFL]

    python -m scripts.nfl_archive check  --nfl-dir data/NFL [--slate 2026-10-04 --season 2025]

``record --legacy`` records the files the pre-run-bundle slates (2026-09-27,
2026-10-04, 2026-10-05) still have, exactly as they sit, and copies each once
under ``archive/legacy/<sha256>/``. The originals are only read.

For a legacy ``--slate``, ``check`` also prints ``STATUS <slate>
legacy_regradable|unrecoverable``, whether an original card exists, and each
file's verdict (original / partial / post_slate per game; snapshots per row).

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
from outlier_nfl import archive_legacy as al


def _record(args: argparse.Namespace) -> int:
    nfl_dir = Path(args.nfl_dir)
    if args.legacy and not args.run_id:
        result = ar._empty_result()
    elif args.run_id:
        result = ar.record_run(nfl_dir, args.run_id, args.slate)
    else:
        result = ar.record_pointed_runs(nfl_dir)
    if args.legacy:
        lg = al.record_legacy(nfl_dir, tuple(args.legacy_slate or al.LEGACY_SLATES),
                              args.reports_dir)
        result.recorded += lg.recorded
        result.already += lg.already
        result.conflicts += lg.conflicts
        result.missing += lg.missing
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
    legacy = tuple(s for s in args.slate or [] if s in al.LEGACY_SLATES)
    report = ar.check_archive(Path(args.nfl_dir), args.slate, args.season, legacy)
    statuses = [al.legacy_status(Path(args.nfl_dir), s) for s in legacy]
    if args.json:
        print(json.dumps({"slates": report.slates, "complete": report.complete_slates,
                          "seasons": report.seasons, "entries": report.entries,
                          "gaps": [asdict(g) for g in report.gaps],
                          "legacy": [{**asdict(st), "files": [
                              {**asdict(f), "verdict": f.verdict} for f in st.files]}
                              for st in statuses]}, indent=2))
    else:
        for st in statuses:
            card = ", ".join(f"{k} {v}" for k, v in sorted(st.card.items())) or "none"
            print(f"STATUS {st.slate:<19} {st.status:<18} regradable games "
                  f"{len(st.regradable_events)}, first kickoff {st.first_kickoff} "
                  f"({st.kickoff_source}), files dated by mtime, card: {card}"
                  + ("" if st.status != "legacy_regradable" else
                     f", rows: original_model_p {st.probability_sources['original_model_p']} "
                     "(original, pre-repair), recomputed_from_hit_rates "
                     f"{st.probability_sources['recomputed_from_hit_rates']}"))
            if st.refused_reason:
                print(f"  REFUSED {st.refused_reason}")
            for f in st.files:
                print(f"  {f.verdict:<24} {f.original_path}"
                      + (f" (original for {len(f.original_for)}/{f.games} games)"
                         if f.verdict == "partial" else "")
                      + (" (same bytes as a pre-kickoff copy)" if f.via_pre_kickoff_copy else ""))
            if st.snapshot_rows:
                r = st.snapshot_rows
                print(f"  snapshot rows ({r['copies']} copies): pre_kickoff "
                      f"{r['pre_kickoff']}, post_slate "
                      f"{r['post_slate']}, with_run_id {r['with_run_id']}, "
                      f"no_kickoff {r['no_kickoff']}")

        for g in report.gaps:
            where = g.slate or (g.role or "manifest")
            print(f"GAP {where:<22} {g.kind:<25} {g.detail}")
        print(f"slates {len(report.slates)}, complete {len(report.complete_slates)}, "
              f"entries {report.entries}, gaps {len(report.gaps)}")
    if any(g.kind == "invalid_line" for g in report.gaps):
        return 2
    return 1 if report.gaps or any(st.status == "refused" for st in statuses) else 0


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
    rec.add_argument("--legacy", action="store_true",
                     help="record the pre-run-bundle slates' files as they sit")
    rec.add_argument("--legacy-slate", action="append",
                     help="limit --legacy to this slate (repeatable)")
    rec.add_argument("--reports-dir", help="game scripts folder (default <repo>/reports/NFL)")
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
