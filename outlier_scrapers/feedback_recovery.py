"""Salvage a corrupted feedback ledger into a fresh, schema-current database."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from outlier_scrapers.feedback_db import (
    DECISION_FIELDS,
    FeedbackError,
    MARKET_SNAPSHOT_FIELDS,
    PACK_MEMBERSHIP_FIELDS,
    SETTLEMENT_FIELDS,
    _utc_now,
    initialize_database,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoverStats:
    market_snapshots: int
    pack_snapshot_memberships: int
    decisions: int
    settlements: int
    skipped_rows: int
    used_sqlite_cli: bool


# How many consecutive blind resume attempts to make before accepting that the
# rest of the table is unreachable. The stride doubles each time, so this
# clears any damaged region a real file can contain long before it is spent.
_MAX_SALVAGE_SKIPS = 64

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
    """Read every row sqlite3 will still hand back, resuming past damage.

    ``sqlite3`` raises ``DatabaseError`` once a cursor walks into a damaged
    page. A single sequential ``SELECT`` therefore surrenders the whole
    remainder of the table at the first bad page -- on the production ledger
    that meant 253 of ~148,000 rows.

    Corruption is almost always local to one subtree, so after a failure the
    scan re-enters the table with a ``rowid`` seek past the rows it already
    has. That descends the b-tree again through different interior cells and
    picks up everything below the damaged branch. When even the seek fails,
    the resume point advances by a doubling stride until it clears the
    damaged region or the attempts are exhausted, which also guarantees
    termination.
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
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if table not in existing_tables:
                return rows, skipped
            existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            select_fields = [field for field in fields if field in existing_columns]
            if not select_fields:
                return rows, skipped
            # A probe read: it fails the same way a corrupt schema page or a
            # missing rowid (WITHOUT ROWID table) would, and lets the resume
            # loop below assume both are usable.
            conn.execute(f"SELECT rowid, {', '.join(select_fields)} FROM {table} LIMIT 0")
        except sqlite3.Error as exc:
            logger.warning("Could not scan %s (schema/page corruption): %s", table, exc)
            skipped += 1
            return rows, skipped
        resume_from: int | None = None
        stride = 1
        consecutive_failures = 0
        while consecutive_failures <= _MAX_SALVAGE_SKIPS:
            try:
                if resume_from is None:
                    # An unfiltered scan enters at the leftmost leaf; a
                    # `rowid >= ?` seek has to descend through the interior
                    # cells instead. When those cells are the damaged part --
                    # as in a wrecked root page -- the plain scan still
                    # returns rows the seek cannot reach, so always start
                    # with it and only seek to resume.
                    cursor = conn.execute(
                        f"SELECT rowid AS _salvage_rowid, {', '.join(select_fields)} "
                        f"FROM {table}"
                    )
                else:
                    cursor = conn.execute(
                        f"SELECT rowid AS _salvage_rowid, {', '.join(select_fields)} "
                        f"FROM {table} WHERE rowid >= ? ORDER BY rowid",
                        (resume_from,),
                    )
            except sqlite3.DatabaseError:
                skipped += 1
                consecutive_failures += 1
                resume_from = (resume_from or 0) + stride
                stride *= 2
                continue
            exhausted = False
            last_rowid: int | None = None
            while True:
                try:
                    row = cursor.fetchone()
                except sqlite3.DatabaseError:
                    skipped += 1
                    break
                if row is None:
                    exhausted = True
                    break
                record = dict(row)
                last_rowid = record.pop("_salvage_rowid")
                rows.append(record)
            if exhausted:
                break
            if last_rowid is None:
                consecutive_failures += 1
                resume_from = (resume_from or 0) + stride
                stride *= 2
            else:
                consecutive_failures = 0
                resume_from = last_rowid + 1
                stride = 1
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
