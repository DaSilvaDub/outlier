param(
    [Parameter(Mandatory = $true)][ValidateSet("capture", "settle")][string]$Mode,
    # Write the shared feedback ledger instead of the local trial ledger. Leave off until
    # a trial settle has graded real box scores correctly.
    [switch]$SharedLedger
)

$ErrorActionPreference = "Stop"

# NBA shadow capture/settle for Task Scheduler. No reasoning-model calls.
#   capture: ticks every 15 min; `--only-if-due` skips unless a tip within 60 min lacks a
#            verified-window capture (or the last capture is >12h old), so a tick is cheap
#            and a real capture (~2 min, ~175 MB) runs only when CLV needs it.
#   settle : once a morning, grades the last 3 days against box scores.
# Ledger and run bundles live under %LOCALAPPDATA%\Outlier, off the cloud-filtered
# checkout (see calibration corruption history) and out of %TEMP% (wiped daily at 03:00).
# Install: scripts\install_nba_shadow_tasks.ps1

$repoRoot = Split-Path -Parent $PSScriptRoot
$localRoot = Join-Path $env:LOCALAPPDATA "Outlier"
$nbaDir = Join-Path $localRoot "NBA"
$trialLedger = Join-Path $localRoot "nba_trial_ledger.sqlite3"
$logDir = Join-Path $localRoot "logs"
$statusPath = Join-Path $logDir "nba_${Mode}_status.json"
$logPath = Join-Path $logDir ("nba_shadow_{0}.log" -f (Get-Date -Format "yyyy-MM"))

New-Item -ItemType Directory -Path $nbaDir, $logDir -Force | Out-Null

function Write-Status {
    param([string]$Status, [int]$ExitCode, [string]$Summary)
    $payload = [ordered]@{
        mode = $Mode
        status = $Status
        exit_code = $ExitCode
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        ledger = $(if ($SharedLedger) { "shared" } else { $trialLedger })
        summary = $Summary
    }
    $temporary = "$statusPath.tmp"
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}

$arguments = @("-m", "outlier_nba.cli", $Mode)
if (-not $SharedLedger) { $arguments += @("--db", $trialLedger) }
if ($Mode -eq "capture") { $arguments += @("--nba-dir", $nbaDir, "--only-if-due") }

$exitCode = 1
$summary = ""
try {
    Set-Location -LiteralPath $repoRoot
    $python = (Get-Command python -ErrorAction Stop).Source
    # The CLI prints one JSON line on stdout; structlog goes to stderr. Keep both.
    # Windows PowerShell 5.1 turns native stderr into terminating errors under "Stop".
    $ErrorActionPreference = "Continue"
    $output = & $python @arguments 2>&1 | ForEach-Object { "$_" }
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $summary = ($output | Where-Object { $_.StartsWith("{") } | Select-Object -Last 1)
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value @(
        "=== $(Get-Date -Format o) $Mode exit=$exitCode ==="
        $output
    )
}
catch {
    $summary = $_.Exception.Message
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "=== $(Get-Date -Format o) $Mode error: $summary"
}

Write-Status -Status $(if ($exitCode -eq 0) { "ok" } else { "failed" }) -ExitCode $exitCode -Summary $summary
exit $exitCode
