"""Regression guards for scripts/refresh_nba_pack.ps1 report publishing."""

from __future__ import annotations

from pathlib import Path
import csv
import json
import shutil
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_refresh_nba_pack_publishes_matchup_focus_and_drops_legacy_sas_okc():
    script = (REPO_ROOT / "scripts" / "refresh_nba_pack.ps1").read_text(encoding="utf-8")

    # References may be published, but this path must not run the broad analysis pipeline.
    assert "$nbaPipelineRepo\\MATCHUP_FOCUS.txt" in script

    # Must not copy legacy one-off SAS_OKC_*.txt artifacts into Drive.
    assert "$nbaPipelineRepo\\SAS_OKC_" not in script

    # Must clean up legacy stale files and guard against publishing 0-byte txt files.
    assert "Remove-Item -LiteralPath $stalePath" in script
    assert ".Length -gt 0" in script


def test_refresh_nba_pack_defaults_to_rg_free_mode():
    script = (REPO_ROOT / "scripts" / "refresh_nba_pack.ps1").read_text(encoding="utf-8")
    assert "[switch]$UseRotoGrinders" in script
    assert '"scripts\\refresh_nba_upstream.py"' in script
    assert "if ($UseRotoGrinders)" in script
    assert '"--use-rg"' in script
    assert "nba_data_watcher.py" not in script
    assert "refresh_lineups_injuries.ps1" not in script


def test_sync_all_sports_summary_lists_matchup_focus():
    script = (REPO_ROOT / "scripts" / "sync_all_sports_to_drive.ps1").read_text(encoding="utf-8")
    assert "MATCHUP_FOCUS.txt" in script


def test_scratch_refresh_replaces_prior_slate_without_live_pipeline(tmp_path):
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell driver smoke test requires Windows")
    source = tmp_path / "stale.csv"
    source.write_text(
        "slate_date,player,game_time,recommended_side,recommended_odds,line\n"
        "2026-11-22,Old Player,2026-11-22T23:00:00Z,OVER,-110,20.5\n"
    )
    output = tmp_path / "nba_only.csv"
    output.write_text("old desktop contents")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "scripts" / "refresh_nba_pack.ps1"),
            "-Date",
            "2026-11-23",
            "-SkipPipeline",
            "-SkipDrive",
            "-ScratchOut",
            str(output),
            "-PropSource",
            str(source),
            "-MinDiskSpaceGB",
            "0",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with output.open(newline="") as stream:
        assert list(csv.DictReader(stream)) == []
    status = json.loads(output.with_suffix(".status.json").read_text())
    summary = json.loads((tmp_path / "nba_only_filter_summary_2026-11-23.json").read_text())
    assert status["status"] == "awaiting_odds"
    assert summary["after_rows"] == status["actionable_rows"] == 0
    assert summary["csv_sha256"] == status["csv_sha256"]
    for name in ("nba_prop_edges.csv", "nba_graded_plays.csv"):
        with (tmp_path / name).open(newline="") as stream:
            assert list(csv.DictReader(stream)) == []


def test_failed_live_refresh_publishes_empty_instead_of_retained_source(tmp_path):
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell driver smoke test requires Windows")
    missing_pipeline = tmp_path / "missing-pipeline"
    missing_pipeline.mkdir()
    source = tmp_path / "retained.csv"
    source.write_text("slate_date,player\n2026-11-26,Retained Player\n")
    output = tmp_path / "nba_only.csv"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "scripts/refresh_nba_pack.ps1"),
            "-Date",
            "2026-11-26",
            "-SkipDrive",
            "-ScratchOut",
            str(output),
            "-PropSource",
            str(source),
            "-NbaPipelineRepo",
            str(missing_pipeline),
            "-NbaDataRoot",
            str(tmp_path / "raw"),
            "-MinDiskSpaceGB",
            "0",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(output.with_suffix(".status.json").read_text())
    assert status["status"] == "awaiting_odds" and status["row_count"] == 0
    upstream = json.loads(
        (tmp_path / "upstream_2026-11-26/upstream_inputs.status.json").read_text()
    )
    assert upstream["error"] == "odds: required upstream script missing"
