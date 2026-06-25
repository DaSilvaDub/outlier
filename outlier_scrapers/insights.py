from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime
from typing import Any

from .api import AuthRequiredError, OutlierApiClient, OutlierApiError
from .normalizer import (
    _to_float,
    _to_int,
    detect_scope,
    implied_probability,
    parse_market_descriptor,
    parse_player_name,
    percent_number,
)
from .paths import league_paths
from .props import write_json
from .registry import (
    SportConfig,
    get_sport_config,
    normalize_market,
    normalize_team,
    supported_leagues,
)


def _resolve_teams(
    insight: dict[str, Any],
    config: SportConfig,
) -> tuple[str | None, str | None, str | None, str | None, str | None, dict[str, Any]]:
    event = insight.get("event") if isinstance(insight.get("event"), dict) else {}
    away = event.get("away") if isinstance(event.get("away"), dict) else {}
    home = event.get("home") if isinstance(event.get("home"), dict) else {}
    away_alias = away.get("alias") or away.get("abbr") or away.get("name")
    home_alias = home.get("alias") or home.get("abbr") or home.get("name")
    away_id = str(away.get("teamId") or "").strip()
    home_id = str(home.get("teamId") or "").strip()
    team_id = str(insight.get("teamId") or "").strip()

    away_norm = normalize_team(config, away_alias)
    home_norm = normalize_team(config, home_alias)
    matchup_raw = None
    if away_alias or home_alias:
        matchup_raw = f"{away_norm or away_alias} @ {home_norm or home_alias}"

    if team_id and team_id == away_id:
        team, team_raw, opp, opp_raw = (
            away_norm,
            str(away_alias or "").strip() or None,
            home_norm,
            str(home_alias or "").strip() or None,
        )
    elif team_id and team_id == home_id:
        team, team_raw, opp, opp_raw = (
            home_norm,
            str(home_alias or "").strip() or None,
            away_norm,
            str(away_alias or "").strip() or None,
        )
    else:
        team = team_raw = opp = opp_raw = None
    return team, team_raw, opp, opp_raw, matchup_raw, event


def _last_n(values: Any) -> tuple[list[bool] | None, str | None, float | None]:
    if not isinstance(values, list) or not values:
        return None, None, None
    bools = [bool(v) for v in values]
    hits = sum(1 for value in bools if value)
    total = len(bools)
    return bools, f"{hits}/{total}", round(100.0 * hits / total, 3)


def normalize_insights(payload: dict[str, Any], config: SportConfig) -> list[dict[str, Any]]:
    insights = payload.get("insights")
    if not isinstance(insights, list):
        return []

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        insight_id = str(insight.get("insightId") or "").strip()
        if not insight_id:
            continue

        market_label = str(insight.get("marketLabel") or "").strip()
        proposition = insight.get("proposition")
        prop_label = str(insight.get("propLabel") or "").strip()
        market_raw = (
            parse_market_descriptor({"marketLabel": market_label, "proposition": proposition})
            or prop_label
            or None
        )

        # Canonical market is full-game only; scoped variants keep market_raw.
        scope = detect_scope(market_label, prop_label, market_raw)
        market = None
        if scope == "full_game":
            market = normalize_market(config, proposition) or normalize_market(config, market_raw)

        player_raw = parse_player_name(insight) or None
        team, team_raw, opp, opp_raw, matchup_raw, event = _resolve_teams(insight, config)
        last_n_raw, last_n_record, last_n_pct = _last_n(insight.get("lastN"))
        side = str(insight.get("position") or "").strip().upper()

        row = {
            "league": config.league_id,
            "insight_id": insight_id,
            "subject_type": str(insight.get("subjectType") or "").strip().lower() or None,
            "event_id": str(insight.get("eventId") or "").strip() or None,
            "market_id": str(insight.get("marketId") or "").strip() or None,
            "market_outcome_id": insight.get("marketOutcomeId"),
            "player": player_raw,
            "player_raw": player_raw,
            "player_id": insight.get("playerId"),
            "team": team,
            "team_raw": team_raw,
            "opponent": opp,
            "opponent_raw": opp_raw,
            "matchup": matchup_raw,
            "matchup_raw": matchup_raw,
            "market": market,
            "market_raw": market_raw,
            "scope": scope,
            "side": side if side in {"OVER", "UNDER"} else None,
            "line": _to_float(insight.get("line")),
            "hit_rate_pct": percent_number(insight.get("hitRate")),
            "last_n_record": last_n_record,
            "last_n_pct": last_n_pct,
            "relevancy": _to_int(insight.get("relevancy")),
            "best_odds": _to_int(insight.get("bestOdds")),
            "ip_pct": implied_probability(insight.get("bestOdds")),
            "text": str(insight.get("text") or "").strip() or None,
            "sport_context": {
                "proposition": proposition,
                "market_label": market_label or None,
                "prop_label": prop_label or None,
                "outcome_label": insight.get("outcomeLabel"),
                "line_label": insight.get("lineLabel"),
                "market_type": insight.get("marketType"),
                "market_active": insight.get("marketActive"),
                "include_overtime": insight.get("includeOvertime"),
                "player_position": insight.get("playerPosition"),
                "last_n": last_n_raw,
                "splits": insight.get("splits"),
                "player_data": insight.get("playerData"),
                "event_scheduled_time": event.get("scheduledTime"),
                "source": f"sportsdata/leagues/{config.league_id}/insights",
            },
        }
        deduped[(config.league_id, insight_id)] = row

    return sorted(
        deduped.values(),
        key=lambda row: (
            -(row.get("relevancy") or 0),
            str(row.get("matchup_raw") or ""),
            str(row.get("player_raw") or ""),
            str(row.get("market_raw") or ""),
            str(row.get("side") or ""),
        ),
    )


