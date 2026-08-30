"""Permanent feedback ledgers, settlement grading, and calibration reports.

Compatibility import surface for the decomposed feedback modules
(``feedback_db``, ``feedback_recovery``, ``feedback_capture``,
``feedback_settlement``, ``calibration``, ``feedback_reporting``,
``feedback_retention``). Owns the CLI entry point: ``main``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import sqlite3
import subprocess
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from outlier_scrapers import (
    drawdown,
    paths,
    probability_blend,
    stake_calibration,
    totals_model,
)  # totals_model: tests use feedback.totals_model
from outlier_scrapers.portfolio import PortfolioPolicy, allocate_portfolio_risk
from outlier_scrapers.utils import _american_to_decimal, _write_csv

# --- feedback_db: schema, migrations, connections -------------------------
from outlier_scrapers.feedback_db import (
    DEFAULT_DB_PATH,
    SCHEMA_VERSION,
    MARKET_SNAPSHOT_FIELDS,
    DECISION_FIELDS,
    SETTLEMENT_REQUIRED_FIELDS,
    SETTLEMENT_FIELDS,
    PACK_MEMBERSHIP_FIELDS,
    PROBABILITY_COLUMNS,
    MARKET_SNAPSHOT_COLUMN_DEFINITIONS,
    DECISION_COLUMN_DEFINITIONS,
    SETTLEMENT_COLUMN_DEFINITIONS,
    REQUIRED_TABLE_IDENTITY_COLUMNS,
    TABLE_COLUMN_ADD_STATEMENTS,
    SELECT_DECISION_BY_ID_SQL,
    SETTLEMENT_DECISION_SELECT_SQL,
    SETTLEMENT_SNAPSHOT_SELECT_SQL,
    SETTLEMENT_MARKET_SELECT_SQL,
    SETTLEMENT_OUTCOME_SELECT_SQL,
    FeedbackError,
    _utc_now,
    _parse_utc,
    _stable_id,
    _text,
    _float,
    _probability,
    _truthy,
    _coalesce,
    _append_flag,
    _normal_result,
    _ensure_table_columns,
    _migrate_blend_segments,
    initialize_database,
    _migrate_probability_semantics,
    _timestamp_rank,
    _timestamp_extreme,
    _migrate_decision_ids,
    _connect,
    open_database,
    _joined_rows,
    _mean,
)

# --- feedback_capture: pack capture and decision identity -----------------
from outlier_scrapers.feedback_capture import (
    CaptureStats,
    ImportStats,
    _validate_decision_snapshot_identities,
    _read_csv,
    _pack_fallback_timestamp,
    _selection_side,
    _total_representation_key,
    _load_pack_rows,
    _signal_fields,
    _snapshot_from_pack_row,
    _decision_seed,
    capture_pack,
    capture_t30_pack,
    import_decisions,
)

# --- feedback_recovery: ledger salvage ------------------------------------
from outlier_scrapers.feedback_recovery import (
    RecoverStats,
    _RECOVERY_TABLE_ORDER,
    _RECOVERY_TABLE_FIELDS,
    _run_sqlite_cli_recover,
    _open_for_salvage,
    _salvage_rows_directly,
    _insert_recovered_row,
    recover_corrupted_database,
)

# --- feedback_settlement: settlement import and CLV -----------------------
from outlier_scrapers.feedback_settlement import (
    compute_clv_line,
    compute_clv_price,
    find_distinct_closing_snapshot,
    closing_line_from_movement_export,
    _find_distinct_closes_for_settlements,
    recompute_settlement_clv,
    _is_play,
    _computed_pnl,
    import_settlements,
    import_settlement_file,
    import_settlement_inbox,
)

# --- calibration: fitters and learned-multiplier promotion ----------------
from outlier_scrapers.calibration import (
    DEFAULT_LEARNED_MULTIPLIER_PROMOTION_PATH,
    LEARNED_MULTIPLIER_POPULATION,
    LEARNED_MULTIPLIER_PROMOTION_DEFAULTS,
    _positive_unit_recommendation_rows,
    fit_blend_weights,
    fit_stake_calibration_from_db,
    compute_drawdown_from_db,
    load_learned_multiplier_promotion_policy,
    _active_learned_multiplier_source_column,
    learned_multiplier_promotion_signal,
)

# --- feedback_reporting: read-only reports and ledger export --------------
from outlier_scrapers.feedback_reporting import (
    DEFAULT_REPORT_DIR,
    _flat_pnl,
    _group_metrics,
    GROUP_METRIC_FIELDS,
    _grouped,
    _scoring_probability,
    _probability_metrics,
    _calibration_curve,
    _edge_bucket,
    _odds_bucket,
    _bucketed,
    _signal_results,
    _play_vs_stand_down,
    DECISION_COVERAGE_FIELDS,
    _decision_coverage,
    _model_performance,
    ULTIMATE_ALT_SHADOW_FIELDS,
    ultimate_alt_shadow_release,
    export_ledgers,
    _fmt,
    _report_markdown,
    _missing_edge_diagnostics,
    TOTALS_MODEL_PROB_SOURCES,
    _is_totals_row,
    _wilson_interval,
    totals_paired_oos_loss,
    _totals_edge_band,
    TOTALS_EDGE_BAND_ORDER,
    totals_edge_bucket_report,
    generate_report,
    replay_portfolio,
)

# --- feedback_retention: settled never-played slimming --------------------
from outlier_scrapers.feedback_retention import (
    RetentionStats,
    _RETENTION_FULL_FIDELITY_VERDICTS,
    _RETENTION_SLIMMED_COLUMNS,
    apply_retention_policy,
)

logger = logging.getLogger(__name__)

# Listed explicitly (not `import *`) so underscore-prefixed names re-export too.
__all__ = [
    "argparse",
    "csv",
    "hashlib",
    "json",
    "logging",
    "math",
    "sqlite3",
    "subprocess",
    "defaultdict",
    "nullcontext",
    "dataclass",
    "datetime",
    "timedelta",
    "timezone",
    "Path",
    "Any",
    "Iterable",
    "Sequence",
    "drawdown",
    "paths",
    "probability_blend",
    "stake_calibration",
    "totals_model",
    "PortfolioPolicy",
    "allocate_portfolio_risk",
    "_american_to_decimal",
    "_write_csv",
    "DEFAULT_DB_PATH",
    "SCHEMA_VERSION",
    "MARKET_SNAPSHOT_FIELDS",
    "DECISION_FIELDS",
    "SETTLEMENT_REQUIRED_FIELDS",
    "SETTLEMENT_FIELDS",
    "PACK_MEMBERSHIP_FIELDS",
    "PROBABILITY_COLUMNS",
    "MARKET_SNAPSHOT_COLUMN_DEFINITIONS",
    "DECISION_COLUMN_DEFINITIONS",
    "SETTLEMENT_COLUMN_DEFINITIONS",
    "REQUIRED_TABLE_IDENTITY_COLUMNS",
    "TABLE_COLUMN_ADD_STATEMENTS",
    "SELECT_DECISION_BY_ID_SQL",
    "SETTLEMENT_DECISION_SELECT_SQL",
    "SETTLEMENT_SNAPSHOT_SELECT_SQL",
    "SETTLEMENT_MARKET_SELECT_SQL",
    "SETTLEMENT_OUTCOME_SELECT_SQL",
    "FeedbackError",
    "_utc_now",
    "_parse_utc",
    "_stable_id",
    "_text",
    "_float",
    "_probability",
    "_truthy",
    "_coalesce",
    "_append_flag",
    "_normal_result",
    "_ensure_table_columns",
    "_migrate_blend_segments",
    "initialize_database",
    "_migrate_probability_semantics",
    "_timestamp_rank",
    "_timestamp_extreme",
    "_migrate_decision_ids",
    "_connect",
    "open_database",
    "_joined_rows",
    "_mean",
    "CaptureStats",
    "ImportStats",
    "_validate_decision_snapshot_identities",
    "_read_csv",
    "_pack_fallback_timestamp",
    "_selection_side",
    "_total_representation_key",
    "_load_pack_rows",
    "_signal_fields",
    "_snapshot_from_pack_row",
    "_decision_seed",
    "capture_pack",
    "capture_t30_pack",
    "import_decisions",
    "RecoverStats",
    "_RECOVERY_TABLE_ORDER",
    "_RECOVERY_TABLE_FIELDS",
    "_run_sqlite_cli_recover",
    "_open_for_salvage",
    "_salvage_rows_directly",
    "_insert_recovered_row",
    "recover_corrupted_database",
    "compute_clv_line",
    "compute_clv_price",
    "find_distinct_closing_snapshot",
    "closing_line_from_movement_export",
    "_find_distinct_closes_for_settlements",
    "recompute_settlement_clv",
    "_is_play",
    "_computed_pnl",
    "import_settlements",
    "import_settlement_file",
    "import_settlement_inbox",
    "DEFAULT_LEARNED_MULTIPLIER_PROMOTION_PATH",
    "LEARNED_MULTIPLIER_POPULATION",
    "LEARNED_MULTIPLIER_PROMOTION_DEFAULTS",
    "_positive_unit_recommendation_rows",
    "fit_blend_weights",
    "fit_stake_calibration_from_db",
    "compute_drawdown_from_db",
    "load_learned_multiplier_promotion_policy",
    "_active_learned_multiplier_source_column",
    "learned_multiplier_promotion_signal",
    "DEFAULT_REPORT_DIR",
    "_flat_pnl",
    "_group_metrics",
    "GROUP_METRIC_FIELDS",
    "_grouped",
    "_scoring_probability",
    "_probability_metrics",
    "_calibration_curve",
    "_edge_bucket",
    "_odds_bucket",
    "_bucketed",
    "_signal_results",
    "_play_vs_stand_down",
    "DECISION_COVERAGE_FIELDS",
    "_decision_coverage",
    "_model_performance",
    "ULTIMATE_ALT_SHADOW_FIELDS",
    "ultimate_alt_shadow_release",
    "export_ledgers",
    "_fmt",
    "_report_markdown",
    "_missing_edge_diagnostics",
    "TOTALS_MODEL_PROB_SOURCES",
    "_is_totals_row",
    "_wilson_interval",
    "totals_paired_oos_loss",
    "_totals_edge_band",
    "TOTALS_EDGE_BAND_ORDER",
    "totals_edge_bucket_report",
    "generate_report",
    "replay_portfolio",
    "RetentionStats",
    "_RETENTION_FULL_FIDELITY_VERDICTS",
    "_RETENTION_SLIMMED_COLUMNS",
    "apply_retention_policy",
    "write_templates",
    "main",
]


def write_templates(output_dir: Path) -> None:
    output_dir = Path(output_dir)
    _write_csv(output_dir / "decisions_template.csv", DECISION_FIELDS, [])
    _write_csv(output_dir / "settlements_template.csv", SETTLEMENT_FIELDS, [])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture Outlier decisions, import settlements, and report calibration."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create or migrate the permanent ledger database.")

    capture_parser = subparsers.add_parser("capture", help="Capture a dated pack.")
    capture_parser.add_argument("--pack", type=Path, required=True)

    decisions_parser = subparsers.add_parser("decisions", help="Import a filled decision CSV.")
    decisions_parser.add_argument("--input", type=Path, required=True)

    settle_parser = subparsers.add_parser("settle", help="Import settlement/result rows.")
    settle_parser.add_argument("--input", type=Path, required=True)

    report_parser = subparsers.add_parser("report", help="Generate all feedback reports.")
    report_parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_DIR)

    blend_parser = subparsers.add_parser(
        "fit-blend", help="Fit market/model weights from settled pregame snapshots."
    )
    blend_parser.add_argument("--output", type=Path, default=probability_blend.DEFAULT_WEIGHTS_PATH)
    blend_parser.add_argument("--min-samples", type=int, default=200)
    blend_parser.add_argument("--prior-strength", type=float, default=30.0)

    stake_cal_parser = subparsers.add_parser(
        "fit-stake-calibration",
        help="Fit stake-calibration artifact from settled chronological rows (Track C1).",
    )
    stake_cal_parser.add_argument(
        "--output", type=Path, default=stake_calibration.DEFAULT_ARTIFACT_PATH
    )
    stake_cal_parser.add_argument("--min-samples", type=int, default=30)
    stake_cal_parser.add_argument("--prior-strength", type=float, default=30.0)
    stake_cal_parser.add_argument("--confidence-level", type=float, default=0.80)
    stake_cal_parser.add_argument(
        "--source-column",
        default=stake_calibration.DEFAULT_SOURCE_PROBABILITY_COLUMN,
        help="Probability column that drove historical recommendations.",
    )
    stake_cal_parser.add_argument(
        "--as-of",
        default=None,
        help="ISO timestamp training cutoff (default: now UTC).",
    )

    drawdown_parser = subparsers.add_parser(
        "compute-drawdown",
        help="Compute drawdown equity state from settled placed wagers (Track C3).",
    )
    drawdown_parser.add_argument("--output", type=Path, default=drawdown.DEFAULT_STATE_PATH)
    drawdown_parser.add_argument(
        "--as-of",
        default=None,
        help="ISO timestamp equity cutoff (default: now UTC).",
    )

    export_parser = subparsers.add_parser("export", help="Export the three permanent ledgers.")
    export_parser.add_argument("--output", type=Path, required=True)

    template_parser = subparsers.add_parser(
        "templates", help="Write decision/settlement templates."
    )
    template_parser.add_argument("--output", type=Path, required=True)

    replay_parser = subparsers.add_parser(
        "replay-portfolio", help="Chronological portfolio replay."
    )
    replay_parser.add_argument("--from", dest="start_date", required=True, help="YYYY-MM-DD")
    replay_parser.add_argument("--to", dest="end_date", required=True, help="YYYY-MM-DD")
    replay_parser.add_argument(
        "--policy", type=Path, required=True, help="Path to portfolio risk policy JSON"
    )
    replay_parser.add_argument("--as-of-strict", action="store_true", default=True)

    recover_parser = subparsers.add_parser(
        "recover", help="Salvage a corrupted ledger database into a fresh one."
    )
    recover_parser.add_argument("--corrupted", type=Path, required=True)
    recover_parser.add_argument("--output", type=Path, required=True)

    recompute_clv_parser = subparsers.add_parser(
        "recompute-clv",
        help="Recompute settlement CLV where the recorded close was actually the take.",
    )
    recompute_clv_parser.add_argument("--dry-run", action="store_true")

    retention_parser = subparsers.add_parser(
        "retention", help="Slim settled, never-played ledger rows and reclaim disk space."
    )
    retention_parser.add_argument("--cutoff-days", type=int, default=90)
    retention_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(initialize_database(args.db))
        elif args.command == "capture":
            capture_stats = capture_pack(args.pack, args.db)
            print(json.dumps(capture_stats.__dict__, sort_keys=True))
        elif args.command == "decisions":
            decision_stats = import_decisions(args.input, args.db)
            print(json.dumps(decision_stats.__dict__, sort_keys=True))
        elif args.command == "settle":
            settlement_stats = import_settlement_file(args.input, args.db)
            print(json.dumps(settlement_stats, sort_keys=True))
        elif args.command == "report":
            print(generate_report(args.db, args.output))
        elif args.command == "fit-blend":
            artifact = fit_blend_weights(
                args.db,
                args.output,
                min_samples=args.min_samples,
                prior_strength=args.prior_strength,
            )
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "status": artifact["status"],
                        "model_version": artifact["model_version"],
                        "eligible_samples": artifact["eligible_samples"],
                        "global_market_weight": artifact["global"].get("market_weight"),
                        "global_holdout": artifact.get("global_holdout"),
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "fit-stake-calibration":
            as_of = None
            if args.as_of:
                as_of_text = args.as_of
                if as_of_text.endswith("Z"):
                    as_of_text = f"{as_of_text[:-1]}+00:00"
                as_of = datetime.fromisoformat(as_of_text)
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=timezone.utc)
            artifact = fit_stake_calibration_from_db(
                args.db,
                args.output,
                as_of=as_of,
                source_probability_column=args.source_column,
                min_samples=args.min_samples,
                prior_strength=args.prior_strength,
                confidence_level=args.confidence_level,
            )
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "status": artifact["status"],
                        "artifact_version": artifact["artifact_version"],
                        "eligible_samples": artifact["eligible_samples"],
                        "source_probability_column": artifact["source_probability_column"],
                        "training_cutoff": artifact["training_cutoff"],
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "compute-drawdown":
            as_of = None
            if args.as_of:
                as_of_text = args.as_of
                if as_of_text.endswith("Z"):
                    as_of_text = f"{as_of_text[:-1]}+00:00"
                as_of = datetime.fromisoformat(as_of_text)
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=timezone.utc)
            state = compute_drawdown_from_db(args.db, args.output, as_of=as_of)
            print(json.dumps(state.to_dict(), sort_keys=True))
        elif args.command == "export":
            print(json.dumps(export_ledgers(args.db, args.output), sort_keys=True))
        elif args.command == "templates":
            write_templates(args.output)
            print(args.output)
        elif args.command == "replay-portfolio":
            summary = replay_portfolio(
                args.db, args.start_date, args.end_date, args.policy, as_of_strict=args.as_of_strict
            )
            print(json.dumps(summary, indent=2, sort_keys=True))
        elif args.command == "recover":
            recover_stats = recover_corrupted_database(args.corrupted, args.output)
            print(json.dumps(recover_stats.__dict__, sort_keys=True))
        elif args.command == "recompute-clv":
            clv_summary = recompute_settlement_clv(args.db, dry_run=args.dry_run)
            print(json.dumps(clv_summary, sort_keys=True))
        elif args.command == "retention":
            retention_stats = apply_retention_policy(
                args.db, cutoff_days=args.cutoff_days, dry_run=args.dry_run
            )
            print(json.dumps(retention_stats.__dict__, sort_keys=True))
    except (FeedbackError, OSError, sqlite3.Error, ValueError) as exc:
        logger.error("Feedback tool failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
