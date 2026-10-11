"""End-to-end orchestration pipeline for outlier_nfl.

Coordinates ingestion from the Outlier NFL REST API (or offline fixtures),
executes schema validation gates, normalizes game lines and player props,
and persists normalized datasets to disk atomically.
"""

from __future__ import annotations

from collections import Counter

import argparse
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timezone
import logging
import uuid
from pathlib import Path
import sys
from typing import Any
from outlier_nfl.external import Client as ExternalClient, load_external_metrics

# Ensure Windows stdout handles UTF-8 gracefully
_stdout_reconfigure = getattr(sys.stdout, "reconfigure", None) if sys.stdout else None
if callable(_stdout_reconfigure):
    _stdout_reconfigure(encoding="utf-8", errors="replace")

from outlier_nfl.api import IncompletePaginationError, OutlierNflApiClient
from outlier_nfl.constants import (
    MARKET_TYPE_GAMELINE,
    MARKET_TYPE_TEAM_PROP,
)
from outlier_nfl.models import NflGameLine, NflPlayerProp
from outlier_nfl.tape_nflverse import (
    load_tape_inactives,
    refresh_prior_week_tape,
)
from outlier_nfl.usage import append_signals, load_usage, usage_signals
from outlier_nfl.weather import apply_weather, load_slate_weather
from outlier_nfl.matchup import (
    _event_team_codes,
    apply_matchup_signals,
    build_matchup_scripts,
    TapeEnvelope,
    load_tape_envelope,
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
from outlier_nfl.roster import EVIDENCED_STARTER_STATUSES, build_team_roster_index
from outlier_nfl.best_bets import TraceInputs, build_best_bets, render_best_bets_markdown
from outlier_nfl.run_context import SourceRecord, make_run_context, parse_utc
from outlier_nfl.run_writer import RunWriter
from outlier_nfl.season_phase import season_phase_receipt, slate_season_type
from outlier_nfl.stage_receipts import (
    props_admission_check,
    quote_integrity_receipt,
    StageReceipt,
    failing,
    gate_reason,
    receipt,
    run_status,
    validation_receipt,
)
from outlier_nfl.snapshots import append_snapshot, load_snapshots, movement_index, snapshot_path
from outlier_nfl.enrich_close import attach_close_fields
from outlier_nfl.calibration import (
    DEFAULT_MARKET_PRIOR_KAPPA,
    model_p_bucket_counts,
)
from outlier_nfl.high_prob_rank import (
    actionable_high_prob_records,
    apply_same_player_correlation_guard,
    correlation_guard_summary,
)
from outlier_nfl.utils import (
    matches_kickoff_window,
    normalize_kickoff_window,
    safe_read_json,
    to_eastern_date,
)

logger = logging.getLogger("outlier_nfl.pipeline")


def _schedule_events(schedule_raw: Any) -> list[dict[str, Any]]:
    """The schedule's event objects; anything malformed is already an INVALID receipt."""
    events = schedule_raw.get("events") if isinstance(schedule_raw, dict) else None
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


class NflPipeline:
    """Orchestrator for NFL betting data extraction, normalization, and persistence."""

    def __init__(
        self,
        client: OutlierNflApiClient | None = None,
        data_dir: Path | str = "data",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client: OutlierNflApiClient | None = client
        # Wall clock (injectable for tests and replays); decides publication with as_of.
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
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
        write_latest: bool | None = None,
        target_alt_book: str = "HARDROCK",
        as_of_utc: datetime | str | None = None,
        refresh_tape: bool = False,
        tape_last_n: int | None = None,
    ) -> dict[str, Any]:
        """Execute full extraction and normalization run.

        Args:
            date: Target slate date (YYYY-MM-DD). If omitted, defaults to current Eastern date.
            window: Optional kickoff window filter (e.g. '1pm', 'early', '4pm', 'late', 'snf').
            offline_fixtures_dir: Optional path to JSON fixtures for offline execution.
            generate_game_script: If True, automatically synthesize game script report.
            reports_dir: Directory the game script markdown is written to. Defaults to
                ./reports/NFL relative to the working directory.
            write_latest: Override whether ``*_latest.json`` files are written. Defaults to
                True only for an unwindowed run; the weekly runner passes False so one
                slate date does not overwrite another's ``latest``.
            as_of_utc: Prediction-time cutoff (timezone-aware). Sources are limited to
                what was knowable at this instant. Defaults to the run start; a naive
                value raises ``ValueError``. Publication is decided from both this and
                the wall clock (``clock``): a run started at/after the slate's first
                kickoff, or one whose as_of is materially in the past (a replay),
                writes only its bundle (see :class:`RunContext`).

        Returns:
            NflExtractionSummary dictionary.
        """
        now_dt = parse_utc(self.clock())
        now_utc = now_dt.isoformat()
        target_date = date or to_eastern_date(now_dt) or "2026-09-13"
        as_of_dt = parse_utc(as_of_utc) if as_of_utc is not None else now_dt

        window = normalize_kickoff_window(window)
        if write_latest is None:
            write_latest = window is None
        window_label = f" (window: {window})" if window else ""
        logger.info("Starting Outlier NFL Pipeline run for date: %s%s", target_date, window_label)
        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        schedule_raw: dict[str, Any] = {}
        raw_props: Any = None
        raw_markets: dict[str, Any] = {}
        all_game_lines: list[NflGameLine] = []
        all_player_props: list[NflPlayerProp] = []
        errors: list[str] = []
        # Required-stage receipts; any one not OK/EMPTY blocks publication (F03).
        receipts: list[StageReceipt] = []
        drop_counts: Counter[str] = Counter()

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
            raw_props, raw_markets = player_props_raw, {"fixture": event_markets_raw}

            sched_errs = validate_schedule_payload(schedule_raw)
            if sched_errs:
                errors.extend(sched_errs)
                logger.error("Schedule schema validation failed: %s", sched_errs)
            receipts.append(validation_receipt("schedule", sched_errs))

            team_index = build_team_index(schedule_raw)
            schedule_index = build_schedule_index(schedule_raw)

            # An invalid schedule yields an explicit FAILED run, not a crash (F03).
            events = _schedule_events(schedule_raw)
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
                lines = normalize_game_markets(event, event_markets_raw, team_index, drop_counts)
                all_game_lines.extend(lines)
            mkt_errs = validate_event_markets_payload(event_markets_raw) if slate_events else []
            receipts.append(
                validation_receipt("event_markets", mkt_errs, count=len(slate_events),
                                   empty=not slate_events)
            )
            prop_payload_errs = validate_player_props_payload(player_props_raw)
            receipts.append(validation_receipt("player_props", prop_payload_errs))

            props = normalize_player_props(player_props_raw, schedule_index, drop_counts)
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
                logger.error("Schedule payload validation failed: %s", sched_errs)
            receipts.append(validation_receipt("schedule", sched_errs))

            events = _schedule_events(schedule_raw)
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
            market_failures: list[str] = []
            market_invalid: list[str] = []
            market_requests = 0
            for event in slate_events:
                event_id = str(event.get("eventId") or event.get("id") or "")
                if not event_id:
                    market_invalid.append("slate event without an event id")
                    continue

                event_markets: list[dict[str, Any]] = []
                for m_type in (MARKET_TYPE_GAMELINE, MARKET_TYPE_TEAM_PROP):
                    market_requests += 1
                    try:
                        mkt_payload = client.fetch_event_markets(event_id, market_type=m_type)
                    except Exception as exc:
                        logger.error(
                            "Failed fetching %s markets for event %s: %s", m_type, event_id, exc
                        )
                        market_failures.append(f"{event_id} {m_type}: {exc}")
                        continue
                    val_errs = validate_event_markets_payload(mkt_payload)
                    if val_errs:
                        logger.error(
                            "Market payload errors for %s (%s): %s", event_id, m_type, val_errs
                        )
                        market_invalid.extend(f"{event_id} {m_type}: {e}" for e in val_errs)
                    if isinstance(mkt_payload, dict) and isinstance(
                        mkt_payload.get("markets"), list
                    ):
                        event_markets.extend(mkt_payload["markets"])

                raw_markets[event_id] = event_markets
                lines = normalize_game_markets(event, {"markets": event_markets}, team_index, drop_counts)
                all_game_lines.extend(lines)

            if not slate_events:
                receipts.append(receipt("event_markets", "EMPTY", expected=0, received=0))
            elif market_invalid:
                receipts.append(receipt(
                    "event_markets", "INVALID", reason=f"{len(market_invalid)} validation error(s)",
                    expected=market_requests, received=market_requests - len(market_failures),
                    errors=market_invalid + market_failures,
                ))
            elif market_failures:
                every = len(market_failures) >= market_requests
                receipts.append(receipt(
                    "event_markets", "FAILED" if every else "INCOMPLETE",
                    reason=f"{len(market_failures)} of {market_requests} market requests failed",
                    expected=market_requests, received=market_requests - len(market_failures),
                    errors=market_failures,
                ))
            else:
                receipts.append(receipt("event_markets", "OK", expected=market_requests,
                                        received=market_requests))

            # Ingest bulk player props if slate has events
            if slate_events:
                try:
                    logger.info("Fetching bulk player props from Outlier API...")
                    player_props_raw = client.fetch_player_props()
                    val_errs = validate_player_props_payload(player_props_raw)
                    if val_errs:
                        logger.error("Player props payload failed validation: %s", val_errs)
                    receipts.append(validation_receipt("player_props", val_errs))
                except IncompletePaginationError as exc:
                    # Never publish the pages that did arrive as the slate (F04).
                    logger.error("Player props feed incomplete: %s", exc)
                    receipts.append(receipt(
                        "player_props", "INCOMPLETE", reason=f"{exc.reason}: {exc}",
                        expected=exc.pages_expected, received=exc.pages_fetched,
                    ))
                    player_props_raw = {"props": []}
                except Exception as exc:
                    logger.error("Failed fetching player props: %s", exc)
                    receipts.append(receipt("player_props", "FAILED", reason=str(exc)))
                    player_props_raw = {"props": []}
                raw_props = player_props_raw

                props = normalize_player_props(player_props_raw, schedule_index, drop_counts)
                slate_event_ids = {str(e.get("eventId") or e.get("id")) for e in slate_events}
                all_player_props = [p for p in props if p.event_id in slate_event_ids]
            else:
                all_player_props = []
                receipts.append(receipt("player_props", "EMPTY", received=0))

        # A slate with games but no admitted props (feed glitch, props not posted
        # yet, or none matching the slate) must not pass as a complete slate (F03).
        props_admission_check(receipts, slate_events, all_player_props)

        ctx = make_run_context(
            slate_date=target_date,
            window=window,
            as_of_utc=as_of_dt,
            fixture=offline_fixtures_dir is not None,
            slate_events=slate_events,
            run_started_utc=now_dt,
        )
        if refresh_tape:
            # After ctx, so the tape's injury admission uses this run's own mode
            # (window-aware), never a re-derivation from the whole slate day.
            _refresh_tape(self.nfl_dir, target_date, tape_last_n, ctx.as_of_utc, ctx.mode)
        sources: list[SourceRecord] = []

        # Load external advanced metrics for the season
        # Offline fixture replays never touch the network.
        external_metrics: list[dict[str, Any]] = []
        phase_records: list[dict[str, Any]] = []
        # First week whose data is not admissible at ctx.as_of_utc (None: no verified cutoff).
        before_week: int | None = None
        if offline_fixtures_dir is None:
            try:
                # January/February playoff slates belong to the previous season.
                season_year = ctx.season
                if season_year is None:
                    logger.warning(
                        "Could not derive an NFL season from target date %r; "
                        "skipping external metrics",
                        target_date,
                    )
                    sources.append(
                        SourceRecord("external", "UNAVAILABLE", "no NFL season for slate date")
                    )
                else:
                    loaded = load_external_metrics(
                        season_year,
                        slate_date=target_date,
                        as_of_utc=ctx.as_of_utc,
                        client=ExternalClient(cache_dir=self.nfl_dir / "cache" / "external"),
                    )
                    external_metrics = loaded.records
                    phase_records = loaded.phase_records
                    before_week = loaded.before_week
                    sources.extend(loaded.sources)
                    logger.info(
                        "Loaded %d external metric records (weeks < %s admitted at %s)",
                        len(external_metrics), before_week, ctx.as_of_iso,
                    )
            except Exception as exc:
                logger.warning("Failed loading external metrics: %s", exc)
                sources.append(SourceRecord("external", "UNAVAILABLE", f"load failed: {exc}"))
        else:
            sources.append(SourceRecord("external", "UNAVAILABLE", "offline fixture replay"))

        # Every artifact is staged into runs/<run_id>/ and published at the end (F27).
        # One suffix names every published file: a window run writes only
        # *_<date>_<window> names, never the slate-wide bare-date files (F11).
        suffix = f"{target_date}_{window.strip().lower()}" if window else target_date
        run_id = f"{suffix}-{now_dt:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        writer = RunWriter(self.nfl_dir, run_id)
        norm = self.normalized_dir
        # A run made after the slate (or window) kicked off, by its wall clock or its
        # as_of, or a replay with as_of in the past, cannot replace the pregame
        # originals: it is kept as an immutable bundle only (F11).
        publish = ctx.publishes
        publication_reason = ctx.publication_reason
        # Only a regular-season slate of a supported season can publish (F29).
        receipts.append(season_phase_receipt(slate_events, ctx.season, phase_records))
        season_type = slate_season_type(slate_events, phase_records)
        # A required stage that is not OK keeps the run out of every dated and
        # latest file and pointer, whatever its mode (F03).
        gate = gate_reason(receipts)
        if gate and publish:
            publish, publication_reason = False, gate
        if not publish:
            logger.warning(
                "Bundle-only run (%s); dated and latest files untouched", publication_reason
            )
        publish_latest = publish and write_latest

        def stage(stem: str, payload: Any, *, latest: bool = True) -> None:
            dests = [norm / f"{stem}_{suffix}.json"] if publish else []
            if latest and publish_latest:
                dests.append(norm / f"{stem}_latest.json")
            writer.stage_json(f"{stem}.json", payload, publish=dests)

        writer.stage_json("raw/schedule.json", schedule_raw)
        writer.stage_json("raw/event_markets.json", raw_markets)
        writer.stage_json("raw/player_props.json", raw_props)

        # =====================================================================
        # 1.5 Per-matchup tape analysis, then consensus + calibration
        # =====================================================================
        # One validated, point-in-time tape read feeds scripts, inactives and the
        # injury pillar; a refused tape disables all of them together (F02).
        tape = load_tape_envelope(self.nfl_dir, ctx)
        sources.append(tape.source)
        if not tape.admitted:
            logger.warning("Matchup tape %s: %s", tape.source.status, tape.source.reason)
        tapes = tape.teams
        # Persist external metrics to JSON for downstream use and analysis
        external_metrics_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "as_of_utc": ctx.as_of_iso,
            "before_week": before_week,
            "count": len(external_metrics),
            "records": external_metrics,
        }
        stage("nfl_external_metrics", external_metrics_payload)

        matchup_scripts = build_matchup_scripts(
            all_game_lines,
            tapes,
            injuries_by_event=injuries_by_event(
                tape.team_lists("inactive"), all_game_lines, slate_events
            ),
            slate_events=slate_events,
            defensive_out_by_team=tape.team_lists("defensive_starters_out"),
            static_fallback=ctx.mode == "fixture",
        )
        if matchup_scripts:
            logger.info(
                "Built %d matchup scripts from prior-week tape (%d teams)",
                len(matchup_scripts),
                len(tapes),
            )

        weather_records: list[dict[str, Any]] = []
        usage_players: list[dict[str, Any]] = []

        # Game-day weather: open-air forecasts add pass-volume haircut signals.
        if offline_fixtures_dir is None and matchup_scripts:
            try:
                weathers = load_slate_weather(
                    slate_events,
                    [r for r in external_metrics if r.get("source") == "schedule"],
                )
                matchup_scripts = apply_weather(matchup_scripts, weathers, tapes)
                weather_records = [w.to_dict() for w in weathers.values()]
                weather_payload = {
                    "date": target_date,
                    "window": window,
                    "updated_at": now_utc,
                    "count": len(weathers),
                    "records": weather_records,
                }
                stage("nfl_weather", weather_payload, latest=False)
                logger.info(
                    "Weather: %d games forecast, %d with a pass haircut",
                    len(weathers),
                    sum(1 for w in weathers.values() if w.pass_adjustment < 0),
                )
            except Exception as exc:
                logger.warning("Weather calibration skipped: %s", exc)

        # Player usage: vacated volume and efficiency regression re-base stale hit rates.
        if offline_fixtures_dir is None and matchup_scripts and (
            before_week is None or ctx.season is None
        ):
            # No verified cutoff: usage would otherwise read every week of the
            # season, including games played after this slate (F01).
            sources.append(SourceRecord("usage", "UNAVAILABLE", "missing verified cutoff"))
        elif offline_fixtures_dir is None and matchup_scripts and ctx.season is not None:
            try:
                profiles = load_usage(ctx.season, before_week)
                event_by_team: dict[str, str] = {}
                for script in matchup_scripts:
                    event_by_team[script.home_team] = script.event_id
                    event_by_team[script.away_team] = script.event_id
                usage = usage_signals(profiles, tape.team_lists("inactive"), event_by_team)
                matchup_scripts = append_signals(matchup_scripts, usage)
                usage_players = [
                    p.to_dict() for p in profiles.values() if p.team in event_by_team
                ]
                stage(
                    "nfl_player_usage",
                    {
                        "date": target_date,
                        "window": window,
                        "updated_at": now_utc,
                        "players": usage_players,
                        "signals": [asdict(sig) for sig in usage],
                    },
                    latest=False,
                )
                logger.info("Usage: %d profiles, %d signals", len(profiles), len(usage))
                sources.append(SourceRecord(
                    "usage", "AVAILABLE", rows_admitted=len(profiles),
                    max_week_admitted=(before_week - 1) if before_week else None))
            except Exception as exc:
                logger.warning("Usage signals skipped: %s", exc)
                sources.append(SourceRecord("usage", "UNAVAILABLE", f"load failed: {exc}"))

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
        receipts.append(validation_receipt(
            "normalized_validation", game_errs + prop_errs, count=len(games_dict) + len(props_dict)
        ))
        if failing(receipts[-1:]) and publish:
            # Withdraw what was already staged for publication (metrics, weather).
            writer.withhold_publication()
            publish, publish_latest = False, False
            publication_reason = gate_reason(receipts)
            logger.warning("Bundle-only run (%s)", publication_reason)
        # Informational (never required, always OK): what normalization dropped (F26).
        receipts.append(quote_integrity_receipt(drop_counts))

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

        stage("nfl_games", games_payload)
        stage("nfl_props", props_payload)
        stage("nfl_calibrated_props", props_payload)

        anchors = [
            p.to_dict()
            for p in calibrated_props
            if p.confidence_tier == "TIER_1_ANCHOR" and p.scope in (None, "", "full_game")
        ]
        # Persist emit-time odds as close_* with honest source label (not book close).
        for row in anchors:
            attach_close_fields(row, mode="snapshot_best", attach_model_p="empirical_hit_rate_market_prior", overwrite_model_p=True)
        # Same-player correlation guard: tag PRIMARY vs CORRELATED_SAME_PLAYER;
        # full Tier-1 dump kept in records; actionable_records is the action set.
        anchors = apply_same_player_correlation_guard(anchors)
        actionable_anchors = actionable_high_prob_records(anchors)
        anchors_payload = {
            "date": target_date,
            "window": window,
            "run_id": run_id,  # F25: the export carries the run that produced it
            "updated_at": now_utc,
            "count": len(anchors),
            "actionable_count": len(actionable_anchors),
            "records": anchors,
            "actionable_records": actionable_anchors,
            "close_enrichment": {
                "mode": "snapshot_best",
                "note": "close_* copied from line/best_odds/implied at emit; not true book close.",
            },
            "model_p_enrichment": {
                "mode": "empirical_hit_rate_market_prior",
                "kappa": DEFAULT_MARKET_PRIOR_KAPPA,
                "note": (
                    "model_p = Beta shrink of L10/L20/L5/season hit rates toward "
                    "sportsbook-only implied prior (source=empirical_hit_rate_market_prior); "
                    "PrizePicks excluded from prior/edge; never a raw copy of implied_probability. "
                    "Laplace still available via enrich_close --attach-model-p empirical_hit_rate_laplace."
                ),
            },
            "model_p_distribution": model_p_bucket_counts(anchors),
            "correlation_guard": correlation_guard_summary(anchors),
        }
        stage("nfl_high_prob_props", anchors_payload)

        # Build verified active roster index
        rosters = build_team_roster_index(
            all_player_props, include_league_baseline=ctx.mode == "fixture", tape_roles=tapes
        )
        rosters_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "teams_count": len(rosters),
            "rosters": rosters,
        }
        stage("nfl_rosters", rosters_payload)

        scripts_payload = {
            "date": target_date,
            "window": window,
            "updated_at": now_utc,
            "run_id": run_id,
            "count": len(matchup_scripts),
            "records": scripts_to_records(matchup_scripts),
        }
        stage("nfl_matchup_scripts", scripts_payload)

        matchup_prop_records = [
            p.to_dict()
            for p in calibrated_props
            if any(str(tag).startswith("MATCHUP_") for tag in p.calibration_tags)
        ]
        for row in matchup_prop_records:
            attach_close_fields(row, mode="snapshot_best", attach_model_p="empirical_hit_rate_market_prior", overwrite_model_p=True)
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
                "mode": "empirical_hit_rate_market_prior",
                "kappa": DEFAULT_MARKET_PRIOR_KAPPA,
                "note": (
                    "model_p = Beta shrink of L10/L20/L5/season hit rates toward "
                    "sportsbook-only implied prior (source=empirical_hit_rate_market_prior); "
                    "PrizePicks excluded from prior/edge; never a raw copy of implied_probability. "
                    "Laplace still available via enrich_close --attach-model-p empirical_hit_rate_laplace."
                ),
            },
        }
        stage("nfl_matchup_props", matchup_props_payload)

        best_bets = self._trace_best_bets(
            target_date=target_date,
            window=window,
            now_utc=now_utc,
            as_of_utc=ctx.as_of_utc,
            before_week=before_week,
            props_dict=props_dict,
            scripts_records=scripts_to_records(matchup_scripts),
            external_metrics=external_metrics,
            usage_players=usage_players,
            weather_records=weather_records,
            tape=tape,
            suffix=suffix,
            publish=publish,
            publish_latest=publish_latest,
            writer=writer,
        )

        # Compute counts and breakdown
        spreads_count = sum(1 for g in all_game_lines if g.market == "SPREAD")
        totals_count = sum(1 for g in all_game_lines if g.market == "TOTAL")
        team_totals_count = sum(1 for g in all_game_lines if g.market_type == "TEAM_PROP")
        consensus_props_count = sum(1 for p in calibrated_props if p.is_consensus_line)
        tier_1_anchors_count = len(anchors)
        tier_1_actionable_count = len(actionable_anchors)

        prop_breakdown: dict[str, int] = {}
        for p in all_player_props:
            prop_breakdown[p.market] = prop_breakdown.get(p.market, 0) + 1

        starting_qbs = {t: r["starting_qb"] for t, r in rosters.items() if r.get("starting_qb")}

        # =====================================================================
        # Sportsbook Alternate Floor Props (e.g. Hard Rock Bet)
        # =====================================================================
        alt_floors_summary: dict[str, Any] = {}
        try:
            from outlier_nfl.alt_floors import generate_alt_floors_pipeline

            starting_qbs_set = {
                r["starting_qb"] for r in rosters.values()
                if r.get("starting_qb") and r.get("starting_qb_status") in EVIDENCED_STARTER_STATUSES
            }
            alt_floors_summary = generate_alt_floors_pipeline(
                props=all_player_props,
                exports_dir=writer.stage_dir / "exports",
                reports_dir=writer.stage_dir / "reports",
                date_str=suffix,
                target_book=target_alt_book,
                starting_qbs=starting_qbs_set,
                weather_records=weather_records,
                tapes=tapes,
                write_latest=publish_latest,
                static_depth=ctx.mode == "fixture",
                # F23: executable floors need a pre-kickoff quote and an admitted
                # injury report that does not list the player.
                as_of_utc=ctx.as_of_utc,
                inactive_by_team=tape.injury_report(),
                require_injury_evidence=True,
            )
            if publish:
                writer.stage_tree("exports", self.data_dir / "NFL" / "exports")
            logger.info(
                "Alt floor props generated: %d ranked props for %s",
                alt_floors_summary.get("count", 0),
                target_alt_book,
            )
        except Exception as exc:
            logger.warning("Failed generating alt floor props: %s", exc)

        status = run_status(receipts)
        summary: dict[str, Any] = {
            "status": status,
            "date": target_date,
            "window": window,
            "timestamp_utc": now_utc,
            "run_id": run_id,
            "run_dir": str(writer.run_dir),
            "publication": "published" if publish else "bundle_only",
            "publication_reason": publication_reason,
            "as_of_utc": ctx.as_of_iso,
            "run_mode": ctx.mode,
            "replay": ctx.replay,
            "before_week": before_week,
            "sources": [src.to_dict() for src in sources],
            "stage_receipts": [r.to_dict() for r in receipts],
            "season_type": season_type,
            "events_count": len(slate_events),
            "game_lines_count": len(all_game_lines),
            "spreads_count": spreads_count,
            "totals_count": totals_count,
            "team_totals_count": team_totals_count,
            "player_props_count": len(all_player_props),
            "props_count": len(all_player_props),
            "consensus_props_count": consensus_props_count,
            "tier_1_anchors_count": tier_1_anchors_count,
            "tier_1_actionable_count": tier_1_actionable_count,
            "player_props_breakdown": prop_breakdown,
            "starting_qbs": starting_qbs,
            "matchup_scripts_count": len(matchup_scripts),
            "matchup_tagged_props_count": len(matchup_prop_records),
            "best_bets_counts": best_bets.get("counts", {}),
            "best_bets_error": best_bets.get("error"),
            "alt_floors": alt_floors_summary,
            "alt_floors_count": alt_floors_summary.get("count", 0),
            "errors": errors + ([best_bets["error"]] if best_bets.get("error") else []),
        }

        report_root = Path(reports_dir) if reports_dir is not None else Path("reports/NFL")
        # Optional Game Script Generation — one markdown file per matchup
        if generate_game_script and all_game_lines:
            try:
                from scripts.nfl_game_script import NflGameScriptGenerator

                generator = NflGameScriptGenerator(data_dir=self.normalized_dir)
                written: list[str] = []
                for script in matchup_scripts:
                    slug = f"{script.away_team}_{script.home_team}".replace(" ", "_")
                    report_file = writer.staged_path(f"reports/{suffix}_{slug}_Game_Script.md")
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

        # Reports (alt floors, game scripts) publish under the reports root; summary
        # paths name the published copies, not the staging directory.
        if publish:
            writer.stage_tree("reports", report_root)
        outputs = alt_floors_summary.get("outputs")
        if isinstance(outputs, dict):
            alt_floors_summary["outputs"] = {k: writer.resolve(v) for k, v in outputs.items()}
        if "game_script_files" in summary:
            summary["game_script_files"] = [writer.resolve(f) for f in summary["game_script_files"]]
            summary["game_script_file"] = summary["game_script_files"][0]
        stage("summary", summary)
        pointers = [norm / f"nfl_run_pointer_{suffix}.json"] if publish else []
        if publish_latest:
            pointers.append(norm / "nfl_run_pointer_latest.json")
        writer.commit(
            {
                "created_utc": now_utc,
                "context": ctx.to_dict(),
                "status": status,
                "stage_receipts": summary["stage_receipts"],
                "sources": summary["sources"],
                "publication": summary["publication"],
                "publication_reason": publication_reason,
            },
            pointers=pointers,
        )

        if status != "OK":
            logger.error(
                "NFL Pipeline run %s (%s): nothing published; bundle kept at %s",
                status, gate_reason(receipts), writer.run_dir,
            )
        logger.info(
            "NFL Pipeline run completed: %d games, %d spreads, %d totals, %d team totals, %d props (%d consensus, %d Tier-1 anchors)",
            len(all_game_lines),
            spreads_count,
            totals_count,
            team_totals_count,
            len(all_player_props),
            consensus_props_count,
            tier_1_anchors_count,
        )
        return summary

    def _trace_best_bets(
        self,
        *,
        target_date: str,
        window: str | None,
        now_utc: str,
        as_of_utc: datetime,
        before_week: int | None,
        props_dict: list[dict[str, Any]],
        scripts_records: list[dict[str, Any]],
        external_metrics: list[dict[str, Any]],
        usage_players: list[dict[str, Any]],
        weather_records: list[dict[str, Any]],
        tape: TapeEnvelope,
        suffix: str,
        publish: bool,
        publish_latest: bool,
        writer: RunWriter,
    ) -> dict[str, Any]:
        """Snapshot this run's prices, then trace every candidate through all six pillars."""
        try:
            append_snapshot(self.nfl_dir, target_date, props_dict, now_utc, run_id=writer.run_id)
            # Only prices captured by the prediction time (a replay must not see later runs).
            movement = movement_index(
                load_snapshots(snapshot_path(self.nfl_dir, target_date), as_of_utc=as_of_utc)
            )
            payload = build_best_bets(
                TraceInputs(
                    props=props_dict,
                    run_date=to_eastern_date(now_utc) or target_date,
                    scripts=scripts_records,
                    external_metrics=external_metrics,
                    usage_players=usage_players,
                    weather=weather_records,
                    inactive_by_team=tape.injury_report(),
                    tapes=tape.teams,
                    movement=movement,
                    as_of_utc=as_of_utc.isoformat(),
                    before_week=before_week,
                )
            )
        except Exception as exc:  # the slate's data outputs above are already written
            logger.error("Best-bets trace failed: %s", exc)
            # Never leave an older card behind for a reader to mistake for this run's.
            # A bundle-only run publishes nothing, so it leaves published cards alone.
            stale_cards = (
                [f"nfl_best_bets_{suffix}.json", f"nfl_best_bets_{suffix}.md"] if publish else []
            )
            # This run would have overwritten _latest, so a surviving one is the
            # previous run's card under the name readers treat as current.
            if publish_latest:
                stale_cards.append("nfl_best_bets_latest.json")
            for stale in stale_cards:
                (self.normalized_dir / stale).unlink(missing_ok=True)
            return {"error": f"best-bets trace failed: {exc}"}
        payload.update({"date": target_date, "window": window, "updated_at": now_utc})
        card_dests = [self.normalized_dir / f"nfl_best_bets_{suffix}.json"] if publish else []
        if publish_latest:
            card_dests.append(self.normalized_dir / "nfl_best_bets_latest.json")
        writer.stage_json("nfl_best_bets.json", payload, publish=card_dests)
        writer.stage_text(
            "nfl_best_bets.md",
            render_best_bets_markdown(payload, title=f"NFL Traced Best Bets - slate {suffix}"),
            publish=[self.normalized_dir / f"nfl_best_bets_{suffix}.md"] if publish else [],
        )
        logger.info("Best bets: %s", payload.get("counts"))
        return payload


