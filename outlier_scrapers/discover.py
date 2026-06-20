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

# Candidate endpoint paths probed in --deep mode. These are guesses; the probe
# reports which respond so we learn the real paths from live status instead of
# assuming them (same approach used for the pagination param). "{L}" -> league.
INSIGHT_PATH_CANDIDATES = (
    "/sportsdata/leagues/{L}/insights",
    "/sportsdata/leagues/{L}/trending",
    "/sportsdata/leagues/{L}/trending/insights",
    "/sportsdata/leagues/{L}/trendingInsights",
    "/insights/leagues/{L}",
)
EV_ARB_PATH_CANDIDATES = (
    "/sportsdata/leagues/{L}/ev",
    "/sportsdata/leagues/{L}/positiveEV",
    "/sportsdata/leagues/{L}/arbitrage",
    "/sportsdata/leagues/{L}/middles",
    "/sportsdata/leagues/{L}/boosts",
    "/sportsdata/leagues/{L}/promos",
)
# Key-name fragments that indicate a market-detail payload carries line history.
MOVEMENT_KEY_FRAGMENTS = (
    "history",
    "movement",
    "open",
    "delta",
    "timestamp",
    "line",
    "odds",
    "prev",
    "change",
)


def _count_array(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else None


def _all_key_names(payload: Any, acc: set[str] | None = None, depth: int = 0) -> set[str]:
    """Recursively collect dict key names (capped depth) for signal detection."""
    if acc is None:
        acc = set()
    if depth > 6:
        return acc
    if isinstance(payload, dict):
        for key, value in payload.items():
            acc.add(str(key))
            _all_key_names(value, acc, depth + 1)
    elif isinstance(payload, list):
        for item in payload[:5]:
            _all_key_names(item, acc, depth + 1)
    return acc


def _probe(client: OutlierApiClient, path: str) -> dict[str, Any]:
    """Read-only probe of an arbitrary path; returns status + redacted shape."""
    try:
        payload = client.fetch_json(path)
    except AuthRequiredError as exc:
        return {"status": "auth_required", "error": str(exc)[:160]}
    except OutlierApiError as exc:
        return {"status": "error", "error": str(exc)[:160]}
    return {
        "status": "ok",
        "top_level_keys": sorted(str(key) for key in payload.keys()),
        "shape": shape_summary(payload, max_depth=4),
    }


def _sample_market_ids(props_payload: dict[str, Any] | None, limit: int = 3) -> list[str]:
    if not isinstance(props_payload, dict):
        return []
    props = props_payload.get("props")
    if not isinstance(props, list):
        return []
    seen: list[str] = []
    for record in props:
        if not isinstance(record, dict):
            continue
        outcome = record.get("outcome") if isinstance(record.get("outcome"), dict) else {}
        market_id = str(outcome.get("marketId") or "").strip()
        if market_id and market_id not in seen:
            seen.append(market_id)
            if len(seen) >= limit:
                break
    return seen


def _probe_market_detail(client: OutlierApiClient, market_id: str) -> dict[str, Any]:
    try:
        payload = client.fetch_market(market_id)
    except AuthRequiredError as exc:
        return {"market_id": market_id, "status": "auth_required", "error": str(exc)[:160]}
    except OutlierApiError as exc:
        return {"market_id": market_id, "status": "error", "error": str(exc)[:160]}
    keys = _all_key_names(payload)
    movement_keys = sorted(
        k for k in keys if any(frag in k.lower() for frag in MOVEMENT_KEY_FRAGMENTS)
    )
    return {
        "market_id": market_id,
        "status": "ok",
        "top_level_keys": sorted(str(key) for key in payload.keys()),
        "movement_signal_keys": movement_keys[:40],
        "shape": shape_summary(payload, max_depth=5),
    }


def _deep_probe(
    client: OutlierApiClient,
    league: str,
    props_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    market_ids = _sample_market_ids(props_payload, limit=3)
    return {
        "market_detail": [_probe_market_detail(client, mid) for mid in market_ids],
        "sampled_market_ids": market_ids,
        "insights_candidates": {
            path.format(L=league): _probe(client, path.format(L=league))
            for path in INSIGHT_PATH_CANDIDATES
        },
        "ev_arb_candidates": {
            path.format(L=league): _probe(client, path.format(L=league))
            for path in EV_ARB_PATH_CANDIDATES
        },
    }


def summarize_league(client: OutlierApiClient, league: str, *, deep: bool = False) -> dict[str, Any]:
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

    payloads: dict[str, dict[str, Any]] = {}
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

        payloads[name] = payload
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

    if deep:
        report["deep"] = _deep_probe(client, config.league_id, payloads.get("playerProps"))

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
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Also probe market-detail (line movement) and candidate insights/EV endpoints.",
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
        report = summarize_league(client, league, deep=args.deep)
        write_discovery_report(league, report)
        suffix = " (deep)" if args.deep else ""
        print(f"{league.upper()}: {report['status']}{suffix}")
        if report["status"] != "ok":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
