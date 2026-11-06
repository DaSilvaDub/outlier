"""Tests for scripts/organize_desktop_today.py archive guards (disk-full incident 2026-11-03)."""

from __future__ import annotations

import os
import shutil
import sys
from collections import namedtuple
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import organize_desktop_today as org  # noqa: E402

GB = 1024**3
Usage = namedtuple("Usage", "total used free")


def _plenty(_path):
    return Usage(total=500 * GB, used=100 * GB, free=400 * GB)


def _seed_fresh(root: Path, sport: str = "NBA") -> Path:
    fresh = root / "Fresh_Data" / sport
    (fresh / "01_Packs").mkdir(parents=True)
    (fresh / "05_Audits").mkdir(parents=True)
    (fresh / "01_Packs" / "pack.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (fresh / "05_Audits" / "audit.md").write_text("# audit\n", encoding="utf-8")
    (fresh / "05_Audits" / "nba_trial_ledger.sqlite3").write_bytes(b"x" * 4096)
    (fresh / "05_Audits" / "nba_trial_ledger.sqlite3-wal").write_bytes(b"w" * 64)
    return fresh


def _runs(root: Path, sport: str = "NBA") -> list[Path]:
    older = root / "Older_Data" / sport
    return sorted(older.glob("Run_*")) if older.exists() else []


def test_archive_skips_database_files(tmp_path):
    _seed_fresh(tmp_path)
    result = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    assert result.status == "archived"
    (run,) = _runs(tmp_path)
    assert (run / "01_Packs" / "pack.csv").is_file()
    assert (run / "05_Audits" / "audit.md").is_file()
    assert not (run / "05_Audits" / "nba_trial_ledger.sqlite3").exists()
    assert not (run / "05_Audits" / "nba_trial_ledger.sqlite3-wal").exists()
    # Fresh copy of the ledger is left untouched.
    assert (tmp_path / "Fresh_Data" / "NBA" / "05_Audits" / "nba_trial_ledger.sqlite3").is_file()


def test_unchanged_fresh_tree_is_not_archived_twice(tmp_path):
    _seed_fresh(tmp_path)
    first = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)
    second = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    assert first.status == "archived"
    assert second.status == "unchanged"
    assert len(_runs(tmp_path)) == 1


def test_changed_file_triggers_new_archive(tmp_path):
    fresh = _seed_fresh(tmp_path)
    org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)
    pack = fresh / "01_Packs" / "pack.csv"
    pack.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    st = pack.stat()
    os.utime(pack, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    result = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    assert result.status == "archived"
    assert len(_runs(tmp_path)) == 2


def test_ledger_only_change_does_not_trigger_archive(tmp_path):
    fresh = _seed_fresh(tmp_path)
    org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)
    (fresh / "05_Audits" / "nba_trial_ledger.sqlite3").write_bytes(b"y" * 8192)

    result = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    assert result.status == "unchanged"


def test_low_disk_refuses_to_archive(tmp_path):
    _seed_fresh(tmp_path)

    def low(_path):
        return Usage(total=500 * GB, used=496 * GB, free=4 * GB)

    with pytest.raises(org.InsufficientDiskSpace, match="4.00 GB free"):
        org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=low, min_free_gb=5.0)
    assert _runs(tmp_path) == []


def test_empty_fresh_tree_creates_subdirs_without_archive(tmp_path):
    result = org.archive_and_refresh_sport("NFL", today_root=tmp_path, disk_usage=_plenty)

    assert result.status == "empty"
    assert _runs(tmp_path, "NFL") == []
    for sub in org.SUBDIRS:
        assert (tmp_path / "Fresh_Data" / "NFL" / sub).is_dir()


def test_copy_failure_propagates_and_leaves_no_baseline(tmp_path, monkeypatch):
    _seed_fresh(tmp_path)

    def boom(*_a, **_k):
        raise shutil.Error([("src", "dst", "[WinError 112] There is not enough space on the disk")])

    monkeypatch.setattr(org.shutil, "copytree", boom)
    with pytest.raises(shutil.Error):
        org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    # A half-written run must not become the "unchanged" baseline for the next run.
    monkeypatch.undo()
    retry = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)
    assert retry.status == "archived"


def test_failed_copy_removes_its_partial_run_dir(tmp_path, monkeypatch):
    _seed_fresh(tmp_path)

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(org.shutil, "copytree", boom)
    for _ in range(3):
        with pytest.raises(OSError):
            org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    assert _runs(tmp_path) == []


def test_corrupt_manifest_is_skipped_not_fatal(tmp_path):
    _seed_fresh(tmp_path)
    first = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)
    (first.archive_dir / org.MANIFEST_NAME).write_text('{"01_Packs/pack.csv": [8', encoding="utf-8")

    result = org.archive_and_refresh_sport("NBA", today_root=tmp_path, disk_usage=_plenty)

    assert result.status == "archived"
    assert len(_runs(tmp_path)) == 2


def test_main_returns_nonzero_when_any_sport_fails(tmp_path, monkeypatch, capsys):
    _seed_fresh(tmp_path, "NBA")

    def low(_path):
        return Usage(total=500 * GB, used=499 * GB, free=1 * GB)

    monkeypatch.setattr(org.shutil, "disk_usage", low)
    rc = org.main(["--sport", "ALL", "--today-root", str(tmp_path)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "NBA" in err and "Insufficient disk space" in err
    # Sports with nothing to archive still get their folders.
    assert (tmp_path / "Fresh_Data" / "NFL" / "01_Packs").is_dir()


def test_main_accepts_date_argument(tmp_path, monkeypatch):
    monkeypatch.setattr(org.shutil, "disk_usage", _plenty)
    assert org.main(["--sport", "NFL", "--date", "2026-11-04", "--today-root", str(tmp_path)]) == 0
