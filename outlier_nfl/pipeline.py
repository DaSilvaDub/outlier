"""End-to-end orchestration pipeline for outlier_nfl.

Coordinates ingestion from the Outlier NFL REST API (or offline fixtures),
executes schema validation gates, normalizes game lines and player props,
and persists normalized datasets to disk atomically.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
from typing import Any

from outlier_nfl.api import OutlierNflApiClient
from outlier_nfl.constants import (
    MARKET_TYPE_GAMELINE,
    MARKET_TYPE_TEAM_PROP,
)
from outlier_nfl.models import NflGameLine, NflPlayerProp
from outlier_nfl.normalizer import (
    build_schedule_index,
    build_team_index,
    normalize_game_markets,
    normalize_player_props,
)
from outlier_nfl.schema import (
    validate_event_markets_payload,
    validate_normalized_dataset,
    validate_player_props_payload,
    validate_schedule_payload,
)
from outlier_nfl.utils import (
    safe_read_json,
    safe_write_json,
    to_eastern_date,
)

logger = logging.getLogger("outlier_nfl.pipeline")


class NflPipeline:
    """Orchestrator for NFL betting data extraction, normalization, and persistence."""

    def __init__(
        self,
        client: OutlierNflApiClient | None = None,
        data_dir: Path | str = "data",
    ) -> None:
        self.client: OutlierNflApiClient | None = client
        self.data_dir: Path = Path(data_dir).resolve()
        self.nfl_dir: Path = self.data_dir / "NFL"
        self.normalized_dir: Path = self.nfl_dir / "normalized"
        self.raw_dir: Path = self.nfl_dir / "raw"

    def _get_client(self) -> OutlierNflApiClient:
        """Return existing API client or initialize default authenticated client."""
        if self.client is None:
            self.client = OutlierNflApiClient()
        return self.client

    def run(
        self,
        date: str | None = None,
        offline_fixtures_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        """Execute full extraction and normalization run.

        Args:
            date: Target slate date (YYYY-MM-DD). If omitted, defaults to current Eastern date.
            offline_fixtures_dir: Optional path to JSON fixtures for offline execution.

        Returns:
            NflExtractionSummary dictionary.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        target_date = date or to_eastern_date(datetime.now(timezone.utc)) or "2026-09-13"

        logger.info("Starting Outlier NFL Pipeline run for date: %s", target_date)
        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        schedule_raw: dict[str, Any] = {}
        all_game_lines: list[NflGameLine] = []
        all_player_props: list[NflPlayerProp] = []
        errors: list[str] = []

        # =====================================================================
        # 1. Ingestion Phase
        # =====================================================================
        if offline_fixtures_dir:
            fix_path = Path(offline_fixtures_dir)
            logger.info("Loading offline fixtures from %s", fix_path)
            schedule_raw = safe_read_json(fix_path / "schedule.json", default={"events": []})
            event_markets_raw = safe_read_json(fix_path / "event_markets.json", default={"markets": []})
            player_props_raw = safe_read_json(fix_path / "player_props.json", default={"props": []})

            sched_errs = validate_schedule_payload(schedule_raw)
            if sched_errs:
                errors.extend(sched_errs)
                logger.warning("Schedule schema validation warnings: %s", sched_errs)

            team_index = build_team_index(schedule_raw)
            schedule_index = build_schedule_index(schedule_raw)

            events = schedule_raw.get("events", [])
            slate_events = [
                e for e in events
                if to_eastern_date(e.get("scheduledTime") or e.get("startTime")) == target_date
            ]

            # If no events matched target date exactly, fall back to all events in fixture mode
            if not slate_events and events:
                slate_events = events

            for event in slate_events:
                lines = normalize_game_markets(event, event_markets_raw, team_index)
                all_game_lines.extend(lines)

            props = normalize_player_props(player_props_raw, schedule_index)
            slate_event_ids = {str(e.get("eventId") or e.get("id")) for e in slate_events}
            if slate_event_ids:
                all_player_props = [p for p in props if p.event_id in slate_event_ids or not p.event_id]
                if not all_player_props and props:
                    all_player_props = props
            else:
                all_player_props = props

        else:
            client = self._get_client()
            logger.info("Fetching live schedule from Outlier API...")
            schedule_raw = client.fetch_schedule()

            sched_errs = validate_schedule_payload(schedule_raw)
            if sched_errs:
                errors.extend(sched_errs)
                logger.warning("Schedule payload validation warnings: %s", sched_errs)

            events = schedule_raw.get("events", []) if isinstance(schedule_raw, dict) else []
            team_index = build_team_index(schedule_raw)
            schedule_index = build_schedule_index(schedule_raw)

            slate_events = [
                e for e in events
                if to_eastern_date(e.get("scheduledTime") or e.get("startTime")) == target_date
            ]
            logger.info("Found %d scheduled events for slate date %s", len(slate_events), target_date)

            # Extract markets per slate event
            for event in slate_events:
                event_id = str(event.get("eventId") or event.get("id") or "")
                if not event_id:
                    continue

                event_markets: list[dict[str, Any]] = []
                for m_type in (MARKET_TYPE_GAMELINE, MARKET_TYPE_TEAM_PROP):
                    try:
                        mkt_payload = client.fetch_event_markets(event_id, market_type=m_type)
                        val_errs = validate_event_markets_payload(mkt_payload)
                        if val_errs:
                            logger.warning("Market payload errors for %s (%s): %s", event_id, m_type, val_errs)
                        if isinstance(mkt_payload, dict) and isinstance(mkt_payload.get("markets"), list):
                            event_markets.extend(mkt_payload["markets"])
                    except Exception as exc:
                        logger.warning("Failed fetching %s markets for event %s: %s", m_type, event_id, exc)

                lines = normalize_game_markets(event, {"markets": event_markets}, team_index)
                all_game_lines.extend(lines)

            # Ingest bulk player props if slate has events
            if slate_events:
                try:
                    logger.info("Fetching bulk player props from Outlier API...")
                    player_props_raw = client.fetch_player_props()
                    val_errs = validate_player_props_payload(player_props_raw)
                    if val_errs:
                        logger.warning("Player props payload warnings: %s", val_errs)
                except Exception as exc:
                    logger.warning("Failed fetching player props: %s", exc)
                    player_props_raw = {"props": []}

                props = normalize_player_props(player_props_raw, schedule_index)
                slate_event_ids = {str(e.get("eventId") or e.get("id")) for e in slate_events}
                all_player_props = [p for p in props if p.event_id in slate_event_ids]
            else:
                all_player_props = []

        # =====================================================================
        # 2. Schema Validation Phase
        # =====================================================================
        games_dict = [g.to_dict() for g in all_game_lines]
        props_dict = [p.to_dict() for p in all_player_props]

        game_errs = validate_normalized_dataset(games_dict, dataset_type="games")
        prop_errs = validate_normalized_dataset(props_dict, dataset_type="props")
        if game_errs:
            errors.extend(game_errs)
            logger.error("Validation failed on %d game line records: %s", len(game_errs), game_errs[:5])
        if prop_errs:
            errors.extend(prop_errs)
            logger.error("Validation failed on %d player prop records: %s", len(prop_errs), prop_errs[:5])

        # =====================================================================
        # 3. Persistence Phase
        # =====================================================================
        games_payload = {
            "date": target_date,
            "updated_at": now_utc,
            "count": len(games_dict),
            "records": games_dict,
        }
        props_payload = {
            "date": target_date,
            "updated_at": now_utc,
            "count": len(props_dict),
            "records": props_dict,
        }

        # Write normalized outputs atomically
        safe_write_json(self.normalized_dir / "nfl_games_latest.json", games_payload)
        safe_write_json(self.normalized_dir / "nfl_props_latest.json", props_payload)
        safe_write_json(self.normalized_dir / f"nfl_games_{target_date}.json", games_payload)
        safe_write_json(self.normalized_dir / f"nfl_props_{target_date}.json", props_payload)

        # Compute counts and breakdown
        spreads_count = sum(1 for g in all_game_lines if g.market == "SPREAD")
        totals_count = sum(1 for g in all_game_lines if g.market == "TOTAL")
        team_totals_count = sum(1 for g in all_game_lines if g.market_type == "TEAM_PROP")

        prop_breakdown: dict[str, int] = {}
        for p in all_player_props:
            prop_breakdown[p.market] = prop_breakdown.get(p.market, 0) + 1

        summary: dict[str, Any] = {
            "status": "OK",
            "date": target_date,
            "timestamp_utc": now_utc,
            "events_count": len(slate_events),
            "game_lines_count": len(all_game_lines),
            "spreads_count": spreads_count,
            "totals_count": totals_count,
            "team_totals_count": team_totals_count,
            "player_props_count": len(all_player_props),
            "props_count": len(all_player_props),
            "player_props_breakdown": prop_breakdown,
            "errors": errors,
        }

        safe_write_json(self.normalized_dir / "summary_latest.json", summary)
        safe_write_json(self.normalized_dir / f"summary_{target_date}.json", summary)

        logger.info(
            "NFL Pipeline run completed successfully: %d games, %d spreads, %d totals, %d team totals, %d props",
            len(all_game_lines),
            spreads_count,
            totals_count,
            team_totals_count,
            len(all_player_props),
        )
        return summary