def _subject_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: collections.Counter[str] = collections.Counter(
        str(row.get("subject_type") or "unknown") for row in rows
    )
    return dict(sorted(counts.items()))


def build_insights_payload(
    *,
    config: SportConfig,
    insights_payload: dict[str, Any],
    source_url: str,
) -> dict[str, Any]:
    rows = normalize_insights(insights_payload, config)
    pagination = (
        insights_payload.get("_page_summary")
        if isinstance(insights_payload.get("_page_summary"), dict)
        else None
    )
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "league": config.league_id,
        "source_url": source_url,
        "source_method": "api",
        "record_count": len(rows),
        "pagination": pagination,
        "subject_type_counts": _subject_type_counts(rows),
        "primary_record_array": "records",
        "records": rows,
        "data_contract": {
            "dataset": "outlier_insights",
            "version": "1.0",
            "intended_use": "Standalone Outlier player/team insights for betting triage.",
            "dedupe_key": "league+insight_id",
            "row_grain": "one row per insight_id",
        },
    }


def export_insights_for_league(client: OutlierApiClient, league: str) -> dict[str, Any]:
    config = get_sport_config(league)
    paths = league_paths(config.league_id).ensure()
    insights_payload = client.fetch_insights(config.league_id)

    exported_at = datetime.now().astimezone().isoformat()
    raw_payload = {
        "exported_at": exported_at,
        "generated_at": datetime.now().astimezone().isoformat(),
        "league": config.league_id,
        "source": "Outlier authenticated API",
        "insights": insights_payload,
    }
    raw_latest = paths.raw / f"{config.league_id.lower()}_insights_raw_latest.json"
    raw_archive = paths.timestamped(paths.raw, "insights_raw")
    write_json(raw_latest, raw_payload)
    write_json(raw_archive, raw_payload)

    source_url = client.url_for(f"/sportsdata/leagues/{config.league_id}/insights")
    normalized = build_insights_payload(
        config=config,
        insights_payload=insights_payload,
        source_url=source_url,
    )
    normalized_latest = paths.normalized / f"{config.league_id.lower()}_insights_latest.json"
    normalized_archive = paths.timestamped(paths.normalized, "insights")
    write_json(normalized_latest, normalized)
    write_json(normalized_archive, normalized)

    status = {
        "league": config.league_id,
        "status": "ok",
        "generated_at": datetime.now().astimezone().isoformat(),
        "raw_latest": str(raw_latest),
        "normalized_latest": str(normalized_latest),
        "record_count": normalized["record_count"],
        "subject_type_counts": normalized["subject_type_counts"],
        "pagination": normalized["pagination"],
    }
    write_json(paths.reports / "insights_status_latest.json", status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export standalone Outlier insights")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument(
        "--all", action="store_true", help="Accepted for clarity; the API exports all insights"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = OutlierApiClient()
        status = export_insights_for_league(client, args.league)
    except AuthRequiredError as exc:
        paths = league_paths(args.league).ensure()
        report = {
            "league": args.league.upper(),
            "status": "auth_required",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:200],
        }
        write_json(paths.reports / "insights_status_latest.json", report)
        print(f"{args.league.upper()}: auth_required")
        return 1
    except (OutlierApiError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        paths = league_paths(args.league).ensure()
        report = {
            "league": args.league.upper(),
            "status": "error",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:300],
        }
        write_json(paths.reports / "insights_status_latest.json", report)
        print(f"{args.league.upper()}: error")
        return 1

    print(
        f"{status['league']}: exported {status['record_count']} insights {status['subject_type_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
