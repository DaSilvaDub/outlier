"""SQLite schema, migrations, and connection helpers for the feedback ledger."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from outlier_scrapers import probability_blend

DEFAULT_DB_PATH = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3")
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
    # Totals' only non-market signal (L10 hit rate at the line). Persisted so
    # its out-of-sample predictive value can be scored against settlements
    # instead of asserted from in-pack behavior alone.
    "recency_hit_prob",
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
    "totals_recency_l10": "recency_hit_prob",
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
    "recency_hit_prob": "REAL",
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
        "recency_hit_prob": "ALTER TABLE market_snapshots ADD COLUMN recency_hit_prob REAL",
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
    from outlier_scrapers.feedback_capture import (
        _load_pack_rows,
        _pack_fallback_timestamp,
        _snapshot_from_pack_row,
    )

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
    from outlier_scrapers.feedback_capture import _validate_decision_snapshot_identities

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
                recency_hit_prob REAL,
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
                s.movement_component, s.orf_component, s.recency_hit_prob
            FROM settlements t
            JOIN decisions d ON d.decision_id = t.decision_id
            JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
            ORDER BY s.captured_at, d.decision_id
            """
        )
    ]


def _mean(values: Iterable[float | None]) -> float | None:
    kept = [value for value in values if value is not None]
    return sum(kept) / len(kept) if kept else None
