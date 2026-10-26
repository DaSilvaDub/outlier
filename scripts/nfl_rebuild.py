"""Rebuild archived NFL slates as versioned replays (#232 part a).

    python -m scripts.nfl_rebuild admissible --nfl-dir data/NFL [--slate 2026-10-04]
    python -m scripts.nfl_rebuild rebuild    --nfl-dir data/NFL --slate 2026-10-04

``admissible`` is read-only. It prints each expected slate's verdict, with the
reasons for any refusal. Exit 0 when every listed slate is admissible, 1 otherwise.

``rebuild`` refuses any slate that isn't admissible (exit 2). Otherwise it writes
only ``<nfl-dir>/versions/<rebuild_id>/`` (the replay bundle,
``rebuild_manifest.json``, ``comparison.json`` and ``comparison.md``) and prints
the folder. Originals are never modified.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess  # nosec B404 - fixed argv, reads the git commit only
import sys

from outlier_nfl import rebuild as rb


def _code_version() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,  # nosec B603 B607
                             check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _admissible(args: argparse.Namespace) -> int:
    verdicts = rb.admissibility(Path(args.nfl_dir), args.slate)
    for v in verdicts:
        late = " archived_late" if v.admissible and v.archived_late else ""
        print(f"{'ADMISSIBLE' if v.admissible else 'REFUSED   '} {v.slate} run={v.run_id}{late}")
        for r in v.reasons:
            print(f"    - {r}")
    ok = sum(v.admissible for v in verdicts)
    late_n = sum(v.admissible and v.archived_late for v in verdicts)
    print(f"slates {len(verdicts)}, admissible {ok} (archived_late {late_n})")
    return 0 if verdicts and ok == len(verdicts) else 1


def _rebuild(args: argparse.Namespace) -> int:
    try:
        m = rb.rebuild_slate(Path(args.nfl_dir), args.slate, rebuild_id=args.rebuild_id,
                             code_version=_code_version())
    except rb.RebuildRefused as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 2
    out = Path(args.nfl_dir) / rb.VERSIONS_DIR / m["rebuild_id"]
    print(f"rebuilt {m['slate']} from run {m['source_run_id']} as {m['rebuild_id']} "
          f"(mode {m['rebuilt_mode']}, status {m['rebuilt_status']})")
    print(f"differences {m['comparison']['differences']}: {m['comparison']['cause_counts']}")
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nfl_rebuild", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("admissible", help="read-only admissibility verdicts")
    a.add_argument("--nfl-dir", default="data/NFL")
    a.add_argument("--slate", action="append")
    a.set_defaults(func=_admissible)
    r = sub.add_parser("rebuild", help="rebuild one admissible slate")
    r.add_argument("--nfl-dir", default="data/NFL")
    r.add_argument("--slate", required=True)
    r.add_argument("--rebuild-id")
    r.set_defaults(func=_rebuild)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
