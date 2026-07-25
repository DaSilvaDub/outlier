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
import sqlite3
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from outlier_scrapers import drawdown, paths, probability_blend, stake_calibration
from outlier_scrapers.portfolio import PortfolioPolicy, allocate_portfolio_risk
from outlier_scrapers.utils import _american_to_decimal, _write_csv

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3")
DEFAULT_REPORT_DIR = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\reports\latest")
SCHEMA_VERSION = 4

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
    "pack_path",
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
    "pack_path": "TEXT",
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
        "pack_path": "ALTER TABLE market_snapshots ADD COLUMN pack_path TEXT",
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
           units, kill_reason, news_override
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                pack_path TEXT,
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
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_snapshots_market "
            "ON market_snapshots(event_id, market_id, outcome_id, captured_at)",
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
        ):
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 4")
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


def _load_pack_rows(pack_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    opportunities_path = pack_dir / "opportunities.csv"
    candidates_path = pack_dir / "candidates.csv"
    base_source = "opportunities" if opportunities_path.exists() else "candidates"
    base_rows = _read_csv(opportunities_path if opportunities_path.exists() else candidates_path)
    if base_source == "candidates":
        for row in base_rows:
            row["selected"] = "true"

    specialized: list[tuple[str, dict[str, Any]]] = []
    for filename, source in (
        ("game_totals.csv", "game_totals"),
        ("team_totals.csv", "team_totals"),
    ):
        for row in _read_csv(pack_dir / filename):
            row["selected"] = "true"
            specialized.append((source, row))

    specialized_keys = {_total_representation_key(row) for _source, row in specialized}
    output: list[tuple[str, dict[str, Any]]] = []
    for row in base_rows:
        # A specialized totals ledger is the authoritative representation for a
        # selected total.  Keep non-selected raw opportunities for auditability.
        if _truthy(row.get("selected")) and _total_representation_key(row) in specialized_keys:
            continue
        output.append((base_source, row))
    output.extend(specialized)
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
        if not signal_flags:
            signal_flags = data_quality_flags
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
        "odds_range": _text(row.get("odds_range"))
        or probability_blend.odds_range(price),
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
        "pack_path": str((recorded_pack_path or pack_dir).resolve()),
    }


def _decision_seed(snapshot: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    units = _float(row.get("recommended_units_pre_news"), field="units") or 0.0
    play = bool(snapshot["selected"]) and _truthy(row.get("actionable")) and units > 0
    decision_id = _stable_id("decision", snapshot["snapshot_id"])
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
        else (snapshot["data_quality_flags"] or "not_selected_or_actionable"),
        "news_override": "",
    }


