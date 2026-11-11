<#
.SYNOPSIS
  Installs and registers identical Windows Scheduled Tasks for Daily Pack Refreshes
  across all supported sports: NFL, NBA, and NCAA (CFB).

.PARAMETER Force
  Re-register / overwrite existing scheduled tasks.

.NOTES
  Tasks registered:
    - Sports_NFL_Pack_Refresh_1130  (Daily 11:30 AM ET) -> run_nfl_pack_refresh.ps1
    - Sports_NFL_Pack_Refresh_1630  (Daily 04:30 PM ET) -> run_nfl_pack_refresh.ps1
    - Sports_NBA_Pack_Refresh_1230  (Daily 12:30 PM ET) -> run_nba_pack_refresh.ps1
    - Sports_NBA_Pack_Refresh_1730  (Daily 05:30 PM ET) -> run_nba_pack_refresh.ps1
    - Sports_NCAA_Pack_Refresh_1200 (Daily 12:00 PM ET) -> run_ncaa_pack_refresh.ps1
    - Sports_NCAA_Pack_Refresh_1700 (Daily 05:00 PM ET) -> run_ncaa_pack_refresh.ps1
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$scriptsDir = Join-Path $repoRoot "scripts"

$tasks = @(
    @{
        Name = "Sports_NFL_Pack_Refresh_1130"
        Time = "11:30"
        Script = "run_nfl_pack_refresh.ps1"
        Comment = "11:30 AM ET: NFL midday pack refresh (ruled out injury filter, alt floors & line movements)."
    },
    @{
        Name = "Sports_NFL_Pack_Refresh_1630"
        Time = "16:30"
        Script = "run_nfl_pack_refresh.ps1"
        Comment = "04:30 PM ET: NFL pre-lock pack refresh (late injury status & prime-time game prep)."
    },
    @{
        Name = "Sports_NBA_Pack_Refresh_1230"
        Time = "12:30"
        Script = "run_nba_pack_refresh.ps1"
        Comment = "12:30 PM ET: NBA midday pack refresh (shootaround injury reports & matinee tips)."
    },
    @{
        Name = "Sports_NBA_Pack_Refresh_1730"
        Time = "17:30"
        Script = "run_nba_pack_refresh.ps1"
        Comment = "05:30 PM ET: NBA pre-lock pack refresh (confirmed starting lineups & evening locks)."
    },
    @{
        Name = "Sports_NCAA_Pack_Refresh_1200"
        Time = "12:00"
        Script = "run_ncaa_pack_refresh.ps1"
        Comment = "12:00 PM ET: NCAA midday pack refresh (noon lock & early line updates)."
    },
    @{
        Name = "Sports_NCAA_Pack_Refresh_1700"
        Time = "17:00"
        Script = "run_ncaa_pack_refresh.ps1"
        Comment = "05:00 PM ET: NCAA pre-lock pack refresh (confirmed inactives & evening game lines)."
    }
)

Write-Output "Registering daily pack refresh automations (NFL, NBA, NCAA)..."

foreach ($t in $tasks) {
    $scriptPath = Join-Path $scriptsDir $t.Script
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Warning "Target runner script not found: $scriptPath. Skipping $($t.Name)."
        continue
    }

    $actionCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    if ($Force) {
        & schtasks /Create /TN $t.Name /SC DAILY /ST $t.Time /RL LIMITED /TR $actionCmd /F 2>&1 | Out-Null
    } else {
        & schtasks /Create /TN $t.Name /SC DAILY /ST $t.Time /RL LIMITED /TR $actionCmd 2>&1 | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Output " [OK] Registered: $($t.Name) at $($t.Time) Daily"
    } else {
        Write-Output " [EXISTS/STATUS] $($t.Name) (exit code $LASTEXITCODE; pass -Force to overwrite)"
    }
}

Write-Output "All daily pack refresh automations verified."
