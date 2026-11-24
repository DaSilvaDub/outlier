"""Regression guards for scripts/refresh_nba_pack.ps1 report publishing."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_refresh_nba_pack_publishes_matchup_focus_and_drops_legacy_sas_okc():
    script = (REPO_ROOT / "scripts" / "refresh_nba_pack.ps1").read_text(encoding="utf-8")

    # Must generate and publish the dynamic MATCHUP_FOCUS.txt report.
    assert "analysis\\matchup_focus.py" in script
    assert "$nbaPipelineRepo\\MATCHUP_FOCUS.txt" in script

    # Must not copy legacy one-off SAS_OKC_*.txt artifacts into Drive.
    assert "$nbaPipelineRepo\\SAS_OKC_" not in script

    # Must clean up legacy stale files and guard against publishing 0-byte txt files.
    assert "Remove-Item -LiteralPath $stalePath" in script
    assert ".Length -gt 0" in script


def test_sync_all_sports_summary_lists_matchup_focus():
    script = (REPO_ROOT / "scripts" / "sync_all_sports_to_drive.ps1").read_text(encoding="utf-8")
    assert "MATCHUP_FOCUS.txt" in script