def main() -> int:
    """CLI entry point to execute Outlier NFL Pipeline."""
    parser = argparse.ArgumentParser(description="Execute Outlier NFL betting data pipeline.")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target NFL slate date (YYYY-MM-DD). Default: current Eastern date.",
    )
    parser.add_argument(
        "--mode",
        choices=["live", "fixture"],
        default="live",
        help="Execution mode: 'live' (authenticated API) or 'fixture' (offline replay). Default: live.",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=Path(__file__).parent.parent / "tests" / "fixtures" / "nfl",
        help="Directory containing offline JSON fixtures.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Output root directory for NFL data. Default: ./data",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logging.",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        pipeline = NflPipeline(data_dir=args.data_dir)
        summary = pipeline.run(
            date=args.date,
            offline_fixtures_dir=args.fixtures_dir if args.mode == "fixture" else None,
        )
        print("=" * 60)
        print("OUTLIER NFL PIPELINE EXECUTION SUMMARY")
        print(f"Date:               {summary.get('date')}")
        print(f"Status:             {summary.get('status')}")
        print(f"Events Count:       {summary.get('events_count')}")
        print(f"Game Lines Count:   {summary.get('game_lines_count')}")
        print(f"  - Spreads:        {summary.get('spreads_count')}")
        print(f"  - Totals:         {summary.get('totals_count')}")
        print(f"  - Team Totals:    {summary.get('team_totals_count')}")
        print(f"Player Props Count: {summary.get('player_props_count')}")
        print("=" * 60)
        return 0
    except Exception as exc:
        logger.exception("Pipeline execution failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
