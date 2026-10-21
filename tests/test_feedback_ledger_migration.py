"""Moving the ledger to local data: refusal guard and the migrate-ledger command.

Every path here lives under tmp_path; LOCALAPPDATA and the canonical dir are
monkeypatched, so neither the real local ledger nor the in-repo one is touched.
"""

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from outlier_scrapers import feedback, feedback_db, feedback_migration, pack_publish, paths
from outlier_scrapers.feedback_migration import LedgerMigrationError, migrate_ledger
from outlier_scrapers.paths import LedgerMigrationRequired

TODAY = date(2026, 10, 21)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """Fake canonical calibration dir + LOCALAPPDATA; returns (legacy, local) ledger paths."""
    canonical = tmp_path / "canonical" / "calibration"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(paths, "CANONICAL_CALIBRATION_DIR", canonical)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "checkout")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)
    local = tmp_path / "AppData" / "Local" / "outlier" / "calibration" / "feedback.sqlite3"
    return canonical / "feedback.sqlite3", local


def _seed(db_path: Path, snapshots: int = 3) -> None:
    feedback_db.initialize_database(db_path)
    # closing(): sqlite3's own context manager commits but leaves the connection
    # (and its WAL frames) open, which the migration rightly refuses.
    with closing(sqlite3.connect(db_path)) as conn, conn:
        for i in range(snapshots):
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    selection, created_at
                ) VALUES (?, '2026-10-01T12:00:00Z', 'MLB', 'evt', 'mkt', ?, 'X', 'now')
                """,
                (f"snap-{i}", f"out-{i}"),
            )


def _count(db_path: Path, table: str = "market_snapshots") -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- refusal guard ---------------------------------------------------------


def test_init_refuses_empty_local_ledger_while_legacy_exists(layout):
    legacy, local = layout
    _seed(legacy)

    with pytest.raises(LedgerMigrationRequired, match="migrate-ledger"):
        feedback_db.initialize_database(local)
    assert not local.exists()


def test_enforce_gate_refuses_instead_of_reporting_zero_shadow_days(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy)
    monkeypatch.setattr(paths, "FEEDBACK_DB_PATH", local)

    with pytest.raises(LedgerMigrationRequired):
        pack_publish._feedback_db_path()


def test_guard_ignores_explicit_non_default_paths(layout, tmp_path):
    legacy, _local = layout
    _seed(legacy)
    elsewhere = tmp_path / "elsewhere" / "feedback.sqlite3"

    assert feedback_db.initialize_database(elsewhere) == elsewhere


def test_fresh_machine_without_any_ledger_initializes_locally(layout):
    _legacy, local = layout

    assert feedback_db.initialize_database(local) == local
    assert local.exists()


def test_guard_is_inert_without_localappdata(layout, monkeypatch):
    legacy, _local = layout
    _seed(legacy)
    monkeypatch.delenv("LOCALAPPDATA")

    paths.require_migrated_ledger(legacy)  # CI / cloud: the in-repo ledger is the default


def test_lost_local_ledger_after_migration_refuses_to_restart_from_zero(layout):
    legacy, local = layout
    _seed(legacy)
    migrate_ledger(today=TODAY)
    local.rename(local.with_name("moved-away.sqlite3"))

    with pytest.raises(LedgerMigrationRequired, match="feedback.migrated-2026-10-21"):
        feedback_db.initialize_database(local)


def test_zero_byte_default_does_not_disarm_the_guard(layout):
    """A bare sqlite3.connect() elsewhere leaves 0 bytes; that is not a migrated ledger."""
    legacy, local = layout
    _seed(legacy)
    local.parent.mkdir(parents=True)
    local.touch()

    with pytest.raises(LedgerMigrationRequired, match="migrate-ledger"):
        feedback_db.initialize_database(local)
    assert local.stat().st_size == 0


def test_env_override_equal_to_default_is_not_guarded(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy)
    monkeypatch.setenv("OUTLIER_FEEDBACK_DB", str(local))

    assert feedback_db.initialize_database(local) == local


# --- migrate-ledger ----------------------------------------------------------


def test_migrate_replaces_a_zero_byte_destination(layout):
    legacy, local = layout
    _seed(legacy, snapshots=2)
    local.parent.mkdir(parents=True)
    local.touch()

    assert migrate_ledger(today=TODAY).status == "migrated"
    assert _count(local) == 2


def test_migrate_finishes_a_run_interrupted_before_promotion(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy, snapshots=4)
    real_promote = feedback_migration._promote_staging

    def crash(*args):
        raise KeyboardInterrupt  # dies after archiving, before the copy goes live

    monkeypatch.setattr(feedback_migration, "_promote_staging", crash)
    with pytest.raises(KeyboardInterrupt):
        migrate_ledger(today=TODAY)
    staging = local.with_name("feedback.sqlite3.migrating")
    assert not legacy.exists() and staging.exists() and not local.exists()
    with pytest.raises(LedgerMigrationRequired, match="interrupted"):
        feedback_db.initialize_database(local)

    monkeypatch.setattr(feedback_migration, "_promote_staging", real_promote)
    result = migrate_ledger(today=TODAY)

    assert result.status == "migrated"
    assert result.archived_as.endswith("feedback.migrated-2026-10-21.sqlite3")
    assert _count(local) == 4
    assert not staging.exists()


def test_interrupted_staging_that_does_not_match_the_archive_is_not_trusted(layout):
    legacy, local = layout
    _seed(legacy, snapshots=4)
    legacy.rename(legacy.with_name("feedback.migrated-2026-10-21.sqlite3"))
    staging = local.with_name("feedback.sqlite3.migrating")
    staging.parent.mkdir(parents=True)
    _seed(staging, snapshots=1)

    with pytest.raises(LedgerMigrationError, match="does not match"):
        migrate_ledger(today=TODAY)
    assert not local.exists()
    assert staging.exists()


def test_failed_verification_removes_only_its_own_staging(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy)
    monkeypatch.setattr(feedback_migration, "_integrity_check", lambda conn: ["damaged"])

    with pytest.raises(LedgerMigrationError):
        migrate_ledger(today=TODAY)
    assert legacy.exists()
    assert not local.with_name("feedback.sqlite3.migrating").exists()


def test_migrate_copies_verifies_and_archives(layout):
    legacy, local = layout
    _seed(legacy, snapshots=5)

    result = migrate_ledger(today=TODAY)

    archived = legacy.with_name("feedback.migrated-2026-10-21.sqlite3")
    assert result.status == "migrated"
    assert result.destination == str(local)
    assert result.archived_as == str(archived)
    assert result.row_counts["market_snapshots"] == 5
    assert set(result.row_counts) == set(feedback_migration.VERIFIED_TABLES)
    assert not legacy.exists()
    assert _count(archived) == 5  # original kept, never deleted
    assert _count(local) == 5
    assert not local.with_name("feedback.sqlite3.migrating").exists()
    # The migrated ledger is now the one writers open.
    assert feedback_db.initialize_database(local) == local


def test_migrate_is_idempotent(layout):
    legacy, _local = layout
    _seed(legacy)
    migrate_ledger(today=TODAY)

    second = migrate_ledger(today=TODAY)

    assert second.status == "already_migrated"
    assert sorted(p.name for p in legacy.parent.iterdir()) == [
        "feedback.migrated-2026-10-21.sqlite3"
    ]


def test_migrate_with_nothing_to_migrate_creates_nothing(layout):
    _legacy, local = layout

    assert migrate_ledger(today=TODAY).status == "nothing_to_migrate"
    assert not local.exists()


def test_migrate_refuses_when_both_ledgers_exist(layout):
    legacy, local = layout
    _seed(legacy)
    staged = local.with_name("staged.sqlite3")  # init at the default itself is guarded
    _seed(staged, snapshots=1)
    staged.rename(local)

    with pytest.raises(LedgerMigrationError, match="Refusing to overwrite"):
        migrate_ledger(today=TODAY)
    assert _count(legacy) == 3
    assert _count(local) == 1


def test_migrate_refuses_with_uncheckpointed_wal(layout):
    legacy, local = layout
    _seed(legacy)
    legacy.with_name("feedback.sqlite3-wal").write_bytes(b"pending frames")

    with pytest.raises(LedgerMigrationError, match="uncheckpointed"):
        migrate_ledger(today=TODAY)
    assert legacy.exists()
    assert not local.exists()


def test_migrate_does_not_overwrite_an_earlier_archive(layout):
    legacy, _local = layout
    earlier = legacy.with_name("feedback.migrated-2026-10-21.sqlite3")
    earlier.write_bytes(b"earlier archive")
    _seed(legacy)

    result = migrate_ledger(today=TODAY)

    assert result.archived_as.endswith("feedback.migrated-2026-10-21-2.sqlite3")
    assert earlier.read_bytes() == b"earlier archive"


def test_migrate_aborts_on_row_count_mismatch(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy)
    real_counts = feedback_migration._row_counts
    calls = {"n": 0}

    def skewed(conn):
        calls["n"] += 1
        counts = real_counts(conn)
        if calls["n"] == 2:  # the copy's counts
            counts["market_snapshots"] -= 1
        return counts

    monkeypatch.setattr(feedback_migration, "_row_counts", skewed)

    with pytest.raises(LedgerMigrationError, match="Row counts differ"):
        migrate_ledger(today=TODAY)
    assert legacy.exists()  # never archived on a failed verification
    assert not local.exists()


def test_migrate_aborts_on_failed_integrity_check(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy)
    monkeypatch.setattr(
        feedback_migration,
        "_integrity_check",
        lambda conn: ["*** in database main *** Page 7: btree damaged"],
    )

    with pytest.raises(LedgerMigrationError, match="integrity_check failed"):
        migrate_ledger(today=TODAY)
    assert legacy.exists()
    assert not local.exists()


def test_migrate_leaves_source_live_when_it_cannot_be_archived(layout, monkeypatch):
    legacy, local = layout
    _seed(legacy)

    def refuse(self, target):
        raise PermissionError("in use by another process")

    monkeypatch.setattr(Path, "rename", refuse)

    with pytest.raises(LedgerMigrationError, match="could not be renamed"):
        migrate_ledger(today=TODAY)
    assert legacy.exists()
    assert not local.exists()
    assert not local.with_name("feedback.sqlite3.migrating").exists()


def test_migrate_requires_dest_without_localappdata(layout, monkeypatch):
    legacy, _local = layout
    _seed(legacy)
    monkeypatch.delenv("LOCALAPPDATA")

    with pytest.raises(LedgerMigrationError, match="--dest"):
        migrate_ledger(today=TODAY)


def test_cli_migrate_ledger_prints_result(layout, capsys):
    legacy, local = layout
    _seed(legacy)

    assert feedback.main(["migrate-ledger"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "migrated"
    assert out["destination"] == str(local)
    assert local.exists()


def test_cli_reports_refusal_without_traceback(layout):
    legacy, local = layout
    _seed(legacy)

    assert feedback.main(["--db", str(local), "init"]) == 1
    assert not local.exists()