def capture_pack(
    pack_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    recorded_pack_path: Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> CaptureStats:
    """Persist a dated pack's opportunity snapshots and seed pipeline decisions.

    Re-capturing an identical source timestamp is idempotent.  A new line, price,
    source timestamp, side, or alternate outcome creates a new snapshot.
    """

    pack_dir = Path(pack_dir)
    if not pack_dir.exists():
        raise FeedbackError(f"Pack directory does not exist: {pack_dir}")

    fallback_timestamp = _pack_fallback_timestamp(pack_dir)
    source_rows = _load_pack_rows(pack_dir)
    snapshots: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for source, row in source_rows:
        snapshot = _snapshot_from_pack_row(
            source, row, pack_dir, fallback_timestamp, recorded_pack_path
        )
        snapshots.append(snapshot)
        decisions.append(_decision_seed(snapshot, row))

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
                    insight_component, movement_component, orf_component, pack_path, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    pack_path = excluded.pack_path,
                    push_prob = COALESCE(excluded.push_prob, market_snapshots.push_prob),
                    signal_flags = excluded.signal_flags,
                    hit_rate_component = excluded.hit_rate_component,
                    insight_component = excluded.insight_component,
                    movement_component = excluded.movement_component,
                    orf_component = excluded.orf_component
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
                    units, kill_reason, news_override, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at = excluded.updated_at
                WHERE COALESCE(decisions.final_verdict, '') = ''
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = decisions.snapshot_id
                )
                """,
                [decision[field] for field in DECISION_FIELDS] + [now, now],
            )

        decision_ids = [decision["decision_id"] for decision in decisions]
        current: list[dict[str, Any]] = []
        for decision_id in sorted(set(decision_ids)):
            row = conn.execute(SELECT_DECISION_BY_ID_SQL, (decision_id,)).fetchone()
            if row is not None:
                current.append(dict(row))

    _write_csv(pack_dir / "decisions.csv", DECISION_FIELDS, current)
    return CaptureStats(snapshots=len(snapshots), decisions=len(current))


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
                    units, kill_reason, news_override, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                      AND COALESCE(s.player_id, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.player_id, '') END
                      AND COALESCE(s.line, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.line, '') END
                      AND COALESCE(s.book, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.book, '') END
                """
                matches = conn.execute(
                    query, 
                    (sport, sport, event_id, market_id, player_id, player_id, line, line, book, book)
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
                matched_decision_id or matched_snapshot_id or (matched_event_id, matched_market_id, matched_outcome_id),
            )

            # Idempotent re-import check
            existing = conn.execute("SELECT 1 FROM settlements WHERE settlement_id = ?", (settlement_id,)).fetchone()
            if existing:
                summary["duplicate_count"] += 1
                continue

            clv_line = _float(row.get("clv_line"), field="clv_line")
            clv_price = _float(row.get("clv_price"), field="clv_price")
            pnl = _float(row.get("pnl"), field="pnl")
            closing_line = _text(row.get("closing_line"))
            closing_price = _float(row.get("closing_price"), field="closing_price")
            
            if clv_line is None:
                clv_line = compute_clv_line(_text(matched["selection"]), matched["line"], closing_line)
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

            would_have = _normal_result(row.get("would_have_result") or result, field="would_have_result")

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
                s.movement_component, s.orf_component
            FROM settlements t
            JOIN decisions d ON d.decision_id = t.decision_id
            JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
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
    _write_csv(output_dir / "market_snapshots.csv", MARKET_SNAPSHOT_FIELDS, snapshot_rows)
    _write_csv(output_dir / "decisions.csv", DECISION_FIELDS, decision_rows)
    _write_csv(output_dir / "settlements.csv", SETTLEMENT_FIELDS, settlement_rows)
    return {
        "market_snapshots": len(snapshot_rows),
        "decisions": len(decision_rows),
        "settlements": len(settlement_rows),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _report_markdown(
    coverage: dict[str, int],
    probability_metrics: list[dict[str, Any]],
    market_type: list[dict[str, Any]],
) -> str:
    lines = [
        "# Feedback-loop calibration report",
        "",
        f"Generated: {_utc_now()}",
        "",
        "## Coverage",
        "",
        f"- Market snapshots: {coverage['market_snapshots']}",
        f"- Decisions: {coverage['decisions']}",
        f"- Settlements: {coverage['settlements']}",
        f"- Graded and linked decisions: {coverage['graded_and_linked']}",
        f"- Unlinked settlements: {coverage['unlinked_settlements']}",
        f"- Independent-model probabilities: {coverage['independent_probabilities']}",
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
        "Market-consensus and final-blended metrics will be identical while the independent-model "
        "column is empty. This is intentional: the report exposes the current market-derived "
        "baseline instead of relabeling it as an independent model.",
        "",
    ]
    return "\n".join(lines)


def generate_report(db_path: Path = DEFAULT_DB_PATH, output_dir: Path = DEFAULT_REPORT_DIR) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
        coverage = {
            "market_snapshots": conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0],
            "decisions": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
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
    edge_buckets = _bucketed(rows, "edge", "edge_bucket", _edge_bucket)
    odds_ranges = _bucketed(rows, "price", "odds_range", _odds_bucket)
    books = _grouped(rows, "book", "book")
    leagues = _grouped(rows, "sport", "league")
    signal_flags = _signal_results(rows)
    play_vs_stand_down = _play_vs_stand_down(rows)
    model_performance = _model_performance(rows)

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
    for filename, label, table in (
        ("market_type.csv", "market_type", market_type),
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
    (output_dir / "summary.json").write_text(
        json.dumps(
            {"coverage": coverage, "probability_metrics": probability_metrics},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        _report_markdown(coverage, probability_metrics, market_type), encoding="utf-8"
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
                allocation_rows.append({
                    "stable_wager_id": r["snapshot_id"],
                    "actionable": str(r.get("board") == "A").lower(),
                    "board": r.get("board"),
                    "units": r.get("legacy_units", 0.0) if as_of_strict else policy.max_wager_units,
                    "event_id": r.get("event_id"),
                    "player_id": r.get("player_id"),
                    "team": None,
                    "market_type": r.get("market_type"),
                    "cluster_id": None,
                    "sportsbook": r.get("book"),
                    "edge_pct": r.get("edge", 0.0),
                })
                
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
    blend_parser.add_argument(
        "--output", type=Path, default=probability_blend.DEFAULT_WEIGHTS_PATH
    )
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
    drawdown_parser.add_argument(
        "--output", type=Path, default=drawdown.DEFAULT_STATE_PATH
    )
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

    replay_parser = subparsers.add_parser("replay-portfolio", help="Chronological portfolio replay.")
    replay_parser.add_argument("--from", dest="start_date", required=True, help="YYYY-MM-DD")
    replay_parser.add_argument("--to", dest="end_date", required=True, help="YYYY-MM-DD")
    replay_parser.add_argument("--policy", type=Path, required=True, help="Path to portfolio risk policy JSON")
    replay_parser.add_argument("--as-of-strict", action="store_true", default=True)

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
            settlement_stats = import_settlements(args.input, args.db)
            print(json.dumps(settlement_stats.__dict__, sort_keys=True))
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
                args.db,
                args.start_date,
                args.end_date,
                args.policy,
                as_of_strict=args.as_of_strict
            )
            print(json.dumps(summary, indent=2, sort_keys=True))
    except (FeedbackError, OSError, sqlite3.Error, ValueError) as exc:
        logger.error("Feedback tool failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
