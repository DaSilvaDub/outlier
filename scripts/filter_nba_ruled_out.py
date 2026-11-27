"""Filter NBA availability using player and game scoped report evidence.

Roster membership and an old injury entry do not establish availability for a
new game. Unknown/unsubmitted reports stand down publication without labeling
the player OUT. Probable and questionable are retained when explicitly reported
for the requested game.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "resources" / "nba_roster_registry.json"
OUT = {"OUT", "INACTIVE", "SUSPENDED", "IR"}
AVAILABLE = {"ACTIVE", "AVAILABLE", "PROBABLE", "QUESTIONABLE", "DOUBTFUL"}


class RegistryError(RuntimeError):
    """The availability evidence cannot be read."""


def _load_registry(path: Path) -> dict:
    if not path.is_file():
        raise RegistryError(f"roster registry not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rosters = [team.get("roster", []) for team in data.get("teams", {}).values()]
        if any(not isinstance(roster, list) or any(not isinstance(player, dict) for player in roster)
               for roster in rosters):
            raise RegistryError(f"roster registry unreadable: malformed players: {path}")
        if not any(rosters):
            raise RegistryError(f"roster registry lists no players: {path}")
        return data
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise RegistryError(f"roster registry unreadable: {path}: {exc}") from exc


def _name(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _matchup(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").upper()).replace("VS.", "@").replace("VS", "@")


def _utc(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def availability(row: dict, registry: dict, slate_date: str, now: datetime) -> str:
    """Return status only from fresh, sourced evidence for this exact game.

    Evidence may be a roster entry's game_availability list, an injuries entry,
    or the entry itself, but must carry slate_date, reported_utc, source and
    game_id (matching both sides) or matchup/matchup_abbr. Generic roster status
    deliberately has no authority. Conflicting or duplicate identities are unknown.
    """
    if not slate_date or row.get("slate_date") != slate_date:
        return "UNKNOWN"
    players = [player for team in registry.get("teams", {}).values()
               for player in team.get("roster", [])
               if _name(player.get("name")) == _name(row.get("player"))]
    if len(players) != 1 or not _name(row.get("player")):
        return "UNKNOWN"
    player = players[0]
    game_reports, injuries = player.get("game_availability", []), player.get("injuries", [])
    if not isinstance(game_reports, list) or not isinstance(injuries, list):
        return "UNKNOWN"
    observations = [player, *game_reports, *injuries]
    statuses = []
    for evidence in observations:
        if not isinstance(evidence, dict) or evidence.get("slate_date") != slate_date:
            continue
        if evidence.get("report_submitted") is False:
            continue
        row_id, report_id = str(row.get("game_id") or ""), str(evidence.get("game_id") or "")
        if row_id and report_id:
            same_game = row_id == report_id
        else:
            a = _matchup(row.get("matchup_abbr") or row.get("matchup"))
            b = _matchup(evidence.get("matchup_abbr") or evidence.get("matchup"))
            same_game = bool(a and b and a == b)
        stamp = _utc(evidence.get("reported_utc"))
        if not same_game or not stamp or not str(evidence.get("source") or "").strip():
            continue
        if not 0 <= (now - stamp).total_seconds() <= 6 * 3600:
            continue
        status = str(evidence.get("status") or "").upper()
        if status in OUT | AVAILABLE:
            statuses.append((stamp, status))
    if not statuses:
        return "UNKNOWN"
    newest = max(stamp for stamp, _ in statuses)
    latest = {status for stamp, status in statuses if stamp == newest}
    return latest.pop() if len(latest) == 1 else "UNKNOWN"


def get_ruled_out_set(registry_path: Path | None = None) -> set[str]:
    """Compatibility diagnostic; generic roster injuries never imply OUT.

    Publication uses availability() with the row's game identity instead.
    """
    _load_registry(registry_path or REGISTRY_PATH)
    return set()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def filter_csv(csv_path: Path, out_path: Path, summary_path: Path | None = None,
               registry_path: Path | None = None, slate_date: str | None = None,
               *, now: datetime | None = None) -> tuple[int, int, list[str]]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"input pack not found: {csv_path}")
    now = now or datetime.now(timezone.utc)
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames, rows = reader.fieldnames or [], list(reader)
    # No availability evidence is needed to publish an explicit empty result.
    registry_error = ""
    try:
        registry = _load_registry(registry_path or REGISTRY_PATH) if rows else {}
    except RegistryError as exc:
        registry, registry_error = {}, str(exc)
    dates = {row.get("slate_date", "") for row in rows}
    slate_date = slate_date or (dates.pop() if len(dates) == 1 else "")
    kept_rows, removed_players, unknown_players = [], [], []
    for row in rows:
        status = availability(row, registry, slate_date, now)
        if status in OUT:
            removed_players.append(row.get("player", ""))
        elif status == "UNKNOWN":
            unknown_players.append(row.get("player", ""))
        else:
            kept_rows.append(row)
    if "rank" in fieldnames:
        for index, row in enumerate(kept_rows, 1):
            row["rank"] = str(index)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(kept_rows)
    raw = buffer.getvalue().encode("utf-8")
    _atomic_write(out_path, raw)
    input_status = csv_path.with_suffix(".status.json")
    try:
        status_data = json.loads(input_status.read_text(encoding="utf-8"))
        if not isinstance(status_data, dict):
            status_data = {}
    except (OSError, ValueError):
        status_data = {}
    status_data.update(slate_date=slate_date, row_count=len(kept_rows),
                       actionable_rows=len(kept_rows), csv_sha256=hashlib.sha256(raw).hexdigest())
    if unknown_players:
        status_data["status"] = "awaiting_availability" if not kept_rows else "ready"
    elif removed_players and not kept_rows:
        status_data["status"] = "awaiting_availability"
    elif kept_rows:
        status_data["status"] = "ready"
    else:
        status_data.setdefault("status", "awaiting_odds")
    status_data["availability_unknown_rows"] = len(unknown_players)
    if registry_error:
        status_data["availability_error"] = registry_error
    if removed_players and not kept_rows:
        status_data["availability_cause"] = "all_players_ruled_out"
    _atomic_write(out_path.with_suffix(".status.json"), json.dumps(status_data, indent=2).encode())
    summary = {**status_data, "before_rows": len(rows), "after_rows": len(kept_rows),
               "removed_count": len(removed_players), "removed_players": removed_players,
               "unknown_count": len(unknown_players), "unknown_players": unknown_players,
               "ruled_out_index_size": len(set(removed_players))}
    if summary_path:
        _atomic_write(summary_path, json.dumps(summary, indent=2).encode())
    return len(rows), len(kept_rows), removed_players


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-json")
    parser.add_argument("--registry")
    parser.add_argument("--date", help="Requested slate date YYYY-MM-DD")
    args = parser.parse_args()
    try:
        before, after, removed = filter_csv(Path(args.csv), Path(args.out),
            Path(args.summary_json) if args.summary_json else None,
            Path(args.registry) if args.registry else None, args.date)
    except (RegistryError, FileNotFoundError) as exc:
        print(f"filter_nba_ruled_out: FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"filter_nba_ruled_out: before={before} after={after} removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
