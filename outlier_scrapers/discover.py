from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from .api import AuthRequiredError, OutlierApiClient, OutlierApiError
from .paths import league_paths
from .redaction import shape_summary
from .registry import get_sport_config, supported_leagues


def _count_array(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else None


def summarize_league(client: OutlierApiClient, league: str) -> dict[str, Any]:
    config = get_sport_config(league)
    report: dict[str, Any] = {
        "league": config.league_id,
        "generated_at": datetime.now().astimezone().isoformat(),
        "app_routes": {
            "props": f"https://app.outlier.bet{config.props_route}",
            "insights": f"https://app.outlier.bet{config.insights_route}",
        },
        "status": "ok",
        "endpoints": {},
    }

    for name, fetcher, count_key in (
        ("schedule", lambda: client.fetch_schedule(config.league_id), "events"),
        ("playerProps", lambda: client.fetch_player_props(config.league_id), "props"),
    ):
        try:
            payload = fetcher()
        except AuthRequiredError as exc:
            report["status"] = "auth_required"
            report["endpoints"][name] = {"status": "auth_required", "error": str(exc)[:200]}
            continue
        except OutlierApiError as exc:
            if report["status"] == "ok":
                report["status"] = "partial"
            report["endpoints"][name] = {"status": "error", "error": str(exc)[:200]}
            continue

        report["endpoints"][name] = {
            "status": "ok",
            "top_level_keys": sorted(str(key) for key in payload.keys()),
            "record_count": _count_array(payload, count_key),
            "shape": shape_summary(payload, max_depth=4),
        }

        if name == "playerProps":
            props = payload.get("props")
            if isinstance(props, list):
                report["endpoints"][name]["sample_id_presence"] = {
                    "sampled": min(len(props), 25),
                    "event_id_non_null": sum(
                        1
                        for row in props[:25]
                        if isinstance(row, dict)
                        and isinstance(row.get("outcome"), dict)
                        and row["outcome"].get("eventId")
                    ),
                    "market_id_non_null": sum(
                        1
                        for row in props[:25]
                        if isinstance(row, dict)
                        and isinstance(row.get("outcome"), dict)
                        and row["outcome"].get("marketId")
                    ),
                }

    return report


def write_discovery_report(league: str, report: dict[str, Any]) -> None:
    paths = league_paths(league).ensure()
    latest = paths.reports / "discovery_latest.json"
    archive = paths.timestamped(paths.reports, "discovery")
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    latest.write_text(payload, encoding="utf-8")
    archive.write_text(payload, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Outlier league capability shape")
    parser.add_argument(
        "--league",
        action="append",
        choices=supported_leagues(),
        required=True,
        help="League to inspect. Repeat for multiple leagues.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = OutlierApiClient()
    except Exception as exc:
        for league in args.league:
            report = {
                "league": league.upper(),
                "generated_at": datetime.now().astimezone().isoformat(),
                "status": "auth_required",
                "error": str(exc),
                "endpoints": {},
            }
            write_discovery_report(league, report)
            print(f"{league.upper()}: auth_required")
        return 1

    exit_code = 0
    for league in args.league:
        report = summarize_league(client, league)
        write_discovery_report(league, report)
        print(f"{league.upper()}: {report['status']}")
        if report["status"] != "ok":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

