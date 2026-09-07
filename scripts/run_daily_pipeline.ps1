$ErrorActionPreference = "Stop"

# Local daily pack + export. Paid reasoning stays off.
# Install once (08:00 local, change /ST as needed):
#   schtasks /Create /TN "Outlier Daily Pipeline" /SC DAILY /ST 08:00 /RL LIMITED /F `
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\dasil\Dev\GitHub\outlier\scripts\run_daily_pipeline.ps1"

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$alertsDir = Join-Path $repoRoot "calibration\alerts"
$statusPath = Join-Path $alertsDir "daily_pipeline_status.json"

function Invoke-CheckedPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "python $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
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

    Write-Output "Running local daily job (reasoning off)..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.daily_job",
        "--analysis-profile", "local",
        "--leagues", "MLB,WNBA"
    )

    $today = Get-Date -Format "yyyy-MM-dd"
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
        Write-Output ("Pitcher identity audit: status={0} so={1} mismatch={2} unconfirmed={3}" -f `
            $identityReceipt.status, $identityReceipt.so_rows, `
            $identityReceipt.mismatch_count, $identityReceipt.unconfirmed_count)
    }
    else {
        Write-Output "Pitcher identity audit: identity_audit.json not written for $today"
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
