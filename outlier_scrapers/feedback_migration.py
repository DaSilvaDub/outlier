"""Move the feedback ledger out of the cloud-synced checkout into local data.

The canonical checkout sits behind a cloud-sync filter that truncates large SQLite
writes, so the ledger moved to :func:`paths.local_calibration_dir`. This copies it
there once, through SQLite's backup API, verifies the copy, and keeps the original
renamed beside itself. Nothing is ever deleted, and a second run is a no-op.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from outlier_scrapers import paths

logger = logging.getLogger(__name__)

# One plain-literal query per table (no SQL built from strings).
_COUNT_QUERIES = {
    "market_snapshots": "SELECT COUNT(*) FROM market_snapshots",
    "decisions": "SELECT COUNT(*) FROM decisions",
    "settlements": "SELECT COUNT(*) FROM settlements",
    "pack_snapshot_memberships": "SELECT COUNT(*) FROM pack_snapshot_memberships",
}
VERIFIED_TABLES = tuple(_COUNT_QUERIES)
_SIDECAR_SUFFIXES = ("-wal", "-shm")


class LedgerMigrationError(RuntimeError):
    """The migration refused to run, or its copy failed verification."""


@dataclass(frozen=True)
class LedgerMigration:
    status: str  # "migrated" | "already_migrated" | "nothing_to_migrate"
    source: str
    destination: str
    archived_as: str = ""
    row_counts: dict[str, int] = field(default_factory=dict)


def _sidecar(db_path: Path, suffix: str) -> Path:
    return db_path.with_name(db_path.name + suffix)


def _archive_path(source: Path, today: date) -> Path:
    candidate = source.with_name(f"feedback.migrated-{today.isoformat()}.sqlite3")
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"feedback.migrated-{today.isoformat()}-{counter}.sqlite3")
        counter += 1
    return candidate


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    present = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return {
        table: conn.execute(query).fetchone()[0]
        for table, query in _COUNT_QUERIES.items()
        if table in present
    }


def _integrity_check(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("PRAGMA integrity_check")]


def _copy_and_verify(source: Path, staging: Path) -> dict[str, int]:
    """Back ``source`` up into ``staging``; raise unless the copy is intact and complete."""
    src = sqlite3.connect(source)
    try:
        src.execute("PRAGMA query_only = ON")
        dst = sqlite3.connect(staging)
        try:
            src.backup(dst)
            source_counts = _row_counts(src)
            integrity = _integrity_check(dst)
            copied_counts = _row_counts(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if integrity != ["ok"]:
        raise LedgerMigrationError(
            f"integrity_check failed on the copy of {source}: {integrity[:5]}. The source "
            "is likely damaged; repair it first (python -m outlier_scrapers.feedback recover)."
        )
    if copied_counts != source_counts:
        raise LedgerMigrationError(
            f"Row counts differ after copying {source}: source={source_counts} "
            f"copy={copied_counts}."
        )
    return copied_counts


def _remove_staging(staging: Path) -> None:
    # Only this command ever writes the staging file, so a leftover is ours.
    for path in (staging, *(_sidecar(staging, s) for s in _SIDECAR_SUFFIXES)):
        path.unlink(missing_ok=True)


def _promote_staging(staging: Path, destination: Path, archived: Path) -> None:
    try:
        staging.replace(destination)
    except OSError as exc:
        raise LedgerMigrationError(
            f"The verified copy is at {staging} and the original was archived as "
            f"{archived}, but the copy could not be renamed to {destination} ({exc}). "
            "Re-run the command to finish; nothing was lost."
        ) from exc


def _finish_interrupted(source: Path, staging: Path, destination: Path) -> LedgerMigration:
    """Finish a run that archived the original but died before promoting the copy.

    The staging copy is re-verified against the newest archive before it becomes
    the live ledger, so a stray staging file is never trusted on its own.
    """
    archives = paths.archived_ledgers(source.parent)
    if not archives:
        raise LedgerMigrationError(
            f"{staging} exists but there is no archived original next to {source}; "
            "inspect it by hand."
        )
    archived = archives[-1]
    with closing(sqlite3.connect(archived)) as conn:
        conn.execute("PRAGMA query_only = ON")
        expected = _row_counts(conn)
    with closing(sqlite3.connect(staging)) as conn:
        integrity = _integrity_check(conn)
        counts = _row_counts(conn)
    if integrity != ["ok"] or counts != expected:
        raise LedgerMigrationError(
            f"Interrupted migration: {staging} does not match {archived} "
            f"(integrity={integrity[:3]}, staging={counts}, archive={expected}). "
            "Inspect both by hand."
        )
    _promote_staging(staging, destination, archived)
    logger.info("Finished interrupted ledger migration: %s -> %s", staging, destination)
    return LedgerMigration(
        status="migrated",
        source=str(source),
        destination=str(destination),
        archived_as=str(archived),
        row_counts=counts,
    )


def migrate_ledger(
    source: Path | None = None,
    destination: Path | None = None,
    *,
    today: date | None = None,
) -> LedgerMigration:
    """Copy the in-repo ledger to local data, verify it, and archive the original.

    Order matters for a crash at any step: the copy is staged and verified first,
    the original is archived only after that, and the staged copy becomes the live
    ledger last. If archiving fails (for example a pipeline still holds the file
    open), the live ledger is left exactly where it was.
    """
    if destination is None:
        local_dir = paths.local_calibration_dir()
        if local_dir is None:
            raise LedgerMigrationError(
                "LOCALAPPDATA is not set, so there is no local-data default; pass --dest."
            )
        destination = local_dir / paths.FEEDBACK_DB_NAME
    source = Path(source) if source is not None else paths.legacy_feedback_db_path()
    destination = Path(destination)

    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(destination)):
        raise LedgerMigrationError(f"Source and destination are the same file: {source}")
    staging = destination.with_name(destination.name + ".migrating")
    if not source.exists():
        if staging.exists() and not paths.ledger_file_present(destination):
            return _finish_interrupted(source, staging, destination)
        status = "already_migrated" if destination.exists() else "nothing_to_migrate"
        return LedgerMigration(status=status, source=str(source), destination=str(destination))
    # A 0-byte destination is what a bare sqlite3.connect() leaves behind: no data,
    # so the verified copy may replace it.
    if paths.ledger_file_present(destination):
        raise LedgerMigrationError(
            f"Both {source} and {destination} exist. Refusing to overwrite either; "
            "compare them and move one aside by hand."
        )
    wal = _sidecar(source, "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise LedgerMigrationError(
            f"{wal} holds uncheckpointed writes: a pipeline is running or the last one "
            "stopped uncleanly. Stop every pipeline; if none is running, checkpoint with "
            f'python -m outlier_scrapers.feedback --db "{source}" init, then re-run.'
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_staging(staging)
    try:
        counts = _copy_and_verify(source, staging)
    except (LedgerMigrationError, sqlite3.Error):
        _remove_staging(staging)
        raise

    archived = _archive_path(source, today or date.today())
    try:
        source.rename(archived)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise LedgerMigrationError(
            f"Copy verified, but {source} could not be renamed ({exc}). It is probably "
            "open in a running pipeline; stop it and re-run. Nothing was changed."
        ) from exc
    # From here on the verified copy only exists as staging; a crash or error now is
    # finished by re-running the command (see _finish_interrupted).
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = _sidecar(source, suffix)
        if sidecar.exists():
            sidecar.rename(_sidecar(archived, suffix))
    _promote_staging(staging, destination, archived)
    logger.info("Feedback ledger migrated: %s -> %s (archived %s)", source, destination, archived)
    return LedgerMigration(
        status="migrated",
        source=str(source),
        destination=str(destination),
        archived_as=str(archived),
        row_counts=counts,
    )
