"""Filesystem/transaction concerns for the pack writer.

``write_pack`` renders every derived pack artifact (candidates.csv, briefing,
dossiers, alt boards, the portfolio-risk sidecar) to disk; the swap/retry
helpers make republishing a pack an atomic, rollback-safe operation.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from outlier_scrapers import paths
from outlier_scrapers.pack_context import build_candidate_coverage_section
from outlier_scrapers.pack_market import _canonical_pack_date, _to_float, load_json
from outlier_scrapers.pack_render import _format_game_totals_md, build_briefing, build_dossier
from outlier_scrapers.pack_selection import (
    CANDIDATES_HEADER,
    _freeze_t30_originals,
    _opportunity_key,
    _reconcile_candidates_with_totals_board,
)
from outlier_scrapers.pack_sizing import (
    _apply_enforced_portfolio_units,
    _apply_learned_stake_before_caps,
    _load_learned_stake_runtime,
)
from outlier_scrapers.schema import ValidationError, validate_candidate_row
from outlier_scrapers.storage import (
    PACK_STREAM_CANDIDATES,
    PACK_STREAM_DECISIONS,
    PACK_STREAM_GAME_TOTALS,
    PACK_STREAM_OPPORTUNITIES,
    PACK_STREAM_TEAM_TOTALS,
    save_pack_artifacts,
)
from outlier_scrapers.utils import _write_csv, safe_write_text

logger = logging.getLogger(__name__)

# Versioned desk publications live under packs/<date>/verdicts/. That subtree
# must never appear here: rebuild cleanup uses Path.unlink(), which raises on
# a directory, and wiping the snapshot on every rebuild would discard the
# coherence contract. Retention is desk_snapshot.prune_publications only.
VERDICTS_SUBTREE = "verdicts"

DERIVED_PACK_OUTPUTS = (
    "candidate_coverage.json",
    "feed_health.json",
    "chatgpt_a.md",
    "gemini_b.md",
    "chatgpt_c.md",
    "claude_d.md",
    "claude_e.md",
    "daily_betting_report.md",
    "manual_betting_report.md",
    "mlb_betting_report.md",
    "reasoning_status.json",
    "manifest.json",
    "projections.jsonl",
    "portfolio_risk.json",
    "mlb_alt_bankroll_props.csv",
    "mlb_alt_bankroll_parlays.csv",
    "wnba_alt_bankroll_props.csv",
    "mlb_alt_spreads.csv",
    "wnba_alt_spreads.csv",
    "ultimate_alt.csv",
    "ultimate_alt_parlays.csv",
)


def clear_derived_pack_outputs(out_dir: Path) -> None:
    """Unlink derived files named in DERIVED_PACK_OUTPUTS.

    Never touches packs/<date>/verdicts/ (directory or any path under it).
    Directory entries are skipped so a future accidental add of ``verdicts``
    cannot crash rebuild with IsADirectoryError.
    """
    verdicts_root = (out_dir / VERDICTS_SUBTREE).resolve()
    for name in DERIVED_PACK_OUTPUTS:
        if name == VERDICTS_SUBTREE or str(name).replace("\\", "/").startswith(f"{VERDICTS_SUBTREE}/"):
            continue
        target = out_dir / name
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if resolved == verdicts_root or verdicts_root in resolved.parents:
            continue
        if target.is_dir():
            continue
        target.unlink(missing_ok=True)


def load_props_norm_by_league(leagues: Sequence[str]) -> dict[str, Any]:
    """Load normalized player-prop payloads without changing pack-builder API."""
    payloads: dict[str, Any] = {}
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        league_root = paths.league_paths(league).normalized
        payloads[league] = load_json(league_root / f"{league.lower()}_props_latest.json")
    return payloads


def _retry_replace(src: Path, dst: Path, retries: int = 10, delay: float = 0.1) -> None:
    last_err = None
    for _ in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    if last_err:
        raise last_err


def _retry_rmtree(path: Path, retries: int = 10, delay: float = 0.1) -> None:
    last_err = None
    for _ in range(retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    if last_err:
        raise last_err


def _swap_staged_pack(staging_dir: Path, out_dir: Path) -> Path | None:
    """Publish staging while retaining the prior pack for transaction rollback."""

    backup_dir = out_dir.parent / f".{out_dir.name}.feedback-backup-{uuid.uuid4().hex}"
    had_existing = out_dir.exists()
    if had_existing:
        _retry_replace(out_dir, backup_dir)
    try:
        _retry_replace(staging_dir, out_dir)
    except Exception:
        if had_existing and backup_dir.exists() and not out_dir.exists():
            _retry_replace(backup_dir, out_dir)
        raise
    return backup_dir if had_existing else None


def _restore_published_pack(out_dir: Path, backup_dir: Path | None) -> None:
    if out_dir.exists():
        _retry_rmtree(out_dir)
    if backup_dir is not None and backup_dir.exists():
        _retry_replace(backup_dir, out_dir)


def write_pack(
    rows: list[dict[str, Any]],
    out_dir: Path,
    freshness_lines: list[str] | None = None,
    *,
    games_norm_by_league: dict[str, Any] | None = None,
    props_norm_by_league: dict[str, Any] | None = None,
    target_date: str | None = None,
    coverage: dict[str, dict[str, int]] | None = None,
    feed_health_by_league: dict[str, dict[str, Any]] | None = None,
    opportunity_rows: list[dict[str, Any]] | None = None,
    projection_records: list[dict[str, Any]] | None = None,
) -> None:

    # Validate all candidate rows against schema constraints
    for idx, row in enumerate(rows):
        row_errors = validate_candidate_row(row, CANDIDATES_HEADER)
        if row_errors:
            # If a critical field is missing or empty, raise ValidationError
            critical_mismatch = any(
                "Critical field" in err or "dictionary" in err for err in row_errors
            )
            if critical_mismatch:
                raise ValidationError(
                    f"Critical schema compatibility violation at row {idx}: {'; '.join(row_errors)}"
                )
            # Log minor issues as warnings
            for err in row_errors:
                logger.warning("Candidate row schema warning at index %d: %s", idx, err)

    from outlier_scrapers.portfolio import load_portfolio_policy
    import json

    policy = load_portfolio_policy()
    # Immutable enforce pack check
    if policy.mode == "enforce":
        sidecar_path = out_dir / "portfolio_risk.json"
        if sidecar_path.exists():
            with open(sidecar_path, "r", encoding="utf-8") as sf:
                try:
                    existing = json.load(sf)
                    if existing.get("mode") == "enforce":
                        raise ValueError(
                            "Enforce pack already exists for this slate. Refusing to overwrite immutable pack."
                        )
                except json.JSONDecodeError:
                    pass

        # 14-day shadow window check
        import sqlite3
        from outlier_scrapers import paths

        db_path = paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3"
        if not db_path.exists():
            raise ValueError(
                "Enforce mode refused: feedback.sqlite3 not found (0 shadow days). 14 required."
            )
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(decisions)")
                columns = {row[1] for row in cursor.fetchall()}
                if "portfolio_mode" in columns:
                    query = """
                        SELECT COUNT(DISTINCT SUBSTR(m.captured_at, 1, 10))
                        FROM market_snapshots m
                        JOIN decisions d ON m.snapshot_id = d.snapshot_id
                        WHERE d.portfolio_mode = 'shadow'
                    """
                else:
                    query = (
                        "SELECT COUNT(DISTINCT SUBSTR(captured_at, 1, 10)) FROM market_snapshots"
                    )
                cursor.execute(query)
                shadow_days = cursor.fetchone()[0]
        except Exception as e:
            raise ValueError(f"Enforce mode refused: failed to query shadow window: {e}")
        if shadow_days < 14:
            raise ValueError(
                f"Enforce mode refused: only {shadow_days} days of shadow history found. 14 required."
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    pack_date = _canonical_pack_date(target_date, out_dir)
    clear_derived_pack_outputs(out_dir)
    dossiers_dir = out_dir / "dossiers"
    if dossiers_dir.exists():
        for stale_dossier in dossiers_dir.glob("*.md"):
            stale_dossier.unlink()

    # Totals rows (game + team) rarely carry an Outlier EV devig, which left
    # every OVER total with a blank model_prob/edge_pct. Fill them from the
    # two-sided book ladder blended with the recent-games L10 signal.
    if games_norm_by_league:
        from outlier_scrapers.totals_model import backfill_totals_probabilities

        rows = backfill_totals_probabilities(rows, games_norm_by_league)
        if opportunity_rows is not None:
            opportunity_rows = backfill_totals_probabilities(opportunity_rows, games_norm_by_league)

    from outlier_scrapers.game_totals import (
        GAME_TOTALS_HEADER,
        build_game_totals,
        TEAM_TOTALS_HEADER,
        build_team_totals,
    )
    from outlier_scrapers.portfolio import (
        project_risk_identity,
        collapse_duplicate_outcomes,
        unify_and_dedup_streams,
        allocate_portfolio_risk,
        load_portfolio_policy,
        policy_fingerprint,
    )
    from outlier_scrapers.probable_pitchers import load_probable_pitcher_lookup
    import subprocess

    totals_rows: list[dict[str, Any]] = []
    team_totals_rows: list[dict[str, Any]] = []
    for lg, payload in (games_norm_by_league or {}).items():
        probable_pitchers = load_probable_pitcher_lookup(lg)
        totals_rows.extend(
            build_game_totals(rows, payload, sport=lg, probable_pitchers=probable_pitchers)
        )
        team_totals_rows.extend(
            build_team_totals(rows, payload, sport=lg, probable_pitchers=probable_pitchers)
        )

    # Filter totals rows to ONLY keep games/teams active on the current target date slate
    slate_eids = {str(r.get("event_id")) for r in rows if r.get("event_id")}
    slate_matchups = {str(r.get("matchup")).upper() for r in rows if r.get("matchup")}
    slate_teams = set()
    for r in rows:
        if r.get("team"):
            slate_teams.add(str(r.get("team")).upper())
        if r.get("opponent"):
            slate_teams.add(str(r.get("opponent")).upper())

    if slate_eids or slate_matchups or slate_teams:
        totals_rows = [
            r
            for r in totals_rows
            if str(r.get("event_id")) in slate_eids
            or str(r.get("matchup")).upper() in slate_matchups
        ]
        team_totals_rows = [
            r
            for r in team_totals_rows
            if str(r.get("event_id")) in slate_eids
            or str(r.get("matchup")).upper() in slate_matchups
            or (r.get("team") and str(r.get("team")).upper() in slate_teams)
        ]

    _reconcile_candidates_with_totals_board(rows, totals_rows, team_totals_rows)
    if opportunity_rows is not None:
        _reconcile_candidates_with_totals_board(
            opportunity_rows, totals_rows, team_totals_rows
        )

    # Build all legacy alternate artifacts first, then compare them on one
    # conservative, price-aware shadow surface.  The legacy files remain for
    # compatibility; only ultimate_alt is used for new shadow evaluation.
    from outlier_scrapers.alt_team_totals import (
        ALT_TEAM_TOTAL_PARLAYS_HEADER,
        ALT_TEAM_TOTALS_HEADER,
        build_alt_team_total_board,
        build_alt_team_total_parlays,
        format_alt_team_totals_md,
    )
    from outlier_scrapers.alt_bankroll_props import (
        ALT_BANKROLL_PARLAYS_HEADER,
        ALT_BANKROLL_PROPS_HEADER,
        build_alt_bankroll_board,
        build_alt_bankroll_parlays,
    )
    from outlier_scrapers.alt_spreads import ALT_SPREADS_HEADER, build_alt_spreads_board
    from outlier_scrapers.alt_player_props import (
        ALT_PLAYER_PROPS_PARLAYS_HEADER,
        ALT_PLAYER_PROPS_HEADER,
        build_alt_player_props_board,
        build_alt_player_props_parlays,
        format_alt_player_props_md,
    )
    from outlier_scrapers.ultimate_alt import (
        SHADOW_UNITS,
        ULTIMATE_ALT_HEADER,
        ULTIMATE_ALT_PARLAYS_HEADER,
        build_ultimate_alt_board,
        build_ultimate_alt_parlays,
        format_ultimate_alt_md,
    )

    alt_tt_rows: list[dict[str, Any]] = []
    alt_tt_parlays: list[dict[str, Any]] = []
    alt_spread_rows: list[dict[str, Any]] = []
    alt_total_rows: list[dict[str, Any]] = []
    bankroll_rows_by_league: dict[str, list[dict[str, Any]]] = {}
    bankroll_parlays_by_league: dict[str, list[dict[str, Any]]] = {}
    for lg, payload in (games_norm_by_league or {}).items():
        league_tt_rows = build_alt_team_total_board(payload, league=lg, target_date=pack_date)
        alt_tt_rows.extend(league_tt_rows)
        alt_tt_parlays.extend(build_alt_team_total_parlays(league_tt_rows))
        bankroll_rows = build_alt_bankroll_board(payload, league=lg, target_date=pack_date)
        bankroll_rows_by_league[lg] = bankroll_rows
        bankroll_parlays_by_league[lg] = build_alt_bankroll_parlays(bankroll_rows)
        alt_spread_rows.extend(build_alt_spreads_board(payload, league=lg, target_date=pack_date))
        alt_total_rows.extend(
            row
            for row in bankroll_rows
            if str(row.get("proposition") or "").upper() == "TOTAL"
            or str(row.get("market_type") or "").upper() == "TEAM_PROP"
        )
    alt_total_rows.extend(alt_tt_rows)

    ev_over_players: set[str] = set()
    for row in rows:
        edge = _to_float(row.get("edge_pct"))
        if " OVER " in str(row.get("selection") or "").upper() and edge is not None and edge > 0:
            ev_over_players.add(str(row.get("player_id") or "").strip())
    ev_over_players.discard("")

    alt_player_rows: list[dict[str, Any]] = []
    alt_player_parlays: list[dict[str, Any]] = []
    for lg, payload in (props_norm_by_league or {}).items():
        league_rows = build_alt_player_props_board(
            payload,
            league=lg,
            target_date=pack_date,
            ev_over_players=ev_over_players
        )
        alt_player_rows.extend(league_rows)
        alt_player_parlays.extend(build_alt_player_props_parlays(league_rows))

    ultimate_alt_rows = build_ultimate_alt_board(
        spread_rows=alt_spread_rows,
        total_rows=alt_total_rows,
        player_rows=alt_player_rows,
    )
    ultimate_alt_parlays = build_ultimate_alt_parlays(ultimate_alt_rows)

    # --- PORTFOLIO RISK ALLOCATION ---
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=str(Path(__file__).parent)
        ).strip()
    except Exception:
        git_sha = "unknown"

    policy = load_portfolio_policy()

    # Immutable enforce pack check
    if policy.mode == "enforce":
        sidecar_path = out_dir / "portfolio_risk.json"
        if sidecar_path.exists():
            with open(sidecar_path, "r", encoding="utf-8") as sf:
                try:
                    existing = json.load(sf)
                    if existing.get("mode") == "enforce":
                        raise ValueError(
                            "Enforce pack already exists for this slate. Refusing to overwrite immutable pack."
                        )
                except json.JSONDecodeError:
                    pass

        # 14-day shadow window check
        import sqlite3
        from outlier_scrapers import paths

        db_path = paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3"
        if not db_path.exists():
            raise ValueError(
                "Enforce mode refused: feedback.sqlite3 not found (0 shadow days). 14 required."
            )
        with sqlite3.connect(db_path) as conn:
            try:
                res = conn.execute(
                    "SELECT COUNT(DISTINCT SUBSTR(captured_at, 1, 10)) FROM market_snapshots"
                ).fetchone()
                days = res[0] if res else 0
                if days < 14:
                    raise ValueError(
                        f"Enforce mode refused: only {days} days of shadow history found. 14 required."
                    )
            except sqlite3.OperationalError:
                pass  # table might not exist in an empty db

    learned_runtime = _load_learned_stake_runtime(policy)
    all_projected = []
    for stream_name, stream_rows in [
        ("candidates", rows),
        ("game_totals", totals_rows),
        ("team_totals", team_totals_rows),
    ]:
        for r in stream_rows:
            units_val = r.get("recommended_units_pre_news")
            if units_val not in (None, ""):
                r["units"] = float(units_val)
            proj = project_risk_identity(r, stream_name)
            _apply_learned_stake_before_caps(
                proj,
                r,
                stream=stream_name,
                policy=policy,
                runtime=learned_runtime,
            )
            proj["_original_ref"] = r
            proj["stream"] = stream_name
            all_projected.append(proj)
    ultimate_projected = []
    for r in ultimate_alt_rows:
        if r.get("shadow_status") != "QUALIFIED":
            continue
        risk_input = dict(r)
        risk_input.update(
            {
                "actionable": "true",
                "board": "A",
                "units": SHADOW_UNITS,
                "pre_cap_units": SHADOW_UNITS,
            }
        )
        proj = project_risk_identity(risk_input, "ultimate_alt")
        proj["_original_ref"] = r
        proj["_ultimate_shadow"] = True
        proj["stream"] = "ultimate_alt"
        ultimate_projected.append(proj)

    collapsed = collapse_duplicate_outcomes(all_projected)
    unified = unify_and_dedup_streams(collapsed, policy)
    alloc_result = allocate_portfolio_risk(unified, policy)

    # Ultimate Alt competes with every live stream for shadow capacity, but it
    # never changes live/enforce allocations until a manual promotion follows
    # the settlement release gate.
    combined_shadow = unify_and_dedup_streams(
        collapse_duplicate_outcomes([*all_projected, *ultimate_projected]), policy
    )
    combined_shadow_result = allocate_portfolio_risk(combined_shadow, policy)

    for u_row in unified:
        orig = u_row.pop("_original_ref", None)
        if orig is not None:
            wager_id = u_row.get("stable_wager_id")
            if wager_id and wager_id in alloc_result.allocated_units:
                u_row["portfolio_units"] = alloc_result.allocated_units[wager_id]
                if not policy.shadow_mode:
                    _apply_enforced_portfolio_units(u_row, u_row["portfolio_units"])
            for k, v in u_row.items():
                if policy.shadow_mode and k in (
                    "recommended_units_pre_news",
                    "actionable",
                    "board",
                ):
                    continue
                orig[k] = v

    for u_row in combined_shadow:
        if not u_row.get("_ultimate_shadow"):
            continue
        orig = u_row.get("_original_ref")
        if orig is None:
            continue
        wager_id = u_row.get("stable_wager_id")
        allocated = combined_shadow_result.allocated_units.get(wager_id, 0.0) if wager_id else 0.0
        orig["portfolio_shadow_units"] = allocated
        for key in (
            "stable_wager_id",
            "risk_market_family",
            "risk_subject_id",
            "correlation_cluster_ids",
            "missing_risk_identity",
        ):
            if key in u_row:
                orig[key] = u_row[key]
        if wager_id:
            orig["cap_reasons"] = ";".join(combined_shadow_result.cap_reasons.get(wager_id, []))

    legacy_units = sum(
        float(r.get("recommended_units_pre_news") or 0.0)
        for r in all_projected
        if str(r.get("actionable", "")).lower() == "true"
    )
    raw_kelly_units = sum(
        float(r.get("kelly_025_units") or 0.0)
        for r in all_projected
        if str(r.get("actionable", "")).lower() == "true"
    )
    pre_cap_units = sum(
        float(r.get("pre_cap_units", r.get("units", 0.0)))
        for r in unified
        if r.get("risk_role") == "PRIMARY" and str(r.get("actionable", "")).lower() == "true"
    )
    shadow_units = sum(alloc_result.allocated_units.values())
    ultimate_shadow_units = 0.0
    for shadow_row in combined_shadow:
        if not shadow_row.get("_ultimate_shadow"):
            continue
        shadow_wager_id = str(shadow_row.get("stable_wager_id") or "")
        if shadow_wager_id:
            ultimate_shadow_units += combined_shadow_result.allocated_units.get(
                shadow_wager_id, 0.0
            )
    final_units = shadow_units if not policy.shadow_mode else legacy_units

    bs_breakdown: dict[str, int] = {}
    for r in unified:
        bs = r.get("book_source", "unknown")
        bs_breakdown[bs] = bs_breakdown.get(bs, 0) + 1

    # --- Flat metrics consumed by portfolio_report.py / portfolio replay (A8) ---
    # A7 (this sidecar writer) and A8 (portfolio_report.py, plus the pinned
    # fixtures in test_portfolio_report.py / test_portfolio_replay.py) were
    # implemented in separate commits and never reconciled: the reader expects
    # flat keys (legacy_total_units, caps_binding, book_source_exposure, ...)
    # that this sidecar never wrote, so every real pack silently produced a
    # report of zeros. These are computed here, alongside the existing nested
    # diagnostic fields, using data already available above.
    caps_binding: dict[str, int] = {}
    for group_key in alloc_result.binding_constraints:
        cap_type = group_key.split(":", 1)[0]
        caps_binding[cap_type] = caps_binding.get(cap_type, 0) + 1

    book_source_exposure: dict[str, float] = {}
    zeroed_rows = 0
    for r in unified:
        if r.get("risk_role") != "PRIMARY":
            continue
        wager_id = r.get("stable_wager_id")
        allocated = alloc_result.allocated_units.get(wager_id, 0.0) if wager_id else 0.0
        bs = r.get("book_source", "unknown")
        book_source_exposure[bs] = round(book_source_exposure.get(bs, 0.0) + allocated, 4)
        pre_cap_row = float(r.get("pre_cap_units", r.get("units", 0.0)))
        if pre_cap_row > 0.0 and allocated == 0.0:
            zeroed_rows += 1

    missing_identity_counts = sum(1 for r in all_projected if r.get("missing_risk_identity"))
    duplicate_collapse_counts = (len(all_projected) - len(collapsed)) + (
        len(collapsed) - len(unified)
    )

    sidecar = {
        "schema_version": policy.schema_version,
        "policy_version": policy.policy_version,
        "policy_fingerprint": policy_fingerprint(policy),
        "mode": policy.mode,
        "code_git_sha": git_sha,
        "slate_date": pack_date,
        "row_counts": {
            "candidates": len(rows),
            "game_totals": len(totals_rows),
            "team_totals": len(team_totals_rows),
            "ultimate_alt": sum(
                1 for row in ultimate_alt_rows if row.get("shadow_status") == "QUALIFIED"
            ),
            "total_in_scope": len(all_projected),
            "unified": len(unified),
        },
        "unit_totals": {
            "legacy": legacy_units,
            "raw_kelly": raw_kelly_units,
            "pre_cap": pre_cap_units,
            "shadow": shadow_units,
            "ultimate_alt_shadow": ultimate_shadow_units,
            "final": final_units,
        },
        "cap_limits": {
            "max_wager_units": policy.max_wager_units,
            "max_daily_units": policy.max_daily_units,
            "max_event_units": policy.max_event_units,
            "max_player_units": policy.max_player_units,
            "max_team_units": policy.max_team_units,
            "max_market_type_units": policy.max_market_type_units,
            "max_correlated_cluster_units": policy.max_correlated_cluster_units,
            "max_book_units": policy.max_book_units,
        },
        "reserved_capacity": {},
        "cap_utilization": alloc_result.utilization,
        "binding_constraints": alloc_result.binding_constraints,
        "dedup_log": {
            "collapsed": len(all_projected) - len(collapsed),
            "unified": len(collapsed) - len(unified),
        },
        "missing_identity_warnings": missing_identity_counts,
        "book_source_breakdown": bs_breakdown,
        "quantization_only_difference_totals": 0,
        "order_invariance_hash": alloc_result.order_invariance_hash,
        # Flat aliases required by portfolio_report.py / portfolio replay.
        "legacy_total_units": legacy_units,
        "shadow_total_units": shadow_units,
        "caps_binding": caps_binding,
        "zeroed_rows": zeroed_rows,
        "missing_identity_counts": missing_identity_counts,
        "duplicate_collapse_counts": duplicate_collapse_counts,
        "book_source_exposure": book_source_exposure,
        # Ticket A1 (continuous, unrounded pre_cap_units) is not yet merged onto
        # this branch, so pre_cap_units currently falls back to the already
        # half-unit-rounded legacy `units` value (see project_risk_identity /
        # allocate_portfolio_risk). With no continuous baseline to diff
        # against, a genuine quantization-only difference can't be computed
        # yet -- report 0 with an explicit flag rather than a value that would
        # look measured but isn't.
        "quantization_differences": 0,
        "quantization_diff_measurable": False,
    }

    safe_write_text(out_dir / "portfolio_risk.json", json.dumps(sidecar, indent=2))
    # --- END PORTFOLIO RISK ALLOCATION ---

    _write_csv(out_dir / "candidates.csv", CANDIDATES_HEADER, rows)

    _freeze_t30_originals(
        out_dir,
        rows,
        games_norm_by_league=games_norm_by_league,
        props_norm_by_league=props_norm_by_league,
        totals_rows=[*totals_rows, *team_totals_rows],
        target_date=pack_date,
    )

    selected_keys = {_opportunity_key(row) for row in rows}
    opportunity_output: list[dict[str, Any]] = []
    for source_row in opportunity_rows if opportunity_rows is not None else rows:
        row = dict(source_row)
        opportunity_key = _opportunity_key(row)
        row["selected"] = "true" if opportunity_key in selected_keys else "false"
        opportunity_output.append(row)
    _write_csv(out_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], opportunity_output)

    proj_content = "\n".join(json.dumps(p, sort_keys=True) for p in (projection_records or [])) + ("\n" if projection_records else "")
    safe_write_text(out_dir / "projections.jsonl", proj_content)

    safe_write_text(
        out_dir / "briefing.md",
        build_briefing(
            rows,
            pack_date,
            freshness_lines,
            totals_rows if games_norm_by_league is not None else None,
            team_totals_rows if games_norm_by_league is not None else None,
            build_candidate_coverage_section(coverage) if coverage is not None else None,
            ultimate_alt_rows,
        ),
    )
    if coverage is not None:
        safe_write_text(out_dir / "candidate_coverage.json", json.dumps(coverage, indent=2, sort_keys=True))
    if feed_health_by_league is not None:
        safe_write_text(out_dir / "feed_health.json", json.dumps(feed_health_by_league, indent=2, sort_keys=True))
    dossiers_dir.mkdir(exist_ok=True)
    by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        eid = r.get("event_id")
        if eid:
            by_event.setdefault((r["sport"], str(eid)), []).append(r)
    for (sport, eid), erows in by_event.items():
        slug = erows[0].get("_slug", "unknown")
        safe_write_text(dossiers_dir / f"{sport}_{eid}_{slug}.md", build_dossier(erows, sport))
    from outlier_scrapers.feedback import DECISION_FIELDS

    decision_rows: list[dict[str, Any]] = []
    _write_csv(out_dir / "decisions.csv", DECISION_FIELDS, decision_rows)

    sections_dir = out_dir / "sections"
    sections_dir.mkdir(exist_ok=True)

    _write_csv(out_dir / "game_totals.csv", GAME_TOTALS_HEADER, totals_rows)
    safe_write_text(sections_dir / "game_totals.md", _format_game_totals_md(totals_rows))

    _write_csv(out_dir / "team_totals.csv", TEAM_TOTALS_HEADER, team_totals_rows)
    safe_write_text(sections_dir / "team_totals.md", _format_game_totals_md(team_totals_rows, title="# Team totals"))

    _write_csv(out_dir / "alt_team_totals.csv", ALT_TEAM_TOTALS_HEADER, alt_tt_rows)
    _write_csv(
        out_dir / "alt_team_total_parlays.csv",
        ALT_TEAM_TOTAL_PARLAYS_HEADER,
        alt_tt_parlays,
    )
    safe_write_text(
        sections_dir / "alt_team_totals.md",
        format_alt_team_totals_md(alt_tt_rows, alt_tt_parlays),
    )

    for lg, bankroll_rows in bankroll_rows_by_league.items():
        _write_csv(
            out_dir / f"{lg.lower()}_alt_bankroll_props.csv",
            ALT_BANKROLL_PROPS_HEADER,
            bankroll_rows,
        )
        _write_csv(
            out_dir / f"{lg.lower()}_alt_bankroll_parlays.csv",
            ALT_BANKROLL_PARLAYS_HEADER,
            bankroll_parlays_by_league.get(lg) or [],
        )

    for lg in games_norm_by_league or {}:
        spread_rows = [row for row in alt_spread_rows if row.get("league") == lg]
        _write_csv(
            out_dir / f"{lg.lower()}_alt_spreads.csv",
            ALT_SPREADS_HEADER,
            spread_rows,
        )

    _write_csv(out_dir / "alt_player_props.csv", ALT_PLAYER_PROPS_HEADER, alt_player_rows)
    _write_csv(
        out_dir / "alt_player_props_parlays.csv",
        ALT_PLAYER_PROPS_PARLAYS_HEADER,
        alt_player_parlays,
    )
    safe_write_text(
        sections_dir / "alt_player_props.md",
        format_alt_player_props_md(alt_player_rows, alt_player_parlays),
    )

    _write_csv(out_dir / "ultimate_alt.csv", ULTIMATE_ALT_HEADER, ultimate_alt_rows)
    _write_csv(
        out_dir / "ultimate_alt_parlays.csv",
        ULTIMATE_ALT_PARLAYS_HEADER,
        ultimate_alt_parlays,
    )
    safe_write_text(
        sections_dir / "ultimate_alt.md",
        format_ultimate_alt_md(ultimate_alt_rows, ultimate_alt_parlays),
    )

    save_pack_artifacts(
        pack_date,
        {
            PACK_STREAM_CANDIDATES: rows,
            PACK_STREAM_OPPORTUNITIES: opportunity_output,
            PACK_STREAM_GAME_TOTALS: totals_rows,
            PACK_STREAM_TEAM_TOTALS: team_totals_rows,
            PACK_STREAM_DECISIONS: decision_rows,
        },
    )
