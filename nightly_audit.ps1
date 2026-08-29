$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$feedbackDb = Join-Path $repoRoot "calibration\feedback.sqlite3"
$alertsDir = Join-Path $repoRoot "calibration\alerts"
$statusPath = Join-Path $alertsDir "nightly_audit_status.json"

function Invoke-CheckedPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "python $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Write-AuditStatus {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("ok", "failed")][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message,
        [hashtable]$LearnedMultiplierPromotion = @{}
    )

    New-Item -ItemType Directory -Path $alertsDir -Force | Out-Null
    $payload = [ordered]@{
        status = $Status
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        message = $Message
        learned_multiplier_promotion = $LearnedMultiplierPromotion
    }
    $temporary = "$statusPath.tmp"
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}

try {
    Set-Location -LiteralPath $repoRoot

    Write-Output "Recomputing trustworthy closing-line value..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.feedback", "--db", $feedbackDb, "recompute-clv"
    )

    Write-Output "Applying the 90-day feedback retention policy..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.feedback", "--db", $feedbackDb,
        "retention", "--cutoff-days", "90"
    )

    Write-Output "Generating nightly feedback report..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.feedback", "--db", $feedbackDb,
        "report", "--output", (Join-Path $repoRoot "calibration\reports\latest")
    )

    $reportPath = Join-Path $repoRoot "calibration\reports\latest\play_vs_stand_down.csv"
    if (-not (Test-Path -LiteralPath $reportPath)) {
        throw "missing $reportPath after feedback report generation"
    }
    $promotionPath = Join-Path $repoRoot "calibration\reports\latest\learned_multiplier_promotion.json"
    if (-not (Test-Path -LiteralPath $promotionPath)) {
        throw "missing $promotionPath after feedback report generation"
    }
    $promotion = Get-Content -Raw -LiteralPath $promotionPath | ConvertFrom-Json
    $promotionReceipt = @{
        status = [string]$promotion.status
        ready_for_manual_promotion_review = [bool]$promotion.ready_for_manual_promotion_review
        failed_gates = @($promotion.failed_gates)
    }
    Write-Output "Learned-multiplier promotion signal: $($promotionReceipt.status)"

    Write-Output "Refitting audit-only blend weights..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.feedback", "--db", $feedbackDb,
        "fit-blend", "--output", (Join-Path $repoRoot "calibration\blend_weights.json")
    )

    Write-Output "Refreshing market-consensus stake calibration..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.feedback", "--db", $feedbackDb,
        "fit-stake-calibration",
        "--output", (Join-Path $repoRoot "calibration\stake_calibration.json"),
        "--source-column", "market_consensus_prob"
    )

    $toDate = Get-Date -Format "yyyy-MM-dd"
    $fromDate = (Get-Date).AddDays(-90).ToString("yyyy-MM-dd")
    Write-Output "Running nightly portfolio replay smoke check..."
    Invoke-CheckedPython @(
        "-m", "outlier_scrapers.feedback", "--db", $feedbackDb,
        "replay-portfolio", "--from", $fromDate, "--to", $toDate,
        "--policy", (Join-Path $repoRoot "config\portfolio_risk.json")
    )

    Write-AuditStatus -Status "ok" -Message "Nightly calibration audit completed." `
        -LearnedMultiplierPromotion $promotionReceipt
    Write-Output "Nightly calibration audit completed."
}
catch {
    Write-AuditStatus -Status "failed" -Message $_.Exception.Message
    throw
}
