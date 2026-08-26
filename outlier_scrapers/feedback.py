"""Permanent feedback ledgers, settlement grading, and calibration reports.

The daily pack is a transient research artifact.  This module closes the loop by
copying every pre-ranking opportunity into a durable SQLite database, retaining
the decision attached to the exact snapshot/line, importing settlements, and
producing deterministic calibration and performance reports.

No network or reasoning-provider calls are made here.  Result/closing-line data
arrives through the explicit settlement CSV contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import re
import sqlite3
import subprocess
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from outlier_scrapers import drawdown, paths, probability_blend, stake_calibration
from outlier_scrapers.portfolio import PortfolioPolicy, allocate_portfolio_risk
from outlier_scrapers.team_totals import is_team_total_proposition
from outlier_scrapers.utils import _american_to_decimal, _write_csv

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3")
DEFAULT_REPORT_DIR = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\reports\latest")
SCHEMA_VERSION = 5

MARKET_SNAPSHOT_FIELDS = [
    "snapshot_id",
    "captured_at",
    "sport",
    "event_id",
    "market_id",
    "outcome_id",
    "player_id",
    "selection",
    "line",
    "price",
    "book",
    "market_consensus_prob",
    "independent_model_prob",
    "final_blended_prob",
    "blend_market_weight",
    "blend_model_weight",
    "blend_weight_source",
    "blend_model_version",
    "blend_segment",
    "push_prob",
    "edge",
    "data_quality_flags",
    "data_quality_tier",
    "event_starts_at",
    "hours_before_game",
    "odds_range",
    "time_before_game",
    # Required for the requested segmentation and future weight fitting.
    "market_type",
    "model_prob_source",
    "decimal_price",
    "implied_prob",
    "board",
    "selected",
    "signal_flags",
    "hit_rate_component",
    "insight_component",
    "movement_component",
    "orf_component",
    "projection_feature_hash",
    "projection_quality_flags",
    "pack_path",
    "policy_fingerprint",
    "portfolio_mode",
    "pre_cap_units",
    "portfolio_units",
    "cap_reasons",
]

DECISION_FIELDS = [
    "decision_id",
    "snapshot_id",
    "pipeline_verdict",
    "A_verdict",
    "B_verdict",
    "C_verdict",
    "D_verdict",
    "final_verdict",
    "units",
    "kill_reason",
    "news_override",
    "policy_fingerprint",
    "portfolio_mode",
    "pre_cap_units",
    "portfolio_units",
    "cap_reasons",
]

SETTLEMENT_REQUIRED_FIELDS = [
    "event_id",
    "market_id",
    "actual_result",
    "win_loss_push",
    "closing_line",
    "closing_price",
    "clv_line",
    "clv_price",
    "pnl",
    "would_have_result",
]

# The leading identity fields are additive.  They prevent event_id + market_id
# collisions for alternate lines and opposing outcomes while preserving the
# user-requested settlement columns verbatim.
SETTLEMENT_FIELDS = [
    "settlement_id",
    "decision_id",
    "snapshot_id",
    "outcome_id",
    *SETTLEMENT_REQUIRED_FIELDS,
]

PACK_MEMBERSHIP_FIELDS = [
    "pack_capture_id",
    "snapshot_id",
    "pack_path",
    "pack_timestamp",
    "source",
    "pipeline_verdict",
    "units",
    "selected",
    "actionable",
    "created_at",
]

PROBABILITY_COLUMNS = {
    "market_consensus": "market_consensus_prob",
    "independent_model": "independent_model_prob",
    "final_blended": "final_blended_prob",
}

# ``CREATE TABLE IF NOT EXISTS`` does not evolve an existing SQLite table.  The
# definitions below are deliberately valid for ``ALTER TABLE ... ADD COLUMN``;
# new databases still receive the stricter primary-key/foreign-key declarations
# in ``initialize_database``.
MARKET_SNAPSHOT_COLUMN_DEFINITIONS = {
    "snapshot_id": "TEXT",
    "captured_at": "TEXT NOT NULL DEFAULT ''",
    "sport": "TEXT NOT NULL DEFAULT ''",
    "event_id": "TEXT NOT NULL DEFAULT ''",
    "market_id": "TEXT NOT NULL DEFAULT ''",
    "outcome_id": "TEXT NOT NULL DEFAULT ''",
    "player_id": "TEXT",
    "selection": "TEXT NOT NULL DEFAULT ''",
    "line": "TEXT",
    "price": "REAL",
    "book": "TEXT",
    "market_consensus_prob": "REAL",
    "independent_model_prob": "REAL",
    "final_blended_prob": "REAL",
    "blend_market_weight": "REAL",
    "blend_model_weight": "REAL",
    "blend_weight_source": "TEXT",
    "blend_model_version": "TEXT",
    "blend_segment": "TEXT",
    "push_prob": "REAL",
    "edge": "REAL",
    "data_quality_flags": "TEXT",
    "data_quality_tier": "TEXT",
    "event_starts_at": "TEXT",
    "hours_before_game": "REAL",
    "odds_range": "TEXT",
    "time_before_game": "TEXT",
    "market_type": "TEXT",
    "model_prob_source": "TEXT",
    "decimal_price": "REAL",
    "implied_prob": "REAL",
    "board": "TEXT",
    "selected": "INTEGER NOT NULL DEFAULT 0",
    "signal_flags": "TEXT",
    "hit_rate_component": "REAL",
    "insight_component": "REAL",
    "movement_component": "REAL",
    "orf_component": "REAL",
    "projection_feature_hash": "TEXT",
    "projection_quality_flags": "TEXT",
    "pack_path": "TEXT",
    "policy_fingerprint": "TEXT",
    "portfolio_mode": "TEXT",
    "pre_cap_units": "REAL",
    "portfolio_units": "REAL",
    "cap_reasons": "TEXT",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}

DECISION_COLUMN_DEFINITIONS = {
    "decision_id": "TEXT",
    "snapshot_id": "TEXT",
    "pipeline_verdict": "TEXT",
    "A_verdict": "TEXT",
    "B_verdict": "TEXT",
    "C_verdict": "TEXT",
    "D_verdict": "TEXT",
    "final_verdict": "TEXT",
    "units": "REAL",
    "kill_reason": "TEXT",
    "news_override": "TEXT",
    "policy_fingerprint": "TEXT",
    "portfolio_mode": "TEXT",
    "pre_cap_units": "REAL",
    "portfolio_units": "REAL",
    "cap_reasons": "TEXT",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
}

SETTLEMENT_COLUMN_DEFINITIONS = {
    "settlement_id": "TEXT",
    "decision_id": "TEXT",
    "snapshot_id": "TEXT",
    "outcome_id": "TEXT",
    "event_id": "TEXT NOT NULL DEFAULT ''",
    "market_id": "TEXT NOT NULL DEFAULT ''",
    "actual_result": "TEXT",
    "win_loss_push": "TEXT NOT NULL DEFAULT ''",
    "closing_line": "TEXT",
    "closing_price": "REAL",
    "clv_line": "REAL",
    "clv_price": "REAL",
    "pnl": "REAL",
    "would_have_result": "TEXT",
    "settled_at": "TEXT NOT NULL DEFAULT ''",
}

REQUIRED_TABLE_IDENTITY_COLUMNS = {
    "market_snapshots": {"snapshot_id"},
    "decisions": {"decision_id", "snapshot_id"},
    "settlements": {"settlement_id"},
}

# SQLite does not support bind parameters for identifiers. Keep every schema
# upgrade statement explicit so only these trusted table/column names can reach
# ``execute`` and static security scanners can verify that boundary.
TABLE_COLUMN_ADD_STATEMENTS = {
    "market_snapshots": {
        "snapshot_id": "ALTER TABLE market_snapshots ADD COLUMN snapshot_id TEXT",
        "captured_at": "ALTER TABLE market_snapshots ADD COLUMN captured_at TEXT NOT NULL DEFAULT ''",
        "sport": "ALTER TABLE market_snapshots ADD COLUMN sport TEXT NOT NULL DEFAULT ''",
        "event_id": "ALTER TABLE market_snapshots ADD COLUMN event_id TEXT NOT NULL DEFAULT ''",
        "market_id": "ALTER TABLE market_snapshots ADD COLUMN market_id TEXT NOT NULL DEFAULT ''",
        "outcome_id": "ALTER TABLE market_snapshots ADD COLUMN outcome_id TEXT NOT NULL DEFAULT ''",
        "player_id": "ALTER TABLE market_snapshots ADD COLUMN player_id TEXT",
        "selection": "ALTER TABLE market_snapshots ADD COLUMN selection TEXT NOT NULL DEFAULT ''",
        "line": "ALTER TABLE market_snapshots ADD COLUMN line TEXT",
        "price": "ALTER TABLE market_snapshots ADD COLUMN price REAL",
        "book": "ALTER TABLE market_snapshots ADD COLUMN book TEXT",
        "market_consensus_prob": "ALTER TABLE market_snapshots ADD COLUMN market_consensus_prob REAL",
        "independent_model_prob": "ALTER TABLE market_snapshots ADD COLUMN independent_model_prob REAL",
        "final_blended_prob": "ALTER TABLE market_snapshots ADD COLUMN final_blended_prob REAL",
        "blend_market_weight": "ALTER TABLE market_snapshots ADD COLUMN blend_market_weight REAL",
        "blend_model_weight": "ALTER TABLE market_snapshots ADD COLUMN blend_model_weight REAL",
        "blend_weight_source": "ALTER TABLE market_snapshots ADD COLUMN blend_weight_source TEXT",
        "blend_model_version": "ALTER TABLE market_snapshots ADD COLUMN blend_model_version TEXT",
        "blend_segment": "ALTER TABLE market_snapshots ADD COLUMN blend_segment TEXT",
        "push_prob": "ALTER TABLE market_snapshots ADD COLUMN push_prob REAL",
        "edge": "ALTER TABLE market_snapshots ADD COLUMN edge REAL",
        "data_quality_flags": "ALTER TABLE market_snapshots ADD COLUMN data_quality_flags TEXT",
        "data_quality_tier": "ALTER TABLE market_snapshots ADD COLUMN data_quality_tier TEXT",
        "event_starts_at": "ALTER TABLE market_snapshots ADD COLUMN event_starts_at TEXT",
        "hours_before_game": "ALTER TABLE market_snapshots ADD COLUMN hours_before_game REAL",
        "odds_range": "ALTER TABLE market_snapshots ADD COLUMN odds_range TEXT",
        "time_before_game": "ALTER TABLE market_snapshots ADD COLUMN time_before_game TEXT",
        "market_type": "ALTER TABLE market_snapshots ADD COLUMN market_type TEXT",
        "model_prob_source": "ALTER TABLE market_snapshots ADD COLUMN model_prob_source TEXT",
        "decimal_price": "ALTER TABLE market_snapshots ADD COLUMN decimal_price REAL",
        "implied_prob": "ALTER TABLE market_snapshots ADD COLUMN implied_prob REAL",
        "board": "ALTER TABLE market_snapshots ADD COLUMN board TEXT",
        "selected": "ALTER TABLE market_snapshots ADD COLUMN selected INTEGER NOT NULL DEFAULT 0",
        "signal_flags": "ALTER TABLE market_snapshots ADD COLUMN signal_flags TEXT",
        "hit_rate_component": "ALTER TABLE market_snapshots ADD COLUMN hit_rate_component REAL",
        "insight_component": "ALTER TABLE market_snapshots ADD COLUMN insight_component REAL",
        "movement_component": "ALTER TABLE market_snapshots ADD COLUMN movement_component REAL",
        "orf_component": "ALTER TABLE market_snapshots ADD COLUMN orf_component REAL",
        "projection_feature_hash": "ALTER TABLE market_snapshots ADD COLUMN projection_feature_hash TEXT",
        "projection_quality_flags": "ALTER TABLE market_snapshots ADD COLUMN projection_quality_flags TEXT",
        "pack_path": "ALTER TABLE market_snapshots ADD COLUMN pack_path TEXT",
        "policy_fingerprint": "ALTER TABLE market_snapshots ADD COLUMN policy_fingerprint TEXT",
        "portfolio_mode": "ALTER TABLE market_snapshots ADD COLUMN portfolio_mode TEXT",
        "pre_cap_units": "ALTER TABLE market_snapshots ADD COLUMN pre_cap_units REAL",
        "portfolio_units": "ALTER TABLE market_snapshots ADD COLUMN portfolio_units REAL",
        "cap_reasons": "ALTER TABLE market_snapshots ADD COLUMN cap_reasons TEXT",
        "created_at": "ALTER TABLE market_snapshots ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
    },
    "decisions": {
        "decision_id": "ALTER TABLE decisions ADD COLUMN decision_id TEXT",
        "snapshot_id": "ALTER TABLE decisions ADD COLUMN snapshot_id TEXT",
        "pipeline_verdict": "ALTER TABLE decisions ADD COLUMN pipeline_verdict TEXT",
        "A_verdict": "ALTER TABLE decisions ADD COLUMN A_verdict TEXT",
        "B_verdict": "ALTER TABLE decisions ADD COLUMN B_verdict TEXT",
        "C_verdict": "ALTER TABLE decisions ADD COLUMN C_verdict TEXT",
        "D_verdict": "ALTER TABLE decisions ADD COLUMN D_verdict TEXT",
        "final_verdict": "ALTER TABLE decisions ADD COLUMN final_verdict TEXT",
        "units": "ALTER TABLE decisions ADD COLUMN units REAL",
        "kill_reason": "ALTER TABLE decisions ADD COLUMN kill_reason TEXT",
        "news_override": "ALTER TABLE decisions ADD COLUMN news_override TEXT",
        "policy_fingerprint": "ALTER TABLE decisions ADD COLUMN policy_fingerprint TEXT",
        "portfolio_mode": "ALTER TABLE decisions ADD COLUMN portfolio_mode TEXT",
        "pre_cap_units": "ALTER TABLE decisions ADD COLUMN pre_cap_units REAL",
        "portfolio_units": "ALTER TABLE decisions ADD COLUMN portfolio_units REAL",
        "cap_reasons": "ALTER TABLE decisions ADD COLUMN cap_reasons TEXT",
        "created_at": "ALTER TABLE decisions ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE decisions ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    },
    "settlements": {
        "settlement_id": "ALTER TABLE settlements ADD COLUMN settlement_id TEXT",
        "decision_id": "ALTER TABLE settlements ADD COLUMN decision_id TEXT",
        "snapshot_id": "ALTER TABLE settlements ADD COLUMN snapshot_id TEXT",
        "outcome_id": "ALTER TABLE settlements ADD COLUMN outcome_id TEXT",
        "event_id": "ALTER TABLE settlements ADD COLUMN event_id TEXT NOT NULL DEFAULT ''",
        "market_id": "ALTER TABLE settlements ADD COLUMN market_id TEXT NOT NULL DEFAULT ''",
        "actual_result": "ALTER TABLE settlements ADD COLUMN actual_result TEXT",
        "win_loss_push": "ALTER TABLE settlements ADD COLUMN win_loss_push TEXT NOT NULL DEFAULT ''",
        "closing_line": "ALTER TABLE settlements ADD COLUMN closing_line TEXT",
        "closing_price": "ALTER TABLE settlements ADD COLUMN closing_price REAL",
        "clv_line": "ALTER TABLE settlements ADD COLUMN clv_line REAL",
        "clv_price": "ALTER TABLE settlements ADD COLUMN clv_price REAL",
        "pnl": "ALTER TABLE settlements ADD COLUMN pnl REAL",
        "would_have_result": "ALTER TABLE settlements ADD COLUMN would_have_result TEXT",
        "settled_at": "ALTER TABLE settlements ADD COLUMN settled_at TEXT NOT NULL DEFAULT ''",
    },
}

SELECT_DECISION_BY_ID_SQL = """
    SELECT decision_id, snapshot_id, pipeline_verdict,
           A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
           units, kill_reason, news_override,
           policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons
    FROM decisions
    WHERE decision_id = ?
"""

SETTLEMENT_DECISION_SELECT_SQL = """
    SELECT d.*, s.event_id, s.market_id, s.outcome_id, s.selection, s.line,
           s.decimal_price
    FROM decisions d
    JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
    WHERE d.decision_id = ?
"""

SETTLEMENT_SNAPSHOT_SELECT_SQL = """
    SELECT d.*, s.event_id, s.market_id, s.outcome_id, s.selection, s.line,
           s.decimal_price
    FROM decisions d
    JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
    WHERE d.snapshot_id = ?
