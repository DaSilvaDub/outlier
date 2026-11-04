# NBA Nightly Postgame Audit & Settlement Runner
param(
    [string]$Date = ""
)

$ErrorActionPreference = "Stop"
$outlierRepo = "C:\Users\dasil\Dev\GitHub\outlier"
$nbaPipelineRepo = "C:\Users\dasil\Dev\GitHub\nba-props-pipeline"
$python = "C:\Users\dasil\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not $Date) {
    try {
        $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
        $nowEt = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
        if ($nowEt.Hour -lt 12) {
            $Date = $nowEt.AddDays(-1).ToString("yyyy-MM-dd")
        } else {
            $Date = $nowEt.ToString("yyyy-MM-dd")
        }
    }
    catch {
        $Date = (Get-Date).ToString("yyyy-MM-dd")
    }
}

try {
    Write-Output "[$(Get-Date -Format 'o')] Starting NBA Nightly Postgame Audit for $Date..."

    # Pre-Flight Storage Check (NBA-SYS-001)
    $freeGb = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
    Write-Output "[$(Get-Date -Format 'o')] Host C: storage check: $freeGb GB free."
    if ($freeGb -lt 5.0) {
        Write-Error "[NBA-SYS-001 VIOLATION] Host C: drive has only $freeGb GB free (< 5.0 GB minimum required). Aborting audit."
        exit 2
    }

    # 1. Automated Scoreboard Health Verification (02:30 AM ET cron check)
    $healthCheckPy = Join-Path $outlierRepo "scripts\verify_espn_boxscore_health.py"
    if (Test-Path -LiteralPath $healthCheckPy) {
        Write-Output "[$(Get-Date -Format 'o')] Checking ESPN scoreboard & boxscore health..."
        & $python $healthCheckPy --date $Date
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "ESPN scoreboard health check failed; continuing with available local records."
        }
    }

    # Native commands (python.exe) do not trip $ErrorActionPreference = "Stop" in
    # Windows PowerShell 5.1, so every call below checks $LASTEXITCODE explicitly.

    # 2. Settle shadow ledger against boxscores. Delegate to run_nba_shadow.ps1 so
    # settle grades the same ledger capture writes to (the local trial ledger by
    # default; the shared ledger only with its explicit -SharedLedger switch) and
    # records nba_settle_status.json. A bare `outlier_nba.cli settle` defaults to
    # paths.FEEDBACK_DB_PATH and would skip the captured trial rows.
    Set-Location -LiteralPath $outlierRepo
    Write-Output "[$(Get-Date -Format 'o')] Settling outlier_nba shadow ledger..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $outlierRepo "scripts\run_nba_shadow.ps1") -Mode settle
    if ($LASTEXITCODE -ne 0) { throw "run_nba_shadow.ps1 -Mode settle failed with exit code $LASTEXITCODE" }

    # 3. Ingest historical box scores in nba-props-pipeline if script exists
    $fetchBoxPy = Join-Path $nbaPipelineRepo "scripts\fetch_historical_boxscores.py"
    if (Test-Path -LiteralPath $fetchBoxPy) {
        Write-Output "[$(Get-Date -Format 'o')] Ingesting historical boxscores..."
        & $python $fetchBoxPy --date $Date
        if ($LASTEXITCODE -ne 0) { throw "fetch_historical_boxscores.py failed with exit code $LASTEXITCODE" }
    }

    # 4. Auto-correct and apply recommended audit upgrades immediately
    $autoCorrectPy = Join-Path $outlierRepo "scripts\auto_correct_audit.py"
    if (Test-Path -LiteralPath $autoCorrectPy) {
        Write-Output "[$(Get-Date -Format 'o')] Executing auto-correction and audit upgrades..."
        & $python $autoCorrectPy --sport NBA --date $Date
        if ($LASTEXITCODE -ne 0) { throw "auto_correct_audit.py failed with exit code $LASTEXITCODE" }
    }

    # 5. Organize Desktop Today Audits folder
    Write-Output "[$(Get-Date -Format 'o')] Organizing Desktop Today for NBA..."
    $organizerPy = Join-Path $outlierRepo "scripts\organize_desktop_today.py"
    if (Test-Path -LiteralPath $organizerPy) {
        & $python $organizerPy --sport NBA --date $Date
        if ($LASTEXITCODE -ne 0) { throw "organize_desktop_today.py failed with exit code $LASTEXITCODE" }
    }

    Write-Output "[$(Get-Date -Format 'o')] NBA Nightly Postgame Audit completed successfully for $Date."
}
catch {
    Write-Error "[$(Get-Date -Format 'o')] NBA Nightly Postgame Audit failed: $_"
    exit 1
}
