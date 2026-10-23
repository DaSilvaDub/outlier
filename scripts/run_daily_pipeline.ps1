$ErrorActionPreference = "Stop"

# Local daily pack + export. Paid reasoning stays off.
# Install once (08:00 local, change /ST as needed):
#   schtasks /Create /TN "Outlier Daily Pipeline" /SC DAILY /ST 08:00 /RL LIMITED /F `
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\dasil\Dev\GitHub\outlier\scripts\run_daily_pipeline.ps1"

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$alertsDir = Join-Path $repoRoot "calibration\alerts"
$statusPath = Join-Path $alertsDir "daily_pipeline_status.json"

function Get-PackRunSummary {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][DateTimeOffset]$PipelineStartedUtc
    )

    $summary = @{
        reasoning_status = "unknown"
        publication_status = "unknown"
        pack_artifact_count = 0
        stale_pack_artifact_count = 0
        oldest_pack_artifact_generated_at_utc = $null
        newest_pack_artifact_generated_at_utc = $null
        identity_audit_generated_at_utc = $null
        identity_audit_fresh_for_run = $false
    }

    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        return $summary
    }

    try {
        $manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
        if ($manifest.overall) {
            $summary.reasoning_status = [string]$manifest.overall
        }
        $packArtifacts = @($manifest.artifacts | Where-Object { $_.producer -eq "pack" })
        $summary.pack_artifact_count = $packArtifacts.Count
        $generatedTimes = @()

        foreach ($artifact in $packArtifacts) {
            $generatedAt = [DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse(
                [string]$artifact.generated_at,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeUniversal,
                [ref]$generatedAt
            )) {
                continue
            }
            $generatedTimes += $generatedAt.ToUniversalTime()
            if ($generatedAt.ToUniversalTime() -lt $PipelineStartedUtc.AddSeconds(-5)) {
                $summary.stale_pack_artifact_count++
            }

            if ($artifact.path -eq "identity_audit.json") {
                $summary.identity_audit_generated_at_utc = $generatedAt.ToUniversalTime().ToString("o")
                $summary.identity_audit_fresh_for_run = $generatedAt.ToUniversalTime() -ge $PipelineStartedUtc.AddSeconds(-5)
            }
        }

        if ($generatedTimes.Count -gt 0) {
            $summary.oldest_pack_artifact_generated_at_utc = ($generatedTimes | Sort-Object | Select-Object -First 1).ToString("o")
            $summary.newest_pack_artifact_generated_at_utc = ($generatedTimes | Sort-Object | Select-Object -Last 1).ToString("o")
        }

        if ($summary.pack_artifact_count -eq 0 -or $generatedTimes.Count -ne $summary.pack_artifact_count) {
            $summary.publication_status = "unknown"
        }
        elseif ($summary.stale_pack_artifact_count -eq $summary.pack_artifact_count) {
            $summary.publication_status = "reused_immutable"
        }
        elseif ($summary.stale_pack_artifact_count -gt 0) {
            $summary.publication_status = "mixed_freshness"
        }
        else {
            $summary.publication_status = "fresh"
        }
    }
    catch {
        Write-Warning "Could not read pack manifest freshness from $ManifestPath`: $($_.Exception.Message)"
    }

    return $summary
}

function Invoke-CheckedPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "python $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Write-PipelineStatus {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("ok", "partial", "failed")][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][ValidateSet("ok", "failed")][string]$ProcessStatus,
        [hashtable]$IdentityAudit = @{},
        [hashtable]$RunSummary = @{}
    )
    New-Item -ItemType Directory -Path $alertsDir -Force | Out-Null
    $reasoningStatus = "unknown"
    $publicationStatus = "unknown"
    $packArtifactCount = 0
    $stalePackArtifactCount = 0
    if ($RunSummary.ContainsKey("reasoning_status")) { $reasoningStatus = [string]$RunSummary.reasoning_status }
    if ($RunSummary.ContainsKey("publication_status")) { $publicationStatus = [string]$RunSummary.publication_status }
    if ($RunSummary.ContainsKey("pack_artifact_count")) { $packArtifactCount = [int]$RunSummary.pack_artifact_count }
    if ($RunSummary.ContainsKey("stale_pack_artifact_count")) { $stalePackArtifactCount = [int]$RunSummary.stale_pack_artifact_count }
    $payload = [ordered]@{
        status = $Status
        process_status = $ProcessStatus
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        message = $Message
        reasoning_status = $reasoningStatus
        pack_publication_status = $publicationStatus
        pack_artifact_count = $packArtifactCount
        stale_pack_artifact_count = $stalePackArtifactCount
        oldest_pack_artifact_generated_at_utc = $RunSummary.oldest_pack_artifact_generated_at_utc
        newest_pack_artifact_generated_at_utc = $RunSummary.newest_pack_artifact_generated_at_utc
        identity_audit = $IdentityAudit
    }
    $temporary = "$statusPath.tmp"
    $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}

