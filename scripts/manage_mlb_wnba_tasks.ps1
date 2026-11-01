# Manage Outlier MLB / WNBA Scheduled Tasks (Off-Season Control)
[CmdletBinding()]
param(
    [switch]$Disable,
    [switch]$Enable,
    [switch]$Status
)

$tasks = @(
    "Outlier Daily Pipeline",
    "Outlier Afternoon Lineup Refresh",
    "Outlier T-30 Reprice Gate",
    "Outlier_T30_Reprice",
    "Outlier Nightly Slate Audit"
)

if (-not $Disable -and -not $Enable) {
    $Status = $true
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Outlier MLB & WNBA Scheduled Tasks Status" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

foreach ($taskName in $tasks) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "[-] $taskName : Not Found" -ForegroundColor DarkGray
        continue
    }

    if ($Disable) {
        try {
            Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            Write-Host "[x] Disabled $taskName" -ForegroundColor Yellow
        }
        catch {
            Write-Warning "Could not disable $taskName ($($_.Exception.Message)). Administrator rights may be required for this task."
        }
    }
    elseif ($Enable) {
        try {
            Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            Write-Host "[+] Enabled $taskName" -ForegroundColor Green
        }
        catch {
            Write-Warning "Could not enable $taskName ($($_.Exception.Message)). Administrator rights may be required for this task."
        }
    }

    $refreshed = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $stateColor = if ($refreshed.State -eq "Ready") { "Green" } else { "DarkGray" }
    Write-Host ("- {0}: {1}" -f $taskName, $refreshed.State) -ForegroundColor $stateColor
}

Write-Host "==========================================================" -ForegroundColor Cyan
