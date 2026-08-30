"""AI Research Desk pack writer.

Transforms the pipeline's player + game triage cards into a daily betting pack
(candidates.csv, briefing.md, dossiers/) for the AI research desk.

Includes Tier 1 fixes: display dedup selection, player_id + push_prob, strict date,
full round-robin+global-fill quota, candidate-scoped freshness, decisions.csv scaffold.

Compatibility import surface for the decomposed ``pack_*`` modules
(``pack_market``, ``pack_projections``, ``pack_sizing``, ``pack_ranking``,
``pack_context``, ``pack_render``, ``pack_publish``, ``pack_selection``).
Owns the top-level orchestration entry points: ``process_stream``,
``select_date``, ``_expected_pack_slate_date``, ``build_pack_with_coverage``,
``build_pack``, ``main``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from outlier_scrapers import feed_health, paths, probability_blend, slate_quality
from outlier_scrapers.game_totals import is_full_game_total
from outlier_scrapers.registry import (
    classify_foreign_market,
    get_sport_config,
    team_display_name,
)
from outlier_scrapers.sizing import compute_historical_edge, compute_sizing
from outlier_scrapers.schema import ValidationError, validate_candidate_row
from outlier_scrapers.utils import _local_date, _parse_start, drop_locked_events, _write_csv
from outlier_scrapers.team_totals import is_team_total_proposition

# --- pack_market: market/odds resolution and formatting -------------------
from outlier_scrapers.pack_market import (
    NO_PUSH_MARKETS,
    EXCLUDED_MARKETS,
    LONGSHOT_AMERICAN_PRICE,
    SIGNED_MARGIN_PROPOSITIONS,
    PLAYER_PROP_LINE_CEILING,
    _GAMELINE_SIDE_PROPOSITIONS,
    _SLATE_DATE_RE,
    american_to_decimal,
    _selected_ev_devig_decimal,
    load_json,
    index_ev_by_outcome,
    match_ev_records,
    is_excluded_market,
    is_longshot_price,
    is_no_push_market,
    get_research_leverage,
    format_source_timestamps,
    _slug,
    _coalesce,
    _fmt_line,
    _fmt_signed_line,
    _to_float,
    _canonical_pack_date,
    _matchup_sides,
    _home_away,
    _opponent_from_matchup,
    _align_gameline_team,
    _selection_side,
    _priced_line_from_ev,
    market_validation_flags,
    build_selection,
)

# --- pack_projections: independent projections + probability blend --------
from outlier_scrapers.pack_projections import (
    _validate_projection_artifact_date,
    index_projections,
    apply_shadow_projection,
    _projection_side_conflicts,
    apply_learned_probability_blend,
    load_projection_records,
)

# --- pack_sizing: stake sizing -------------------------------------------
from outlier_scrapers.pack_sizing import (
    _apply_blended_sizing,
    _apply_enforced_portfolio_units,
    _load_learned_stake_runtime,
    _apply_learned_stake_before_caps,
)

# --- pack_ranking: board ranking / quota fill ------------------------------
from outlier_scrapers.pack_ranking import rank_rows, _round_robin_then_fill

# --- pack_context: contextual enrichment -----------------------------------
from outlier_scrapers.pack_context import (
    FEED_STATUS_FIELDS,
    _INJURY_ANALYSIS_MAX_CHARS,
    build_event_starts,
    build_injuries,
    _injury_return_date,
    _format_injury,
    build_freshness_section,
    build_candidate_coverage_section,
    build_feed_health_by_league,
)

# --- pack_render: markdown rendering ---------------------------------------
from outlier_scrapers.pack_render import (
    MLB_QUESTIONS,
    WNBA_QUESTIONS,
    ROLE_BLOCK,
    _TOTALS_NAME_RE,
    _matchup_display,
    _totals_event_display,
    build_dossier,
    _format_game_totals_md,
    build_briefing,
)

# --- pack_publish: filesystem/transaction concerns -------------------------
from outlier_scrapers.pack_publish import (
    VERDICTS_SUBTREE,
    DERIVED_PACK_OUTPUTS,
    clear_derived_pack_outputs,
    load_props_norm_by_league,
    _retry_replace,
    _retry_rmtree,
    _swap_staged_pack,
    _restore_published_pack,
    write_pack,
)

# --- pack_selection: candidate row selection policy -------------------------
from outlier_scrapers.pack_selection import (
    CANDIDATES_HEADER,
    DISQUALIFYING_DQ_FLAGS,
    CROSS_SPORT_DQ_PREFIX,
    _opportunity_key,
    _is_original_recommendation,
    _freeze_t30_originals,
    _total_reconciliation_key,
    _reconcile_candidates_with_totals_board,
    _blank_neutral_component,
    build_row,
)

logger = logging.getLogger(__name__)

# Listed explicitly (not `import *`) so underscore-prefixed names re-export too.
__all__ = [
    "os",
    "shutil",
    "time",
    "uuid",
    "feed_health",
    "paths",
    "probability_blend",
    "slate_quality",
    "is_full_game_total",
    "classify_foreign_market",
    "get_sport_config",
    "team_display_name",
    "compute_historical_edge",
    "compute_sizing",
    "ValidationError",
    "validate_candidate_row",
    "_local_date",
    "_parse_start",
    "drop_locked_events",
    "_write_csv",
    "is_team_total_proposition",
    "NO_PUSH_MARKETS",
    "EXCLUDED_MARKETS",
    "LONGSHOT_AMERICAN_PRICE",
    "SIGNED_MARGIN_PROPOSITIONS",
    "PLAYER_PROP_LINE_CEILING",
    "_GAMELINE_SIDE_PROPOSITIONS",
    "_SLATE_DATE_RE",
    "american_to_decimal",
    "_selected_ev_devig_decimal",
    "load_json",
    "index_ev_by_outcome",
    "match_ev_records",
    "is_excluded_market",
    "is_longshot_price",
    "is_no_push_market",
    "get_research_leverage",
    "format_source_timestamps",
    "_slug",
    "_coalesce",
    "_fmt_line",
    "_fmt_signed_line",
    "_to_float",
    "_canonical_pack_date",
    "_matchup_sides",
    "_home_away",
    "_opponent_from_matchup",
    "_align_gameline_team",
    "_selection_side",
    "_priced_line_from_ev",
    "market_validation_flags",
    "build_selection",
    "_validate_projection_artifact_date",
    "index_projections",
    "apply_shadow_projection",
    "_projection_side_conflicts",
    "apply_learned_probability_blend",
    "load_projection_records",
    "_apply_blended_sizing",
    "_apply_enforced_portfolio_units",
    "_load_learned_stake_runtime",
    "_apply_learned_stake_before_caps",
    "rank_rows",
    "_round_robin_then_fill",
    "FEED_STATUS_FIELDS",
    "_INJURY_ANALYSIS_MAX_CHARS",
    "build_event_starts",
    "build_injuries",
    "_injury_return_date",
    "_format_injury",
    "build_freshness_section",
    "build_candidate_coverage_section",
    "build_feed_health_by_league",
    "MLB_QUESTIONS",
    "WNBA_QUESTIONS",
    "ROLE_BLOCK",
    "_TOTALS_NAME_RE",
    "_matchup_display",
    "_totals_event_display",
    "build_dossier",
    "_format_game_totals_md",
    "build_briefing",
    "VERDICTS_SUBTREE",
    "DERIVED_PACK_OUTPUTS",
    "clear_derived_pack_outputs",
    "load_props_norm_by_league",
    "_retry_replace",
    "_retry_rmtree",
    "_swap_staged_pack",
    "_restore_published_pack",
    "write_pack",
    "CANDIDATES_HEADER",
    "DISQUALIFYING_DQ_FLAGS",
    "CROSS_SPORT_DQ_PREFIX",
    "_opportunity_key",
    "_is_original_recommendation",
    "_freeze_t30_originals",
    "_total_reconciliation_key",
    "_reconcile_candidates_with_totals_board",
    "_blank_neutral_component",
    "build_row",
    "process_stream",
    "select_date",
    "_expected_pack_slate_date",
    "build_pack_with_coverage",
    "build_pack",
    "main",
]


def process_stream(
    cards_payload: dict | None,
    lm_payload: dict | None,
    norm_payload: dict | None,
    enrichment_payload: dict | None,
    sport: str,
    stream: str,
    event_starts: dict[str, str],
    injuries: dict[str, str],
    projections_payload: dict[str, Any] | None = None,
    blend_artifact: dict[str, Any] | None = None,
    health_payload: dict[str, Any] | None = None,
    probable_pitchers: dict[str, dict[str, Any]] | None = None,
    expected_projection_date: str | None = None,
) -> list[dict[str, Any]]:
    if not cards_payload:
        return []
    cards_key = "cards" if stream == "props" else "games_cards"
    lm_key = "line_movement" if stream == "props" else "games_line_movement"
    norm_key = "props" if stream == "props" else "games"
    source_ts: dict[str, str | None] = {cards_key: cards_payload.get("generated_at")}
    if lm_payload:
        source_ts[lm_key] = lm_payload.get("generated_at")
    if norm_payload:
        source_ts[norm_key] = norm_payload.get("generated_at")
    if enrichment_payload:
        source_ts["games_enrichment"] = enrichment_payload.get("generated_at")
    if projections_payload:
        source_ts["projections"] = projections_payload.get("generated_at")
    odds_ts = lm_payload.get("generated_at") if lm_payload else None
    norm_ts = norm_payload.get("generated_at") if norm_payload else None
    ev_records = lm_payload.get("ev_records", []) if lm_payload else []
    by_outcome = index_ev_by_outcome(ev_records)
    projections_by_outcome = index_projections(projections_payload, expected_projection_date)
    rows: list[dict[str, Any]] = []
    for card in (cards_payload.get("board_a") or []) + (cards_payload.get("board_b") or []):
        row = build_row(
            card,
            ev_records,
            by_outcome,
            sport,
            odds_ts,
            norm_ts,
            source_ts,
            event_starts,
            injuries,
            projections_by_outcome,
            blend_artifact,
            stream,
            health_payload,
            probable_pitchers,
        )
        if row is not None:
            row["_stream"] = stream
            rows.append(row)
    return rows


def select_date(
    rows: list[dict[str, Any]], requested: str | None
) -> tuple[list[dict[str, Any]], str]:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    dated: set[str] = {d for r in rows if (d := _local_date(r.get("_event_starts_at"))) is not None}
    if not dated:
        return rows, (requested or today)
    if requested and requested in dated:
        target = requested
    else:
        target = today if today in dated else max(dated)
    kept = [r for r in rows if _local_date(r.get("_event_starts_at")) in (target, None)]
    if requested and requested not in dated:
        logger.warning(
            "Requested date %s has no events; emitting empty pack for that date.", requested
        )
    return kept, target


def _expected_pack_slate_date(leagues: Sequence[str], requested: str | None) -> str:
    """Select the pack date from feed event timestamps before projection joins."""

    proxy_rows: list[dict[str, Any]] = []
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        league_root = paths.league_paths(league)
        normalized = league_root.normalized
        cards_dir = league_root.root / "cards"
        low = league.lower()
        props_payload = load_json(normalized / f"{low}_props_latest.json")
        games_payload = load_json(normalized / f"{low}_games_latest.json")
        event_starts = build_event_starts(props_payload, games_payload)
        for stream in ("props", "games"):
            cards_name = f"{low}_{'games_' if stream == 'games' else ''}cards_latest.json"
            movement_name = (
                f"{low}_{'games_' if stream == 'games' else ''}line_movement_latest.json"
            )
            cards_payload = load_json(cards_dir / cards_name)
            movement_payload = load_json(normalized / movement_name)
            ev_records = (movement_payload or {}).get("ev_records") or []
            by_outcome = index_ev_by_outcome(ev_records)
            for card in (cards_payload or {}).get("board_a", []) + (
                (cards_payload or {}).get("board_b", [])
            ):
                headline_side = card.get("headline_side")
                side_view = (card.get("sides") or {}).get(headline_side) or {}
                if not headline_side or not side_view:
                    continue
                matched = match_ev_records(
                    card.get("card_id") or card.get("market_id"),
                    side_view.get("outcome_id"),
                    headline_side,
                    side_view.get("line"),
                    ev_records,
                    by_outcome,
                )
                ref = matched[0] if matched else {}
                event_id = card.get("event_id") or ref.get("event_id")
                starts_at = event_starts.get(str(event_id)) if event_id else None
                if starts_at:
                    proxy_rows.append({"_event_starts_at": starts_at})
    _, selected = select_date(proxy_rows, requested)
    return selected


def build_pack_with_coverage(
    leagues: Sequence[str],
    requested_date: str | None,
    top_ev_n: int,
    top_signal_n: int,
    *,
    opportunity_rows_out: list[dict[str, Any]] | None = None,
    blend_artifact: dict[str, Any] | None = None,
    feed_health_by_league: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], dict[str, dict[str, int]]]:
    all_rows: list[dict[str, Any]] = []
    games_norm_by_league: dict[str, Any] = {}
    coverage: dict[str, dict[str, int]] = {}
    expected_slate_date = _expected_pack_slate_date(leagues, requested_date)
    for raw_league in leagues:
        lg = raw_league.strip().upper()
        if not lg:
            continue
        health_payload = (feed_health_by_league or {}).get(lg)
        if health_payload is None:
            health_payload = feed_health.build_feed_health(lg, write=True)
        safe, reasons = feed_health.validate_feed_health(health_payload)
        if not safe:
            raise RuntimeError(f"{lg} feed health unsafe: {'; '.join(reasons)}")
        lp = paths.league_paths(lg)
        cards_dir = lp.root / "cards"
        norm = lp.normalized
        low = lg.lower()
        games_norm = load_json(norm / f"{low}_games_latest.json")
        games_norm_by_league[lg] = games_norm
        props_norm = load_json(norm / f"{low}_props_latest.json")
        projections_payload = load_json(norm / f"{low}_projections_latest.json")
        event_starts = build_event_starts(props_norm, games_norm)
        injuries = build_injuries(games_norm)
        from outlier_scrapers.probable_pitchers import load_probable_pitcher_lookup

        probable_pitchers = load_probable_pitcher_lookup(lg)
        props_cards = load_json(cards_dir / f"{low}_cards_latest.json")
        games_cards = load_json(cards_dir / f"{low}_games_cards_latest.json")
        props_rows = process_stream(
            props_cards,
            load_json(norm / f"{low}_line_movement_latest.json"),
            props_norm,
            None,
            lg,
            "props",
            event_starts,
            injuries,
            projections_payload,
            blend_artifact,
            health_payload,
            probable_pitchers,
            expected_slate_date,
        )
        games_rows = process_stream(
            games_cards,
            load_json(norm / f"{low}_games_line_movement_latest.json"),
            games_norm,
            load_json(norm / f"{low}_games_enrichment_latest.json"),
            lg,
            "games",
            event_starts,
            injuries,
            projections_payload,
            blend_artifact,
            health_payload,
            probable_pitchers,
            expected_slate_date,
        )
        if not games_cards:
            logger.warning("%s: no game-cards stream found", lg)
        coverage[lg] = {
            "cards": sum(
                len((payload or {}).get(key) or [])
                for payload in (props_cards, games_cards)
                for key in ("board_a", "board_b")
            ),
            "rows_built": len(props_rows) + len(games_rows),
            "date_filtered": 0,
            "started_dropped": 0,
            "unverified_start_dropped": 0,
            "emitted": 0,
        }
        all_rows.extend(props_rows)
        all_rows.extend(games_rows)
    kept, target_date = select_date(all_rows, requested_date)
    if target_date != expected_slate_date:
        raise ValidationError(
            "Pack slate selection changed after projection validation: "
            f"expected {expected_slate_date}, selected {target_date}."
        )
    for lg, stats in coverage.items():
        before = sum(1 for row in all_rows if row.get("sport") == lg)
        after = sum(1 for row in kept if row.get("sport") == lg)
        stats["date_filtered"] = before - after
    kept, locked = drop_locked_events(kept)
    for row in locked:
        league_stats = coverage.get(str(row.get("sport") or ""))
        if league_stats is None:
            continue
        if _parse_start(row.get("_event_starts_at")) is None:
            league_stats["unverified_start_dropped"] += 1
        else:
            league_stats["started_dropped"] += 1
    if locked:
        locked_ids = sorted({str(r.get("market_id")) for r in locked})
        sample = locked_ids[:10]
        logger.warning(
            "Dropped %d non-pregame candidate(s) — event started or start time is invalid; "
            "market sample=%s%s",
            len(locked),
            sample,
            " ..." if len(locked_ids) > len(sample) else "",
        )
    if opportunity_rows_out is not None:
        opportunity_rows_out.extend(dict(row) for row in kept)
    final_rows = rank_rows(kept, top_ev_n, top_signal_n)
    for lg, stats in coverage.items():
        stats["emitted"] = sum(1 for row in final_rows if row.get("sport") == lg)
    return final_rows, target_date, games_norm_by_league, coverage


def build_pack(
    leagues: Sequence[str], requested_date: str | None, top_ev_n: int, top_signal_n: int
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Backward-compatible pack builder; coverage-aware callers use the companion helper."""
    rows, target_date, games_norm, _coverage = build_pack_with_coverage(
        leagues, requested_date, top_ev_n, top_signal_n
    )
    return rows, target_date, games_norm


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description="Build the daily AI research-desk pack.")
    parser.add_argument("--leagues", default="MLB,WNBA")
    parser.add_argument("--date")
    # Portfolio caps are enforced (config/portfolio_risk.json mode=enforce), so
    # the risk allocator — not a pre-risk truncation — prunes the board. A tight
    # top-ev-n cut candidates before any cap ever saw them.
    parser.add_argument("--top-ev-n", type=int, default=40)
    parser.add_argument("--top-signal-n", type=int, default=10)
    parser.add_argument("--feedback-db", type=Path)
    parser.add_argument(
        "--blend-weights",
        type=Path,
        default=probability_blend.DEFAULT_WEIGHTS_PATH,
        help="Versioned learned-weight artifact; missing/insufficient history stays market-only.",
    )
    parser.add_argument(
        "--no-feedback-ledger",
        action="store_true",
        help="Build pack artifacts without writing the permanent feedback database.",
    )
    args = parser.parse_args(argv)
    leagues = args.leagues.split(",")
    feed_health_by_league = build_feed_health_by_league(leagues)
    blend_artifact = probability_blend.load_weight_artifact(args.blend_weights)
    blend_promotion = probability_blend.promotion_status(blend_artifact)
    logger.info(
        "Blend weights: mode=%s eligible_samples=%s drives_sizing=%s%s",
        blend_promotion["mode"],
        blend_promotion["eligible_samples"],
        blend_promotion["drives_sizing"],
        f" ({'; '.join(blend_promotion['reasons'])})" if blend_promotion["reasons"] else "",
    )
    opportunity_rows: list[dict[str, Any]] = []
    build_kwargs: dict[str, Any] = {
        "opportunity_rows_out": opportunity_rows,
        "feed_health_by_league": feed_health_by_league,
    }
    if blend_artifact is not None:
        build_kwargs["blend_artifact"] = blend_artifact
    final_rows, target_date, games_norm, coverage = build_pack_with_coverage(
        leagues,
        args.date,
        args.top_ev_n,
        args.top_signal_n,
        **build_kwargs,
    )
    props_norm_by_league = load_props_norm_by_league(leagues)
    projection_records = load_projection_records(leagues, target_date)
    freshness = build_freshness_section(leagues, feed_health_by_league)
    out_dir = paths.PROJECT_ROOT / "packs" / target_date
    if args.no_feedback_ledger:
        write_pack(
            final_rows,
            out_dir,
            freshness,
            games_norm_by_league=games_norm,
            props_norm_by_league=props_norm_by_league,
            target_date=target_date,
            coverage=coverage,
            feed_health_by_league=feed_health_by_league,
            opportunity_rows=opportunity_rows,
            projection_records=projection_records,
        )
    else:
        from outlier_scrapers import feedback

        feedback_db = args.feedback_db or (paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3")
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = out_dir.parent / f".{out_dir.name}.feedback-staging-{uuid.uuid4().hex}"
        if out_dir.exists():
            recommendation_path = out_dir / "original_recommendations.csv"
            context_path = out_dir / "original_t30_context.json"
            if recommendation_path.exists() != context_path.exists():
                raise ValidationError(
                    "Incomplete published T-30 original snapshot: refusing to rebuild the pack."
                )
            sidecar_path = out_dir / "portfolio_risk.json"
            if sidecar_path.exists():
                try:
                    with open(sidecar_path, "r", encoding="utf-8") as sf:
                        existing = json.load(sf)
                        if existing.get("mode") == "enforce":
                            logger.info("Enforce pack already exists. Skipping rebuild.")
                            return out_dir
                except Exception:
                    pass
            shutil.copytree(out_dir, staging_dir)
        conn = None
        backup_dir: Path | None = None
        published = False
        try:
            write_pack(
                final_rows,
                staging_dir,
                freshness,
                games_norm_by_league=games_norm,
                props_norm_by_league=props_norm_by_league,
                target_date=target_date,
                coverage=coverage,
                feed_health_by_league=feed_health_by_league,
                opportunity_rows=opportunity_rows,
                projection_records=projection_records,
            )
            conn = feedback.open_database(feedback_db)
            conn.execute("BEGIN IMMEDIATE")
            stats = feedback.capture_pack(
                staging_dir,
                feedback_db,
                recorded_pack_path=out_dir,
                connection=conn,
            )
            backup_dir = _swap_staged_pack(staging_dir, out_dir)
            published = True
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            if published:
                _restore_published_pack(out_dir, backup_dir)
            if staging_dir.exists():
                _retry_rmtree(staging_dir)
            raise
        finally:
            if conn is not None:
                conn.close()
        if backup_dir is not None and backup_dir.exists():
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                logger.warning("Could not remove prior pack backup %s: %s", backup_dir, exc)
        logger.info(
            "Captured %d feedback snapshots and %d decisions in %s",
            stats.snapshots,
            stats.decisions,
            feedback_db,
        )
    logger.info("Wrote %d rows to %s", len(final_rows), out_dir)
    return out_dir


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
