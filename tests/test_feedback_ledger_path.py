"""One feedback ledger path for every reader and writer (paths.FEEDBACK_DB_PATH)."""

import sqlite3
import sys
from pathlib import Path

import pytest

from outlier_scrapers import (
    feedback,
    feedback_db,
    feedback_reporting,
    pack_publish,
    paths,
    so_eval,
    so_promotion,
)
from scripts import backfill_projection_hashes as backfill
from scripts import diagnose_so_gamelog_calibration as diagnose


@pytest.fixture
def canonical(tmp_path, monkeypatch):
    """No LOCALAPPDATA (CI / cloud sandbox) and a fake checkout; returns its calibration dir.

    There is no hardcoded canonical checkout any more: the calibration dir is this
    checkout's own (or OUTLIER_CALIBRATION_DIR).
    """
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(paths, "PROJECT_ROOT", checkout)
    monkeypatch.delenv("OUTLIER_CALIBRATION_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return checkout / "calibration"


@pytest.fixture
def local_appdata(canonical, monkeypatch, tmp_path):
    """A Windows-like host: LOCALAPPDATA points at tmp_path."""
    appdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    return appdata / "outlier" / "calibration"


def test_env_var_wins_over_everything(local_appdata, canonical, monkeypatch, tmp_path):
    canonical.mkdir(parents=True)
    local_appdata.mkdir(parents=True)
    override = tmp_path / "elsewhere" / "ledger.sqlite3"
    monkeypatch.setenv("OUTLIER_FEEDBACK_DB", str(override))

    assert paths.resolve_feedback_db_path() == override


def test_local_data_dir_wins_over_canonical(local_appdata, canonical, monkeypatch):
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)
    canonical.mkdir(parents=True)

    # Chosen even before it exists: the ledger moved, the directory is created on init.
    assert paths.resolve_feedback_db_path() == local_appdata / "feedback.sqlite3"


def test_blank_local_appdata_is_ignored(canonical, monkeypatch):
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "   ")
    canonical.mkdir(parents=True)

    assert paths.local_calibration_dir() is None
    assert paths.resolve_feedback_db_path() == canonical / "feedback.sqlite3"


def test_report_dir_stays_in_repo_with_local_data(local_appdata, canonical, monkeypatch):
    monkeypatch.delenv("OUTLIER_FEEDBACK_REPORT_DIR", raising=False)
    canonical.mkdir(parents=True)

    assert paths.resolve_feedback_report_dir() == canonical / "reports" / "latest"


def test_checkout_ledger_used_when_its_directory_exists(canonical, monkeypatch):
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)
    canonical.mkdir(parents=True)

    assert paths.resolve_feedback_db_path() == canonical / "feedback.sqlite3"


def test_checkout_ledger_path_does_not_need_the_directory_to_exist(canonical, monkeypatch):
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)

    assert not canonical.exists()
    assert paths.resolve_feedback_db_path() == canonical / "feedback.sqlite3"


def test_calibration_dir_env_override_moves_every_calibration_path(canonical, monkeypatch, tmp_path):
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)
    monkeypatch.delenv("OUTLIER_FEEDBACK_REPORT_DIR", raising=False)
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("OUTLIER_CALIBRATION_DIR", str(elsewhere))

    assert paths.resolve_feedback_db_path() == elsewhere / "feedback.sqlite3"
    assert paths.legacy_feedback_db_path() == elsewhere / "feedback.sqlite3"
    assert paths.resolve_feedback_report_dir() == elsewhere / "reports" / "latest"


def test_worktree_finds_the_main_checkouts_legacy_ledger_through_git(tmp_path, monkeypatch):
    """Found from the worktree's .git file, with no hardcoded path to any checkout."""
    main = tmp_path / "main"
    gitdir = main / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    monkeypatch.setattr(paths, "PROJECT_ROOT", worktree)
    monkeypatch.delenv("OUTLIER_CALIBRATION_DIR", raising=False)

    assert paths.main_checkout_root() == main
    assert paths.legacy_feedback_db_path() == main / "calibration" / "feedback.sqlite3"
    # Anything this run writes still lands in the worktree's own calibration dir.
    assert paths._repo_calibration_dir() == worktree / "calibration"


def test_plain_checkout_is_its_own_main_checkout(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)

    assert paths.main_checkout_root() == tmp_path


def test_blank_env_var_is_ignored(canonical, monkeypatch):
    monkeypatch.setenv("OUTLIER_FEEDBACK_DB", "  ")
    canonical.mkdir(parents=True)

    assert paths.resolve_feedback_db_path() == canonical / "feedback.sqlite3"


def test_report_dir_follows_the_same_resolution(canonical, monkeypatch, tmp_path):
    monkeypatch.delenv("OUTLIER_FEEDBACK_REPORT_DIR", raising=False)
    expected = tmp_path / "checkout" / "calibration" / "reports" / "latest"
    assert paths.resolve_feedback_report_dir() == expected

    canonical.mkdir(parents=True)
    assert paths.resolve_feedback_report_dir() == expected

    override = tmp_path / "reports-override"
    monkeypatch.setenv("OUTLIER_FEEDBACK_REPORT_DIR", str(override))
    assert paths.resolve_feedback_report_dir() == override


