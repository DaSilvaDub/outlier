"""Filter explicit NBA OUT statuses using fresh, raw-source-attested identities."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from nba_roster_registry import DEFAULT_PATH, OUT, RegistryError, atomic_write, current_date, digest, load, name_key
from outlier_nba.identity import resolve_team


def get_ruled_out_set(registry_path: Path | None = None, date: str | None = None) -> set[str]:
    registry = load(registry_path, date)
    return {name_key(p["name"]) for t in registry["teams"].values() for p in t["roster"] if p["status"] in OUT}


def filter_csv(csv_path: Path, out_path: Path, summary_path: Path | None = None,
               registry_path: Path | None = None, date: str | None = None) -> tuple[int, int, list[str]]:
    date = date or current_date()
    registry = load(registry_path, date)
    players = {name_key(p["name"]): p for t in registry["teams"].values() for p in t["roster"]}
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    if not fields or "player" not in fields or "slate_date" not in fields or not rows:
        raise RegistryError("Missing player/slate_date columns or empty input pack")
    kept, removed = [], []
    for row in rows:
        if row["slate_date"] != date:
            raise RegistryError("Pack slate-date mismatch")
        player = players.get(name_key(row["player"]))
        if not player:
            raise RegistryError(f"Unknown roster identity: {row['player']}")
        if row.get("espn_id") and row["espn_id"] != player["espn_id"]:
            raise RegistryError("Player ID/name mismatch")
        for team_field in ("team", "team_abbr"):
            if row.get(team_field) and resolve_team(row[team_field]) != player["team"]:
                raise RegistryError("Player team does not match current roster")
        if player["status"] in OUT:
            removed.append(row["player"])
        else:
            kept.append(row)
    if "rank" in fields:
        for rank, row in enumerate(kept, 1):
            row["rank"] = str(rank)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(kept)
    raw = buffer.getvalue().encode()
    # Validate everything before changing an in-place input or creating output.
    atomic_write(out_path, raw)
    if summary_path:
        atomic_write(summary_path, json.dumps({"before_rows": len(rows), "after_rows": len(kept),
            "removed_count": len(removed), "removed_players": removed,
            "registry_as_of_utc": registry["as_of_utc"], "registry_season_year": registry["season_year"],
            "registry_source_count": len(registry["sources"]), "registry_sources": registry["sources"],
            "registry_snapshot": str((registry_path or DEFAULT_PATH).parent / registry.get("immutable_snapshot", "registry.json")),
            "filtered_sha256": digest(raw)}, indent=2).encode())
    return len(rows), len(kept), removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    try:
        before, after, removed = filter_csv(args.csv, args.out, args.summary_json, args.registry, args.date)
    except (RegistryError, OSError, ValueError) as exc:
        print(f"NBA injury filter FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"NBA injury filter: before={before} after={after} removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