"""

SETTLEMENT_MARKET_SELECT_SQL = """
    SELECT d.*, s.event_id, s.market_id, s.outcome_id, s.selection, s.line,
           s.decimal_price
    FROM decisions d
    JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
    WHERE s.event_id = ? AND s.market_id = ?
"""

SETTLEMENT_OUTCOME_SELECT_SQL = """
    SELECT d.*, s.event_id, s.market_id, s.outcome_id, s.selection, s.line,
           s.decimal_price
    FROM decisions d
    JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
    WHERE s.event_id = ? AND s.market_id = ? AND s.outcome_id = ?
"""


class FeedbackError(ValueError):
    """Raised when a ledger row is invalid or cannot be joined safely."""


@dataclass(frozen=True)
class CaptureStats:
    snapshots: int
    decisions: int


@dataclass(frozen=True)
class ImportStats:
    imported: int
    unlinked: int = 0


@dataclass(frozen=True)
class RecoverStats:
    market_snapshots: int
    pack_snapshot_memberships: int
    decisions: int
    settlements: int
    skipped_rows: int
    used_sqlite_cli: bool


@dataclass(frozen=True)
class RetentionStats:
    eligible: int
    slimmed: int
    bytes_before: int
    bytes_after: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, separators=(",", ":"), default=str, ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _float(value: Any, *, field: str = "value") -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FeedbackError(f"{field} must be numeric, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise FeedbackError(f"{field} must be finite, got {value!r}")
    return parsed


def _probability(value: Any, *, field: str) -> float | None:
    parsed = _float(value, field=field)
    if parsed is not None and not 0.0 <= parsed <= 1.0:
        raise FeedbackError(f"{field} must be between 0 and 1, got {parsed}")
    return parsed


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _coalesce(*values: Any) -> Any:
    """Return the first populated value while preserving a real numeric zero."""

    for value in values:
        if value not in (None, ""):
            return value
    return None


def _append_flag(flags: Any, flag: str) -> str:
    current = [token.strip() for token in _text(flags).replace(",", ";").split(";")]
    return ";".join(dict.fromkeys([token for token in current if token] + [flag]))


def _normal_result(value: Any, *, field: str = "win_loss_push") -> str:
    token = _text(value).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "W": "W",
        "WIN": "W",
        "WON": "W",
        "L": "L",
        "LOSS": "L",
        "LOST": "L",
        "P": "PUSH",
        "PUSH": "PUSH",
        "VOID": "PUSH",
    }
    if token not in aliases:
        raise FeedbackError(f"{field} must be W, L, or PUSH, got {value!r}")
    return aliases[token]


def _ensure_table_columns(
    conn: sqlite3.Connection, table: str, definitions: dict[str, str]
) -> None:
    statements = TABLE_COLUMN_ADD_STATEMENTS.get(table)
    if statements is None or statements.keys() != definitions.keys():
        raise FeedbackError(f"No trusted schema upgrade map for {table!r}")
    existing = {row[0] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}
    missing_identity = REQUIRED_TABLE_IDENTITY_COLUMNS[table] - existing
    if missing_identity:
        raise FeedbackError(
            f"Cannot safely migrate {table}: missing identity columns "
            f"{', '.join(sorted(missing_identity))}"
        )
    for column in definitions:
        if column not in existing:
            conn.execute(statements[column])


def _migrate_blend_segments(conn: sqlite3.Connection, prior_schema_version: int) -> None:
    """Backfill v4 segment metadata from frozen pack artifacts when available."""

    if prior_schema_version >= 4:
        return
    rows = conn.execute(
        """
        SELECT snapshot_id, pack_path, price, data_quality_flags,
               data_quality_tier, event_starts_at, hours_before_game,
               odds_range, time_before_game
        FROM market_snapshots
        """
    ).fetchall()
    pack_cache: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        snapshot = dict(row)
        recovered: dict[str, Any] = {}
        pack_path = _text(snapshot.get("pack_path"))
        if pack_path:
            if pack_path not in pack_cache:
                recovered_by_id: dict[str, dict[str, Any]] = {}
                pack_dir = Path(pack_path)
                try:
                    fallback = _pack_fallback_timestamp(pack_dir)
                    for source, pack_row in _load_pack_rows(pack_dir):
                        candidate = _snapshot_from_pack_row(
                            source, pack_row, pack_dir, fallback, pack_dir
                        )
                        recovered_by_id[candidate["snapshot_id"]] = candidate
                except (FeedbackError, OSError):
                    recovered_by_id = {}
                pack_cache[pack_path] = recovered_by_id
            recovered = pack_cache[pack_path].get(_text(snapshot.get("snapshot_id")), {})

        event_starts_at = _coalesce(
            snapshot.get("event_starts_at"), recovered.get("event_starts_at")
        )
        hours_to_game = _coalesce(
            snapshot.get("hours_before_game"), recovered.get("hours_before_game")
        )
        odds = _coalesce(
            snapshot.get("odds_range"),
            recovered.get("odds_range"),
            probability_blend.odds_range(snapshot.get("price")),
        )
        tier = _coalesce(
            snapshot.get("data_quality_tier"),
            recovered.get("data_quality_tier"),
            probability_blend.data_quality_tier(snapshot.get("data_quality_flags")),
        )
        time_bucket = _coalesce(
            snapshot.get("time_before_game"),
            recovered.get("time_before_game"),
            probability_blend.time_before_game_bucket(hours_to_game),
        )
        conn.execute(
            """
            UPDATE market_snapshots
            SET data_quality_tier = ?, event_starts_at = ?, hours_before_game = ?,
                odds_range = ?, time_before_game = ?
            WHERE snapshot_id = ?
            """,
            (tier, event_starts_at, hours_to_game, odds, time_bucket, snapshot["snapshot_id"]),
        )


def initialize_database(db_path: Path = DEFAULT_DB_PATH) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        prior_schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                sport TEXT NOT NULL,
                event_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                outcome_id TEXT NOT NULL,
                player_id TEXT,
                selection TEXT NOT NULL,
                line TEXT,
                price REAL,
                book TEXT,
                market_consensus_prob REAL,
                independent_model_prob REAL,
                final_blended_prob REAL,
                blend_market_weight REAL,
                blend_model_weight REAL,
                blend_weight_source TEXT,
                blend_model_version TEXT,
                blend_segment TEXT,
                push_prob REAL,
                edge REAL,
                data_quality_flags TEXT,
                data_quality_tier TEXT,
                event_starts_at TEXT,
                hours_before_game REAL,
                odds_range TEXT,
                time_before_game TEXT,
                market_type TEXT,
                model_prob_source TEXT,
                decimal_price REAL,
                implied_prob REAL,
                board TEXT,
                selected INTEGER NOT NULL DEFAULT 0,
                signal_flags TEXT,
                hit_rate_component REAL,
                insight_component REAL,
                movement_component REAL,
                orf_component REAL,
                projection_feature_hash TEXT,
                projection_quality_flags TEXT,
                pack_path TEXT,
                policy_fingerprint TEXT,
                portfolio_mode TEXT,
                pre_cap_units REAL,
                portfolio_units REAL,
                cap_reasons TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
                pipeline_verdict TEXT,
                A_verdict TEXT,
                B_verdict TEXT,
                C_verdict TEXT,
                D_verdict TEXT,
                final_verdict TEXT,
                units REAL,
                kill_reason TEXT,
                news_override TEXT,
                policy_fingerprint TEXT,
                portfolio_mode TEXT,
                pre_cap_units REAL,
                portfolio_units REAL,
                cap_reasons TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settlements (
                settlement_id TEXT PRIMARY KEY,
                decision_id TEXT REFERENCES decisions(decision_id),
                snapshot_id TEXT REFERENCES market_snapshots(snapshot_id),
                outcome_id TEXT,
                event_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                actual_result TEXT,
                win_loss_push TEXT NOT NULL,
                closing_line TEXT,
                closing_price REAL,
                clv_line REAL,
                clv_price REAL,
                pnl REAL,
                would_have_result TEXT,
                settled_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pack_snapshot_memberships (
                pack_capture_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
                pack_path TEXT NOT NULL,
                pack_timestamp TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                pipeline_verdict TEXT NOT NULL DEFAULT '',
                units REAL,
                selected INTEGER NOT NULL DEFAULT 0,
                actionable INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (pack_capture_id, snapshot_id)
            );

            """
        )
        conn.execute("BEGIN IMMEDIATE")
        _ensure_table_columns(conn, "market_snapshots", MARKET_SNAPSHOT_COLUMN_DEFINITIONS)
        _ensure_table_columns(conn, "decisions", DECISION_COLUMN_DEFINITIONS)
        _ensure_table_columns(conn, "settlements", SETTLEMENT_COLUMN_DEFINITIONS)
        _migrate_blend_segments(conn, prior_schema_version)
        _validate_decision_snapshot_identities(conn)
        _migrate_probability_semantics(conn, prior_schema_version)
        conn.execute("DROP INDEX IF EXISTS idx_decisions_snapshot")
        _migrate_decision_ids(conn)
        if prior_schema_version < 5:
            conn.execute(
                """
                INSERT OR IGNORE INTO pack_snapshot_memberships (
                    pack_capture_id, snapshot_id, pack_path, pack_timestamp,
                    source, pipeline_verdict, units, selected, actionable, created_at
                )
                SELECT
                    'legacy:' || LOWER(HEX(COALESCE(s.pack_path, ''))),
                    s.snapshot_id,
                    COALESCE(s.pack_path, ''),
                    s.captured_at,
                    '',
                    COALESCE(d.pipeline_verdict, ''),
                    COALESCE(d.units, 0),
                    COALESCE(s.selected, 0),
                    CASE WHEN UPPER(COALESCE(d.pipeline_verdict, '')) = 'PLAY'
                         AND COALESCE(d.units, 0) > 0 THEN 1 ELSE 0 END,
                    COALESCE(s.created_at, '')
                FROM market_snapshots s
                LEFT JOIN decisions d ON d.snapshot_id = s.snapshot_id
                WHERE COALESCE(s.pack_path, '') <> ''
                """
            )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_snapshots_market "
            "ON market_snapshots(event_id, market_id, outcome_id, captured_at)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_close_book "
            "ON market_snapshots(event_id, outcome_id, book, captured_at)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_close_outcome "
            "ON market_snapshots(event_id, outcome_id, captured_at)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_close_selection "
            "ON market_snapshots(event_id, market_id, selection, captured_at)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_segment "
            "ON market_snapshots(sport, market_type, book)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_blend_segment "
            "ON market_snapshots("
            "sport, market_type, odds_range, time_before_game, data_quality_tier"
            ")",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_snapshot ON decisions(snapshot_id)",
            "CREATE INDEX IF NOT EXISTS idx_settlements_market "
            "ON settlements(event_id, market_id, outcome_id)",
            "CREATE INDEX IF NOT EXISTS idx_settlements_decision ON settlements(decision_id)",
            "CREATE INDEX IF NOT EXISTS idx_pack_membership_pack_path "
            "ON pack_snapshot_memberships(pack_path, pack_timestamp)",
        ):
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 5")
    return db_path


def _validate_decision_snapshot_identities(conn: sqlite3.Connection) -> None:
    invalid_decision = conn.execute(
        """
        SELECT snapshot_id
        FROM decisions
        WHERE decision_id IS NULL
           OR TRIM(CAST(decision_id AS TEXT)) = ''
        ORDER BY snapshot_id
        LIMIT 1
        """
    ).fetchone()
    if invalid_decision is not None:
        raise FeedbackError(
            "Cannot safely migrate decisions: blank or missing decision_id "
            f"for snapshot {_text(invalid_decision[0])!r}"
        )

    invalid = conn.execute(
        """
        SELECT d.decision_id
        FROM decisions d
        LEFT JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
        WHERE d.snapshot_id IS NULL
           OR TRIM(CAST(d.snapshot_id AS TEXT)) = ''
           OR s.snapshot_id IS NULL
        ORDER BY d.decision_id
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise FeedbackError(
            "Cannot safely migrate decisions: blank, missing, or unknown snapshot_id "
            f"for decision {_text(invalid[0])!r}"
        )


def _migrate_probability_semantics(conn: sqlite3.Connection, prior_schema_version: int) -> None:
    """Convert schema-v2 totals probabilities from conditional to unconditional P(win)."""

    if prior_schema_version >= 3:
        return
    conn.execute(
        """
        UPDATE market_snapshots
        SET market_consensus_prob = CASE
                WHEN market_consensus_prob IS NULL THEN NULL
                ELSE market_consensus_prob * (1.0 - push_prob)
            END,
            final_blended_prob = CASE
                WHEN final_blended_prob IS NULL THEN NULL
                ELSE final_blended_prob * (1.0 - push_prob)
            END,
            edge = CASE
                WHEN final_blended_prob IS NOT NULL AND decimal_price IS NOT NULL
                THEN final_blended_prob * (1.0 - push_prob) * decimal_price
                     + push_prob - 1.0
                ELSE edge
            END,
            data_quality_flags = CASE
                WHEN INSTR(COALESCE(data_quality_flags, ''),
                           'probability_semantics_v3_migrated') > 0
                THEN data_quality_flags
                WHEN COALESCE(data_quality_flags, '') = ''
                THEN 'probability_semantics_v3_migrated'
                ELSE data_quality_flags || ';probability_semantics_v3_migrated'
            END
        WHERE push_prob > 0.0
          AND push_prob < 1.0
          AND board IN ('GAME_TOTALS', 'TEAM_TOTALS')
          AND INSTR(COALESCE(data_quality_flags, ''),
                    'probability_semantics_v3_migrated') = 0
        """
    )


def _timestamp_rank(value: Any) -> tuple[int, str]:
    text = _text(value)
    if not text:
        return (0, "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return (1, text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (2, parsed.astimezone(timezone.utc).isoformat())


def _timestamp_extreme(values: Iterable[Any], *, latest: bool) -> str:
    candidates = [(_timestamp_rank(value), _text(value)) for value in values if _text(value)]
    if not candidates:
        return ""
    valid = [candidate for candidate in candidates if candidate[0][0] == 2]
    pool = valid or candidates
    chooser = max if latest else min
    return chooser(pool, key=lambda candidate: candidate[0])[1]


def _migrate_decision_ids(conn: sqlite3.Connection) -> None:
    """Normalize legacy decisions to one newest deterministic row per snapshot."""

    fields = [
        "decision_id",
        "snapshot_id",
        "pipeline_verdict",
        "A_verdict",
        "B_verdict",
        "C_verdict",
        "D_verdict",
        "final_verdict",
        "units",
        "kill_reason",
        "news_override",
        "created_at",
        "updated_at",
    ]
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT decision_id, snapshot_id, pipeline_verdict,
               A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
               units, kill_reason, news_override, created_at, updated_at
        FROM decisions
        """
    ):
        grouped[_text(row["snapshot_id"])].append(row)

    for snapshot_id, group in grouped.items():
        new_id = _stable_id("decision", snapshot_id)
        if len(group) == 1 and group[0]["decision_id"] == new_id:
            continue
        authoritative = max(
            group,
            key=lambda row: (
                _timestamp_rank(row["updated_at"]),
                int(row["decision_id"] == new_id),
                _text(row["decision_id"]),
            ),
        )
        merged = dict(authoritative)
        merged["decision_id"] = new_id
        merged["snapshot_id"] = snapshot_id
        merged["created_at"] = _timestamp_extreme(
            (row["created_at"] for row in group), latest=False
        )
        merged["updated_at"] = _timestamp_extreme((row["updated_at"] for row in group), latest=True)
        existing = next((row for row in group if row["decision_id"] == new_id), None)
        if existing is None:
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict,
                    A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                    units, kill_reason, news_override, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [merged[field] for field in fields],
            )
        else:
            conn.execute(
                """
                UPDATE decisions SET
                    snapshot_id = ?, pipeline_verdict = ?,
                    A_verdict = ?, B_verdict = ?, C_verdict = ?, D_verdict = ?,
                    final_verdict = ?, units = ?, kill_reason = ?, news_override = ?,
                    created_at = ?, updated_at = ?
                WHERE decision_id = ?
                """,
                [merged[field] for field in fields[1:]] + [new_id],
            )
        for row in group:
            old_id = row["decision_id"]
            if old_id == new_id:
                continue
            conn.execute(
                "UPDATE settlements SET decision_id = ? WHERE decision_id = ?",
                (new_id, old_id),
            )
            conn.execute("DELETE FROM decisions WHERE decision_id = ?", (old_id,))


