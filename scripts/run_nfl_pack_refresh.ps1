# NFL Daily Pack Refresh Runner
param(
    [string]$Date = "",
    [switch]$SkipDrive
)

$ErrorActionPreference = "Stop"
$outlierRepo = "C:\Users\dasil\Dev\GitHub\outlier"
$python = "C:\Users\dasil\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

try {
    Write-Output "[$(Get-Date -Format 'o')] Starting NFL Daily Pack Refresh..."
    $refreshScript = Join-Path $outlierRepo "scripts\refresh_nfl_pack.ps1"
    $refreshArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $refreshScript)
    if ($Date) { $refreshArgs += @("-Date", $Date) }
    if ($SkipDrive) { $refreshArgs += @("-SkipDrive") }

    & powershell.exe @refreshArgs
    if ($LASTEXITCODE -ne 0) { throw "refresh_nfl_pack.ps1 failed with exit code $LASTEXITCODE" }

    # The refresh script already staged Desktop Today locally. This pass only stages
    # Fresh_Data to Drive, and the organizer refuses that (exit 3) while a publication
    # hold is active. -SkipDrive therefore has nothing left to do here.
    if (-not $SkipDrive) {
        Write-Output "[$(Get-Date -Format 'o')] Staging NFL Fresh_Data to Drive..."
        $organizerArgs = @((Join-Path $outlierRepo "scripts\organize_desktop_today.py"), "--sport", "NFL")
        if ($Date) { $organizerArgs += @("--date", $Date) }
        & $python @organizerArgs
        if ($LASTEXITCODE -ne 0) { throw "organize_desktop_today.py failed with exit code $LASTEXITCODE" }
    }

    Write-Output "[$(Get-Date -Format 'o')] NFL Daily Pack Refresh completed successfully."
}
catch {
    Write-Error "[$(Get-Date -Format 'o')] NFL Daily Pack Refresh failed: $_"
    exit 1
}
