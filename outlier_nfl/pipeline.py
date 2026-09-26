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
from outlier_nfl.external import load_external_metrics

# Ensure Windows stdout handles UTF-8 gracefully
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from outlier_nfl.api import OutlierNflApiClient
from outlier_nfl.constants import (
    MARKET_TYPE_GAMELINE,
    MARKET_TYPE_TEAM_PROP,
)
from outlier_nfl.models import NflGameLine, NflPlayerProp
from outlier_nfl.matchup import (
    apply_matchup_signals,
    build_matchup_scripts,
    load_prior_week_tape,
    render_matchup_markdown,
    scripts_to_records,
)
from outlier_nfl.normalizer import (
    apply_game_script_calibration,
    build_schedule_index,
    build_team_index,
    normalize_game_markets,
    normalize_player_props,
    select_consensus_player_props,
)
from outlier_nfl.schema import (
    validate_event_markets_payload,
    validate_normalized_dataset,
    validate_player_props_payload,
    validate_schedule_payload,
)
from outlier_nfl.roster import build_team_roster_index
from outlier_nfl.enrich_close import attach_close_fields
from outlier_nfl.utils import (
    matches_kickoff_window,
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
        window: str | None = None,
        offline_fixtures_dir: Path | str | None = None,
        generate_game_script: bool = False,
        reports_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        """Execute full extraction and normalization run.

        Args:
            date: Target slate date (YYYY-MM-DD). If omitted, defaults to current Eastern date.
            window: Optional kickoff window filter (e.g. '1pm', 'early', '4pm', 'late', 'snf').
            offline_fixtures_dir: Optional path to JSON fixtures for offline execution.
            generate_game_script: If True, automatically synthesize game script report.
            reports_dir: Directory the game script markdown is written to. Defaults to
                ./reports/NFL relative to the working directory.

        Returns:
            NflExtractionSummary dictionary.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        target_date = date or to_eastern_date(datetime.now(timezone.utc)) or "2026-09-13"

        window_label = f" (window: {window})" if window else ""
        logger.info("Starting Outlier NFL Pipeline run for date: %s%s", target_date, window_label)
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
            event_markets_raw = safe_read_json(
                fix_path / "event_markets.json", default={"markets": []}
            )
            player_props_raw = safe_read_json(fix_path / "player_props.json", default={"props": []})

            sched_errs = validate_schedule_payload(schedule_raw)
            if sched_errs:
                errors.extend(sched_errs)
                logger.warning("Schedule schema validation warnings: %s", sched_errs)

            team_index = build_team_index(schedule_raw)
            schedule_index = build_schedule_index(schedule_raw)

            events = schedule_raw.get("events", [])
            slate_events = [
                e
                for e in events
                if to_eastern_date(e.get("scheduledTime") or e.get("startTime")) == target_date
                and matches_kickoff_window(e.get("scheduledTime") or e.get("startTime"), window)
            ]

            # If no events matched target date exactly, fall back to all events in fixture mode
            if not slate_events and events and not window:
                slate_events = events

            for event in slate_events:
                lines = normalize_game_markets(event, event_markets_raw, team_index)
                all_game_lines.extend(lines)

            props = normalize_player_props(player_props_raw, schedule_index)
            slate_event_ids = {str(e.get("eventId") or e.get("id")) for e in slate_events}
            if slate_event_ids:
                all_player_props = [
                    p for p in props if p.event_id in slate_event_ids or not p.event_id
                ]
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
                e
                for e in events
                if to_eastern_date(e.get("scheduledTime") or e.get("startTime")) == target_date
                and matches_kickoff_window(e.get("scheduledTime") or e.get("startTime"), window)
            ]
            logger.info(
                "Found %d scheduled events for slate date %s%s",
                len(slate_events),
                target_date,
                window_label,
            )

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
                            logger.warning(
                                "Market payload errors for %s (%s): %s", event_id, m_type, val_errs
                            )
                        if isinstance(mkt_payload, dict) and isinstance(
                            mkt_payload.get("markets"), list
                        ):
                            event_markets.extend(mkt_payload["markets"])
                    except Exception as exc:
                        logger.warning(
                            "Failed fetching %s markets for event %s: %s", m_type, event_id, exc
                        )

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
        # 1.5 Per-matchup tape analysis, then consensus + calibration
        # =====================================================================
        tapes = load_prior_week_tape(self.nfl_dir)
        # Load external advanced metrics for the season
        try:
            season_year = int(target_date.split('-')[0])
            external_metrics = load_external_metrics(season_year, through_week=22)
            logger.info("Loaded %d external metric records", len(external_metrics))
        except Exception as exc:
            logger.warning("Failed loading external metrics: %s", exc)
            external_metrics = []
        # Persist external metrics to JSON for downstream use and analysis
        external_metrics_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "count": len(external_metrics),
            "records": external_metrics,
        }
        safe_write_json(self.normalized_dir / "nfl_external_metrics_latest.json", external_metrics_payload)
        safe_write_json(self.normalized_dir / f"nfl_external_metrics_{target_date}.json", external_metrics_payload)

        matchup_scripts = build_matchup_scripts(
            all_game_lines,
            tapes,
            slate_events=slate_events,
        )
        if matchup_scripts:
            logger.info(
                "Built %d matchup scripts from prior-week tape (%d teams)",
                len(matchup_scripts),
                len(tapes),
            )

        if all_player_props:
            consensus_props = select_consensus_player_props(all_player_props)
            calibrated_props = apply_game_script_calibration(all_game_lines, consensus_props)
            calibrated_props = apply_matchup_signals(calibrated_props, matchup_scripts)
        else:
            consensus_props = []
            calibrated_props = []

        all_player_props = calibrated_props

        # =====================================================================
        # 2. Schema Validation Phase
        # =====================================================================
        games_dict = [g.to_dict() for g in all_game_lines]
        props_dict = [p.to_dict() for p in all_player_props]

        game_errs = validate_normalized_dataset(games_dict, dataset_type="games")
        prop_errs = validate_normalized_dataset(props_dict, dataset_type="props")
        if game_errs:
            errors.extend(game_errs)
            logger.error(
                "Validation failed on %d game line records: %s", len(game_errs), game_errs[:5]
            )
        if prop_errs:
            errors.extend(prop_errs)
            logger.error(
                "Validation failed on %d player prop records: %s", len(prop_errs), prop_errs[:5]
            )

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

        # Write calibrated and high-probability datasets
        safe_write_json(self.normalized_dir / "nfl_calibrated_props_latest.json", props_payload)
        safe_write_json(
            self.normalized_dir / f"nfl_calibrated_props_{target_date}.json", props_payload
        )

        anchors = [p.to_dict() for p in calibrated_props if p.confidence_tier == "TIER_1_ANCHOR"]
        # Persist emit-time odds as close_* with honest source label (not book close).
        for row in anchors:
            attach_close_fields(row, mode="snapshot_best", attach_model_p="empirical_hit_rate")
        anchors_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "count": len(anchors),
            "records": anchors,
            "close_enrichment": {
                "mode": "snapshot_best",
                "note": "close_* copied from line/best_odds/implied at emit; not true book close.",
            },
            "model_p_enrichment": {
                "mode": "empirical_hit_rate",
                "note": (
                    "model_p from L10/L20/L5/season hit rates "
                    "(source=empirical_hit_rate); never copied from implied_probability."
                ),
            },
        }
        safe_write_json(self.normalized_dir / "nfl_high_prob_props_latest.json", anchors_payload)
        safe_write_json(
            self.normalized_dir / f"nfl_high_prob_props_{target_date}.json", anchors_payload
        )

        # Build verified active roster index
        rosters = build_team_roster_index(all_player_props)
        rosters_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "teams_count": len(rosters),
            "rosters": rosters,
        }
        safe_write_json(self.normalized_dir / "nfl_rosters_latest.json", rosters_payload)
        safe_write_json(self.normalized_dir / f"nfl_rosters_{target_date}.json", rosters_payload)

        scripts_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "count": len(matchup_scripts),
            "records": scripts_to_records(matchup_scripts),
        }
        safe_write_json(self.normalized_dir / "nfl_matchup_scripts_latest.json", scripts_payload)
        safe_write_json(
            self.normalized_dir / f"nfl_matchup_scripts_{target_date}.json",
            scripts_payload,
        )

        matchup_prop_records = [
            p.to_dict()
            for p in calibrated_props
            if any(str(tag).startswith("MATCHUP_") for tag in p.calibration_tags)
        ]
        for row in matchup_prop_records:
            attach_close_fields(row, mode="snapshot_best", attach_model_p="empirical_hit_rate")
        matchup_props_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "count": len(matchup_prop_records),
            "records": matchup_prop_records,
            "close_enrichment": {
                "mode": "snapshot_best",
                "note": "close_* copied from line/best_odds/implied at emit; not true book close.",
            },
            "model_p_enrichment": {
                "mode": "empirical_hit_rate",
                "note": (
                    "model_p from L10/L20/L5/season hit rates "
                    "(source=empirical_hit_rate); never copied from implied_probability."
                ),
            },
        }
        safe_write_json(
            self.normalized_dir / "nfl_matchup_props_latest.json", matchup_props_payload
        )
        safe_write_json(
            self.normalized_dir / f"nfl_matchup_props_{target_date}.json",
            matchup_props_payload,
        )

        if window:
            window_slug = window.strip().lower()
            safe_write_json(
                self.normalized_dir / f"nfl_games_{target_date}_{window_slug}.json", games_payload
            )
            safe_write_json(
                self.normalized_dir / f"nfl_props_{target_date}_{window_slug}.json", props_payload
            )
            safe_write_json(
                self.normalized_dir / f"nfl_calibrated_props_{target_date}_{window_slug}.json",
                props_payload,
            )
            safe_write_json(
                self.normalized_dir / f"nfl_high_prob_props_{target_date}_{window_slug}.json",
                anchors_payload,
            )
            safe_write_json(
                self.normalized_dir / f"nfl_rosters_{target_date}_{window_slug}.json",
                rosters_payload,
            )
            safe_write_json(
                self.normalized_dir / f"nfl_matchup_scripts_{target_date}_{window_slug}.json",
                scripts_payload,
            )
            safe_write_json(
                self.normalized_dir / f"nfl_matchup_props_{target_date}_{window_slug}.json",
                matchup_props_payload,
            )

        # Compute counts and breakdown
        spreads_count = sum(1 for g in all_game_lines if g.market == "SPREAD")
        totals_count = sum(1 for g in all_game_lines if g.market == "TOTAL")
        team_totals_count = sum(1 for g in all_game_lines if g.market_type == "TEAM_PROP")
        consensus_props_count = sum(1 for p in calibrated_props if p.is_consensus_line)
        tier_1_anchors_count = len(anchors)

        prop_breakdown: dict[str, int] = {}
        for p in all_player_props:
            prop_breakdown[p.market] = prop_breakdown.get(p.market, 0) + 1

        starting_qbs = {t: r["starting_qb"] for t, r in rosters.items() if r.get("starting_qb")}

        summary: dict[str, Any] = {
            "status": "OK",
            "date": target_date,
            "window": window,
            "timestamp_utc": now_utc,
            "events_count": len(slate_events),
            "game_lines_count": len(all_game_lines),
            "spreads_count": spreads_count,
            "totals_count": totals_count,
            "team_totals_count": team_totals_count,
            "player_props_count": len(all_player_props),
            "props_count": len(all_player_props),
            "consensus_props_count": consensus_props_count,
            "tier_1_anchors_count": tier_1_anchors_count,
            "player_props_breakdown": prop_breakdown,
            "starting_qbs": starting_qbs,
            "matchup_scripts_count": len(matchup_scripts),
            "matchup_tagged_props_count": len(matchup_prop_records),
            "errors": errors,
        }

        # Optional Game Script Generation — one markdown file per matchup
        if generate_game_script and all_game_lines:
            try:
                from scripts.nfl_game_script import NflGameScriptGenerator

                generator = NflGameScriptGenerator(data_dir=self.normalized_dir)
                report_root = Path(reports_dir) if reports_dir is not None else Path("reports/NFL")
                report_root.mkdir(parents=True, exist_ok=True)
                written: list[str] = []
                for script in matchup_scripts:
                    slug = f"{script.away_team}_{script.home_team}".replace(" ", "_")
                    report_file = report_root / f"{target_date}_{slug}_Game_Script.md"
                    event_games = [g for g in games_dict if g.get("event_id") == script.event_id]
                    event_props = [p for p in props_dict if p.get("event_id") == script.event_id]
                    env = generator.extract_game_environment(
                        event_games,
                        home_team=script.home_team,
                        away_team=script.away_team,
                    )
                    report_md = render_matchup_markdown(
                        script,
                        env=env,
                        props=event_props,
                        date=target_date,
                    )
                    report_file.write_text(report_md, encoding="utf-8")
                    written.append(str(report_file))
                if written:
                    summary["game_script_file"] = written[0]
                    summary["game_script_files"] = written
                    logger.info(
                        "Generated %d matchup game scripts under %s", len(written), report_root
                    )
            except Exception as exc:
                logger.warning("Failed generating game script: %s", exc)

        safe_write_json(self.normalized_dir / "summary_latest.json", summary)
        safe_write_json(self.normalized_dir / f"summary_{target_date}.json", summary)
        if window:
            safe_write_json(
                self.normalized_dir / f"summary_{target_date}_{window_slug}.json", summary
            )

        logger.info(
            "NFL Pipeline run completed successfully: %d games, %d spreads, %d totals, %d team totals, %d props (%d consensus, %d Tier-1 anchors)",
            len(all_game_lines),
            spreads_count,
            totals_count,
            team_totals_count,
            len(all_player_props),
            consensus_props_count,
            tier_1_anchors_count,
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
        "--window",
        type=str,
        default=None,
        help="Kickoff window filter (e.g. '1pm', 'early', '4pm', 'late', 'snf'). Default: all windows.",
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
        "--generate-game-script",
        action="store_true",
        help="Automatically generate structured betting game script markdown report.",
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
            window=args.window,
            offline_fixtures_dir=args.fixtures_dir if args.mode == "fixture" else None,
            generate_game_script=args.generate_game_script,
        )
        print("=" * 60)
        print("OUTLIER NFL PIPELINE EXECUTION SUMMARY")
        print(f"Date:                   {summary.get('date')}")
        if summary.get("window"):
            print(f"Kickoff Window:         {summary.get('window')}")
        print(f"Status:                 {summary.get('status')}")
        print(f"Events Count:           {summary.get('events_count')}")
        print(f"Game Lines Count:       {summary.get('game_lines_count')}")
        print(f"  - Spreads:            {summary.get('spreads_count')}")
        print(f"  - Totals:             {summary.get('totals_count')}")
        print(f"  - Team Totals:        {summary.get('team_totals_count')}")
        print(f"Player Props Count:     {summary.get('player_props_count')}")
        print(f"Consensus Props Count:  {summary.get('consensus_props_count')}")
        print(f"Tier-1 Anchors Count:   {summary.get('tier_1_anchors_count')}")
        print(f"Matchup Scripts:        {summary.get('matchup_scripts_count')}")
        print(f"Matchup-Tagged Props:   {summary.get('matchup_tagged_props_count')}")
        if summary.get("game_script_file"):
            print(f"Game Script Generated:  {summary.get('game_script_file')}")
        if summary.get("starting_qbs"):
            print("-" * 60)
            print("VERIFIED ACTIVE STARTING QUARTERBACKS (FROM FEED):")
            for t, qb in sorted(summary["starting_qbs"].items()):
                print(f"  {t:4s}: {qb}")
        print("=" * 60)
        return 0
    except Exception as exc:
        logger.exception("Pipeline execution failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