def _connect(db_path: Path) -> sqlite3.Connection:
    initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def open_database(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open an initialized connection for a caller-managed transaction."""

    return _connect(Path(db_path))


_RECOVERY_TABLE_ORDER = (
    "market_snapshots",
    "pack_snapshot_memberships",
    "decisions",
    "settlements",
)
_RECOVERY_TABLE_FIELDS = {
    "market_snapshots": [*MARKET_SNAPSHOT_FIELDS, "created_at"],
    "pack_snapshot_memberships": PACK_MEMBERSHIP_FIELDS,
    "decisions": [*DECISION_FIELDS, "created_at", "updated_at"],
    "settlements": [*SETTLEMENT_FIELDS, "settled_at"],
}


def _run_sqlite_cli_recover(corrupted_path: Path, temp_recovered: Path) -> bool:
    """Best-effort ``.recover`` via the sqlite3 CLI, when it is installed.

    ``.recover`` walks every b-tree page it can still read -- including ones
    orphaned by a damaged freelist or schema table -- and emits SQL that
    rebuilds as much of the database as is salvageable. It recovers rows a
    plain ``SELECT`` gives up on. It is optional: when the CLI is missing,
    ``_salvage_rows_directly`` still recovers everything readable before the
    first corrupted page.

    For a large ledger that SQL script can be substantial, so it is piped
    directly from the recovering ``sqlite3`` process into a second ``sqlite3``
    process that writes ``temp_recovered``, rather than buffered as one big
    string in this process.
    """
    # Managed manually rather than via `with Popen(...) as p:` on purpose:
    # that context manager's __exit__ calls p.wait() with no timeout on the
    # way out, so a caught TimeoutExpired would just trade a bounded hang
    # for an unbounded one while unwinding -- never actually reaching the
    # except block below in any reasonable time.
    recover_proc: subprocess.Popen | None = None
    apply_proc: subprocess.Popen | None = None
    try:
        recover_proc = subprocess.Popen(
            ["sqlite3", str(corrupted_path), ".recover"],
            stdout=subprocess.PIPE,
        )
        apply_proc = subprocess.Popen(
            ["sqlite3", str(temp_recovered)],
            stdin=recover_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Close the parent's copy of the read end now that apply_proc has
        # its own: otherwise, if apply_proc exits early, recover_proc has
        # no way to see a SIGPIPE (the parent is still "a reader") and can
        # block writing to a pipe nothing is draining anymore.
        recover_proc.stdout.close()  # type: ignore[union-attr]
        recover_returncode = recover_proc.wait(timeout=300)
        apply_returncode = apply_proc.wait(timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.info("sqlite3 CLI .recover unavailable (%s); falling back to direct salvage.", exc)
        # A caught TimeoutExpired means a process is still running: kill it
        # explicitly (a bounded wait afterward, not the unbounded one a bare
        # .wait() would be) so this doesn't leave an orphaned sqlite3
        # process behind.
        for proc in (recover_proc, apply_proc):
            if proc is None or proc.poll() is not None:
                continue
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return False
    if recover_returncode != 0 or apply_returncode != 0:
        logger.info("sqlite3 CLI .recover produced no usable output; falling back.")
        return False
    return True


def _open_for_salvage(source_path: Path) -> sqlite3.Connection:
    """Open a possibly-corrupted database, applying any pending WAL first.

    A plain read-write connection lets SQLite roll a hot journal or WAL file
    forward the normal way, so rows committed but not yet checkpointed into
    the main file are not silently missed. Only when that fails outright
    (the corruption reaches the header/schema itself) does this fall back to
    an ``immutable`` read-only connection, which skips journal/WAL recovery
    entirely and just reads whatever raw pages remain reachable.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(source_path))
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error:
        if conn is not None:
            conn.close()
    # Percent-encode via as_uri() rather than raw-interpolating the path: a
    # literal '#' or '?' in the filename would otherwise land inside the URI
    # fragment/query instead of the path, and immutable=1 would silently not
    # apply.
    return sqlite3.connect(f"{Path(source_path).resolve().as_uri()}?immutable=1", uri=True)


def _salvage_rows_directly(
    source_path: Path, table: str, fields: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """Read every row sqlite3 will still hand back before hitting corruption.

    ``sqlite3`` raises ``DatabaseError`` once a cursor walks into a damaged
    page; rows already yielded up to that point are real and worth keeping,
    so the table is read one row at a time and the scan stops there instead
    of discarding everything already recovered.
    """
    if table not in _RECOVERY_TABLE_ORDER:
        raise FeedbackError(f"Unsupported recovery table: {table!r}")
    rows: list[dict[str, Any]] = []
    skipped = 0
    conn = _open_for_salvage(source_path)
    conn.row_factory = sqlite3.Row
    try:
        # _open_for_salvage's own sanity check (`SELECT 1`) never touches
        # sqlite_master, so it happily succeeds even when the schema page
        # itself is the corrupted one -- these schema/PRAGMA queries and the
        # initial SELECT's own execute() are where that surfaces instead,
        # and a crash here should mean "this table isn't salvageable", not
        # "abort the whole recovery" for every other table too.
        try:
            existing_tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if table not in existing_tables:
                return rows, skipped
            existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            select_fields = [field for field in fields if field in existing_columns]
            if not select_fields:
                return rows, skipped
            cursor = conn.execute(f"SELECT {', '.join(select_fields)} FROM {table}")
        except sqlite3.Error as exc:
            logger.warning("Could not scan %s (schema/page corruption): %s", table, exc)
            skipped += 1
            return rows, skipped
        while True:
            try:
                row = cursor.fetchone()
            except sqlite3.DatabaseError:
                skipped += 1
                break
            if row is None:
                break
            rows.append(dict(row))
    finally:
        conn.close()
    return rows, skipped


def _insert_recovered_row(
    conn: sqlite3.Connection,
    table: str,
    fields: list[str],
    row: dict[str, Any],
    *,
    extra: dict[str, Any],
) -> bool:
    if table not in _RECOVERY_TABLE_ORDER:
        raise FeedbackError(f"Unsupported recovery table: {table!r}")
    all_fields = [*fields, *extra]
    values = [row.get(field) for field in fields] + list(extra.values())
    try:
        # A plain INSERT (not OR IGNORE) so a NOT NULL/identity violation
        # raises and is logged/counted as skipped, instead of OR IGNORE
        # silently dropping it while this still reports the row as inserted.
        conn.execute(
            f"INSERT INTO {table} ({', '.join(all_fields)}) "
            f"VALUES ({', '.join('?' for _ in all_fields)})",
            values,
        )
        return True
    except sqlite3.IntegrityError as exc:
        logger.warning("Skipping unrecoverable %s row: %s", table, exc)
        return False


def recover_corrupted_database(corrupted_path: Path, output_path: Path) -> RecoverStats:
    """Salvage a corrupted ledger into a fresh, schema-current database.

    Tries the sqlite3 CLI's ``.recover`` first (it survives page-level
    corruption a plain query does not); either way, every row that can still
    be read is copied into a brand-new database created by
    :func:`initialize_database`, so the result carries the current schema and
    indexes rather than whatever partial state the source file was in.
    Rows that fail their NOT NULL/identity constraints on the way in are
    logged and skipped rather than aborting the whole recovery.

    If ``corrupted_path`` was copied from a live WAL-mode database, bring its
    ``-wal`` (and ``-shm``) companion files along next to it under the same
    stem: recovery opens the file with a normal connection first specifically
    so any not-yet-checkpointed commits sitting in the WAL are applied before
    reading, and without those companions that data is invisible here, not
    merely slow to reach.
    """
    corrupted_path = Path(corrupted_path)
    output_path = Path(output_path)
    if not corrupted_path.exists():
        raise FeedbackError(f"Corrupted database does not exist: {corrupted_path}")
    if output_path.exists():
        raise FeedbackError(
            f"Refusing to overwrite an existing database at {output_path}; "
            "recover into a fresh path"
        )

    candidate_temp = corrupted_path.with_name(f"{corrupted_path.name}.recovered.tmp")
    candidate_temp.unlink(missing_ok=True)
    used_sqlite_cli = _run_sqlite_cli_recover(corrupted_path, candidate_temp)
    source_path = candidate_temp if used_sqlite_cli else corrupted_path
    temp_recovered: Path | None = candidate_temp if used_sqlite_cli else None

    salvaged: dict[str, list[dict[str, Any]]] = {}
    skipped_total = 0
    try:
        for table in _RECOVERY_TABLE_ORDER:
            rows, skipped = _salvage_rows_directly(
                source_path, table, _RECOVERY_TABLE_FIELDS[table]
            )
            salvaged[table] = rows
            skipped_total += skipped
    finally:
        if temp_recovered is not None:
            temp_recovered.unlink(missing_ok=True)
        elif candidate_temp.exists():
            candidate_temp.unlink(missing_ok=True)

    initialize_database(output_path)
    conn = sqlite3.connect(output_path)
    conn.row_factory = sqlite3.Row
    inserted = {
        "market_snapshots": 0,
        "pack_snapshot_memberships": 0,
        "decisions": 0,
        "settlements": 0,
    }
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        now = _utc_now()
        for row in salvaged["market_snapshots"]:
            if _insert_recovered_row(
                conn,
                "market_snapshots",
                MARKET_SNAPSHOT_FIELDS,
                row,
                extra={"created_at": row.get("created_at") or now},
            ):
                inserted["market_snapshots"] += 1
        for row in salvaged["pack_snapshot_memberships"]:
            if _insert_recovered_row(
                conn,
                "pack_snapshot_memberships",
                [field for field in PACK_MEMBERSHIP_FIELDS if field != "created_at"],
                row,
                extra={"created_at": row.get("created_at") or now},
            ):
                inserted["pack_snapshot_memberships"] += 1
        for row in salvaged["decisions"]:
            if _insert_recovered_row(
                conn,
                "decisions",
                DECISION_FIELDS,
                row,
                extra={
                    "created_at": row.get("created_at") or now,
                    "updated_at": row.get("updated_at") or now,
                },
            ):
                inserted["decisions"] += 1
        for row in salvaged["settlements"]:
            if _insert_recovered_row(
                conn,
                "settlements",
                SETTLEMENT_FIELDS,
                row,
                extra={"settled_at": row.get("settled_at") or now},
            ):
                inserted["settlements"] += 1
        conn.commit()
    finally:
        conn.close()

    return RecoverStats(
        market_snapshots=inserted["market_snapshots"],
        pack_snapshot_memberships=inserted["pack_snapshot_memberships"],
        decisions=inserted["decisions"],
        settlements=inserted["settlements"],
        skipped_rows=skipped_total
        + sum(len(salvaged[t]) for t in _RECOVERY_TABLE_ORDER)
        - sum(inserted.values()),
        used_sqlite_cli=used_sqlite_cli,
    )


def _read_csv(path: Path, required: Iterable[str] = ()) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = [field for field in required if field not in fieldnames]
        if missing:
            raise FeedbackError(f"{path} is missing required columns: {', '.join(missing)}")
        return list(reader)


def _pack_fallback_timestamp(pack_dir: Path) -> str:
    manifest = pack_dir / "manifest.json"
    if manifest.exists():
        try:
            timestamp = json.loads(manifest.read_text(encoding="utf-8")).get("timestamp")
            if timestamp:
                return str(timestamp)
        except (OSError, json.JSONDecodeError):
            pass
    return _utc_now()


def _selection_side(row: dict[str, Any]) -> str:
    explicit = _text(row.get("best_side")).upper()
    if explicit in {"OVER", "UNDER"}:
        return explicit
    selection = f" {_text(row.get('selection')).upper()} "
    if " UNDER " in selection:
        return "UNDER"
    if " OVER " in selection:
        return "OVER"
    return ""


def _total_representation_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _text(row.get("sport")),
        _text(row.get("event_id")),
        _text(row.get("market_id")),
        _text(row.get("line")),
        _selection_side(row),
    )


# Alt-lane CSVs -> the capture ``source`` recorded on
# ``pack_snapshot_memberships``.  Every lane here is a board of single wagers,
# and the source is the file's own stem so a per-file coverage report can tell
# the MLB board from the WNBA one; the lane *kind* below -- which decides how a
# selection is built -- is what the two share.
#
# The ``*_parlays.csv`` lanes are deliberately absent: a parlay grades only when
# every leg settles, and legs of a cross-game parlay live on different events,
# which ``outlier_scrapers.results`` grades one event at a time.  Capturing them
# as single rows would create decisions that can never settle.
ALT_LANE_SOURCES = {
    "alt_player_props.csv": "alt_player_props",
    "alt_team_totals.csv": "alt_team_totals",
    "mlb_alt_spreads.csv": "mlb_alt_spreads",
    "wnba_alt_spreads.csv": "wnba_alt_spreads",
    "mlb_alt_bankroll_props.csv": "mlb_alt_bankroll_props",
    "wnba_alt_bankroll_props.csv": "wnba_alt_bankroll_props",
}

ALT_LANE_KINDS = {
    "alt_player_props": "alt_player_props",
    "alt_team_totals": "alt_team_totals",
    "mlb_alt_spreads": "alt_spreads",
    "wnba_alt_spreads": "alt_spreads",
    "mlb_alt_bankroll_props": "alt_bankroll_props",
    "wnba_alt_bankroll_props": "alt_bankroll_props",
}

_OVER_UNDER = {"OVER", "UNDER"}
_HOME_AWAY = {"HOME", "AWAY"}


def _alt_side(row: dict[str, Any]) -> str:
    return _text(_coalesce(row.get("position"), row.get("side"), row.get("best_side"))).upper()


def _alt_selection(kind: str, row: dict[str, Any]) -> str:
    """Build a selection string ``results._grade_row`` can actually parse.

    The alt lanes were written for human boards, so they carry ``player`` /
    ``team`` / ``market`` columns instead of the selection grammar the
    settlement collector parses.  Anything that cannot be expressed in that
    grammar returns "" and is skipped at capture rather than stored as a row
    that could only ever be graded by guessing.
    """

    side = _alt_side(row)
    line = _text(row.get("line"))
    team = _text(row.get("team"))
    matchup = _text(row.get("matchup"))

    if kind == "alt_player_props":
        player = _text(row.get("player"))
        market = _text(row.get("market"))
        if not (player and market and line and side in _OVER_UNDER):
            return ""
        return f"{player} - {market} {side} {line}"

    if kind == "alt_team_totals":
        if not (team and line and side in _OVER_UNDER):
            return ""
        return f"{team} Team Total {side} {line}"

    if kind == "alt_spreads":
        if not (matchup and side in _HOME_AWAY):
            return ""
        signed = _text(row.get("signed_line")) or line
        return f"{matchup} Spread {side} {signed}".strip()

    if kind == "alt_bankroll_props":
        market_type = _text(row.get("market_type")).replace("_", "").upper()
        proposition = _text(row.get("proposition") or row.get("market")).upper()
        if market_type == "GAMELINE":
            if proposition == "MONEYLINE" and matchup and side in _HOME_AWAY:
                return f"{matchup} Money Line {side}"
            if proposition == "SPREAD" and matchup and side in _HOME_AWAY:
                signed = _text(row.get("signed_line")) or line
                return f"{matchup} Spread {side} {signed}".strip()
            if proposition == "TOTAL" and matchup and line and side in _OVER_UNDER:
                return f"{matchup} Total O/U {side} {line}"
            return ""
        if market_type == "TEAMPROP":
            # A team-total selection grades against the team's final score, so
            # it may only be built for a proposition that *is* that score.  A
            # non-scoring team prop (team hits, team walks) would otherwise be
            # graded against runs and silently marked wrong.
            sport = _text(row.get("league") or row.get("sport")).upper()
            if not is_team_total_proposition(proposition, sport=sport):
                return ""
            if not (team and line and side in _OVER_UNDER):
                return ""
            return f"{team} Team Total {side} {line}"
    return ""


def _alt_market_type(kind: str, row: dict[str, Any]) -> str:
    explicit = _text(row.get("market_type"))
    if explicit:
        return explicit
    if kind == "alt_player_props":
        return "PLAYER_PROP"
    if kind == "alt_team_totals":
        return "TEAM_PROP"
    return ""


