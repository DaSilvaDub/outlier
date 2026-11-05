param(
    # Required on purpose: the scheduled run must name the checkout it runs in, so it
    # can never silently run in (and write calibration\ and data\ of) a checkout
    # that agents or an editor are also using.
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [switch]$Force,
    [string]$PythonExe = "python",
    [double]$MinDiskSpaceGB = 20.0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "publication_guards.ps1")

# Local daily pack + export. Paid reasoning stays off.
# Install once (08:00 local, change /ST as needed). Point -RepoRoot at a checkout
# reserved for scheduled runs (a separate worktree), not the one used for editing:
#   schtasks /Create /TN "Outlier Daily Pipeline" /SC DAILY /ST 08:00 /RL LIMITED /F `
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File <RepoRoot>\scripts\run_daily_pipeline.ps1 -RepoRoot <RepoRoot>"

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "outlier_scrapers"))) {
    throw "RepoRoot '$RepoRoot' is not an outlier checkout (no outlier_scrapers folder)."
}
$repoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
Assert-PublicationHold $repoRoot
$alertsDir = Join-Path $repoRoot "calibration\alerts"
$statusPath = Join-Path $alertsDir "daily_pipeline_status.json"

function Invoke-CheckedPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    Invoke-CheckedStage $PythonExe $Arguments
}

function Write-PipelineStatus {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("ok", "failed")][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message,
        [hashtable]$IdentityAudit = @{}
    )
    New-Item -ItemType Directory -Path $alertsDir -Force | Out-Null
    $payload = [ordered]@{
        status = $Status
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        message = $Message
        identity_audit = $IdentityAudit
    }
    $temporary = "$statusPath.tmp"
    $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}

$identityReceipt = @{}

try {
    Set-Location -LiteralPath $repoRoot

    # Check off-season: MLB and WNBA seasons are concluded (November through March).
    # To avoid scraping, pack builds, and export errors during off-season, skip daily execution
    # unless -Force is explicitly passed.
    $currentMonth = (Get-Date).Month
    $isOffSeason = ($currentMonth -ge 11 -or $currentMonth -le 3)
    if ($isOffSeason -and -not $Force) {
        Write-Output "MLB and WNBA are currently in off-season (November - March). Daily pipeline execution skipped."
        Write-PipelineStatus -Status "ok" -Message "Daily pipeline skipped: MLB/WNBA off-season active (re-enable in spring or pass -Force)."
        return
    }

    Write-Output "Running local daily job (reasoning off)..."
    Assert-FreeSpace $repoRoot $MinDiskSpaceGB
    $started = [DateTime]::UtcNow
    $today = Get-EasternSlateDate
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.daily_job",
        "--date", $today,
        "--analysis-profile", "local",
        "--leagues", "MLB,WNBA"
    )

    # Exit zero alone does not attest a usable pack. Validate this invocation's
    # manifest and its immutable artifact records before the external exporter.
    $packDir = Join-Path $repoRoot "packs\$today"
    $manifestPath = Join-Path $packDir "manifest.json"
    Assert-RequiredFile $manifestPath
    if ((Get-Item -LiteralPath $manifestPath).LastWriteTimeUtc -lt $started) { throw "Stale pack manifest." }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.run_id -notlike "$today-*" -or [DateTime]::Parse($manifest.timestamp).ToUniversalTime() -lt $started) { throw "Pack slate-date/run mismatch." }
    if ($manifest.overall -notin @("ok", "FULL", "DATA_ONLY") -or $null -eq $manifest.pack_rows -or [int]$manifest.pack_rows -le 0) { throw "Pack failed, degraded, or has zero matching rows." }
    foreach ($name in @("candidates.csv", "briefing.md", "identity_audit.json")) {
        $records = @($manifest.artifacts | Where-Object path -EQ $name)
        $path = Join-Path $packDir $name
        Assert-RequiredFile $path
        if ($records.Count -ne 1 -or $records[0].status -ne "ok" -or $records[0].sha256 -ne (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash) { throw "Pack artifact attestation failed: $name" }
        if ((Get-Item -LiteralPath $path).LastWriteTimeUtc -lt $started) { throw "Stale pack artifact: $name" }
    }
    if (@(Import-Csv -LiteralPath (Join-Path $packDir "candidates.csv")).Count -le 0) { throw "Zero matching candidates." }
    $auditPath = Join-Path $repoRoot "packs\$today\identity_audit.json"
    if (Test-Path -LiteralPath $auditPath) {
        $audit = Get-Content -Raw -LiteralPath $auditPath | ConvertFrom-Json
        $identityReceipt = @{
            status = [string]$audit.status
            so_rows = [int]$audit.so_rows
            mismatch_count = [int]$audit.mismatch_count
            unconfirmed_count = [int]$audit.unconfirmed_count
            fail_closed_count = [int]$audit.fail_closed_count
        }
        if ($audit.status -ne "ok" -or [int]$audit.mismatch_count -gt 0 -or [int]$audit.unconfirmed_count -gt 0 -or [int]$audit.fail_closed_count -gt 0) { throw "Pitcher identity audit did not pass." }
        Write-Output ("Pitcher identity audit: status={0} so={1} mismatch={2} unconfirmed={3}" -f `
            $identityReceipt.status, $identityReceipt.so_rows, `
            $identityReceipt.mismatch_count, $identityReceipt.unconfirmed_count)
    }
    else {
        throw "Pitcher identity audit missing for $today"
    }

    Write-Output "Organizing export folders..."
    Invoke-CheckedPython @("scripts\organize_today_run2.py")

    Write-PipelineStatus -Status "ok" -Message "Daily pipeline completed." `
        -IdentityAudit $identityReceipt
    Write-Output "Daily pipeline completed."
}
catch {
    Write-PipelineStatus -Status "failed" -Message $_.Exception.Message `
        -IdentityAudit $identityReceipt
    throw
}
