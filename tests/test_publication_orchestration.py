"""Run real PowerShell wrappers against isolated, offline producer fixtures."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(SHELL is None, reason="PowerShell required")


def _fixture(tmp_path: Path, producer: str):
    scripts = tmp_path / "repo" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("refresh_nba_pack.ps1", "publication_guards.ps1"):
        shutil.copy2(REPO / "scripts" / name, scripts / name)
    # Production always checks the host quarantine; fixtures have their own host.
    guard = scripts / "publication_guards.ps1"
    guard.write_text(guard.read_text().replace(
        r"C:\Users\dasil\outlier-quarantine\2026-11-05\publication_hold.json",
        str(tmp_path / "host_hold.json"),
    ))
    (scripts / "export_nba_extra_pack.py").write_text(producer)
    drive = tmp_path / "drive"
    drive.mkdir()
    scratch = tmp_path / "result.csv"
    return scripts, drive, scratch


def _run(scripts: Path, drive: Path, scratch: Path):
    return subprocess.run([
        SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(scripts / "refresh_nba_pack.ps1"), "-RepoRoot", str(scripts.parent),
        "-Date", "2026-11-05", "-SkipPipeline", "-SkipInjuryFilter", "-SkipDrive",
        "-PythonExe", sys.executable, "-MinDiskSpaceGB", "0",
        "-DriveRoot", str(drive), "-ScratchOut", str(scratch),
    ], capture_output=True, text=True, timeout=40)


@pytest.mark.parametrize("message,code", [
    ("HTTP 403 Forbidden", 7), ("ENOSPC", 0),
    ("database is locked", 0), ("0 matches", 0),
])
def test_failed_stage_publishes_nothing(tmp_path, message, code):
    scripts, drive, scratch = _fixture(tmp_path, f"print({message!r})\nraise SystemExit({code})\n")
    result = _run(scripts, drive, scratch)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not scratch.exists()
    assert not list(drive.rglob("*"))


@pytest.mark.parametrize("rows", ["slate_date,player\n", "slate_date,player\n2026-11-04,Other\n"])
def test_empty_or_wrong_date_csv_publishes_nothing(tmp_path, rows):
    producer = "import sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index('--out')+1]).write_text(" + repr(rows) + ")\n"
    scripts, drive, scratch = _fixture(tmp_path, producer)
    result = _run(scripts, drive, scratch)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not scratch.exists()
    assert not list(drive.rglob("*"))


def test_valid_scratch_export_succeeds_without_drive_publication(tmp_path):
    producer = "import sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index('--out')+1]).write_text('slate_date,player\\n2026-11-05,Fixture\\n')\n"
    scripts, drive, scratch = _fixture(tmp_path, producer)
    result = _run(scripts, drive, scratch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Fixture" in scratch.read_text()
    assert not list(drive.rglob("*"))


def test_publication_hold_stops_before_producer(tmp_path):
    scripts, drive, scratch = _fixture(tmp_path, "raise AssertionError('producer must not run')")
    hold = scripts.parent / "docs" / "remediation" / "publication_hold.json"
    hold.parent.mkdir(parents=True)
    hold.write_text('{"active":true}')
    result = _run(scripts, drive, scratch)
    assert result.returncode != 0
    assert "Publication hold active" in result.stderr
    assert "producer must not run" not in result.stderr
    assert not scratch.exists()


def test_malformed_publication_hold_fails_closed(tmp_path):
    scripts, drive, scratch = _fixture(tmp_path, "raise AssertionError('producer must not run')")
    (tmp_path / "host_hold.json").write_text('{"active":"false"}')
    result = _run(scripts, drive, scratch)
    assert result.returncode != 0
    assert "Invalid publication hold" in result.stderr
    assert not scratch.exists()


def test_successful_stage_can_emit_stderr(tmp_path):
    producer = "import sys\nfrom pathlib import Path\nprint('progress: fixture export', file=sys.stderr)\nPath(sys.argv[sys.argv.index('--out')+1]).write_text('slate_date,player\\n2026-11-05,Fixture\\n')\n"
    scripts, drive, scratch = _fixture(tmp_path, producer)
    result = _run(scripts, drive, scratch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert scratch.is_file()
    assert not list(drive.rglob("*"))


def test_daily_failed_native_stage_never_invokes_exporter(tmp_path):
    scripts, _drive, _scratch = _fixture(tmp_path, "")
    shutil.copy2(REPO / "scripts" / "run_daily_pipeline.ps1", scripts)
    package = scripts.parent / "outlier_scrapers"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "daily_job.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).parents[1].joinpath('stage_ran.txt').write_text('ran')\n"
        "raise SystemExit(7)\n"
    )
    published = tmp_path / "published.txt"
    (scripts / "organize_today_run2.py").write_text(
        f"from pathlib import Path\nPath({str(published)!r}).write_text('unsafe')\n"
    )
    result = subprocess.run([
        SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(scripts / "run_daily_pipeline.ps1"), "-RepoRoot", str(scripts.parent),
        "-Force", "-PythonExe", sys.executable, "-MinDiskSpaceGB", "0",
    ], capture_output=True, text=True, timeout=40)
    assert result.returncode != 0, result.stdout + result.stderr
    assert (scripts.parent / "stage_ran.txt").exists()
    assert not published.exists()
    # Status writes belong only to this temporary checkout.
    assert '"failed"' in (scripts.parent / "calibration" / "alerts" / "daily_pipeline_status.json").read_text(encoding="utf-8-sig")


def test_universal_failed_child_stops_later_children_and_summary(tmp_path):
    scripts, drive, _scratch = _fixture(tmp_path, "")
    shutil.copy2(REPO / "scripts" / "sync_all_sports_to_drive.ps1", scripts)
    (scripts / "refresh_cfb_pack.ps1").write_text(
        "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'cfb_ran.txt') -Value 'ran'\nexit 7\n"
    )
    (scripts / "refresh_nba_pack.ps1").write_text(
        "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'nba_ran.txt') -Value 'unsafe'\nexit 0\n"
    )
    result = subprocess.run([
        SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(scripts / "sync_all_sports_to_drive.ps1"), "-Date", "2026-11-05",
        "-DriveRoot", str(drive), "-MinDiskSpaceGB", "0",
    ], capture_output=True, text=True, timeout=40)
    assert result.returncode != 0, result.stdout + result.stderr
    assert (scripts / "cfb_ran.txt").exists()
    assert not (scripts / "nba_ran.txt").exists()
    assert not list(drive.rglob("SLATE_SUMMARY.md"))
    assert not [p for p in drive.rglob("*") if p.is_file()]
