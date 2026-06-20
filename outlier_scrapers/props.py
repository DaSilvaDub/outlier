from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from .api import AuthRequiredError, OutlierApiClient, OutlierApiError
from .normalizer import build_normalized_payload
from .paths import league_paths
from .registry import get_sport_config, supported_leagues


def write_json(path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def export_props_for_league(client: OutlierApiClient, league: str) -> dict[str, Any]:
    config = get_sport_config(league)
    paths = league_paths(config.league_id).ensure()
    schedule_payload = client.fetch_schedule(config.league_id)
    props_payload = client.fetch_player_props(config.league_id)

    exported_at = datetime.now().astimezone().isoformat()
    raw_payload = {
        "exported_at": exported_at,
        "league": config.league_id,
        "source": "Outlier authenticated API",
        "schedule": schedule_payload,
        "playerProps": props_payload,
    }
    raw_latest = paths.raw / f"{config.league_id.lower()}_props_raw_latest.json"
    raw_archive = paths.timestamped(paths.raw, "props_raw")
    write_json(raw_latest, raw_payload)
    write_json(raw_archive, raw_payload)

    source_url = client.url_for(f"/sportsdata/leagues/{config.league_id}/playerProps")
    normalized = build_normalized_payload(
        config=config,
        props_payload=props_payload,
        schedule_payload=schedule_payload,
        source_url=source_url,
    )
    normalized_latest = paths.normalized / f"{config.league_id.lower()}_props_latest.json"
    normalized_archive = paths.timestamped(paths.normalized, "props")
    write_json(normalized_latest, normalized)
    write_json(normalized_archive, normalized)

    status = {
        "league": config.league_id,
        "status": "ok",
        "raw_latest": str(raw_latest),
        "normalized_latest": str(normalized_latest),
        "record_count": normalized["record_count"],
    }
    write_json(paths.reports / "props_export_status_latest.json", status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export standalone Outlier props")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument("--all", action="store_true", help="Accepted for compatibility; API exports all")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = OutlierApiClient()
        status = export_props_for_league(client, args.league)
    except AuthRequiredError as exc:
        paths = league_paths(args.league).ensure()
        report = {
            "league": args.league.upper(),
            "status": "auth_required",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:200],
        }
        write_json(paths.reports / "props_export_status_latest.json", report)
        print(f"{args.league.upper()}: auth_required")
        return 1
    except (OutlierApiError, FileNotFoundError, ValueError) as exc:
        paths = league_paths(args.league).ensure()
        report = {
            "league": args.league.upper(),
            "status": "error",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:300],
        }
        write_json(paths.reports / "props_export_status_latest.json", report)
        print(f"{args.league.upper()}: error")
        return 1

    print(f"{status['league']}: exported {status['record_count']} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

