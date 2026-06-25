"""Games scraper.

Fetches and normalizes game-level markets (Moneyline, Spread, Total, Team Totals)
along with event matchups, insights, and injuries.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .api import OutlierApiClient, AuthRequiredError
from .normalizer import normalize_games
from .paths import league_paths
from .registry import get_sport_config, supported_leagues, GAME_MARKET_TYPES

def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Outlier games data")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument("--date", help="Target date in YYYY-MM-DD format (default: today local)")
    parser.add_argument("--days", type=int, default=1, help="Number of days to process from date (default: 1)")
    parser.add_argument("--include-final", action="store_true", help="Include final/completed events")
    return parser.parse_args(argv)

def _is_event_in_window(event: dict[str, Any], start_date: datetime.date, days: int, include_final: bool) -> bool:
    status = str(event.get("status") or "").lower()
    if not include_final and status not in ("pregame", "scheduled"):
        return False
    
    start_time_raw = event.get("scheduledTime")
    if not start_time_raw:
        return False
        
    try:
        dt = datetime.fromisoformat(start_time_raw.replace("Z", "+00:00"))
        dt_local = dt.astimezone()
        event_date = dt_local.date()
        end_date = start_date + timedelta(days=days - 1)
        return start_date <= event_date <= end_date
    except ValueError:
        return False

def export_games_for_league(
    client: OutlierApiClient,
    league: str,
    *,
    target_date: datetime.date | None = None,
    days: int = 1,
    include_final: bool = False,
) -> dict[str, Any]:
    config = get_sport_config(league)
    paths = league_paths(config.league_id)
    
    if target_date is None:
        target_date = datetime.now().astimezone().date()
        
    schedule = client.fetch_schedule(config.league_id)
    events = schedule.get("events")
    if not isinstance(events, list):
        events = []
        
    target_events = [e for e in events if _is_event_in_window(e, target_date, days, include_final)]
    
    events_payloads: list[dict[str, Any]] = []
    seen_team_ids: set[str] = set()
    injuries_by_team: dict[str, list[dict[str, Any]]] = {}
    fetch_errors: list[dict[str, Any]] = []
    markets_step_errors = 0

    def _record_error(step: str, event_id: str, exc: Exception, **extra: Any) -> None:
        fetch_errors.append(
            {"step": step, "event_id": event_id, "error": str(exc)[:200], **extra}
        )

    for event in target_events:
        event_id = str(event.get("eventId") or event.get("id") or "")
        if not event_id:
            continue

        event_payload = {"eventId": event_id, "markets": [], "insights": [], "matchup": {}, "injuries": []}

        # 1. Matchup
        try:
            event_payload["matchup"] = client.fetch_event_matchup(event_id)
        except Exception as e:
            _record_error("matchup", event_id, e)

        # 2. Insights
        try:
            event_payload["insights"] = client.fetch_event_insights(event_id).get("insights", [])
        except Exception as e:
            _record_error("insights", event_id, e)

        # 3. Markets (core payload — a failure here is escalated below)
        for market_type in GAME_MARKET_TYPES:
            try:
                markets_resp = client.fetch_event_markets(event_id, market_type)
                event_payload["markets"].extend(markets_resp.get("markets") or [])
            except Exception as e:
                markets_step_errors += 1
                _record_error("markets", event_id, e, market_type=market_type)

        # 4. Injuries
        for side in ("home", "away"):
            team_payload = event.get(side)
            if isinstance(team_payload, dict):
                team_id = str(team_payload.get("teamId") or team_payload.get("id") or "")
                if team_id and team_id not in seen_team_ids:
                    seen_team_ids.add(team_id)
                    try:
                        injuries_resp = client.fetch_team_injuries(config.league_id, team_id)
                        injuries_by_team[team_id] = injuries_resp.get("players") or []
                    except Exception as e:
                        _record_error("injuries", event_id, e, team_id=team_id)

                if team_id in injuries_by_team:
                    # Injury player items carry no teamId of their own, so stamp
                    # it on each one — normalize_games keys context.teams by it.
                    event_payload["injuries"].extend(
                        {**player, "teamId": team_id}
                        for player in injuries_by_team[team_id]
                    )

        events_payloads.append(event_payload)
        
    # Write raw output
    raw_payload = {
        "schedule": schedule,
        "events": events_payloads,
        "generated_at": datetime.now().astimezone().isoformat()
    }
    raw_path = paths.timestamped(paths.raw, "games_raw")
    write_json(raw_path, raw_payload)
    
    # Normalize
    normalized = normalize_games(
        config=config,
        schedule_payload=schedule,
        events_payloads=events_payloads,
        source_url="api",
    )
    
    latest_path = paths.normalized / f"{config.league_id.lower()}_games_latest.json"
    write_json(latest_path, normalized)
    write_json(paths.timestamped(paths.normalized, "games"), normalized)
    
    # Enrichment file for player cards
    enrichment_map: dict[str, dict[str, Any]] = {}
    for event_data in events_payloads:
        for market in event_data.get("markets", []):
            if str(market.get("marketType")) == "PLAYER_PROP":
                for outcome in market.get("outcomes", []):
                    outcome_id = str(outcome.get("outcomeId") or outcome.get("id") or "")
                    if outcome_id:
                        pos = str(outcome.get("position") or "").strip().upper()
                        public_money = None
                        pm_array = market.get("publicMoney")
                        if isinstance(pm_array, list):
                            for pm in pm_array:
                                if str(pm.get("position") or "").strip().upper() == pos:
                                    public_money = pm
                                    break
                        if not public_money:
                            public_money = outcome.get("publicMoney")
                            
                        enrichment_map[outcome_id] = {
                            "per_book_odds": outcome.get("odds") or [],
                            "public_money": public_money
                        }
                        
    enrichment_payload = {
        "generated_at": normalized["generated_at"],
        "league": config.league_id,
        "enrichment": enrichment_map
    }
    enrichment_path = paths.normalized / f"{config.league_id.lower()}_games_enrichment_latest.json"
    write_json(enrichment_path, enrichment_payload)
    write_json(paths.timestamped(paths.normalized, "games_enrichment"), enrichment_payload)
    
    # Status contract mirrors line_movement: ok / partial / error. An empty
    # markets result caused by fetch failures is the core-payload failure and is
    # escalated to error; a genuinely empty slate (no errors) stays ok.
    record_count = normalized["record_count"]
    if markets_step_errors and record_count == 0:
        status_value = "error"
    elif fetch_errors:
        status_value = "partial"
    else:
        status_value = "ok"

    return {
        "status": status_value,
        "record_count": record_count,
        "enrichment_count": len(enrichment_map),
        "fetch_errors": fetch_errors,
        "fetch_error_count": len(fetch_errors),
    }

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = OutlierApiClient()
    except AuthRequiredError as exc:
        print(f"auth_required: {exc}")
        return 1
        
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            return 1

    try:
        status = export_games_for_league(
            client,
            args.league,
            target_date=target_date,
            days=args.days,
            include_final=args.include_final
        )
        print(
            f"{args.league} games [{status['status']}]: exported {status['record_count']} records, "
            f"{status['enrichment_count']} enrichment records, {status['fetch_error_count']} fetch errors"
        )
        return 1 if status["status"] == "error" else 0
    except Exception as e:
        print(f"{args.league} games: failed ({e})")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
