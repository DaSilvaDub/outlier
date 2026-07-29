#!/usr/bin/env python3
"""Filter perfect-hit prop CSVs for slate + side policy hygiene.

What this script does
---------------------
Reads one or more ``*_100_hit*.csv`` files (columns: player, market_label,
side, line, team, matchup) and writes cleaned copies plus a rejection log.

Default hard filters (aligned with Outlier MLB desk invariants):
1. Drop Home Runs UNDER (HR / Home Runs markets are Over-only).
2. Drop "To Record X..." style milestone UNDER lines when labeled as such.
3. Optionally keep only matchups on an allowlist (from dossiers, candidates,
   or an explicit ``--allow-matchups`` / ``--allow-matchups-file``).
4. Optionally drop rows whose team code does not appear in the matchup string.
5. Optionally restrict to a single side.

It does NOT re-score hit rates, fetch odds, or invent markets — pure hygiene
on already-exported perfect-hit lists.

Importable helpers (used by organize_today_run2):
  normalize_matchup, market_token, team_in_matchup,
  allowlist_from_dossiers_dir, allowlist_from_candidates_csv,
  FilterOptions, reject_reason, filter_rows
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


HR_RE = re.compile(r"\bhome\s*runs?\b|\bhr\b", re.I)
MILESTONE_RE = re.compile(r"\bto\s+record\b|\bmilestone\b", re.I)
PROHIBITED_MARKETS_RE = re.compile(r"\bwalks?\s+allowed\b|\btotal\s+bases\b|\bhits?\s+allowed\b", re.I)
MATCHUP_SPLIT_RE = re.compile(r"\s*@\s*|\s+vs\.?\s+", re.I)
DOSSIER_NAME_RE = re.compile(r"_([a-z0-9]+)---([a-z0-9]+)\.md$", re.I)
MATCHUP_INLINE_RE = re.compile(r"([A-Z]{2,3})\s*@\s*([A-Z]{2,3})")


@dataclass(frozen=True)
class FilterOptions:
    """Policy knobs for perfect-hit hygiene."""

    drop_hr_under: bool = True
    drop_milestone_under: bool = True
    drop_prohibited_markets: bool = True
    require_team_in_matchup: bool = True
    side: str | None = None  # "OVER" | "UNDER" | None
    # None = no slate filter; empty set = drop everything matchup-related
    allow_matchups: frozenset[str] | None = None


def normalize_matchup(s: str) -> str:
    s = (s or "").strip().upper()
    s = re.sub(r"\s+", " ", s)
    s = s.replace(" VS ", " @ ").replace(" VS. ", " @ ")
    return s


def market_token(market_label: str) -> str:
    ml = (market_label or "").strip()
    if " - " in ml:
        return ml.split(" - ", 1)[1].strip()
    return ml


def team_in_matchup(team: str, matchup: str) -> bool:
    t = (team or "").strip().upper()
    m = normalize_matchup(matchup)
    if not t or not m:
        return False
    parts = MATCHUP_SPLIT_RE.split(m)
    return t in {p.strip() for p in parts if p.strip()}


def allowlist_from_dossiers_dir(dossiers_dir: Path | None) -> set[str]:
    allow: set[str] = set()
    if dossiers_dir is None or not dossiers_dir.is_dir():
        return allow
    for p in dossiers_dir.glob("*.md"):
        m = DOSSIER_NAME_RE.search(p.name)
        if m:
            allow.add(normalize_matchup(f"{m.group(1)} @ {m.group(2)}"))
        try:
            head = p.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
        except OSError:
            continue
        for line in head:
            mm = MATCHUP_INLINE_RE.search(line)
            if mm:
                allow.add(normalize_matchup(f"{mm.group(1)} @ {mm.group(2)}"))
    return allow


def allowlist_from_candidates_csv(candidates_csv: Path | None) -> set[str]:
    allow: set[str] = set()
    if candidates_csv is None or not candidates_csv.is_file():
        return allow
    with open(candidates_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mu = (row.get("matchup") or "").strip()
            if mu:
                allow.add(normalize_matchup(mu))
    return allow


def allowlist_from_matchup_strings(items: Iterable[str]) -> set[str]:
    allow: set[str] = set()
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                allow.add(normalize_matchup(part))
    return allow


def reject_reason(
    row: dict,
    opts: FilterOptions,
) -> str | None:
    """Return a rejection reason code, or None if the row is kept."""
    side = (row.get("side") or "").strip().upper()
    market = market_token(row.get("market_label") or "")
    matchup = normalize_matchup(row.get("matchup") or "")

    if opts.drop_prohibited_markets and PROHIBITED_MARKETS_RE.search(market):
        return f"prohibited_market_forbidden:{market}"
    if opts.drop_hr_under and HR_RE.search(market) and side == "UNDER":
        return "hr_under_forbidden"
    if opts.drop_milestone_under and MILESTONE_RE.search(market) and side == "UNDER":
        return "milestone_under_forbidden"
    if opts.allow_matchups is not None and matchup not in opts.allow_matchups:
        return f"off_slate_matchup:{matchup or '(blank)'}"
    if opts.require_team_in_matchup and not team_in_matchup(row.get("team") or "", matchup):
        return "team_not_in_matchup"
    if opts.side and side != opts.side.upper():
        return f"side_filter:{side}"
    return None


def filter_rows(
    rows: Iterable[dict],
    opts: FilterOptions,
) -> tuple[list[dict], list[dict], Counter]:
    """Split rows into kept / rejected; rejected rows gain reject_reason."""
    kept: list[dict] = []
    rejected: list[dict] = []
    reasons: Counter = Counter()
    for row in rows:
        reason = reject_reason(row, opts)
        if reason:
            reasons[reason.split(":")[0]] += 1
            rejected.append({**row, "reject_reason": reason})
        else:
            kept.append(dict(row))
    return kept, rejected, reasons


def load_allowlist_from_cli(args: argparse.Namespace) -> set[str] | None:
    """Return normalized matchup allowlist, or None if no slate filter."""
    allow: set[str] = set()
    if args.allow_matchups:
        allow |= allowlist_from_matchup_strings(args.allow_matchups)
    if args.allow_matchups_file:
        path = Path(args.allow_matchups_file)
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            allow.add(normalize_matchup(line))
    if args.allow_from_dossiers_dir:
        allow |= allowlist_from_dossiers_dir(Path(args.allow_from_dossiers_dir))
    if args.allow_from_candidates_csv:
        allow |= allowlist_from_candidates_csv(Path(args.allow_from_candidates_csv))
    return allow or None


def process_file(
    src: Path,
    out_dir: Path,
    opts: FilterOptions,
) -> dict:
    with open(src, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or [
            "player",
            "market_label",
            "side",
            "line",
            "team",
            "matchup",
        ]
        rows = list(reader)

    kept, rejected, reasons = filter_rows(rows, opts)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    kept_path = out_dir / f"{stem}_filtered.csv"
    rej_path = out_dir / f"{stem}_rejected.csv"

    with open(kept_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(kept)

    with open(rej_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames) + ["reject_reason"])
        w.writeheader()
        w.writerows(rejected)

    allow = opts.allow_matchups
    return {
        "src": str(src),
        "kept_path": str(kept_path),
        "rejected_path": str(rej_path),
        "input": len(rows),
        "kept": len(kept),
        "rejected": len(rejected),
        "reasons": dict(reasons),
        "allowlist_size": len(allow) if allow is not None else None,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Perfect-hit CSV path(s), e.g. MLB_100_hit_rate.csv",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("filtered_perfect_hit"),
        help="Directory for *_filtered.csv and *_rejected.csv (default: ./filtered_perfect_hit)",
    )
    p.add_argument(
        "--allow-matchups",
        action="append",
        default=[],
        help="Comma-separated matchups to keep, e.g. 'LAA @ SF,NYY @ PHI' (repeatable)",
    )
    p.add_argument(
        "--allow-matchups-file",
        type=Path,
        help="Text file: one matchup per line",
    )
    p.add_argument(
        "--allow-from-dossiers-dir",
        type=Path,
        help="Infer slate matchups from dossier filenames/headings",
    )
    p.add_argument(
        "--allow-from-candidates-csv",
        type=Path,
        help="Infer slate matchups from candidates.csv matchup column",
    )
    p.add_argument(
        "--drop-hr-under",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop Home Runs UNDER (default: true)",
    )
    p.add_argument(
        "--drop-milestone-under",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop milestone / 'To Record' UNDER lines (default: true)",
    )
    p.add_argument(
        "--require-team-in-matchup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop rows where team code is not in matchup (default: true)",
    )
    p.add_argument(
        "--side",
        choices=["OVER", "UNDER"],
        help="Keep only this side",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    allow_set = load_allowlist_from_cli(args)
    opts = FilterOptions(
        drop_hr_under=args.drop_hr_under,
        drop_milestone_under=args.drop_milestone_under,
        require_team_in_matchup=args.require_team_in_matchup,
        side=args.side,
        allow_matchups=frozenset(allow_set) if allow_set is not None else None,
    )

    summaries = []
    for src in args.inputs:
        if not src.exists():
            print(f"ERROR: missing input {src}", file=sys.stderr)
            return 2
        summaries.append(process_file(src, args.out_dir, opts))

    print("=== perfect-hit filter summary ===")
    if opts.allow_matchups is not None:
        print(f"allowlist matchups ({len(opts.allow_matchups)}): {sorted(opts.allow_matchups)}")
    else:
        print("allowlist: OFF (all matchups kept subject to other rules)")
    for s in summaries:
        print(f"\n{s['src']}")
        print(f"  in={s['input']}  kept={s['kept']}  rejected={s['rejected']}")
        print(f"  reasons={s['reasons']}")
        print(f"  kept -> {s['kept_path']}")
        print(f"  rejected -> {s['rejected_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