$identityReceipt = @{}
$runSummary = @{}
$pipelineStartedUtc = [DateTimeOffset]::UtcNow

try {
    Set-Location -LiteralPath $repoRoot

    Write-Output "Running local daily job (reasoning off)..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.daily_job",
        "--analysis-profile", "local",
        "--leagues", "MLB,WNBA"
    )

    $today = Get-Date -Format "yyyy-MM-dd"
    $packDir = Join-Path $repoRoot "packs\$today"
    $manifestPath = Join-Path $packDir "manifest.json"
    $runSummary = Get-PackRunSummary -ManifestPath $manifestPath -PipelineStartedUtc $pipelineStartedUtc
    $auditPath = Join-Path $repoRoot "packs\$today\identity_audit.json"
    if (Test-Path -LiteralPath $auditPath) {
        $audit = Get-Content -Raw -LiteralPath $auditPath | ConvertFrom-Json
        $identityReceipt = @{
            status = [string]$audit.status
            so_rows = [int]$audit.so_rows
            mismatch_count = [int]$audit.mismatch_count
            unconfirmed_count = [int]$audit.unconfirmed_count
            fail_closed_count = [int]$audit.fail_closed_count
            generated_at_utc = $runSummary.identity_audit_generated_at_utc
            fresh_for_run = [bool]$runSummary.identity_audit_fresh_for_run
        }
        Write-Output ("Pitcher identity audit: status={0} fresh_for_run={1} generated_at_utc={2} so={3} mismatch={4} unconfirmed={5}" -f `
            $identityReceipt.status, $identityReceipt.fresh_for_run, $identityReceipt.generated_at_utc, `
            $identityReceipt.so_rows, $identityReceipt.mismatch_count, $identityReceipt.unconfirmed_count)
    }
    else {
        $identityReceipt = @{ status = "missing"; fresh_for_run = $false }
        Write-Output "Pitcher identity audit: identity_audit.json not written for $today"
    }

    Write-Output ("Pack publication: status={0} pack_artifacts={1} stale_pack_artifacts={2} oldest_generated_at_utc={3}" -f `
        $runSummary.publication_status, $runSummary.pack_artifact_count, `
        $runSummary.stale_pack_artifact_count, $runSummary.oldest_pack_artifact_generated_at_utc)

    Write-Output "Organizing export folders..."
    Invoke-CheckedPython @("scripts\organize_today_run2.py")

    $hasPartialResult = $runSummary.publication_status -ne "fresh" -or
        $runSummary.reasoning_status -notin @("FULL", "OK", "ok") -or
        $identityReceipt.status -notin @("ok", "")
    $finalStatus = if ($hasPartialResult) { "partial" } else { "ok" }
    $message = if ($finalStatus -eq "ok") {
        "Daily pipeline process completed; pack publication is fresh and desk status is complete."
    }
    else {
        "Daily pipeline process completed with partial or reused artifacts; inspect reasoning_status, pack_publication_status, and identity_audit."
    }
    Write-PipelineStatus -Status $finalStatus -ProcessStatus "ok" -Message $message `
        -IdentityAudit $identityReceipt -RunSummary $runSummary
    Write-Output ("Daily pipeline completed: status={0} process_status=ok reasoning_status={1} pack_publication_status={2}" -f `
        $finalStatus, $runSummary.reasoning_status, $runSummary.publication_status)
}
catch {
    Write-PipelineStatus -Status "failed" -ProcessStatus "failed" `
        -Message $_.Exception.Message -IdentityAudit $identityReceipt -RunSummary $runSummary
    throw
}