def load_injury_report(nfl_dir: Path | str) -> dict[str, list[str]] | None:
    """Injury-report inactives, or None unless the tape confirms the report was fetched.

    Distinguishes "report loaded, nobody out" ({}) from "no report / fetch failed"
    (None) so the best-bets trace never treats a missing report as a clean one.
    Tapes written before the ``injury_report_loaded`` marker existed read as None.
    Unvalidated; pipeline runs use ``TapeEnvelope.injury_report`` from the
    admitted tape instead.
    """
    raw = safe_read_json(Path(nfl_dir) / "tape" / "prior_week.json", default=None)
    if not isinstance(raw, dict) or raw.get("injury_report_loaded") is not True:
        return None
    if not isinstance(raw.get("inactive"), dict):
        return None
    return load_tape_inactives(nfl_dir)


def injuries_by_event(
    inactive_by_team: dict[str, list[str]],
    game_lines: list[NflGameLine],
    slate_events: list[dict[str, Any]] | None,
) -> dict[str, list[str]]:
    """Inactive player names for both teams of every slate event."""
    if not inactive_by_team:
        return {}
    teams: dict[str, tuple[str, str]] = {}
    for event in slate_events or []:
        event_id = str(event.get("eventId") or event.get("id") or "")
        if event_id:
            teams[event_id] = _event_team_codes(event)
    for line in game_lines:
        if line.event_id and line.event_id not in teams:
            teams[line.event_id] = (line.home_team, line.away_team)
    return {
        event_id: inactive_by_team.get(home, []) + inactive_by_team.get(away, [])
        for event_id, (home, away) in teams.items()
    }


