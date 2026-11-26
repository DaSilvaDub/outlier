<#
.SYNOPSIS
  Installs and registers identical Windows Scheduled Tasks for Daily Pack Refreshes
  across all supported sports: NFL, NBA, and NCAA (CFB).

.PARAMETER Force
  Re-register / overwrite existing scheduled tasks. Run once with -Force to move
  tasks created by an older version of this script onto the settings below.

.PARAMETER WakeToRun
  Also let each task wake the PC from sleep at its scheduled time. Off by default.

.NOTES
  Missed runs: every task starts as soon as possible after a missed start (PC asleep,
  off or logged out at the scheduled time) and runs on battery power. Tasks created with
  plain `schtasks /Create` have neither setting, so a PC that was asleep or unplugged at
  12:30 and 17:30 silently skipped that day's NBA refresh (no prop edges for the slate).

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
    [switch]$Force,
    [switch]$WakeToRun
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

    $existing = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    if ($existing -and -not $Force) {
        $missed = $existing.Settings.StartWhenAvailable
        $battery = -not $existing.Settings.DisallowStartIfOnBatteries
        Write-Output " [EXISTS] $($t.Name) (catch up after missed run: $missed; runs on battery: $battery; pass -Force to update)"
        continue
    }

    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
        -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $t.Time
    $settingsArgs = @{
        StartWhenAvailable         = $true
        AllowStartIfOnBatteries    = $true
        DontStopIfGoingOnBatteries = $true
        MultipleInstances          = "IgnoreNew"
        ExecutionTimeLimit         = (New-TimeSpan -Hours 2)
    }
    if ($WakeToRun) { $settingsArgs.WakeToRun = $true }
    $settings = New-ScheduledTaskSettingsSet @settingsArgs

    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger `
        -Settings $settings -Description $t.Comment -RunLevel Limited -Force | Out-Null
    Write-Output " [OK] Registered: $($t.Name) at $($t.Time) Daily (catches up after a missed run)"
}

Write-Output "All daily pack refresh automations verified."
