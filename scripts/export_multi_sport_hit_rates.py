"""Export Outlier 100% Hit Rate Props for NFL, NBA, and NCAA (CFB).

Fetches player props from the Outlier REST API using the active session,
identifies props where the player has achieved a 100% hit rate in their last 5
and/or last 10 games (L5/L10), maps the team and matchup from the schedule,
applies sport-specific hygiene filters, and writes:
  - data/<SPORT>/exports/<SPORT>_100_hit_rate.csv
  - Today/Fresh_Data/<SPORT>/01_Packs/<SPORT>_100_hit_rate.csv
  - Google Drive Today/Fresh_Data/<SPORT>/01_Packs/<SPORT>_100_hit_rate.csv (if mounted)
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_TODAY = Path(r"C:\Users\dasil\Desktop\Today")
DRIVE_ROOTS = [
    Path(r"G:\My Drive\today"),
    Path(r"C:\Users\dasil\My Drive (dasilvadub@gmail.com)\today"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("export_hit_rates")

FIELDNAMES = [
    "player",
    "market_label",
    "side",
    "line",
    "team",
    "matchup",
    "l5_hit_rate",
    "l10_hit_rate",
    "season_hit_rate",
    "best_odds",
    "books",
    "orf",
    "game_time",
]

SPORT_TOKEN_MAP = {
    "NFL": "NFL",
    "NBA": "NBA",
    "NCAA": "NCAAFB",
    "CFB": "NCAAFB",
}


def get_drive_today() -> Path | None:
    for dr in DRIVE_ROOTS:
        if dr.parent.exists():
            return dr
    return None


def resolve_today_str() -> str:
    try:
        return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.date.today().isoformat()


def extract_hit_props_for_sport(
    client: Any,
    sport_name: str,
    target_date: str | None = None,
    min_l5: float = 1.0,
    min_l10: float = 0.8,
) -> list[dict[str, Any]]:
    league_token = SPORT_TOKEN_MAP.get(sport_name.upper(), sport_name.upper())
    logger.info("Fetching schedule and props for %s (token: %s)...", sport_name, league_token)

    try:
        schedule = client.fetch_schedule(league_token)
        events = schedule.get("events") or []
    except Exception as exc:
        logger.error("Failed to fetch schedule for %s: %s", league_token, exc)
        return []

    # Map eventId -> {home, away, matchup, scheduledTime}
    event_map: dict[str, dict[str, Any]] = {}
    team_alias_map: dict[str, str] = {}

    for ev in events:
        eid = ev.get("eventId")
        if not eid:
            continue
        home = ev.get("home") or {}
        away = ev.get("away") or {}
        home_alias = home.get("alias") or home.get("name") or "HOME"
        away_alias = away.get("alias") or away.get("name") or "AWAY"
        matchup = f"{away_alias} @ {home_alias}"
        sched_time = ev.get("scheduledTime") or ""

        event_map[eid] = {
            "home_id": home.get("teamId"),
            "away_id": away.get("teamId"),
            "home_alias": home_alias,
            "away_alias": away_alias,
            "matchup": matchup,
            "scheduled_time": sched_time,
        }
        if home.get("teamId"):
            team_alias_map[home["teamId"]] = home_alias
        if away.get("teamId"):
            team_alias_map[away["teamId"]] = away_alias

    # Fetch props
    try:
        props_payload = client.fetch_player_props(league_token)
        raw_props = props_payload.get("props") or []
    except Exception as exc:
        logger.error("Failed to fetch props for %s: %s", league_token, exc)
        return []

    logger.info("Found %d raw props for %s across %d events", len(raw_props), sport_name, len(event_map))

    qualifying: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for item in raw_props:
        outcome = item.get("outcome") or {}
        stats = item.get("stats") or {}
        l5 = stats.get("l5")
        l10 = stats.get("l10")
        cur_season = stats.get("curSeason")

        # 100% in L5 and at least min_l10 in L10
        if l5 is None or l5 < min_l5:
            continue
        if l10 is not None and l10 < min_l10:
            continue

        eid = outcome.get("eventId")
        ev_info = event_map.get(eid or "", {})
        sched_time = ev_info.get("scheduled_time") or ""

        # Filter by slate date if target_date provided
        if target_date and sched_time:
            try:
                dt = datetime.datetime.fromisoformat(sched_time.replace("Z", "+00:00"))
                ev_date = dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                # Allow within next 3 days for weekly sports like NFL / CFB
                if sport_name.upper() in ("NFL", "NCAA", "CFB"):
                    target_dt = datetime.date.fromisoformat(target_date)
                    ev_d = datetime.date.fromisoformat(ev_date)
                    if not (target_dt <= ev_d <= target_dt + datetime.timedelta(days=6)):
                        continue
                else:
                    if ev_date != target_date:
                        continue
            except Exception:
                pass

        # Determine team
        tid = outcome.get("teamId")
        team = team_alias_map.get(tid or "", "")
        matchup = ev_info.get("matchup") or ""

        # Market & Player
        market_label = outcome.get("marketLabel") or ""
        player = ""
        market_name = market_label
        if " - " in market_label:
            parts = market_label.split(" - ", 1)
            player = parts[0].strip()
            market_name = parts[1].strip()

        side = (outcome.get("position") or outcome.get("outcomeLabel") or "").upper()
        line = outcome.get("line")
        best_odds = outcome.get("bestOdds") or ""
        books = outcome.get("books") or []
        books_str = ";".join(str(b) for b in books) if isinstance(books, list) else str(books)
        orf = item.get("orf") or ""

        # Hygiene check: drop Home Runs Under or Milestone Under
        if side == "UNDER" and re.search(r"\b(home\s*run|touchdown|td)\b", market_name, re.I):
            continue

        dedup_key = f"{player}|{market_name}|{side}|{line}|{matchup}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        qualifying.append({
            "player": player,
            "market_label": market_label,
            "side": side,
            "line": str(line) if line is not None else "",
            "team": team,
            "matchup": matchup,
            "l5_hit_rate": f"{int(round(l5 * 100))}%" if l5 is not None else "",
            "l10_hit_rate": f"{int(round(l10 * 100))}%" if l10 is not None else "",
            "season_hit_rate": f"{int(round(cur_season * 100))}%" if cur_season is not None else "",
            "best_odds": str(best_odds),
            "books": books_str,
            "orf": str(orf),
            "game_time": sched_time,
        })

    # Sort by L10 hit rate (desc), then player
    qualifying.sort(key=lambda r: (r.get("l10_hit_rate") or "0%", r.get("player") or ""), reverse=True)
    logger.info("Found %d qualified 100%% L5 hit rate props for %s", len(qualifying), sport_name)
    return qualifying


def write_hit_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    logger.info("Wrote %d rows -> %s", len(rows), path)


def export_hit_rates_for_sport(
    client: Any,
    sport: str,
    target_date: str,
    export_to_fresh_data: bool = True,
) -> list[dict[str, Any]]:
    sp = sport.upper()
    league_dir_name = "NCAAFB" if sp in ("NCAA", "CFB") else sp
    rows = extract_hit_props_for_sport(client, sp, target_date=target_date)

    # 1. Write to repo exports
    repo_export = REPO_ROOT / "data" / league_dir_name / "exports" / f"{sp}_100_hit_rate.csv"
    write_hit_csv(repo_export, rows)

    # 2. Write to Fresh_Data
    if export_to_fresh_data:
        roots = [DESKTOP_TODAY]
        drive_today = get_drive_today()
        if drive_today:
            roots.append(drive_today)

        for root in roots:
            fresh_pack_dir = root / "Fresh_Data" / sp / "01_Packs"
            dest_csv = fresh_pack_dir / f"{sp}_100_hit_rate.csv"
            write_hit_csv(dest_csv, rows)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", default="ALL", choices=["NFL", "NBA", "NCAA", "ALL"], help="Target sport")
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD")
    parser.add_argument("--no-fresh-data", action="store_true", help="Skip exporting to Fresh_Data")
    args = parser.parse_args()

    date_str = args.date or resolve_today_str()
    from outlier_scrapers.api import OutlierApiClient

    client = OutlierApiClient()
    sports = ["NFL", "NBA", "NCAA"] if args.sport == "ALL" else [args.sport]

    for sp in sports:
        try:
            export_hit_rates_for_sport(
                client,
                sp,
                target_date=date_str,
                export_to_fresh_data=not args.no_fresh_data,
            )
        except Exception as exc:
            logger.error("Error exporting hit rates for %s: %s", sp, exc)


if __name__ == "__main__":
    main()