def _refresh_tape(
    nfl_dir: Path,
    target_date: str | None,
    last_n: int | None,
    as_of_utc: datetime | str | None = None,
    run_mode: str | None = None,
) -> None:
    """Rebuild the matchup tape from games before the slate; keep the old tape on failure.

    The tape is built as of ``as_of_utc`` (default now) and records it, so a run
    can verify the tape was knowable at its own cutoff. ``run_mode`` is the
    calling run's ``RunContext.mode``; only ``live`` admits an unstamped injury
    report, so a call without it fails closed.
    """
    raw = target_date or to_eastern_date(datetime.now(timezone.utc))
    try:
        slate = date.fromisoformat(str(raw))
        season = slate.year if slate.month >= 3 else slate.year - 1
        as_of = parse_utc(as_of_utc) if as_of_utc is not None else datetime.now(timezone.utc)
        refresh_prior_week_tape(
            nfl_dir, season, before=slate, last_n=last_n, as_of_utc=as_of, run_mode=run_mode
        )
    except Exception as exc:  # network/API failure must not block the slate run
        logger.warning("Tape refresh failed, keeping existing tape: %s", exc)


# Exit code when a required stage receipt is not OK (F03); 1 stays "crashed".
EXIT_REQUIRED_STAGE_FAILED = 3


