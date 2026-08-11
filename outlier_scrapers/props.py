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


MAX_SCHEDULE_EVENT_FETCHES = 50


def write_json(path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _referenced_event_ids(props_payload: dict[str, Any]) -> set[str]:
    """Return every event referenced by the player-props feed."""
    event_ids: set[str] = set()
    for item in props_payload.get("props") or []:
        if not isinstance(item, dict):
            continue
        raw_outcome = item.get("outcome")
        outcome = raw_outcome if isinstance(raw_outcome, dict) else item
        event_id = str(outcome.get("eventId") or outcome.get("event_id") or "").strip()
        if event_id:
            event_ids.add(event_id)
    return event_ids


def _missing_schedule_event_ids(
    schedule_payload: dict[str, Any], props_payload: dict[str, Any]
) -> list[str]:
    """Return referenced prop events whose schedule rows lack a start time."""
    events = [e for e in (schedule_payload.get("events") or []) if isinstance(e, dict)]
    start_keys = ("scheduledTime", "startTime", "startDate", "date", "scheduled")
    known = {
        str(event.get("eventId") or event.get("id") or "").strip()
        for event in events
        if any(event.get(key) for key in start_keys)
    }
    return sorted(_referenced_event_ids(props_payload) - known)


def enrich_schedule_for_props(
    client: OutlierApiClient,
    schedule_payload: dict[str, Any],
    props_payload: dict[str, Any],
    *,
    max_missing_events: int = MAX_SCHEDULE_EVENT_FETCHES,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Fill schedule gaps for events that are present in props.

    The league schedule endpoint can omit same-day events while the props feed
    still contains hundreds of markets for them.  Missing schedule rows erase
    ``event_starts_at`` during normalization; the pack's pregame guard then has
    to drop every affected card.  Reuse the existing single-event endpoint only
    for referenced IDs that the schedule did not return.
    """
    events = [e for e in (schedule_payload.get("events") or []) if isinstance(e, dict)]
    start_keys = ("scheduledTime", "startTime", "startDate", "date", "scheduled")
    errors: list[dict[str, str]] = []
    missing = _missing_schedule_event_ids(schedule_payload, props_payload)
    for event_id in missing[max_missing_events:]:
        errors.append({"event_id": event_id, "error": "event detail fetch cap exceeded"})
    for event_id in missing[:max_missing_events]:
        try:
            payload = client.fetch_event(event_id)
        except Exception as exc:
            errors.append({"event_id": event_id, "error": str(exc)[:200]})
            continue
        if isinstance(payload, dict) and isinstance(payload.get("event"), dict):
            event = payload["event"]
        elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
            event = next(
                (
                    item
                    for item in payload["events"]
                    if isinstance(item, dict)
                    and str(item.get("eventId") or item.get("id") or "").strip() == event_id
                ),
                None,
            )
        else:
            event = payload
        if not isinstance(event, dict):
            errors.append({"event_id": event_id, "error": "non-object event payload"})
            continue
        enriched = dict(event)
        enriched.setdefault("eventId", event_id)
        if not any(enriched.get(key) for key in start_keys):
            errors.append({"event_id": event_id, "error": "event payload missing scheduled time"})
            continue
        events = [
            existing
            for existing in events
            if str(existing.get("eventId") or existing.get("id") or "").strip() != event_id
        ]
        events.append(enriched)
    return {**schedule_payload, "events": events}, errors


def export_props_for_league(client: OutlierApiClient, league: str) -> dict[str, Any]:
    config = get_sport_config(league)
    paths = league_paths(config.league_id).ensure()
    schedule_payload = client.fetch_schedule(config.league_id)
    props_payload = client.fetch_player_props(config.league_id)
    schedule_event_fetch_requested_count = len(
        _missing_schedule_event_ids(schedule_payload, props_payload)
    )
    schedule_payload, schedule_event_errors = enrich_schedule_for_props(
        client, schedule_payload, props_payload
    )

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
        "status": "partial" if schedule_event_errors else "ok",
        "generated_at": datetime.now().astimezone().isoformat(),
        "raw_latest": str(raw_latest),
        "normalized_latest": str(normalized_latest),
        "record_count": normalized["record_count"],
        "schedule_event_fetch_requested_count": schedule_event_fetch_requested_count,
        "schedule_event_fetch_error_count": len(schedule_event_errors),
        "schedule_event_fetch_errors": schedule_event_errors,
    }
    write_json(paths.reports / "props_export_status_latest.json", status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export standalone Outlier props")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument(
        "--all", action="store_true", help="Accepted for compatibility; API exports all"
    )
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
