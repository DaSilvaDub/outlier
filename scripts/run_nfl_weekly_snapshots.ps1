param([switch]$Install)
$ErrorActionPreference = "Stop"

# NFL weekly refresh: price snapshots + traced best bets for every remaining game this week.
# Paid reasoning stays off (deterministic pipeline only).
#
# Schedule (times are the machine's local clock; set them for US Eastern):
#   Tue 10:00, Tue 18:00  opening baseline + first movement read (early-week provisional)
#   Thu 15:00             game-day refresh for Thursday Night Football
#   Sun 10:30             game-day refresh for all Sunday games
#   Mon 15:00             game-day refresh for Monday Night Football
# Only a game-day refresh can mark a pick VALIDATED.
#
# Install all five tasks once:
#   powershell -ExecutionPolicy Bypass -File scripts\run_nfl_weekly_snapshots.ps1 -Install

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$alertsDir = Join-Path $repoRoot "calibration\alerts"
$statusPath = Join-Path $alertsDir "nfl_weekly_status.json"
$scriptPath = Join-Path $repoRoot "scripts\run_nfl_weekly_snapshots.ps1"

if ($Install) {
    $action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    $tasks = @(
        @{ Name = "Outlier NFL Weekly - Tue AM"; Day = "TUE"; Time = "10:00" },
        @{ Name = "Outlier NFL Weekly - Tue PM"; Day = "TUE"; Time = "18:00" },
        @{ Name = "Outlier NFL Weekly - Thu";    Day = "THU"; Time = "15:00" },
        @{ Name = "Outlier NFL Weekly - Sun";    Day = "SUN"; Time = "10:30" },
        @{ Name = "Outlier NFL Weekly - Mon";    Day = "MON"; Time = "15:00" }
    )
    foreach ($task in $tasks) {
        schtasks /Create /TN $task.Name /SC WEEKLY /D $task.Day /ST $task.Time /RL LIMITED /F /TR $action
        if ($LASTEXITCODE -ne 0) { throw "schtasks failed for $($task.Name)" }
    }
    Write-Output "Installed $($tasks.Count) NFL weekly tasks."
    return
}

function Write-WeeklyStatus {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("ok", "failed")][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message
    )
    New-Item -ItemType Directory -Path $alertsDir -Force | Out-Null
    $payload = [ordered]@{
        status = $Status
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        message = $Message
    }
    $temporary = "$statusPath.tmp"
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}

try {
    Set-Location -LiteralPath $repoRoot
    Write-Output "Running NFL weekly refresh (reasoning off)..."
    & python -m outlier_nfl.weekly
    if ($LASTEXITCODE -ne 0) {
        throw "python -m outlier_nfl.weekly failed with exit code $LASTEXITCODE"
    }
    Write-WeeklyStatus -Status "ok" -Message "NFL weekly refresh completed."
}
catch {
    Write-WeeklyStatus -Status "failed" -Message $_.Exception.Message
    throw
}