def format_stage_failure(summary: dict[str, Any]) -> str:
    """Operator message for a PARTIAL/FAILED run: what failed and how to recover."""
    lines = [
        f"NFL run {summary.get('status')} for {summary.get('date')}"
        f"{' window ' + str(summary['window']) if summary.get('window') else ''}: "
        "nothing was published; the latest files and run pointers still name the last good run.",
    ]
    for r in summary.get("stage_receipts") or []:
        if r.get("required") and r.get("status") not in ("OK", "EMPTY"):
            lines.append(f"  {r.get('name')}: {r.get('status')} - {r.get('reason') or ''}".rstrip())
            lines.extend(f"      {e}" for e in (r.get("errors") or [])[:3])
    lines.append(f"Bundle for inspection: {summary.get('run_dir')}")
    lines.append(
        "Recover: INCOMPLETE/FAILED fetches are usually transient (Outlier 429/5xx, session) - "
        "rerun the same command. INVALID means the feed shape changed - keep the bundle and "
        "report it. UNSUPPORTED (postseason, pre-2025) has no override until those adapters exist."
    )
    return "\n".join(lines)


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
        "--refresh-tape",
        action="store_true",
        help="Rebuild data/NFL/tape/prior_week.json from nflverse box scores before running.",
    )
    parser.add_argument(
        "--tape-last-n",
        type=int,
        default=None,
        help="With --refresh-tape, average only each team's last N games. Default: all.",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Prediction-time cutoff, timezone-aware ISO (e.g. 2026-10-04T16:00:00Z). "
        "Default: now. Sources are limited to what was knowable then. An --as-of "
        "more than 15 minutes before now is a recorded replay and, like any run "
        "started after the slate's first kickoff, writes only its runs/<run_id>/ bundle.",
    )
    parser.add_argument(
        "--target-alt-book",
        type=str,
        default="HARDROCK",
        help="Target sportsbook for alternate floor props (default: HARDROCK).",
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
        try:
            summary = pipeline.run(
                date=args.date,
                window=args.window,
                offline_fixtures_dir=args.fixtures_dir if args.mode == "fixture" else None,
                generate_game_script=args.generate_game_script,
                target_alt_book=args.target_alt_book,
                as_of_utc=args.as_of,
                refresh_tape=args.refresh_tape,
                tape_last_n=args.tape_last_n,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
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
        print(f"Tier-1 Actionable:      {summary.get('tier_1_actionable_count')}")
        print(f"Matchup Scripts:        {summary.get('matchup_scripts_count')}")
        print(f"Matchup-Tagged Props:   {summary.get('matchup_tagged_props_count')}")
        if summary.get("game_script_file"):
            print(f"Game Script Generated:  {summary.get('game_script_file')}")
        if summary.get("starting_qbs"):
            print("-" * 60)
            print("VERIFIED ACTIVE STARTING QUARTERBACKS (FROM FEED):")
            for t, qb in sorted(summary["starting_qbs"].items()):
                print(f"  {t:4s}: {qb}")
        alt_data = summary.get("alt_floors") or {}
        if alt_data.get("count"):
            print("-" * 60)
            print(f"SPORTSBOOK ALTERNATE FLOOR PROPS ({args.target_alt_book.upper()}):")
            top3_cat = alt_data.get("top3_by_category") or {}
            for mkt, title in [
                ("PASS_YDS", "PASSING YARDS"),
                ("RUSH_YDS", "RUSHING YARDS"),
                ("REC_YDS", "RECEIVING YARDS"),
            ]:
                mkt_props = top3_cat.get(mkt, [])
                if mkt_props:
                    print(f"  {title} (TOP {len(mkt_props)}):")
                    for p in mkt_props:
                        odds_s = (
                            f"{p.get('target_odds'):+d}"
                            if isinstance(p.get("target_odds"), int)
                            else f"{p.get('target_odds')}"
                        )
                        print(
                            f"    #{p.get('category_rank')} {p.get('player_name'):<20} ({p.get('team')}): "
                            f"OVER {p.get('line'):<5} | Odds: {odds_s:<6} | Cons: {p.get('consensus_line'):<5} "
                            f"(Cush: +{p.get('cushion')} yds) | Score: {p.get('confidence_score'):.3f}"
                        )
            master_list = alt_data.get("master_pool") or []
            if master_list:
                top1 = master_list[0]
                print(
                    f"  TOP OVERALL CONFIDENCE PLAY: #{top1.get('master_rank')} {top1.get('player_name')} "
                    f"({top1.get('team')}) - OVER {top1.get('line')} {top1.get('market_display')} "
                    f"(Score: {top1.get('confidence_score'):.3f})"
                )
        print("=" * 60)
        if summary.get("status") != "OK":
            print(format_stage_failure(summary), file=sys.stderr)
            return EXIT_REQUIRED_STAGE_FAILED
        return 0
    except Exception as exc:
        logger.exception("Pipeline execution failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
