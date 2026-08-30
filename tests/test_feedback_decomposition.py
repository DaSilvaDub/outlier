"""Compatibility surface after feedback.py decomposition (PR 5).

Callers keep importing ``outlier_scrapers.feedback``. The facade must re-export
the moved names as the same objects owned by the split modules.
"""

from __future__ import annotations

import importlib
import subprocess

from outlier_scrapers import feedback, totals_model


DECOMPOSED_MODULES = (
    "feedback_db",
    "feedback_recovery",
    "feedback_capture",
    "feedback_settlement",
    "calibration",
    "feedback_reporting",
    "feedback_retention",
)


def test_decomposed_feedback_modules_exist() -> None:
    for name in DECOMPOSED_MODULES:
        importlib.import_module(f"outlier_scrapers.{name}")


def test_feedback_facade_reexports_owner_objects() -> None:
    from outlier_scrapers import (
        calibration,
        feedback_capture,
        feedback_db,
        feedback_recovery,
        feedback_reporting,
        feedback_retention,
        feedback_settlement,
    )

    assert feedback.initialize_database is feedback_db.initialize_database
    assert feedback.open_database is feedback_db.open_database
    assert feedback._connect is feedback_db._connect
    assert feedback.FeedbackError is feedback_db.FeedbackError
    assert feedback.SCHEMA_VERSION == feedback_db.SCHEMA_VERSION
    assert feedback.DEFAULT_DB_PATH is feedback_db.DEFAULT_DB_PATH
    assert feedback.MARKET_SNAPSHOT_FIELDS is feedback_db.MARKET_SNAPSHOT_FIELDS
    assert feedback.DECISION_FIELDS is feedback_db.DECISION_FIELDS
    assert feedback.SETTLEMENT_FIELDS is feedback_db.SETTLEMENT_FIELDS

    assert feedback.capture_pack is feedback_capture.capture_pack
    assert feedback.capture_t30_pack is feedback_capture.capture_t30_pack
    assert feedback.import_decisions is feedback_capture.import_decisions
    assert (
        feedback._validate_decision_snapshot_identities
        is feedback_capture._validate_decision_snapshot_identities
    )
    assert feedback.CaptureStats is feedback_capture.CaptureStats
    assert feedback.ImportStats is feedback_capture.ImportStats
    assert feedback._snapshot_from_pack_row is feedback_capture._snapshot_from_pack_row
    assert feedback._stable_id is feedback_db._stable_id
    assert feedback._utc_now is feedback_db._utc_now

    assert feedback.recover_corrupted_database is feedback_recovery.recover_corrupted_database
    assert feedback._run_sqlite_cli_recover is feedback_recovery._run_sqlite_cli_recover
    assert feedback.RecoverStats is feedback_recovery.RecoverStats

    assert feedback.import_settlements is feedback_settlement.import_settlements
    assert feedback.import_settlement_inbox is feedback_settlement.import_settlement_inbox
    assert feedback.recompute_settlement_clv is feedback_settlement.recompute_settlement_clv
    assert feedback.compute_clv_line is feedback_settlement.compute_clv_line
    assert (
        feedback.find_distinct_closing_snapshot
        is feedback_settlement.find_distinct_closing_snapshot
    )
    assert (
        feedback.closing_line_from_movement_export
        is feedback_settlement.closing_line_from_movement_export
    )

    assert feedback.fit_blend_weights is calibration.fit_blend_weights
    assert feedback.fit_stake_calibration_from_db is calibration.fit_stake_calibration_from_db
    assert (
        feedback.learned_multiplier_promotion_signal
        is calibration.learned_multiplier_promotion_signal
    )
    assert (
        feedback._positive_unit_recommendation_rows
        is calibration._positive_unit_recommendation_rows
    )
    assert (
        feedback.LEARNED_MULTIPLIER_PROMOTION_DEFAULTS
        is calibration.LEARNED_MULTIPLIER_PROMOTION_DEFAULTS
    )

    assert feedback.generate_report is feedback_reporting.generate_report
    assert feedback.export_ledgers is feedback_reporting.export_ledgers
    assert feedback.replay_portfolio is feedback_reporting.replay_portfolio
    assert feedback.totals_paired_oos_loss is feedback_reporting.totals_paired_oos_loss
    assert feedback.DEFAULT_REPORT_DIR is feedback_reporting.DEFAULT_REPORT_DIR
    assert feedback._probability_metrics is feedback_reporting._probability_metrics
    assert feedback._model_performance is feedback_reporting._model_performance
    assert feedback._missing_edge_diagnostics is feedback_reporting._missing_edge_diagnostics

    assert feedback.apply_retention_policy is feedback_retention.apply_retention_policy
    assert feedback.RetentionStats is feedback_retention.RetentionStats

    assert feedback.subprocess is subprocess
    assert feedback.totals_model is totals_model