@pytest.mark.uses_real_ledger
def test_every_module_default_is_the_single_resolved_path():
    for path in (
        feedback_db.DEFAULT_DB_PATH,
        feedback.DEFAULT_DB_PATH,
        so_eval.DEFAULT_DB,
        so_promotion.DEFAULT_DB,
    ):
        assert path == paths.FEEDBACK_DB_PATH
    assert feedback_reporting.DEFAULT_REPORT_DIR == paths.FEEDBACK_REPORT_DIR
    assert feedback.DEFAULT_REPORT_DIR == paths.FEEDBACK_REPORT_DIR


@pytest.mark.uses_enforce_mode
def test_enforce_gate_reads_the_resolved_ledger(tmp_path, monkeypatch):
    """pack_publish used PROJECT_ROOT, so a worktree's gate read an empty ledger."""
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "empty-checkout")
    ledger = tmp_path / "shared" / "feedback.sqlite3"
    monkeypatch.setattr(paths, "FEEDBACK_DB_PATH", ledger)

    assert pack_publish._feedback_db_path() == ledger


def test_generate_report_resolves_output_dir_at_call_time(tmp_path, monkeypatch):
    target = tmp_path / "reports"
    monkeypatch.setattr(feedback_reporting, "DEFAULT_REPORT_DIR", target)
    db_path = tmp_path / "ledger.sqlite3"
    feedback_db.initialize_database(db_path)

    out = feedback_reporting.generate_report(db_path)

    assert Path(out).resolve().is_relative_to(target.resolve())


def _insert_snapshot(
    db_path: Path,
    *,
    selection: str,
    digest: str = "",
    flags: str = "",
    with_settlement: bool = False,
) -> None:
    feedback_db.initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                selection, market_type, line, price, decimal_price,
                market_consensus_prob, independent_model_prob, implied_prob,
                projection_feature_hash, projection_quality_flags, created_at
            ) VALUES (?, ?, 'MLB', 'evt-1', 'mkt-1', 'out-1', ?, 'SO', '5.5', -110, 1.91,
                      0.52, 0.71, 0.50, ?, ?, '2026-08-21T12:00:00Z')
            """,
            (selection, "2026-08-21T12:00:00Z", selection, digest, flags),
        )
        if with_settlement:
            conn.execute(
                """
                INSERT INTO settlements (
                    settlement_id, snapshot_id, event_id, market_id,
                    win_loss_push, actual_result, closing_line, clv_line, pnl, settled_at
                ) VALUES (?, ?, 'evt-1', 'mkt-1', 'W', '6', '5.5', 0.0, 1.0,
                          '2026-08-22T04:00:00Z')
                """,
                (f"settle-{selection}", selection),
            )


def test_backfill_script_writes_outlier_feedback_db(tmp_path, monkeypatch, capsys):
    """The backfill writer fills the env ledger and leaves every other ledger alone."""
    ledger = tmp_path / "override" / "feedback.sqlite3"
    monkeypatch.setenv("OUTLIER_FEEDBACK_DB", str(ledger))
    selection = "Pitcher Strikeouts OVER 5.5"
    _insert_snapshot(ledger, selection=selection)
    decoy = paths.FEEDBACK_DB_PATH
    _insert_snapshot(decoy, selection=selection)

    pack = tmp_path / "packs" / "2026-08-21"
    pack.mkdir(parents=True)
    (pack / "candidates.csv").write_text(
        "event_id,market_id,outcome_id,selection,"
        "projection_feature_hash,projection_quality_flags\n"
        "evt-1,mkt-1,out-1,Pitcher Strikeouts OVER 5.5,so-starter-gamelog-v1,clean\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_projection_hashes.py",
            "--packs-root",
            str(tmp_path / "packs"),
            "--dates",
            "2026-08-21",
        ],
    )

    assert backfill.main() == 0
    assert "updated" in capsys.readouterr().out

    with sqlite3.connect(ledger) as conn:
        updated = conn.execute(
            "SELECT projection_feature_hash, projection_quality_flags FROM market_snapshots"
        ).fetchone()
    with sqlite3.connect(decoy) as conn:
        untouched = conn.execute(
            "SELECT projection_feature_hash, projection_quality_flags FROM market_snapshots"
        ).fetchone()

    assert updated == ("so-starter-gamelog-v1", "clean")
    assert untouched == ("", "")


def test_diagnose_script_reads_outlier_feedback_db(tmp_path, monkeypatch, capsys):
    """The diagnose reader counts the env ledger, not the import-time default."""
    ledger = tmp_path / "override" / "feedback.sqlite3"
    monkeypatch.setenv("OUTLIER_FEEDBACK_DB", str(ledger))
    env_selection = "Env Pitcher Strikeouts OVER 4.5"
    decoy_selection = "Decoy Pitcher Strikeouts OVER 9.5"
    _insert_snapshot(ledger, selection=env_selection, digest=diagnose.GAMELOG, with_settlement=True)
    _insert_snapshot(
        paths.FEEDBACK_DB_PATH,
        selection=decoy_selection,
        digest=diagnose.GAMELOG,
        with_settlement=True,
    )

    real_json = diagnose.DIAGNOSIS_JSON
    before = real_json.read_bytes()
    monkeypatch.setattr(diagnose, "DIAGNOSIS_JSON", tmp_path / "diagnosis.json")
    try:
        diagnose.main()
        out = capsys.readouterr().out
    finally:
        if real_json.exists() and real_json.read_bytes() != before:
            real_json.write_bytes(before)

    assert real_json.read_bytes() == before
    assert "gamelog_settled_n=1" in out
    assert env_selection in out
    assert decoy_selection not in out
    written = (tmp_path / "diagnosis.json").read_text(encoding="utf-8")
    assert env_selection in written
    assert decoy_selection not in written