def _normalize_alt_lane_row(source: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """Translate an alt-lane board row into the shape capture already reads."""

    kind = ALT_LANE_KINDS.get(source, source)
    selection = _alt_selection(kind, row)
    if not selection:
        return None
    normalized = dict(row)
    normalized["selection"] = selection
    normalized["sport"] = _text(row.get("sport")) or _text(row.get("league")).upper()
    normalized["market_type"] = _alt_market_type(kind, row)
    normalized["book"] = _text(_coalesce(row.get("book"), row.get("best_book")))
    # The boards spell the American price three different ways; without one the
    # snapshot has no decimal price and a winning row cannot compute its PnL.
    normalized["price"] = _coalesce(
        row.get("price"), row.get("best_price"), row.get("best_odds")
    )
    # ``_decision_seed`` reads the pack's units/actionable contract; the alt
    # boards spell the same two facts differently.
    units = _float(row.get("recommended_units"), field="recommended_units")
    normalized["recommended_units_pre_news"] = row.get("recommended_units", "")
    normalized["actionable"] = "true" if units and units > 0 else "false"
    # An alt ladder emits every qualifying rung.  Only the headline rung is a
    # selection; the rest stay captured but unselected, exactly as non-selected
    # opportunities do, so the board's accuracy is not inflated by its ladder.
    if _text(row.get("is_best_line")):
        normalized["selected"] = "true" if _truthy(row.get("is_best_line")) else "false"
    else:
        normalized["selected"] = "true"
    return normalized


def _load_alt_lane_rows(pack_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for filename, source in ALT_LANE_SOURCES.items():
        for row in _read_csv(pack_dir / filename):
            normalized = _normalize_alt_lane_row(source, row)
            if normalized is None:
                logger.debug("Skipping ungradeable %s row in %s", source, filename)
                continue
            rows.append((source, normalized))
    return rows


def _load_pack_rows(pack_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    opportunities_path = pack_dir / "opportunities.csv"
    candidates_path = pack_dir / "candidates.csv"
    base_source = "opportunities" if opportunities_path.exists() else "candidates"
    base_rows = _read_csv(opportunities_path if opportunities_path.exists() else candidates_path)
    if base_source == "candidates":
        for row in base_rows:
            row["selected"] = "true"
    event_starts_by_id: dict[str, str] = {}
    for row in base_rows:
        event_id = _text(row.get("event_id"))
        event_start = _text(row.get("_event_starts_at") or row.get("event_starts_at"))
        if event_id and event_start:
            event_starts_by_id.setdefault(event_id, event_start)

    specialized: list[tuple[str, dict[str, Any]]] = []
    for filename, source in (
        ("game_totals.csv", "game_totals"),
        ("team_totals.csv", "team_totals"),
    ):
        for row in _read_csv(pack_dir / filename):
            row["selected"] = "true"
            if not _text(row.get("_event_starts_at") or row.get("event_starts_at")):
                row["_event_starts_at"] = event_starts_by_id.get(
                    _text(row.get("event_id")), ""
                )
            specialized.append((source, row))

    for row in _read_csv(pack_dir / "ultimate_alt.csv"):
        row["selected"] = (
            "true" if _text(row.get("shadow_status")).upper() == "QUALIFIED" else "false"
        )
        specialized.append(("ultimate_alt", row))

    specialized_keys = {
        _total_representation_key(row)
        for source, row in specialized
        if source in {"game_totals", "team_totals"}
    }
    output: list[tuple[str, dict[str, Any]]] = []
    for row in base_rows:
        # A specialized totals ledger is the authoritative representation for a
        # selected total.  Keep non-selected raw opportunities for auditability.
        if _truthy(row.get("selected")) and _total_representation_key(row) in specialized_keys:
            continue
        output.append((base_source, row))
    output.extend(specialized)
    # An alt board can re-list a total the specialized totals ledger already
    # owns at the same event/market/line/side.  That is one wager, not two, and
    # counting it twice would inflate the lane it lands in.
    for source, row in _load_alt_lane_rows(pack_dir):
        if _truthy(row.get("selected")) and _total_representation_key(row) in specialized_keys:
            continue
        output.append((source, row))
    return output


def _signal_fields(
    row: dict[str, Any],
) -> tuple[str, float | None, float | None, float | None, float | None]:
    hit = _float(row.get("hit_rate_component"), field="hit_rate_component")
    insight = _float(row.get("insight_component"), field="insight_component")
    movement = _float(row.get("movement_component"), field="movement_component")
    orf = _float(row.get("orf_component"), field="orf_component")
    flags = _text(row.get("signal_flags"))
    return flags, hit, insight, movement, orf


def _snapshot_from_pack_row(
    source: str,
    row: dict[str, Any],
    pack_dir: Path,
    fallback_timestamp: str,
    recorded_pack_path: Path | None = None,
) -> dict[str, Any]:
    is_totals = source in {"game_totals", "team_totals"}
    is_ultimate_alt = source == "ultimate_alt"
    event_id = _text(row.get("event_id"))
    market_id = _text(row.get("market_id"))
    selection = _text(row.get("selection"))
    if not event_id or not market_id or not selection:
        raise FeedbackError(
            f"{source} row needs event_id, market_id, and selection: "
            f"event={event_id!r} market={market_id!r} selection={selection!r}"
        )

    line = _text(row.get("line"))
    outcome_id = _text(row.get("outcome_id") or row.get("totals_id"))
    data_quality_flags = _text(row.get("data_quality_flags") or row.get("quality_flags"))
    if not outcome_id:
        outcome_id = _stable_id("outcome", event_id, market_id, selection, line)
        data_quality_flags = _append_flag(data_quality_flags, "synthetic_outcome_id")

    captured_at = _text(row.get("as_of")) or fallback_timestamp
    event_starts_at = _text(row.get("_event_starts_at") or row.get("event_starts_at"))
    hours_to_game = _float(row.get("hours_before_game"), field="hours_before_game")
    if hours_to_game is None:
        hours_to_game = probability_blend.hours_before_game(captured_at, event_starts_at)
    market_type = _text(row.get("market_type"))
    board = _text(row.get("board"))
    model_prob_source = _text(row.get("model_prob_source"))
    signal_flags, hit, insight, movement, orf = _signal_fields(row)

    if is_totals:
        total_kind = _text(row.get("total_kind")).lower()
        market_type = market_type or ("TEAM_PROP" if total_kind == "team" else "GAMELINE")
        board = board or ("TEAM_TOTALS" if total_kind == "team" else "GAME_TOTALS")
        best_side = _text(row.get("best_side")).upper()
        probability_value = (
            row.get("projected_under_prob")
            if best_side == "UNDER"
            else row.get("projected_over_prob")
        )
        market_consensus = _probability(
            _coalesce(row.get("market_consensus_prob"), probability_value),
            field="market_consensus_prob",
        )
        final_blended = _probability(
            _coalesce(row.get("final_blended_prob"), probability_value),
            field="final_blended_prob",
        )
        model_prob_source = model_prob_source or _text(row.get("devig_source"))
        shadow_status = (
            "QUALIFIED" if _truthy(row.get("shadow_actionable_4pct")) else "REJECTED"
        )
        signal_flags = ";".join(
            filter(
                None,
                (
                    signal_flags or data_quality_flags,
                    f"totals_shadow_4pct:{shadow_status}",
                    (
                        f"totals_shadow_reason:{_text(row.get('shadow_gate_reasons'))}"
                        if row.get("shadow_gate_reasons")
                        else ""
                    ),
                ),
            )
        )
    elif is_ultimate_alt:
        market_consensus = _probability(row.get("estimated_prob"), field="market_consensus_prob")
        final_blended = _probability(row.get("conservative_prob"), field="final_blended_prob")
        model_prob_source = "ultimate_alt_shadow_conservative"
        data_quality_flags = _text(row.get("rejection_reasons"))
        signal_flags = ";".join(
            filter(
                None,
                (
                    f"ultimate_alt:{_text(row.get('alt_type')).upper()}",
                    data_quality_flags,
                ),
            )
        )
    else:
        market_consensus = _probability(
            _coalesce(row.get("market_consensus_prob"), row.get("model_prob")),
            field="market_consensus_prob",
        )
        final_blended = _probability(
            _coalesce(row.get("final_blended_prob"), row.get("model_prob")),
            field="final_blended_prob",
        )

    independent = _probability(row.get("independent_model_prob"), field="independent_model_prob")
    price = _float(_coalesce(row.get("price"), row.get("best_price")), field="price")
    decimal_price = _float(row.get("decimal_price"), field="decimal_price")
    if decimal_price is None and price is not None:
        decimal_price = _american_to_decimal(price)
    implied_prob = _probability(row.get("implied_prob"), field="implied_prob")
    if implied_prob is None and decimal_price:
        implied_prob = 1.0 / decimal_price

    snapshot_id = _stable_id(
        "snapshot",
        captured_at,
        _text(row.get("sport")),
        event_id,
        market_id,
        outcome_id,
        selection,
        line,
        price,
        _text(row.get("book")),
    )
    return {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at,
        "sport": _text(row.get("sport")),
        "event_id": event_id,
        "market_id": market_id,
        "outcome_id": outcome_id,
        "player_id": _text(row.get("player_id")),
        "selection": selection,
        "line": line,
        "price": price,
        "book": _text(row.get("book")),
        "market_consensus_prob": market_consensus,
        "independent_model_prob": independent,
        "final_blended_prob": final_blended,
        "blend_market_weight": _probability(
            row.get("blend_market_weight"), field="blend_market_weight"
        ),
        "blend_model_weight": _probability(
            row.get("blend_model_weight"), field="blend_model_weight"
        ),
        "blend_weight_source": _text(row.get("blend_weight_source")),
        "blend_model_version": _text(row.get("blend_model_version")),
        "blend_segment": _text(row.get("blend_segment")),
        "push_prob": _probability(row.get("push_prob"), field="push_prob"),
        "edge": _float(_coalesce(row.get("edge"), row.get("edge_pct")), field="edge"),
        "data_quality_flags": data_quality_flags,
        "data_quality_tier": _text(row.get("data_quality_tier"))
        or probability_blend.data_quality_tier(
            data_quality_flags, row.get("projection_quality_flags")
        ),
        "event_starts_at": event_starts_at,
        "hours_before_game": hours_to_game,
        "odds_range": _text(row.get("odds_range")) or probability_blend.odds_range(price),
        "time_before_game": _text(row.get("time_before_game"))
        or probability_blend.time_before_game_bucket(hours_to_game),
        "market_type": market_type,
        "model_prob_source": model_prob_source,
        "decimal_price": decimal_price,
        "implied_prob": implied_prob,
        "board": board,
        "selected": 1 if _truthy(row.get("selected")) else 0,
        "signal_flags": signal_flags,
        "hit_rate_component": hit,
        "insight_component": insight,
        "movement_component": movement,
        "orf_component": orf,
        "projection_feature_hash": _text(row.get("projection_feature_hash")),
        "projection_quality_flags": _text(row.get("projection_quality_flags")),
        "pack_path": str((recorded_pack_path or pack_dir).resolve()),
        "policy_fingerprint": _text(row.get("_policy_fingerprint")),
        "portfolio_mode": _text(row.get("_portfolio_mode"))
        or ("shadow" if is_ultimate_alt else ""),
        "pre_cap_units": _float(
            _coalesce(
                row.get("_pre_cap_units"),
                row.get("recommended_units_pre_news") if is_ultimate_alt else None,
            ),
            field="pre_cap_units",
        ),
        "portfolio_units": _float(
            _coalesce(
                row.get("_portfolio_units"),
                row.get("portfolio_shadow_units") if is_ultimate_alt else None,
            ),
            field="portfolio_units",
        ),
        "cap_reasons": _text(
            row.get("_cap_reasons") or (row.get("cap_reasons") if is_ultimate_alt else "")
        ),
    }


def _decision_seed(snapshot: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    units = _float(row.get("recommended_units_pre_news"), field="units") or 0.0
    play = bool(snapshot["selected"]) and _truthy(row.get("actionable")) and units > 0
    decision_id = _stable_id("decision", snapshot["snapshot_id"])
    t30_status = _text(row.get("t30_status")).upper()
    return {
        "decision_id": decision_id,
        "snapshot_id": snapshot["snapshot_id"],
        "pipeline_verdict": "PLAY" if play else "STAND_DOWN",
        "A_verdict": "",
        "B_verdict": "",
        "C_verdict": "",
        "D_verdict": "",
        "final_verdict": "",
        "units": units if play else 0.0,
        "kill_reason": ""
        if play
        else (t30_status or snapshot["data_quality_flags"] or "not_selected_or_actionable"),
        "news_override": t30_status,
        "policy_fingerprint": snapshot.get("policy_fingerprint", ""),
        "portfolio_mode": snapshot.get("portfolio_mode", ""),
        "pre_cap_units": snapshot.get("pre_cap_units"),
        "portfolio_units": snapshot.get("portfolio_units"),
        "cap_reasons": snapshot.get("cap_reasons", ""),
    }


def capture_pack(
    pack_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    recorded_pack_path: Path | None = None,
    connection: sqlite3.Connection | None = None,
    source_filename: str | None = None,
    snapshot_namespace: str = "",
    decision_filename: str = "decisions.csv",
) -> CaptureStats:
    """Persist a dated pack's opportunity snapshots and seed pipeline decisions.

    Re-capturing an identical source timestamp is idempotent.  A new line, price,
    source timestamp, side, or alternate outcome creates a new snapshot.
    """

    pack_dir = Path(pack_dir)
    if not pack_dir.exists():
        raise FeedbackError(f"Pack directory does not exist: {pack_dir}")

    fallback_timestamp = _pack_fallback_timestamp(pack_dir)
    recorded_path = str((recorded_pack_path or pack_dir).resolve())
    if source_filename:
        source_path = pack_dir / source_filename
        if not source_path.exists():
            raise FeedbackError(f"Pack source does not exist: {source_path}")
        source_rows = []
        for row in _read_csv(source_path):
            row["selected"] = "true"
            source_rows.append(("candidates", row))
    else:
        source_rows = _load_pack_rows(pack_dir)
    pack_timestamp = fallback_timestamp
    if not (pack_dir / "manifest.json").exists():
        pack_timestamp = _timestamp_extreme(
            (row.get("as_of") for _, row in source_rows), latest=True
        ) or fallback_timestamp
    pack_capture_id = _stable_id("pack-capture", recorded_path, pack_timestamp)
    captured_rows: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    snapshots: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for source, row in source_rows:
        snapshot = _snapshot_from_pack_row(
            source, row, pack_dir, fallback_timestamp, recorded_pack_path
        )
        if snapshot_namespace:
            snapshot["snapshot_id"] = _stable_id(
                snapshot_namespace, snapshot["snapshot_id"]
            )
        snapshots.append(snapshot)
        decision = _decision_seed(snapshot, row)
        decisions.append(decision)
        captured_rows.append((source, row, snapshot, decision))

    now = _utc_now()
    connection_context = (
        nullcontext(connection) if connection is not None else _connect(Path(db_path))
    )
    with connection_context as conn:
        if conn is None:
            raise FeedbackError("capture_pack requires a valid SQLite connection")
        for snapshot in snapshots:
            values = [snapshot[field] for field in MARKET_SNAPSHOT_FIELDS]
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    player_id, selection, line, price, book, market_consensus_prob,
                    independent_model_prob, final_blended_prob, blend_market_weight,
                    blend_model_weight, blend_weight_source, blend_model_version,
                    blend_segment, push_prob, edge, data_quality_flags,
                    data_quality_tier, event_starts_at, hours_before_game, odds_range,
                    time_before_game, market_type, model_prob_source, decimal_price,
                    implied_prob, board, selected, signal_flags, hit_rate_component,
                    insight_component, movement_component, orf_component,
                    projection_feature_hash, projection_quality_flags, pack_path,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    market_consensus_prob = COALESCE(
                        excluded.market_consensus_prob, market_snapshots.market_consensus_prob
                    ),
                    independent_model_prob = COALESCE(
                        excluded.independent_model_prob, market_snapshots.independent_model_prob
                    ),
                    final_blended_prob = COALESCE(
                        excluded.final_blended_prob, market_snapshots.final_blended_prob
                    ),
                    blend_market_weight = excluded.blend_market_weight,
                    blend_model_weight = excluded.blend_model_weight,
                    blend_weight_source = excluded.blend_weight_source,
                    blend_model_version = excluded.blend_model_version,
                    blend_segment = excluded.blend_segment,
                    edge = excluded.edge,
                    data_quality_flags = excluded.data_quality_flags,
                    data_quality_tier = excluded.data_quality_tier,
                    event_starts_at = excluded.event_starts_at,
                    hours_before_game = excluded.hours_before_game,
                    odds_range = excluded.odds_range,
                    time_before_game = excluded.time_before_game,
                    market_type = excluded.market_type,
                    model_prob_source = excluded.model_prob_source,
                    decimal_price = excluded.decimal_price,
                    implied_prob = excluded.implied_prob,
                    board = excluded.board,
                    selected = excluded.selected,
                    policy_fingerprint = excluded.policy_fingerprint,
                    portfolio_mode = excluded.portfolio_mode,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units,
                    cap_reasons = excluded.cap_reasons,
                    push_prob = COALESCE(excluded.push_prob, market_snapshots.push_prob),
                    signal_flags = excluded.signal_flags,
                    hit_rate_component = excluded.hit_rate_component,
                    insight_component = excluded.insight_component,
                    movement_component = excluded.movement_component,
                    orf_component = excluded.orf_component,
                    projection_feature_hash = excluded.projection_feature_hash,
                    projection_quality_flags = excluded.projection_quality_flags
                WHERE NOT EXISTS (
                    SELECT 1 FROM decisions
                    WHERE decisions.snapshot_id = market_snapshots.snapshot_id
                      AND COALESCE(decisions.final_verdict, '') <> ''
                )
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = market_snapshots.snapshot_id
                )
                """,
                [*values, now],
            )

        for decision in decisions:
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict,
                    A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                    units, kill_reason, news_override,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    pipeline_verdict = excluded.pipeline_verdict,
                    units = CASE
                        WHEN COALESCE(decisions.final_verdict, '') = '' THEN excluded.units
                        ELSE decisions.units
                    END,
                    kill_reason = CASE
                        WHEN COALESCE(decisions.final_verdict, '') = '' THEN excluded.kill_reason
                        ELSE decisions.kill_reason
                    END,
                    policy_fingerprint = excluded.policy_fingerprint,
                    portfolio_mode = excluded.portfolio_mode,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units,
                    cap_reasons = excluded.cap_reasons,
                    updated_at = excluded.updated_at
                WHERE COALESCE(decisions.final_verdict, '') = ''
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = decisions.snapshot_id
                )
                """,
                [decision[field] for field in DECISION_FIELDS] + [now, now],
            )

        for source, row, snapshot, decision in captured_rows:
            conn.execute(
                """
                INSERT INTO pack_snapshot_memberships (
                    pack_capture_id, snapshot_id, pack_path, pack_timestamp,
                    source, pipeline_verdict, units, selected, actionable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pack_capture_id, snapshot_id) DO NOTHING
                """,
                (
                    pack_capture_id,
                    snapshot["snapshot_id"],
                    snapshot["pack_path"],
                    pack_timestamp,
                    source,
                    decision["pipeline_verdict"],
                    decision["units"],
                    snapshot["selected"],
                    1 if _truthy(row.get("actionable")) else 0,
                    now,
                ),
            )

        decision_ids = [decision["decision_id"] for decision in decisions]
        current: list[dict[str, Any]] = []
        for decision_id in sorted(set(decision_ids)):
            row = conn.execute(SELECT_DECISION_BY_ID_SQL, (decision_id,)).fetchone()
            if row is not None:
                current.append(dict(row))

    _write_csv(pack_dir / decision_filename, DECISION_FIELDS, current)
    return CaptureStats(snapshots=len(snapshots), decisions=len(current))


def capture_t30_pack(
    pack_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    recorded_pack_path: Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> CaptureStats:
    """Persist only the dedicated T-30 output under a separate snapshot namespace."""

    return capture_pack(
        pack_dir,
        db_path,
        recorded_pack_path=recorded_pack_path,
        connection=connection,
        source_filename="t30_reprice.csv",
        snapshot_namespace="t30",
        decision_filename="t30_decisions.csv",
    )


def import_decisions(input_path: Path, db_path: Path = DEFAULT_DB_PATH) -> ImportStats:
    rows = _read_csv(Path(input_path), DECISION_FIELDS)
    now = _utc_now()
    with _connect(Path(db_path)) as conn:
        for row_number, row in enumerate(rows, start=2):
            snapshot_id = _text(row.get("snapshot_id"))
            snapshot = conn.execute(
                "SELECT snapshot_id FROM market_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None:
                raise FeedbackError(
                    f"{input_path}:{row_number} references unknown snapshot_id {snapshot_id!r}"
                )
            expected_decision_id = _stable_id("decision", snapshot_id)
            supplied_decision_id = _text(row.get("decision_id"))
            if supplied_decision_id and supplied_decision_id != expected_decision_id:
                raise FeedbackError(
                    f"{input_path}:{row_number} decision_id {supplied_decision_id!r} does not "
                    f"match snapshot_id {snapshot_id!r} ({expected_decision_id!r})"
                )
            decision_id = expected_decision_id
            values: dict[str, Any] = {
                field: (
                    _text(row.get(field)).upper() if "verdict" in field else _text(row.get(field))
                )
                for field in DECISION_FIELDS
            }
            values["decision_id"] = decision_id
            values["snapshot_id"] = snapshot_id
            values["units"] = _float(row.get("units"), field="units") or 0.0
            existing = conn.execute(
                SELECT_DECISION_BY_ID_SQL,
                (decision_id,),
            ).fetchone()
            settled = conn.execute(
                "SELECT 1 FROM settlements WHERE snapshot_id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            if existing is not None and (
                _text(existing["final_verdict"]) != "" or settled is not None
            ):
                changed = any(
                    (
                        float(existing[field] or 0.0) != float(values[field] or 0.0)
                        if field == "units"
                        else _text(existing[field]) != _text(values[field])
                    )
                    for field in DECISION_FIELDS
                )
                if changed:
                    raise FeedbackError(
                        f"{input_path}:{row_number} cannot change finalized or settled "
                        f"decision {decision_id!r}"
                    )
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict,
                    A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                    units, kill_reason, news_override,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    pipeline_verdict = excluded.pipeline_verdict,
                    A_verdict = excluded.A_verdict,
                    B_verdict = excluded.B_verdict,
                    C_verdict = excluded.C_verdict,
                    D_verdict = excluded.D_verdict,
                    final_verdict = excluded.final_verdict,
                    units = excluded.units,
                    kill_reason = excluded.kill_reason,
                    news_override = excluded.news_override,
                    policy_fingerprint = excluded.policy_fingerprint,
                    portfolio_mode = excluded.portfolio_mode,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units,
                    cap_reasons = excluded.cap_reasons,
                    updated_at = excluded.updated_at
                WHERE COALESCE(decisions.final_verdict, '') = ''
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = decisions.snapshot_id
                )
                """,
                [values[field] for field in DECISION_FIELDS] + [now, now],
            )
    return ImportStats(imported=len(rows))


def compute_clv_line(selection: str, taken_line: Any, closing_line: Any) -> float | None:
    """Return positive line CLV when the selected side beat the closing number."""

    taken = _float(taken_line, field="taken_line")
    closing = _float(closing_line, field="closing_line")
    if taken is None or closing is None:
        return None
    selection_upper = selection.upper()
    if " UNDER " in f" {selection_upper} ":
        return taken - closing
    if " OVER " in f" {selection_upper} ":
        return closing - taken
    # Spread convention: +3 taken vs +2 close, or -2.5 taken vs -3 close,
    # is positive CLV for the selected team.
    return taken - closing


def compute_clv_price(taken_decimal: Any, closing_price: Any) -> float | None:
    taken = _float(taken_decimal, field="taken_decimal")
    closing_decimal = _american_to_decimal(closing_price)
    if taken is None or closing_decimal is None or taken <= 1.0 or closing_decimal <= 1.0:
        return None
    return taken / closing_decimal - 1.0


def find_distinct_closing_snapshot(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    outcome_id: str,
    market_id: str,
    selection: str,
    book: str,
    exclude_snapshot_id: str,
    after_captured_at: str,
) -> tuple[Any, Any]:
    """Best available *distinct, later* pre-start snapshot for CLV.

    Prefers same book + outcome_id, falls back to any book on the same
    event/outcome, then market_id + selection. ``exclude_snapshot_id`` is
    always excluded: a single capture is not its own close, and returning it
    as one silently manufactures zero CLV on every row that was only ever
    captured once. Excluding the taken snapshot is not enough on its own,
    though: without also requiring ``captured_at > after_captured_at``, an
    outcome whose *only* other history is an *older* quote would have that
    stale, pre-take price returned as the "close" -- fabricating CLV from a
    snapshot that isn't a close at all, just an earlier one. A close has to
    be a later observation, not merely a different one.
    """
    params_tail = (exclude_snapshot_id, after_captured_at)
    close = conn.execute(
        """
        SELECT line, price FROM market_snapshots
        WHERE event_id = ? AND outcome_id = ? AND book = ?
          AND snapshot_id != ? AND captured_at > ?
          AND captured_at <= event_starts_at
        ORDER BY captured_at DESC LIMIT 1
        """,
        (event_id, outcome_id, book, *params_tail),
    ).fetchone()
    if close:
        return close["line"], close["price"]

    close = conn.execute(
        """
        SELECT line, price FROM market_snapshots
        WHERE event_id = ? AND outcome_id = ?
          AND snapshot_id != ? AND captured_at > ?
          AND captured_at <= event_starts_at
        ORDER BY captured_at DESC LIMIT 1
        """,
        (event_id, outcome_id, *params_tail),
    ).fetchone()
    if close:
        return close["line"], close["price"]

    if market_id and selection:
        close = conn.execute(
            """
            SELECT line, price FROM market_snapshots
            WHERE event_id = ? AND market_id = ? AND selection = ?
              AND snapshot_id != ? AND captured_at > ?
              AND captured_at <= event_starts_at
            ORDER BY captured_at DESC LIMIT 1
            """,
            (event_id, market_id, selection, *params_tail),
        ).fetchone()
        if close:
            return close["line"], close["price"]
    return None, None


def closing_line_from_movement_export(
    *,
    sport: str,
    event_id: str,
    market_id: str,
    outcome_id: str,
    selection: str,
    event_starts_at: str,
    _export_cache: dict[str, list[tuple[datetime, dict, dict]]] | None = None,
) -> tuple[float | None, float | None]:
    """Best-effort closing line/price from the current line-movement export.

    The line-movement feed only keeps a single ``latest`` snapshot per league
    (no dated history), so this is only a genuine close when the export was
    generated at or after the event started; an earlier export is just
    another pregame quote and using it would repeat the zero-CLV bug it is
    meant to fix.
    """
    if not sport or not event_id or not market_id:
        return None, None
    starts = _parse_utc(event_starts_at)
    if starts is None:
        return None, None
    cache = _export_cache if _export_cache is not None else {}
    if sport not in cache:
        exports: list[tuple[datetime, dict, dict]] = []
        league_paths = paths.league_paths(sport)
        for filename in (f"{sport.lower()}_line_movement_latest.json", f"{sport.lower()}_games_line_movement_latest.json"):
            export_path = league_paths.normalized / filename
            if not export_path.exists():
                continue
            try:
                payload = json.loads(export_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            generated = _parse_utc(payload.get("generated_at"))
            if generated is None:
                continue
            by_outcome: dict[tuple[str, str, str], dict[str, Any]] = {}
            by_side: dict[tuple[str, str, str], dict[str, Any]] = {}
            for record in payload.get("records") or []:
                if not isinstance(record, dict):
                    continue
                event_key, market_key = _text(record.get("event_id")), _text(record.get("market_id"))
                outcome_key = _text(record.get("outcome_id"))
                if outcome_key:
                    by_outcome.setdefault((event_key, market_key, outcome_key), record)
                else:
                    side_key = _text(record.get("side")).upper()
                    if side_key:
                        by_side.setdefault((event_key, market_key, side_key), record)
            exports.append((generated, by_outcome, by_side))
        cache[sport] = exports
    side = _selection_side({"selection": selection})
    for generated, by_outcome, by_side in cache[sport]:
        if generated < starts:
            continue
        record = by_outcome.get((event_id, market_id, outcome_id)) if outcome_id else None
        if record is None and side:
            record = by_side.get((event_id, market_id, side))
        if record is None:
            continue
        try:
            line = _float(record.get("current_line"), field="current_line")
        except FeedbackError:
            line = None
        try:
            price = _float(record.get("current_odds"), field="current_odds")
        except FeedbackError:
            price = None
        if line is not None or price is not None:
            return line, price
    return None, None


def _find_distinct_closes_for_settlements(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> dict[str, tuple[Any, Any]]:
    """Resolve all local closes with three indexed joins instead of N*3 selects."""
    if not rows:
        return {}
    conn.execute("DROP TABLE IF EXISTS temp.clv_recompute_targets")
    conn.execute("CREATE TEMP TABLE clv_recompute_targets (settlement_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, event_id TEXT NOT NULL, outcome_id TEXT NOT NULL, market_id TEXT NOT NULL, selection TEXT NOT NULL, book TEXT NOT NULL, captured_at TEXT NOT NULL)")
    try:
        conn.executemany("INSERT INTO clv_recompute_targets VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(_text(r["settlement_id"]), _text(r["snapshot_id"]), _text(r["event_id"]), _text(r["outcome_id"]), _text(r["market_id"]), _text(r["selection"]), _text(r["book"]), _text(r["captured_at"])) for r in rows])
        closes = conn.execute(
            """
            WITH candidates AS (
                SELECT b.settlement_id, c.line, c.price, c.captured_at, 1 AS priority
                FROM clv_recompute_targets b JOIN market_snapshots c
                  ON c.event_id=b.event_id AND c.outcome_id=b.outcome_id AND c.book=b.book
                 AND c.captured_at>b.captured_at AND c.captured_at<=c.event_starts_at AND c.snapshot_id!=b.snapshot_id
                UNION ALL
                SELECT b.settlement_id, c.line, c.price, c.captured_at, 2 AS priority
                FROM clv_recompute_targets b JOIN market_snapshots c
                  ON c.event_id=b.event_id AND c.outcome_id=b.outcome_id
                 AND c.captured_at>b.captured_at AND c.captured_at<=c.event_starts_at AND c.snapshot_id!=b.snapshot_id
                UNION ALL
                SELECT b.settlement_id, c.line, c.price, c.captured_at, 3 AS priority
                FROM clv_recompute_targets b JOIN market_snapshots c
                  ON c.event_id=b.event_id AND c.market_id=b.market_id AND c.selection=b.selection
                 AND b.market_id!='' AND b.selection!='' AND c.captured_at>b.captured_at
                 AND c.captured_at<=c.event_starts_at AND c.snapshot_id!=b.snapshot_id
            ), ranked AS (
                SELECT settlement_id, line, price, ROW_NUMBER() OVER (PARTITION BY settlement_id ORDER BY priority, captured_at DESC) AS rank FROM candidates
            ) SELECT settlement_id, line, price FROM ranked WHERE rank=1
            """
        ).fetchall()
        return {_text(row[0]): (row[1], row[2]) for row in closes}
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.clv_recompute_targets")


def recompute_settlement_clv(
    db_path: Path = DEFAULT_DB_PATH, *, dry_run: bool = False
) -> dict[str, int]:
    """Recompute CLV for settlements whose recorded close was actually the take.

    Historically ``_latest_local_close`` picked the *taken* snapshot as its
    own close whenever no second observation had ever been captured for that
    outcome, so ``clv_line``/``clv_price`` read as a false, precise 0.0
    instead of "unknown". A settlement is only touched when its stored
    ``closing_line``/``closing_price`` exactly match the matched snapshot's
    own ``line``/``price`` -- that match is the fingerprint the bug leaves
    behind. Each flagged row is re-resolved against a genuinely distinct
    local snapshot or, failing that, the line-movement export; if neither
    yields a real close the row is cleared to unknown rather than left wrong.
    """
    summary = {"inspected": 0, "corrected": 0, "cleared": 0, "unchanged": 0}
    conn = _connect(Path(db_path))
    try:
        rows = conn.execute(
            """
            SELECT t.settlement_id, t.snapshot_id, t.outcome_id,
                   t.closing_line, t.closing_price,
                   s.event_id, s.market_id, s.selection, s.line, s.price,
                   s.decimal_price, s.book, s.sport, s.event_starts_at,
                   s.captured_at
            FROM settlements t
            JOIN market_snapshots s ON s.snapshot_id = t.snapshot_id
            """
        ).fetchall()
        bogus_rows = []
        for row in rows:
            summary["inspected"] += 1
            bogus = (
                _text(row["closing_line"]) != ""
                and _text(row["closing_line"]) == _text(row["line"])
                and row["closing_price"] == row["price"]
            )
            if not bogus:
                summary["unchanged"] += 1
                continue
            bogus_rows.append(row)
        local_closes = _find_distinct_closes_for_settlements(conn, bogus_rows)
        export_cache: dict[str, list[tuple[datetime, dict, dict]]] = {}
        updates = []
        for row in bogus_rows:
            new_line, new_price = local_closes.get(_text(row["settlement_id"]), (None, None))
            if new_line is None and new_price is None:
                new_line, new_price = closing_line_from_movement_export(
                    sport=_text(row["sport"]),
                    event_id=_text(row["event_id"]),
                    market_id=_text(row["market_id"]),
                    outcome_id=_text(row["outcome_id"]),
                    selection=_text(row["selection"]),
                    event_starts_at=_text(row["event_starts_at"]),
                    _export_cache=export_cache,
                )

            if new_line is None and new_price is None:
                summary["cleared"] += 1
                closing_line, closing_price, clv_line, clv_price = None, None, None, None
            else:
                summary["corrected"] += 1
                closing_line, closing_price = new_line, new_price
                clv_line = compute_clv_line(_text(row["selection"]), row["line"], closing_line)
                clv_price = compute_clv_price(row["decimal_price"], closing_price)

            updates.append((closing_line, closing_price, clv_line, clv_price, row["settlement_id"]))
        if not dry_run:
            conn.executemany("UPDATE settlements SET closing_line=?, closing_price=?, clv_line=?, clv_price=? WHERE settlement_id=?", updates)
            conn.commit()
    finally:
        conn.close()
    return summary


def _is_play(final_verdict: Any, pipeline_verdict: Any, units: Any) -> bool:
    verdict = (_text(final_verdict) or _text(pipeline_verdict)).upper()
    return verdict in {"PLAY", "BET"} and (_float(units, field="units") or 0.0) > 0


def _computed_pnl(result: str, units: float, decimal_price: float | None, *, play: bool) -> float:
    if not play or units <= 0:
        return 0.0
    if result == "L":
        return -units
    if result == "PUSH":
        return 0.0
    if decimal_price is None:
        raise FeedbackError("pnl is blank and the matched snapshot has no decimal_price")
    return units * (decimal_price - 1.0)


def import_settlements(
    db_path_or_conn: Path | str | sqlite3.Connection,
    settlement_data: list[dict[str, Any]],
) -> dict[str, int]:
    now = _utc_now()
    summary = {
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 0,
    }
    connection_context = (
        nullcontext(db_path_or_conn)
        if isinstance(db_path_or_conn, sqlite3.Connection)
        else _connect(Path(db_path_or_conn))
    )
    with connection_context as conn:
        for row in settlement_data:
            result = _normal_result(row.get("win_loss_push") or "")

            decision_id = _text(row.get("decision_id"))
            snapshot_id = _text(row.get("snapshot_id"))
            outcome_id = _text(row.get("outcome_id"))

            sport = _text(row.get("sport"))
            event_id = _text(row.get("event_id"))
            market_id = _text(row.get("market_id")) or _text(row.get("market_family"))
            player_id = _text(row.get("player_id")) or _text(row.get("subject_id"))
            line = _text(row.get("line"))
            book = _text(row.get("book"))

            # Robust matching logic
            matches = []
            if decision_id:
                matches = conn.execute(SETTLEMENT_DECISION_SELECT_SQL, (decision_id,)).fetchall()
            elif snapshot_id:
                matches = conn.execute(SETTLEMENT_SNAPSHOT_SELECT_SQL, (snapshot_id,)).fetchall()
            else:
                # Match by durable identity
                query = """
                    SELECT d.*, s.event_id, s.market_id, s.outcome_id, s.selection, s.line,
                           s.decimal_price, s.sport, s.player_id, s.book
                    FROM decisions d
                    JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
                    WHERE COALESCE(s.sport, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.sport, '') END
                      AND s.event_id = ? AND s.market_id = ? 
                      AND COALESCE(s.outcome_id, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.outcome_id, '') END
                      AND COALESCE(s.player_id, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.player_id, '') END
                      AND COALESCE(s.line, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.line, '') END
                      AND COALESCE(s.book, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.book, '') END
                """
                matches = conn.execute(
                    query,
                    (
                        sport,
                        sport,
                        event_id,
                        market_id,
                        outcome_id,
                        outcome_id,
                        player_id,
                        player_id,
                        line,
                        line,
                        book,
                        book,
                    ),
                ).fetchall()

            if not matches:
                summary["unmatched_count"] += 1
                continue
            elif len(matches) > 1:
                summary["ambiguous_count"] += 1
                continue

            matched = matches[0]

            matched_decision_id = _text(matched["decision_id"])
            matched_snapshot_id = _text(matched["snapshot_id"])
            matched_outcome_id = _text(matched["outcome_id"])
            matched_event_id = _text(matched["event_id"])
            matched_market_id = _text(matched["market_id"])

            # Validate that provided row identity does not contradict matched identity
            contradicts = False
            if event_id and event_id != matched_event_id:
                contradicts = True
            if market_id and market_id != matched_market_id:
                contradicts = True
            if outcome_id and outcome_id != matched_outcome_id:
                contradicts = True

            if contradicts:
                summary["unmatched_count"] += 1
                continue

            settlement_id = _text(row.get("settlement_id")) or _stable_id(
                "settlement",
                matched_decision_id
                or matched_snapshot_id
                or (matched_event_id, matched_market_id, matched_outcome_id),
            )

            # Idempotent re-import check
            existing = conn.execute(
                "SELECT 1 FROM settlements WHERE settlement_id = ?", (settlement_id,)
            ).fetchone()
            if existing:
                summary["duplicate_count"] += 1
                continue

            clv_line = _float(row.get("clv_line"), field="clv_line")
            clv_price = _float(row.get("clv_price"), field="clv_price")
            pnl = _float(row.get("pnl"), field="pnl")
            closing_line = _text(row.get("closing_line"))
            closing_price = _float(row.get("closing_price"), field="closing_price")

            if clv_line is None:
                clv_line = compute_clv_line(
                    _text(matched["selection"]), matched["line"], closing_line
                )
            if clv_price is None:
                clv_price = compute_clv_price(matched["decimal_price"], closing_price)
            if pnl is None:
                units = _float(matched["units"], field="units") or 0.0
                pnl = _computed_pnl(
                    result,
                    units,
                    _float(matched["decimal_price"], field="decimal_price"),
                    play=_is_play(matched["final_verdict"], matched["pipeline_verdict"], units),
                )

            would_have = _normal_result(
                row.get("would_have_result") or result, field="would_have_result"
            )

            conn.execute(
                """
                INSERT INTO settlements (
                    settlement_id, decision_id, snapshot_id, outcome_id,
                    event_id, market_id, actual_result, win_loss_push,
                    closing_line, closing_price, clv_line, clv_price, pnl,
                    would_have_result, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    settlement_id,
                    matched_decision_id or None,
                    matched_snapshot_id or None,
                    matched_outcome_id,
                    matched_event_id,
                    matched_market_id,
                    _text(row.get("actual_result")),
                    result,
                    closing_line,
                    closing_price,
                    clv_line,
                    clv_price,
                    pnl,
                    would_have,
                    now,
                ),
            )
            summary["updated_count"] += 1

    return summary


def import_settlement_file(
    input_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """Read and import one settlement CSV using the public CLI contract."""
    rows = _read_csv(Path(input_path), required=("win_loss_push",))
    return import_settlements(db_path, rows)


def import_settlement_inbox(
    inbox_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Import pending settlement CSVs, archiving only fully matched files.

    Ambiguous or unmatched files stay in the inbox so an operator can repair
    identity fields without losing the original result feed.
    """
    inbox = Path(inbox_dir)
    archive = Path(archive_dir) if archive_dir else inbox.parent / "processed"
    summary: dict[str, Any] = {
        "file_count": 0,
        "processed_count": 0,
        "retained_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 0,
    }
    if not inbox.exists():
        return summary
    for input_path in sorted(inbox.glob("*.csv")):
        summary["file_count"] += 1
        stats = import_settlement_file(input_path, db_path)
        for key in ("unmatched_count", "ambiguous_count", "duplicate_count", "updated_count"):
            summary[key] += stats[key]
        if stats["unmatched_count"] or stats["ambiguous_count"]:
            summary["retained_count"] += 1
            continue
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / input_path.name
        if destination.exists():
            destination = (
                archive / f"{input_path.stem}-{_stable_id('file', input_path.resolve())[:12]}.csv"
            )
        input_path.replace(destination)
        summary["processed_count"] += 1
    return summary


def _joined_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                t.settlement_id, t.decision_id, t.actual_result, t.win_loss_push,
                t.closing_line, t.closing_price, t.clv_line, t.clv_price,
                t.pnl, t.would_have_result, t.settled_at,
                d.pipeline_verdict, d.A_verdict, d.B_verdict, d.C_verdict,
                d.D_verdict, d.final_verdict, d.units, d.kill_reason,
                d.news_override,
                s.snapshot_id, s.captured_at, s.sport, s.event_id, s.market_id,
                s.outcome_id, s.player_id, s.selection, s.line, s.price, s.book,
                s.market_consensus_prob, s.independent_model_prob,
                s.final_blended_prob, s.blend_market_weight, s.blend_model_weight,
                s.blend_weight_source, s.blend_model_version, s.blend_segment,
                s.push_prob, s.edge, s.data_quality_flags, s.data_quality_tier,
                s.event_starts_at, s.hours_before_game, s.odds_range,
                s.time_before_game, s.market_type, s.model_prob_source, s.decimal_price,
                s.implied_prob, s.board, s.selected, s.signal_flags,
                s.hit_rate_component, s.insight_component,
                s.movement_component, s.orf_component,
                COALESCE(m.source, '') AS pack_source,
                COALESCE(m.pack_path, '') AS pack_source_path,
                COALESCE(m.pack_timestamp, '') AS pack_source_timestamp
            FROM settlements t
            JOIN decisions d ON d.decision_id = t.decision_id
            JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
            LEFT JOIN (
                SELECT
                    snapshot_id, source, pack_path, pack_timestamp,
                    ROW_NUMBER() OVER (
                        PARTITION BY snapshot_id
                        ORDER BY
                            CASE WHEN COALESCE(source, '') = '' THEN 1 ELSE 0 END,
                            pack_timestamp,
                            pack_capture_id
                    ) AS vintage_rank
                FROM pack_snapshot_memberships
            ) m ON m.snapshot_id = s.snapshot_id AND m.vintage_rank = 1
            ORDER BY s.captured_at, d.decision_id
            """
        )
    ]


def fit_blend_weights(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = probability_blend.DEFAULT_WEIGHTS_PATH,
    *,
    min_samples: int = 30,
    prior_strength: float = 30.0,
) -> dict[str, Any]:
    """Fit a versioned blend artifact from pregame, settled ledger snapshots."""

    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
    artifact = probability_blend.fit_weight_artifact(
        rows, min_samples=min_samples, prior_strength=prior_strength
    )
    probability_blend.write_weight_artifact(artifact, Path(output_path))
    return artifact


def fit_stake_calibration_from_db(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = stake_calibration.DEFAULT_ARTIFACT_PATH,
    *,
    as_of: datetime | None = None,
    source_probability_column: str = stake_calibration.DEFAULT_SOURCE_PROBABILITY_COLUMN,
    min_samples: int = stake_calibration.DEFAULT_MIN_SAMPLES,
    prior_strength: float = stake_calibration.DEFAULT_PRIOR_STRENGTH,
    confidence_level: float = stake_calibration.DEFAULT_CONFIDENCE_LEVEL,
    policy_fingerprint: str = "",
) -> dict[str, Any]:
    """Fit a stake-calibration artifact from settled ledger rows (Track C1)."""

    cutoff = as_of or datetime.now(timezone.utc)
    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
    artifact = stake_calibration.fit_stake_calibration(
        rows,
        as_of=cutoff,
        source_probability_column=source_probability_column,
        min_samples=min_samples,
        prior_strength=prior_strength,
        confidence_level=confidence_level,
        policy_fingerprint=policy_fingerprint,
    )
    stake_calibration.write_stake_calibration_artifact(artifact, Path(output_path))
    return artifact


def compute_drawdown_from_db(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = drawdown.DEFAULT_STATE_PATH,
    *,
    as_of: datetime | None = None,
) -> drawdown.DrawdownState:
    """Compute drawdown state from settled placed ledger rows (Track C3)."""

    cutoff = as_of or datetime.now(timezone.utc)
    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
    # Treat decision units + settlement pnl as placed when settlement exists.
    executions: list[dict[str, Any]] = []
    for row in rows:
        result = _text(row.get("win_loss_push")).upper()
        if result not in {"W", "L", "PUSH"}:
            continue
        units = _float(row.get("units"), field="units")
        if units is None or units <= 0:
            continue
        executions.append(
            {
                "decision_id": row.get("decision_id"),
                "snapshot_id": row.get("snapshot_id"),
                "execution_status": "SETTLED",
                "settled_at": row.get("settled_at") or row.get("captured_at"),
                "pnl": row.get("pnl"),
                "placed_units": units,
                "units": units,
                "win_loss_push": result,
            }
        )
    state = drawdown.compute_drawdown_state(executions, as_of=cutoff)
    drawdown.write_drawdown_state(state, Path(output_path))
    return state


def _mean(values: Iterable[float | None]) -> float | None:
    kept = [value for value in values if value is not None]
    return sum(kept) / len(kept) if kept else None


def _flat_pnl(
    row: dict[str, Any], result_field: str = "win_loss_push", *, invert: bool = False
) -> float | None:
    result = _text(row.get(result_field)).upper()
    if invert:
        if result == "W":
            result = "L"
        elif result == "L":
            result = "W"
    if result == "L":
        return -1.0
    if result == "PUSH":
        return 0.0
    if result != "W":
        return None
    decimal_price = _float(row.get("decimal_price"), field="decimal_price")
    return decimal_price - 1.0 if decimal_price is not None else None


def _group_metrics(rows: list[dict[str, Any]], label: str, value: str) -> dict[str, Any]:
    wins = sum(_text(row.get("win_loss_push")).upper() == "W" for row in rows)
    losses = sum(_text(row.get("win_loss_push")).upper() == "L" for row in rows)
    pushes = sum(_text(row.get("win_loss_push")).upper() == "PUSH" for row in rows)
    plays = [
        row
        for row in rows
        if _is_play(row.get("final_verdict"), row.get("pipeline_verdict"), row.get("units"))
    ]
    stand_downs = len(rows) - len(plays)
    units = sum(_float(row.get("units"), field="units") or 0.0 for row in plays)
    profit = sum(_float(row.get("pnl"), field="pnl") or 0.0 for row in plays)
    flat_values = [_flat_pnl(row, "would_have_result") for row in rows]
    flat_pnl = sum(value for value in flat_values if value is not None)
    return {
        label: value or "UNKNOWN",
        "n": len(rows),
        "plays": len(plays),
        "stand_downs": stand_downs,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": wins / (wins + losses) if wins + losses else None,
        "profit": profit,
        "units": units,
        "roi": profit / units if units else None,
        "avg_clv_line": _mean(_float(row.get("clv_line"), field="clv_line") for row in rows),
        "avg_clv_price": _mean(_float(row.get("clv_price"), field="clv_price") for row in rows),
        "would_have_flat_pnl": flat_pnl,
    }


GROUP_METRIC_FIELDS = [
    "n",
    "plays",
    "stand_downs",
    "wins",
    "losses",
    "pushes",
    "hit_rate",
    "profit",
    "units",
    "roi",
    "avg_clv_line",
    "avg_clv_price",
    "would_have_flat_pnl",
]


# Pack-lane -> pack-type labels.  ``pack_snapshot_memberships.source`` records
# which pack CSV a snapshot was captured from; these are the lanes that reach
# the ledger today.  Lanes that are generated but never captured are reported as
# coverage gaps by ``outlier_scrapers.pack_accuracy`` rather than guessed at.
PACK_SOURCE_TYPES = {
    "game_totals": "GAME_TOTAL",
    "team_totals": "TEAM_TOTAL",
    "ultimate_alt": "ULTIMATE_ALT",
    "alt_player_props": "ALT_PLAYER_PROP",
    "alt_team_totals": "ALT_TEAM_TOTAL",
    "mlb_alt_spreads": "ALT_SPREAD",
    "wnba_alt_spreads": "ALT_SPREAD",
    "mlb_alt_bankroll_props": "ALT_BANKROLL_PROP",
    "wnba_alt_bankroll_props": "ALT_BANKROLL_PROP",
}

PACK_TYPE_UNCLASSIFIED = "UNCLASSIFIED"

_PLAYER_SELECTION_RE = re.compile(
    r"(.+?)\s+-\s+(.+?)\s+(OVER|UNDER)\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE
)
_TEAM_TOTAL_SELECTION_RE = re.compile(r"\bTeam Total\b", re.IGNORECASE)
_GAME_TOTAL_SELECTION_RE = re.compile(r"\b(?:Total O/U|TOTAL)\s+(?:OVER|UNDER)\b", re.IGNORECASE)
_SPREAD_SELECTION_RE = re.compile(r"\b(?:Spread|Run Line)\s+(?:HOME|AWAY)\b", re.IGNORECASE)
_MONEYLINE_SELECTION_RE = re.compile(r"\bMoney\s*Line\s+(?:HOME|AWAY)\b", re.IGNORECASE)


def classify_pack_type(row: dict[str, Any]) -> str:
    """Return the pack lane a graded row belongs to.

    The capture lane recorded on ``pack_snapshot_memberships`` is authoritative
    when present.  Rows captured before lane tracking existed (or through the
    generic ``opportunities``/``candidates`` lane, which carries every market
    family at once) are classified from the same selection grammar
    ``outlier_scrapers.results`` grades with, so a pack type never disagrees
    with how the row was settled.
    """

    source = _text(row.get("pack_source")).strip().lower()
    mapped = PACK_SOURCE_TYPES.get(source)
    if mapped:
        return mapped

    selection = _text(row.get("selection"))
    market_type = _text(row.get("market_type")).replace("_", "").upper()
    if _PLAYER_SELECTION_RE.fullmatch(selection.strip()) and market_type not in {
        "GAMELINE",
        "TEAMPROP",
    }:
        return "PLAYER_PROP"
    if _TEAM_TOTAL_SELECTION_RE.search(selection):
        return "TEAM_TOTAL"
    if _MONEYLINE_SELECTION_RE.search(selection):
        return "MONEYLINE"
    if _SPREAD_SELECTION_RE.search(selection):
        return "SPREAD"
    if _GAME_TOTAL_SELECTION_RE.search(selection):
        return "GAME_TOTAL"
    if market_type == "PLAYERPROP":
        return "PLAYER_PROP"
    if market_type == "TEAMPROP":
        return "TEAM_PROP"
    if market_type == "GAMELINE":
        return "GAMELINE_OTHER"
    return PACK_TYPE_UNCLASSIFIED


def with_pack_types(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate joined rows with their ``pack_type`` in place."""

    for row in rows:
        row["pack_type"] = classify_pack_type(row)
    return rows


def _grouped(rows: list[dict[str, Any]], field: str, label: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_text(row.get(field)) or "UNKNOWN"].append(row)
    return [_group_metrics(groups[key], label, key) for key in sorted(groups)]


def _scoring_probability(row: dict[str, Any], column: str) -> float | None:
    """Return P(win | not push), the binary probability used for W/L scoring.

    A blank push probability means the row's binary basis is unknown, so it is
    excluded rather than silently assuming no push. No-push markets explicitly
    carry 0.0.
    """

    probability = _probability(row.get(column), field=column)
    push_prob = _probability(row.get("push_prob"), field="push_prob")
    if probability is None or push_prob is None or push_prob >= 1.0:
        return None
    conditional = probability / (1.0 - push_prob)
    if not 0.0 <= conditional <= 1.0:
        return None
    return conditional


def _probability_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source, column in PROBABILITY_COLUMNS.items():
        pairs: list[tuple[float, float]] = []
        for row in rows:
            result = _text(row.get("win_loss_push")).upper()
            probability = _scoring_probability(row, column)
            if probability is None or result == "PUSH":
                continue
            pairs.append((probability, 1.0 if result == "W" else 0.0))
        brier = _mean((probability - actual) ** 2 for probability, actual in pairs)
        log_loss = _mean(
            -(
                actual * math.log(min(max(probability, 1e-15), 1 - 1e-15))
                + (1 - actual) * math.log(1 - min(max(probability, 1e-15), 1 - 1e-15))
            )
            for probability, actual in pairs
        )
        expected = _mean(probability for probability, _actual in pairs)
        actual = _mean(actual for _probability_value, actual in pairs)
        output.append(
            {
                "probability_source": source,
                "n": len(pairs),
                "brier_score": brier,
                "log_loss": log_loss,
                "expected_hit_rate": expected,
                "actual_hit_rate": actual,
                "calibration_gap": (actual - expected)
                if expected is not None and actual is not None
                else None,
            }
        )
    return output


def _calibration_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source, column in PROBABILITY_COLUMNS.items():
        bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for row in rows:
            result = _text(row.get("win_loss_push")).upper()
            probability = _scoring_probability(row, column)
            if probability is None or result == "PUSH":
                continue
            bucket = min(9, int(probability * 10))
            bins[bucket].append((probability, 1.0 if result == "W" else 0.0))
        for bucket in range(10):
            pairs = bins[bucket]
            output.append(
                {
                    "probability_source": source,
                    "bucket": f"{bucket * 10}-{(bucket + 1) * 10}%",
                    "n": len(pairs),
                    "mean_predicted_prob": _mean(probability for probability, _actual in pairs),
                    "actual_hit_rate": _mean(actual for _probability, actual in pairs),
                }
            )
    return output


def _edge_bucket(value: Any) -> str:
    edge = _float(value, field="edge")
    if edge is None:
        return "missing"
    if edge < 0:
        return "<0%"
    if edge < 0.02:
        return "0-2%"
    if edge < 0.05:
        return "2-5%"
    if edge < 0.10:
        return "5-10%"
    return "10%+"


def _odds_bucket(value: Any) -> str:
    price = _float(value, field="price")
    if price is None:
        return "missing"
    if price <= -200:
        return "<=-200"
    if price <= -121:
        return "-199 to -121"
    if price <= -101:
        return "-120 to -101"
    if price <= 100:
        return "-100 to +100"
    if price <= 149:
        return "+101 to +149"
    return "+150+"


def _bucketed(
    rows: list[dict[str, Any]], field: str, label: str, bucket_fn: Any
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[bucket_fn(row.get(field))].append(row)
    return [_group_metrics(groups[key], label, key) for key in sorted(groups)]


def _signal_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw = _text(row.get("signal_flags") or row.get("data_quality_flags"))
        flags = [flag.strip() for flag in raw.replace(",", ";").split(";") if flag.strip()]
        for flag in flags or ["NO_SIGNAL_FLAG"]:
            groups[flag].append(row)
    return [_group_metrics(groups[key], "signal_flag", key) for key in sorted(groups)]


def _play_vs_stand_down(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group = (
            "PLAY"
            if _is_play(row.get("final_verdict"), row.get("pipeline_verdict"), row.get("units"))
            else "STAND_DOWN"
        )
        groups[group].append(row)
    return [_group_metrics(groups[key], "decision_class", key) for key in sorted(groups)]


DECISION_COVERAGE_FIELDS = [
    "decision_class",
    "total_decisions",
    "settled_decisions",
    "unsettled_decisions",
    "settlement_rate",
    "missing_event_start",
    "total_units",
    "settled_units",
]


def _decision_coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Report publication-level decision coverage separately from settled ROI."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = conn.execute(
        """
        SELECT m.pipeline_verdict, m.units, s.event_starts_at,
               CASE WHEN EXISTS (
                   SELECT 1 FROM settlements t WHERE t.snapshot_id = m.snapshot_id
               ) THEN 1 ELSE 0 END AS settled
        FROM pack_snapshot_memberships m
        JOIN market_snapshots s ON s.snapshot_id = m.snapshot_id
        """
    )
    for raw in rows:
        row = dict(raw)
        decision_class = (
            "PLAY"
            if _is_play("", row.get("pipeline_verdict"), row.get("units"))
            else "STAND_DOWN"
        )
        groups[decision_class].append(row)

    output: list[dict[str, Any]] = []
    for decision_class in sorted(groups):
        group = groups[decision_class]
        settled = [row for row in group if bool(row.get("settled"))]
        total_units = sum(_float(row.get("units"), field="units") or 0.0 for row in group)
        settled_units = sum(_float(row.get("units"), field="units") or 0.0 for row in settled)
        output.append(
            {
                "decision_class": decision_class,
                "total_decisions": len(group),
                "settled_decisions": len(settled),
                "unsettled_decisions": len(group) - len(settled),
                "settlement_rate": len(settled) / len(group) if group else None,
                "missing_event_start": sum(
                    not _text(row.get("event_starts_at")) for row in group
                ),
                "total_units": total_units,
                "settled_units": settled_units,
            }
        )
    return output


def _model_performance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for model, field in (
        ("A", "A_verdict"),
        ("B", "B_verdict"),
        ("C", "C_verdict"),
        ("D", "D_verdict"),
    ):
        for row in rows:
            verdict = _text(row.get(field)).upper()
            if verdict:
                groups[(model, verdict)].append(row)

    output: list[dict[str, Any]] = []
    positive = {"BET", "PLAY", "LEAN", "CONFIRMS"}
    negative = {"FADE", "CONTRADICTS"}
    for (model, verdict), group in sorted(groups.items()):
        wins = sum(_text(row.get("win_loss_push")).upper() == "W" for row in group)
        losses = sum(_text(row.get("win_loss_push")).upper() == "L" for row in group)
        pushes = sum(_text(row.get("win_loss_push")).upper() == "PUSH" for row in group)
        directional_n = wins + losses if verdict in positive | negative else 0
        successes = wins if verdict in positive else (losses if verdict in negative else 0)
        flat_values = [_flat_pnl(row, invert=(verdict in negative)) for row in group]
        output.append(
            {
                "model": model,
                "verdict": verdict,
                "n": len(group),
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "selected_side_hit_rate": wins / (wins + losses) if wins + losses else None,
                "recommendation_accuracy": successes / directional_n if directional_n else None,
                "would_have_flat_pnl": sum(value for value in flat_values if value is not None),
            }
        )
    return output


ULTIMATE_ALT_SHADOW_FIELDS = [
    "alt_type",
    "n",
    "wins",
    "losses",
    "pushes",
    "hit_rate",
    "mean_implied_prob",
    "mean_conservative_prob",
    "calibration_gap",
    "flat_profit",
    "flat_roi",
    "clv_coverage",
    "mean_price_clv",
]


def ultimate_alt_shadow_release(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate, but never auto-promote, the unified alternate shadow lane."""
    shadow_rows = [
        row
        for row in rows
        if str(row.get("board") or "").upper() == "ALT_SHADOW_QUALIFIED"
        and "ultimate_alt:" in str(row.get("signal_flags") or "")
        and _normal_result(row.get("win_loss_push") or "") in {"W", "L", "PUSH"}
    ]

    def alt_type(row: dict[str, Any]) -> str:
        for flag in str(row.get("signal_flags") or "").split(";"):
            if flag.startswith("ultimate_alt:"):
                return flag.split(":", 1)[1] or "UNKNOWN"
        return "UNKNOWN"

    def metrics(label: str, group: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(_normal_result(row.get("win_loss_push") or "") == "W" for row in group)
        losses = sum(_normal_result(row.get("win_loss_push") or "") == "L" for row in group)
        pushes = len(group) - wins - losses
        graded = wins + losses
        implied = [_float(row.get("implied_prob")) for row in group]
        implied = [value for value in implied if value is not None]
        conservative = [_float(row.get("final_blended_prob")) for row in group]
        conservative = [value for value in conservative if value is not None]
        profit = 0.0
        for row in group:
            result = _normal_result(row.get("win_loss_push") or "")
            decimal = _float(row.get("decimal_price"))
            if result == "W" and decimal is not None:
                profit += decimal - 1.0
            elif result == "L":
                profit -= 1.0
        clv = [_float(row.get("clv_price")) for row in group]
        clv = [value for value in clv if value is not None]
        hit_rate = wins / graded if graded else None
        mean_conservative = _mean(conservative)
        return {
            "alt_type": label,
            "n": len(group),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "hit_rate": hit_rate,
            "mean_implied_prob": _mean(implied),
            "mean_conservative_prob": mean_conservative,
            "calibration_gap": (
                hit_rate - mean_conservative
                if hit_rate is not None and mean_conservative is not None
                else None
            ),
            "flat_profit": profit,
            "flat_roi": profit / graded if graded else None,
            "clv_coverage": len(clv) / len(group) if group else 0.0,
            "mean_price_clv": _mean(clv),
        }

    types = sorted({alt_type(row) for row in shadow_rows})
    by_type = [
        metrics(token, [row for row in shadow_rows if alt_type(row) == token]) for token in types
    ]
    overall = metrics("ALL", shadow_rows)
    shadow_days = len({str(row.get("captured_at") or "")[:10] for row in shadow_rows})
    type_counts = {row["alt_type"]: row["n"] for row in by_type}
    gates = {
        "minimum_200_settled": overall["n"] >= 200,
        "minimum_30_shadow_days": shadow_days >= 30,
        "minimum_40_each_type": all(
            type_counts.get(token, 0) >= 40 for token in ("SPREAD", "TOTAL", "PLAYER_PROP")
        ),
        "positive_flat_roi": (overall["flat_roi"] or 0.0) > 0.0,
        "calibration_gap_within_5pct": (
            overall["calibration_gap"] is not None and abs(overall["calibration_gap"]) <= 0.05
        ),
        "minimum_80pct_clv_coverage": overall["clv_coverage"] >= 0.80,
        "nonnegative_mean_price_clv": (
            overall["mean_price_clv"] is not None and overall["mean_price_clv"] >= 0.0
        ),
    }
    return {
        "mode": "shadow",
        "auto_promotion": False,
        "ready_for_manual_promotion_review": all(gates.values()),
        "shadow_days": shadow_days,
        "gates": gates,
        "overall": overall,
        "by_type": by_type,
    }


_RETENTION_FULL_FIDELITY_VERDICTS = {"PLAY", "BET"}
# Only columns _joined_rows() never selects are safe to clear: that query
# backs fit_blend_weights(), fit_stake_calibration_from_db(), and
# generate_report() alike, so anything it reads is live training/calibration
# input, not disposable detail -- clearing it would silently shrink the
# eligible sample for every fitter and report metric derived from these
# rows, not just trim bytes. What's left is portfolio-sizing-at-capture-time
# and pack-provenance metadata that none of them consume.
_RETENTION_SLIMMED_COLUMNS = [
    "projection_feature_hash",
    "projection_quality_flags",
    "pack_path",
    "policy_fingerprint",
    "portfolio_mode",
    "pre_cap_units",
    "portfolio_units",
    "cap_reasons",
]


def apply_retention_policy(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    cutoff_days: int = 90,
    dry_run: bool = False,
) -> RetentionStats:
    """Slim disposable snapshot columns for settled, never-played history.

    A row keeps its portfolio-sizing-at-capture-time and pack-provenance
    columns (``pre_cap_units``, ``portfolio_units``, ``cap_reasons``,
    ``policy_fingerprint``, ``portfolio_mode``, ``pack_path``,
    ``projection_feature_hash``, ``projection_quality_flags``) only if it
    reached ``PLAY``/``BET`` or the desk flagged it (``board =
    'A_FLAGGED'``); those columns are cleared once the row is already
    settled and older than ``cutoff_days``. Every other column -- including
    the probability/edge/signal-component fields ``fit_blend_weights()``,
    ``fit_stake_calibration_from_db()``, and ``generate_report()`` all read
    via the shared settled-row query -- is left alone regardless of age, so
    retention never shrinks the eligible sample those consume. Unsettled,
    recent, played, or flagged rows are never touched. Vacuums afterward to
    actually reclaim the freed pages on disk.
    """
    if cutoff_days < 0:
        # A negative value pushes the cutoff into the future, matching every
        # settled row (including today's) instead of only old ones -- a
        # typo'd sign here would otherwise silently slim recent history.
        raise FeedbackError(f"cutoff_days must be non-negative, got {cutoff_days}")
    db_path = Path(db_path)
    bytes_before = db_path.stat().st_size if db_path.exists() else 0
    conn = _connect(db_path)
    slimmed = 0
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()
        candidates = conn.execute(
            """
            SELECT DISTINCT s.snapshot_id
            FROM market_snapshots s
            JOIN decisions d ON d.snapshot_id = s.snapshot_id
            JOIN settlements t ON t.snapshot_id = s.snapshot_id
            WHERE COALESCE(s.board, '') != 'A_FLAGGED'
              AND UPPER(COALESCE(NULLIF(d.final_verdict, ''), d.pipeline_verdict, ''))
                  NOT IN ('PLAY', 'BET')
              AND t.settled_at < ?
              AND s.captured_at < ?
              AND (
                  s.projection_feature_hash IS NOT NULL OR s.projection_quality_flags IS NOT NULL
                  OR s.pack_path IS NOT NULL OR s.policy_fingerprint IS NOT NULL
                  OR s.portfolio_mode IS NOT NULL OR s.pre_cap_units IS NOT NULL
                  OR s.portfolio_units IS NOT NULL OR s.cap_reasons IS NOT NULL
              )
            """,
            (cutoff, cutoff),
        ).fetchall()
        snapshot_ids = [row["snapshot_id"] for row in candidates]
        if snapshot_ids and not dry_run:
            clear_assignments = ", ".join(
                f"{column} = NULL" for column in _RETENTION_SLIMMED_COLUMNS
            )
            for start in range(0, len(snapshot_ids), 500):
                chunk = snapshot_ids[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"""
                    UPDATE market_snapshots
                    SET {clear_assignments}
                    WHERE snapshot_id IN ({placeholders})
                    """,
                    chunk,
                )
                slimmed += len(chunk)
        conn.commit()
    finally:
        conn.close()

    if slimmed and not dry_run:
        vacuum_conn = sqlite3.connect(db_path)
        try:
            vacuum_conn.execute("VACUUM")
        finally:
            vacuum_conn.close()

    bytes_after = db_path.stat().st_size if db_path.exists() else bytes_before
    return RetentionStats(
        eligible=len(snapshot_ids),
        slimmed=slimmed,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def export_ledgers(db_path: Path, output_dir: Path) -> dict[str, int]:
    output_dir = Path(output_dir)
    with _connect(Path(db_path)) as conn:
        snapshot_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT snapshot_id, captured_at, sport, event_id, market_id,
                       outcome_id, player_id, selection, line, price, book,
                       market_consensus_prob, independent_model_prob,
                       final_blended_prob, blend_market_weight, blend_model_weight,
                       blend_weight_source, blend_model_version, blend_segment,
                       push_prob, edge, data_quality_flags, data_quality_tier,
                       event_starts_at, hours_before_game, odds_range,
                       time_before_game, market_type, model_prob_source,
                       decimal_price, implied_prob,
                       board, selected, signal_flags, hit_rate_component,
                       insight_component, movement_component, orf_component, pack_path
                FROM market_snapshots
                ORDER BY captured_at, snapshot_id
                """
            )
        ]
        decision_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT decision_id, snapshot_id, pipeline_verdict,
                       A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                       units, kill_reason, news_override
                FROM decisions
                ORDER BY decision_id
                """
            )
        ]
        settlement_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT settlement_id, decision_id, snapshot_id, outcome_id,
                       event_id, market_id, actual_result, win_loss_push,
                       closing_line, closing_price, clv_line, clv_price, pnl,
                       would_have_result
                FROM settlements
                ORDER BY settled_at, settlement_id
                """
            )
        ]
        membership_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT pack_capture_id, snapshot_id, pack_path, pack_timestamp,
                       source, pipeline_verdict, units, selected, actionable, created_at
                FROM pack_snapshot_memberships
                ORDER BY pack_timestamp, pack_capture_id, snapshot_id
                """
            )
        ]
    _write_csv(output_dir / "market_snapshots.csv", MARKET_SNAPSHOT_FIELDS, snapshot_rows)
    _write_csv(output_dir / "decisions.csv", DECISION_FIELDS, decision_rows)
    _write_csv(output_dir / "settlements.csv", SETTLEMENT_FIELDS, settlement_rows)
    _write_csv(
        output_dir / "pack_snapshot_memberships.csv",
        PACK_MEMBERSHIP_FIELDS,
        membership_rows,
    )
    return {
        "market_snapshots": len(snapshot_rows),
        "decisions": len(decision_rows),
        "settlements": len(settlement_rows),
        "pack_snapshot_memberships": len(membership_rows),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _report_markdown(
    coverage: dict[str, int],
    decision_coverage: list[dict[str, Any]],
    probability_metrics: list[dict[str, Any]],
    market_type: list[dict[str, Any]],
) -> str:
    play_coverage = next(
        (row for row in decision_coverage if row["decision_class"] == "PLAY"), {}
    )
    lines = [
        "# Feedback-loop calibration report",
        "",
        f"Generated: {_utc_now()}",
        "",
        "## Coverage",
        "",
        f"- Market snapshots: {coverage['market_snapshots']}",
        f"- Decisions: {coverage['decisions']}",
        f"- Pack captures: {coverage['pack_captures']}",
        f"- Pack decision memberships: {coverage['pack_decision_memberships']}",
        f"- Settlements: {coverage['settlements']}",
        f"- Graded and linked decisions: {coverage['graded_and_linked']}",
        f"- Unlinked settlements: {coverage['unlinked_settlements']}",
        f"- Independent-model probabilities: {coverage['independent_probabilities']}",
        f"- Published PLAY decisions: {play_coverage.get('total_decisions', 0)}",
        f"- Settled PLAY decisions: {play_coverage.get('settled_decisions', 0)}",
        f"- Unsettled PLAY decisions: {play_coverage.get('unsettled_decisions', 0)}",
        f"- PLAY decisions missing event start: {play_coverage.get('missing_event_start', 0)}",
        "",
        "## Probability quality",
        "",
        "| Probability | N | Brier | Log loss | Expected hit | Actual hit | Gap |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in probability_metrics:
        lines.append(
            "| {probability_source} | {n} | {brier_score} | {log_loss} | "
            "{expected_hit_rate} | {actual_hit_rate} | {calibration_gap} |".format(
                **{key: _fmt(value) for key, value in row.items()}
            )
        )
    lines += [
        "",
        "## ROI, profit, and CLV by market type",
        "",
        "| Market type | N | Plays | Profit | Units | ROI | Avg line CLV | Avg price CLV |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in market_type:
        lines.append(
            f"| {row['market_type']} | {row['n']} | {row['plays']} | {_fmt(row['profit'])} | "
            f"{_fmt(row['units'])} | {_fmt(row['roi'])} | {_fmt(row['avg_clv_line'])} | "
            f"{_fmt(row['avg_clv_price'])} |"
        )
    lines += [
        "",
        "## Interpretation guardrail",
        "",
        "ROI, profit, hit-rate, and market tables include settled decisions only. "
        "Use decision_coverage.csv to verify how much of the published card is still unsettled or "
        "missing an event start before interpreting performance.",
        "",
        "Market-consensus and final-blended metrics will be identical while the independent-model "
        "column is empty. This is intentional: the report exposes the current market-derived "
        "baseline instead of relabeling it as an independent model.",
        "",
    ]
    return "\n".join(lines)


def _missing_edge_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Explain absent edges without manufacturing probability inputs."""
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    missing = 0
    for row in rows:
        edge = row.get("edge")
        if edge is not None and str(edge).strip() != "":
            continue
        missing += 1
        market_type = _text(row.get("market_type"))
        model_source = _text(row.get("model_prob_source"))
        if market_type.upper() == "GAMELINE" and not model_source:
            reason = "gameline_missing_model_prob_source"
        elif not model_source:
            reason = "missing_model_prob_source"
        elif row.get("market_consensus_prob") in (None, ""):
            reason = "missing_market_consensus_prob"
        elif row.get("decimal_price") in (None, ""):
            reason = "missing_decimal_price"
        else:
            reason = "missing_edge_unclassified"
        counts[(_text(row.get("sport")), market_type, reason)] += 1
    return {
        "settled_rows": len(rows),
        "missing_edge_rows": missing,
        "missing_edge_rate": (missing / len(rows)) if rows else 0.0,
        "breakdown": [
            {"sport": sport, "market_type": market_type, "reason": reason, "n": count}
            for (sport, market_type, reason), count in sorted(counts.items())
        ],
    }


def generate_report(db_path: Path = DEFAULT_DB_PATH, output_dir: Path = DEFAULT_REPORT_DIR) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with _connect(Path(db_path)) as conn:
        rows = with_pack_types(_joined_rows(conn))
        decision_coverage = _decision_coverage(conn)
        coverage = {
            "market_snapshots": conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0],
            "decisions": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
            "pack_captures": conn.execute(
                "SELECT COUNT(DISTINCT pack_capture_id) FROM pack_snapshot_memberships"
            ).fetchone()[0],
            "pack_decision_memberships": conn.execute(
                "SELECT COUNT(*) FROM pack_snapshot_memberships"
            ).fetchone()[0],
            "settlements": conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0],
            "graded_and_linked": len(rows),
            "unlinked_settlements": conn.execute(
                "SELECT COUNT(*) FROM settlements WHERE decision_id IS NULL"
            ).fetchone()[0],
            "independent_probabilities": conn.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE independent_model_prob IS NOT NULL"
            ).fetchone()[0],
        }

    probability_metrics = _probability_metrics(rows)
    calibration = _calibration_curve(rows)
    expected_actual = [
        {
            key: row[key]
            for key in (
                "probability_source",
                "n",
                "expected_hit_rate",
                "actual_hit_rate",
                "calibration_gap",
            )
        }
        for row in probability_metrics
    ]
    market_type = _grouped(rows, "market_type", "market_type")
    pack_type = _grouped(rows, "pack_type", "pack_type")
    edge_buckets = _bucketed(rows, "edge", "edge_bucket", _edge_bucket)
    odds_ranges = _bucketed(rows, "price", "odds_range", _odds_bucket)
    books = _grouped(rows, "book", "book")
    leagues = _grouped(rows, "sport", "league")
    signal_flags = _signal_results(rows)
    play_vs_stand_down = _play_vs_stand_down(rows)
    model_performance = _model_performance(rows)
    missing_edge_diagnostics = _missing_edge_diagnostics(rows)
    ultimate_alt_release = ultimate_alt_shadow_release(rows)

    metric_fields = [
        "probability_source",
        "n",
        "brier_score",
        "log_loss",
        "expected_hit_rate",
        "actual_hit_rate",
        "calibration_gap",
    ]
    _write_csv(output_dir / "probability_metrics.csv", metric_fields, probability_metrics)
    _write_csv(
        output_dir / "calibration_curves.csv",
        ["probability_source", "bucket", "n", "mean_predicted_prob", "actual_hit_rate"],
        calibration,
    )
    _write_csv(
        output_dir / "expected_vs_actual.csv",
        ["probability_source", "n", "expected_hit_rate", "actual_hit_rate", "calibration_gap"],
        expected_actual,
    )
    _write_csv(
        output_dir / "decision_coverage.csv",
        DECISION_COVERAGE_FIELDS,
        decision_coverage,
    )
    for filename, label, table in (
        ("market_type.csv", "market_type", market_type),
        ("pack_type.csv", "pack_type", pack_type),
        ("edge_buckets.csv", "edge_bucket", edge_buckets),
        ("odds_ranges.csv", "odds_range", odds_ranges),
        ("books.csv", "book", books),
        ("leagues.csv", "league", leagues),
        ("signal_flags.csv", "signal_flag", signal_flags),
        ("play_vs_stand_down.csv", "decision_class", play_vs_stand_down),
    ):
        _write_csv(output_dir / filename, [label, *GROUP_METRIC_FIELDS], table)
    _write_csv(
        output_dir / "model_performance.csv",
        [
            "model",
            "verdict",
            "n",
            "wins",
            "losses",
            "pushes",
            "selected_side_hit_rate",
            "recommendation_accuracy",
            "would_have_flat_pnl",
        ],
        model_performance,
    )
    _write_csv(
        output_dir / "missing_edge_diagnostics.csv",
        ["sport", "market_type", "reason", "n"],
        missing_edge_diagnostics["breakdown"],
    )
    _write_csv(
        output_dir / "ultimate_alt_shadow.csv",
        ULTIMATE_ALT_SHADOW_FIELDS,
        [ultimate_alt_release["overall"], *ultimate_alt_release["by_type"]],
    )
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "coverage": coverage,
                "decision_coverage": decision_coverage,
                "probability_metrics": probability_metrics,
                "ultimate_alt_shadow_release": ultimate_alt_release,
                "missing_edge_diagnostics": missing_edge_diagnostics,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        _report_markdown(coverage, decision_coverage, probability_metrics, market_type),
        encoding="utf-8",
    )
    export_ledgers(Path(db_path), output_dir / "ledgers")
    return output_dir


def replay_portfolio(
    db_conn_or_path: Path | str | sqlite3.Connection,
    start_date: str,
    end_date: str,
    policy_path: Path | str,
    as_of_strict: bool = True,
) -> dict[str, Any]:
    with open(policy_path, "r", encoding="utf-8") as f:
        policy_data = json.load(f)

    policy = PortfolioPolicy(
        stake_increment=policy_data.get("stake_increment", 0.1),
        max_wager_units=policy_data.get("max_wager_units", 1.0),
        max_daily_units=policy_data.get("max_daily_units", 10.0),
        max_event_units=policy_data.get("max_event_units", 3.0),
        max_player_units=policy_data.get("max_player_units", 1.0),
        max_team_units=policy_data.get("max_team_units", 2.0),
        max_market_type_units=policy_data.get("max_market_type_units", 5.0),
        max_correlated_cluster_units=policy_data.get("max_correlated_cluster_units", 2.0),
        max_book_units=policy_data.get("max_book_units", 5.0),
    )

    if isinstance(db_conn_or_path, (str, Path)):
        conn = sqlite3.connect(db_conn_or_path)
        conn.row_factory = sqlite3.Row
        close_conn = True
    else:
        conn = db_conn_or_path
        close_conn = False

    try:
        query = """
            SELECT s.*, d.units as legacy_units, d.decision_id
            FROM market_snapshots s
            JOIN decisions d ON s.snapshot_id = d.snapshot_id
            WHERE SUBSTR(s.captured_at, 1, 10) >= ? AND SUBSTR(s.captured_at, 1, 10) <= ?
            ORDER BY s.captured_at
        """
        rows = conn.execute(query, (start_date, end_date)).fetchall()

        # Group by date
        grouped_by_date = defaultdict(list)
        for row in rows:
            date_str = _text(row["captured_at"])[:10]
            grouped_by_date[date_str].append(dict(row))

        summary = {}
        for date_str, daily_rows in sorted(grouped_by_date.items()):
            # Map for allocate_portfolio_risk
            allocation_rows = []
            for r in daily_rows:
                allocation_rows.append(
                    {
                        "stable_wager_id": r["snapshot_id"],
                        "actionable": str(r.get("board") == "A").lower(),
                        "board": r.get("board"),
                        "units": r.get("legacy_units", 0.0)
                        if as_of_strict
                        else policy.max_wager_units,
                        "event_id": r.get("event_id"),
                        "player_id": r.get("player_id"),
                        "team": None,
                        "market_type": r.get("market_type"),
                        "cluster_id": None,
                        "sportsbook": r.get("book"),
                        "edge_pct": r.get("edge", 0.0),
                    }
                )

            result = allocate_portfolio_risk(allocation_rows, policy)

            legacy_total = sum(r.get("legacy_units") or 0.0 for r in daily_rows)
            replayed_total = sum(result.allocated_units.values())

            summary[date_str] = {
                "legacy_total_units": round(legacy_total, 4),
                "replayed_total_units": round(replayed_total, 4),
                "binding_constraints": result.binding_constraints,
                "utilization": result.utilization,
            }

            print(f"--- Replay Date: {date_str} ---")
            print(f"Legacy Units: {legacy_total:.4f} | Replayed Units: {replayed_total:.4f}")
            if result.binding_constraints:
                print(f"Binding constraints: {', '.join(result.binding_constraints)}")
            print()

        return summary
    finally:
        if close_conn:
            conn.close()


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
    blend_parser.add_argument("--min-samples", type=int, default=30)
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
