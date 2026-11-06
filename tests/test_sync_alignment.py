"""Run bootstrap failure paths with a fake git; never fetch or mutate a checkout."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["dirty_checkout", "clean_reset", "head_mismatch"])
def test_bootstrap_stops_before_materializing_after_failed_alignment(tmp_path, scenario):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    script = Path(__file__).resolve().parents[1] / "sync-outlier.ps1"
    harness = tmp_path / "fake-git.ps1"

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    harness.write_text(
        f"$global:fakeRoot = {literal(tmp_path)}\n"
        f"$global:scenario = {literal(scenario)}\n"
        "function global:git {\n"
        "  $line = $args -join ' '\n"
        "  $global:LASTEXITCODE = 0\n"
        "  switch -Wildcard ($line) {\n"
        "    '*rev-parse --show-toplevel' { return $global:fakeRoot }\n"
        "    'remote get-url origin' { return 'https://github.com/DaSilvaDub/outlier.git' }\n"
        "    'rev-parse --verify origin/master' { return 'fetched-target' }\n"
        "    'status --porcelain' {\n"
        "      if ($global:scenario -ne 'clean_reset') { return 'M outlier_nfl/matchup.py' }\n"
        "      return\n"
        "    }\n"
        "    'checkout -B master fetched-target' {\n"
        "      if ($global:scenario -ne 'head_mismatch') { $global:LASTEXITCODE = 17 }\n"
        "      return\n"
        "    }\n"
        "    'reset --hard fetched-target' { $global:LASTEXITCODE = 17; return }\n"
        "    'rev-parse HEAD' { return 'old-head' }\n"
        "    'remote set-url*' { return }\n"
        "    'fetch origin --prune --tags' { return }\n"
        "    default { throw ('Unexpected git invocation: ' + $line) }\n"
        "  }\n"
        "}\n"
        f"& {literal(script)} -RepoRoot $global:fakeRoot\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "FATAL:" in result.stdout
    assert "Materializing key files" not in result.stdout
    assert "Sync complete" not in result.stdout
