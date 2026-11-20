"""Filter ruled-out / inactive NBA players from candidate packs and prop cards.

Enforces Invariant NBA-ROSTER-001 and Invariant NBA-PROP-009:
Drops candidate prop rows where the player is confirmed OUT or inactive.

Fails closed: a missing, unreadable, or empty roster registry raises
RegistryError (exit 2) instead of "filtering" against an empty set and
reporting removed=0. Before 2026-11-04 the default registry path did not
exist, so every pack was published unfiltered.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "resources" / "nba_roster_registry.json"


class RegistryError(RuntimeError):
    """The roster registry cannot be used to decide who is ruled out."""


def get_ruled_out_set(registry_path: Path | None = None) -> set[str]:
    """Extract set of normalized player names marked OUT or injured."""
    p = registry_path or REGISTRY_PATH
    if not p.is_file():
        raise RegistryError(f"roster registry not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        teams = data.get("teams", {})
        rosters = [t_info.get("roster", []) for t_info in teams.values()]
    except (OSError, ValueError, AttributeError) as exc:
        raise RegistryError(f"roster registry unreadable: {p}: {exc}") from exc
    if not any(rosters):
        raise RegistryError(f"roster registry lists no players: {p}")

    ruled_out: set[str] = set()
    for roster in rosters:
        for pl in roster:
            status = str(pl.get("status", "")).upper()
            injuries = pl.get("injuries", [])
            if status in {"OUT", "INACTIVE", "SUSPENDED", "IR"} or injuries:
                name = pl.get("name", "").strip().lower()
                if name:
                    ruled_out.add(name)
    return ruled_out


def filter_csv(
    csv_path: Path,
    out_path: Path,
    summary_path: Path | None = None,
    registry_path: Path | None = None,
) -> tuple[int, int, list[str]]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"input pack not found: {csv_path}")
    ruled_out_set = get_ruled_out_set(registry_path)

    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    before_count = len(rows)
    kept_rows: list[dict[str, Any]] = []
    removed_players: list[str] = []

    for r in rows:
        # A short row leaves DictReader's restval (None) in the cell, so coerce before
        # stripping: a truncated pack must not abort the filter with an AttributeError.
        player_name = str(r.get("player") or "").strip().lower()
        if player_name and player_name in ruled_out_set:
            removed_players.append(r.get("player", ""))
        else:
            kept_rows.append(r)

    # Re-rank
    if "rank" in fieldnames:
        for idx, r in enumerate(kept_rows, start=1):
            r["rank"] = str(idx)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        # extrasaction: a row with more cells than the header lands under DictReader's
        # restkey (None), which DictWriter would otherwise reject outright.
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept_rows)

    after_count = len(kept_rows)
    summary_data = {
        "before_rows": before_count,
        "after_rows": after_count,
        "removed_count": len(removed_players),
        "removed_players": removed_players,
        "ruled_out_index_size": len(ruled_out_set),
    }

    if summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)

    return before_count, after_count, removed_players


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Input CSV path")
    parser.add_argument("--out", required=True, help="Output filtered CSV path")
    parser.add_argument("--summary-json", default=None, help="Summary JSON output path")
    parser.add_argument("--registry", default=None, help="Path to nba_roster_registry.json")

    args = parser.parse_args()
    try:
        before, after, removed = filter_csv(
            Path(args.csv),
            Path(args.out),
            Path(args.summary_json) if args.summary_json else None,
            Path(args.registry) if args.registry else None,
        )
    except (RegistryError, FileNotFoundError) as exc:
        print(f"filter_nba_ruled_out: FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"filter_nba_ruled_out: before={before} after={after} removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
