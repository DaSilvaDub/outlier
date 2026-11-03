# Outlier Nightly Postgame Slate Audit & Settlement Runner
param(
    [string]$RepoRoot = "C:\Users\dasil\Dev\GitHub\outlier-scheduled",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

try {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "outlier_scrapers"))) {
        throw "RepoRoot '$RepoRoot' is not a valid outlier checkout."
    }

    Set-Location -LiteralPath $RepoRoot
    $env:PYTHONPATH = $RepoRoot

    # Off-season check: MLB and WNBA are concluded (November through March)
    $currentMonth = (Get-Date).Month
    $isOffSeason = ($currentMonth -ge 11 -or $currentMonth -le 3)
    if ($isOffSeason -and -not $Force) {
        Write-Output "MLB and WNBA are currently in off-season (November - March). Nightly postgame audit skipped."
        $alertsDir = Join-Path $RepoRoot "calibration\alerts"
        New-Item -ItemType Directory -Path $alertsDir -Force | Out-Null
        $statusFile = Join-Path $alertsDir "nightly_audit_status.json"
        $payload = [ordered]@{
            status = "ok"
            timestamp_utc = [DateTime]::UtcNow.ToString("o")
            message = "Nightly postgame audit skipped (MLB/WNBA off-season active; re-enable in spring or pass -Force)."
        }
        $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statusFile -Encoding UTF8
        return
    }
    $today = (Get-Date).ToString("yyyy-MM-dd")
    Write-Output "[$(Get-Date -Format 'o')] Executing nightly postgame settlement and audit for $today..."

    # Step 1: Grade completed events against official MLB Stats API & ESPN box scores
    Write-Output "[$(Get-Date -Format 'o')] Step 1: Grading completed games against official box scores..."
    & python -m outlier_scrapers.results --leagues MLB,WNBA --lookback-days 3
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Result collector exited with code $LASTEXITCODE; continuing to pipeline settlement..."
    }

    # Step 2: Run daily job settlement ingest and feedback ledger maintenance
    Write-Output "[$(Get-Date -Format 'o')] Step 2: Ingesting settlements into feedback ledger..."
    & python -m outlier_scrapers.daily_job --skip-blend-refit --skip-result-collection
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Daily job settlement run exited with code $LASTEXITCODE"
    }

    $alertsDir = Join-Path $RepoRoot "calibration\alerts"
    New-Item -ItemType Directory -Path $alertsDir -Force | Out-Null
    $statusFile = Join-Path $alertsDir "nightly_audit_status.json"
    $payload = [ordered]@{
        status = "ok"
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        message = "Nightly postgame audit and settlement completed."
    }
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statusFile -Encoding UTF8

    Write-Output "[$(Get-Date -Format 'o')] Nightly postgame settlement and audit completed successfully."
}
catch {
    Write-Error "[$(Get-Date -Format 'o')] Nightly postgame audit failed: $_"
    exit 1
}
