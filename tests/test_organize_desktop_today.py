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


# --- Fresh_Data staging (publication hold, no out-of-repo generators) -------------------


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_is_current_artifact_keeps_undated_and_active_dates_only():
    dates = ["2026-11-10"]
    assert org.is_current_artifact("nba_only.csv", dates)
    assert org.is_current_artifact("nba_only_2026-11-10.csv", dates)
    assert not org.is_current_artifact("nba_only_2026-11-03.csv", dates)


def test_nba_sources_come_only_from_repo_exports(tmp_path):
    exports = tmp_path / "data" / "NBA" / "exports"
    _touch(exports / "nba_only.csv")
    _touch(exports / "NBA_100_hit_rate.csv")
    _touch(exports / "nba_only_2026-11-10.csv")
    _touch(exports / "nba_only_2026-11-03.csv")

    names = sorted(src.name for src, sub in org.nba_sources(tmp_path, "2026-11-10"))

    assert names == ["NBA_100_hit_rate.csv", "nba_only.csv", "nba_only_2026-11-10.csv"]


def test_organizer_has_no_out_of_repo_generator_path():
    import ast

    tree = ast.parse((REPO / "scripts" / "organize_desktop_today.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    string_literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    path_literals = [s for s in string_literals if s.startswith(("C:\\", "G:\\"))]

    assert "export_deliverables" not in imported
    assert not any(".gemini" in s or "nba-props-pipeline" in s for s in path_literals)


def test_nfl_sources_route_reports_and_audits(tmp_path):
    _touch(tmp_path / "data" / "NFL" / "exports" / "NFL_100_hit_rate.csv")
    _touch(tmp_path / "data" / "NFL" / "normalized" / "nfl_props_2026-11-10.json")
    _touch(tmp_path / "data" / "NFL" / "normalized" / "nfl_props_latest.json")  # not staged
    _touch(tmp_path / "reports" / "NFL" / "2026-11-10_master_daily_report.md")
    _touch(tmp_path / "reports" / "NFL" / "2026-11-10_Postgame_Prediction_Audit.md")

    routed = {src.name: sub for src, sub in org.nfl_sources(tmp_path, "2026-11-10")}

    assert routed == {
        "NFL_100_hit_rate.csv": "01_Packs",
        "nfl_props_2026-11-10.json": "02_Full_Pipeline",
        "2026-11-10_master_daily_report.md": "04_Reports",
        "2026-11-10_Postgame_Prediction_Audit.md": "05_Audits",
    }


def test_ncaa_lookahead_finds_next_slate_in_any_year(tmp_path):
    reports = tmp_path / "data" / "reports"
    _touch(reports / "2026-12-31" / "2026-12-31-master-v2-sim.json", '{"games": []}')
    _touch(reports / "2027-01-01" / "2027-01-01-master-v2-sim.json", '{"games": [1]}')

    assert org.ncaa_active_dates(tmp_path, "2026-12-31") == ["2026-12-31", "2027-01-01"]


def test_ncaa_lookahead_corrupt_sim_raises(tmp_path):
    _touch(tmp_path / "data" / "reports" / "2026-11-10" / "2026-11-10-master-v2-sim.json", "{bad")
    with pytest.raises(org.OrganizerError):
        org.ncaa_active_dates(tmp_path, "2026-11-10")


def test_populate_copies_into_every_root(tmp_path):
    repo = tmp_path / "repo"
    _touch(repo / "data" / "NBA" / "exports" / "nba_only.csv", "a,b\n")
    local, drive = tmp_path / "local", tmp_path / "drive"

    copied = org.populate_sport_deliverables(
        "NBA", "2026-11-10", [local, drive], repo_root=repo, cfb_root=tmp_path / "nocfb"
    )

    assert copied == 2
    for root in (local, drive):
        assert (root / "Fresh_Data" / "NBA" / "01_Packs" / "nba_only.csv").is_file()


def test_staging_roots_skip_drive_is_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(org, "get_drive_today", lambda: tmp_path / "drive")
    assert org.resolve_staging_roots(tmp_path, skip_drive=True) == ([tmp_path], False)


def test_staging_roots_hold_blocks_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(org, "get_drive_today", lambda: tmp_path / "drive")
    monkeypatch.setattr(org, "publication_hold_active", lambda _root: True)
    assert org.resolve_staging_roots(tmp_path, skip_drive=False) == ([tmp_path], True)


def test_staging_roots_unreadable_hold_is_treated_as_held(tmp_path, monkeypatch):
    def broken(_root):
        raise org.PublicationHoldError("bad json")

    monkeypatch.setattr(org, "get_drive_today", lambda: tmp_path / "drive")
    monkeypatch.setattr(org, "publication_hold_active", broken)
    assert org.resolve_staging_roots(tmp_path, skip_drive=False) == ([tmp_path], True)


def test_staging_roots_no_hold_includes_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(org, "get_drive_today", lambda: tmp_path / "drive")
    monkeypatch.setattr(org, "publication_hold_active", lambda _root: False)
    roots, held = org.resolve_staging_roots(tmp_path, skip_drive=False)
    assert roots == [tmp_path, tmp_path / "drive"] and held is False


def test_main_exits_3_when_drive_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(org.shutil, "disk_usage", _plenty)
    monkeypatch.setattr(org, "DESKTOP_TODAY", tmp_path)
    monkeypatch.setattr(org, "get_drive_today", lambda: tmp_path / "drive")
    monkeypatch.setattr(org, "publication_hold_active", lambda _root: True)
    staged: list[list[Path]] = []
    monkeypatch.setattr(
        org, "populate_sport_deliverables", lambda sp, d, roots: staged.append(roots) or 0
    )

    rc = org.main(["--sport", "NBA", "--date", "2026-11-10", "--today-root", str(tmp_path)])

    assert rc == org.DRIVE_HELD_EXIT
    assert staged == [[tmp_path]]  # local staging still happened, Drive did not


def test_main_rejects_invalid_date(tmp_path):
    with pytest.raises(SystemExit):
        org.main(["--sport", "NBA", "--date", "11/10/2026", "--today-root", str(tmp_path)])
