"""Regression guards for scripts/install_pack_refresh_tasks.ps1 task settings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_pack_refresh_tasks.ps1"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_pack_refresh_tasks_catch_up_after_missed_runs():
    script = _script()
    # Plain schtasks /Create leaves "run as soon as possible after a missed start" off and
    # "start only on AC power" on, so a sleeping or unplugged PC skipped whole refreshes.
    assert "& schtasks" not in script
    assert "StartWhenAvailable         = $true" in script
    assert "AllowStartIfOnBatteries    = $true" in script
    assert "DontStopIfGoingOnBatteries = $true" in script
    assert 'MultipleInstances          = "IgnoreNew"' in script
    assert "Register-ScheduledTask -TaskName $t.Name" in script


def test_pack_refresh_tasks_keep_their_names_times_and_runners():
    script = _script()
    for name, time, runner in [
        ("Sports_NBA_Pack_Refresh_1230", "12:30", "run_nba_pack_refresh.ps1"),
        ("Sports_NBA_Pack_Refresh_1730", "17:30", "run_nba_pack_refresh.ps1"),
        ("Sports_NFL_Pack_Refresh_1130", "11:30", "run_nfl_pack_refresh.ps1"),
        ("Sports_NFL_Pack_Refresh_1630", "16:30", "run_nfl_pack_refresh.ps1"),
    ]:
        block_start = script.index(f'Name = "{name}"')
        block = script[block_start : block_start + 200]
        assert f'Time = "{time}"' in block
        assert f'Script = "{runner}"' in block


def test_wake_to_run_is_opt_in_and_existing_tasks_need_force():
    script = _script()
    assert "[switch]$WakeToRun" in script
    assert "if ($WakeToRun) { $settingsArgs.WakeToRun = $true }" in script
    assert "if ($existing -and -not $Force)" in script
