# NBA Daily Pack Refresh Runner
param(
    [string]$Date = ""
)

$ErrorActionPreference = "Stop"
$outlierRepo = "C:\Users\dasil\Dev\GitHub\outlier"
$python = "C:\Users\dasil\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

try {
    Write-Output "[$(Get-Date -Format 'o')] Starting NBA Daily Pack Refresh..."
    $refreshScript = Join-Path $outlierRepo "scripts\refresh_nba_pack.ps1"
    # Native commands do not trip $ErrorActionPreference = "Stop" in Windows PowerShell 5.1;
    # check $LASTEXITCODE so a failed refresh is never reported as success.
    if ($Date) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $refreshScript -Date $Date
    } else {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $refreshScript
    }
    if ($LASTEXITCODE -ne 0) { throw "refresh_nba_pack.ps1 failed with exit code $LASTEXITCODE" }

    # refresh_nba_pack.ps1 already ran the organizer; this pass is a no-op when nothing
    # changed. --date goes as two arguments: one "--date X" string is rejected by argparse.
    Write-Output "[$(Get-Date -Format 'o')] Organizing Desktop Today for NBA..."
    $organizerArgs = @((Join-Path $outlierRepo "scripts\organize_desktop_today.py"), "--sport", "NBA")
    if ($Date) { $organizerArgs += @("--date", $Date) }
    & $python @organizerArgs
    if ($LASTEXITCODE -ne 0) { throw "organize_desktop_today.py failed with exit code $LASTEXITCODE" }

    Write-Output "[$(Get-Date -Format 'o')] NBA Daily Pack Refresh completed successfully."
}
catch {
    Write-Error "[$(Get-Date -Format 'o')] NBA Daily Pack Refresh failed: $_"
    exit 1
}
