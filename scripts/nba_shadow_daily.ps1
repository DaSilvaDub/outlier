<#
.SYNOPSIS
  One NBA shadow day: refresh ratings, capture the market, price it with the team model, settle prior nights.

.DESCRIPTION
  ratings -> capture -> run -> settle, against a dedicated local ledger (default
  <DataDir>\ledger\feedback.sqlite3), NOT the shared calibration ledger: that file is large, sits behind
  a sync filter and has corrupted before. Everything is SHADOW (units 0). Nothing here sizes a bet and no
  reasoning model is called.

  Outlier login: run from a checkout that has config\.outlier_session, or pass -SessionDir (the canonical
  checkout's config\.outlier_session). With -SessionDir and no session folder in this checkout, a directory
  junction is created for the duration of the run and removed afterwards (never the target); the
  credentials are not read or copied.

  First time on a machine: build the history once with `python -m outlier_nba.cli backfill --stage all`
  (or copy the store and ESPN cache) and keep it under <DataDir>.
#>
[CmdletBinding()]
param(
  [string]$DataDir = 'C:\Users\dasil\outlier_nba_data',
  [string]$Ledger = '',
  [string]$SessionDir = '',
  [string]$Date = '',
  [int]$Draws = 20000
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
if (-not $Ledger) { $Ledger = Join-Path $DataDir 'ledger\feedback.sqlite3' }
$store = Join-Path $DataDir 'store\nba_games.sqlite3'
$env:OUTLIER_NBA_CACHE = Join-Path $DataDir 'cache'
New-Item -ItemType Directory -Force -Path (Split-Path $Ledger) | Out-Null
if (-not (Test-Path $store)) { throw "No games store at $store. Build it: python -m outlier_nba.cli backfill --stage all --store $store" }

$link = Join-Path $repo 'config\.outlier_session'
$madeLink = $false
if (-not (Test-Path $link) -and $SessionDir) {
  New-Item -ItemType Junction -Path $link -Target $SessionDir | Out-Null
  $madeLink = $true
}

function Invoke-Step([string]$Name, [string[]]$CliArgs) {
  Write-Host "== $Name"
  $out = & python -m outlier_nba.cli @CliArgs 2>&1
  $code = $LASTEXITCODE
  $last = ($out | Select-Object -Last 1)
  if ($code -ne 0) { throw "$Name failed (exit $code): $last" }
  try {
    $j = $last | ConvertFrom-Json
    if ($j.run_id) {
      Write-Host ("   run {0} status={1} published={2}" -f $j.run_id, $j.status, $j.published)
      foreach ($r in $j.receipts) { Write-Host ("   {0,-20} {1,-10} received={2} {3}" -f $r.name, $r.status, $r.received, $r.reason) }
    } else { Write-Host "   $last" }
  } catch { Write-Host "   $last" }
}

try {
  $ratings = @('ratings', '--store', $store)
  if ($Date) { $ratings += @('--date', $Date) }
  Invoke-Step 'ratings (refresh results, fit as of today)' $ratings
  Invoke-Step 'capture (market snapshots into the ledger)' @('capture', '--db', $Ledger, '--nba-dir', (Join-Path $env:TEMP 'nba_runs_capture'))
  $run = @('run', '--db', $Ledger, '--store', $store, '--draws', $Draws, '--nba-dir', (Join-Path $env:TEMP 'nba_runs'))
  if ($Date) { $run += @('--date', $Date) }
  Invoke-Step 'run (price the slate, record model probabilities)' $run
  Invoke-Step 'settle (prior nights)' @('settle', '--db', $Ledger)
  Invoke-Step 'promote (preview only; writes nothing)' @('promote', '--db', $Ledger)
} finally {
  if ($madeLink) { [System.IO.Directory]::Delete($link) }   # removes the junction only, never the target
}
Write-Host "done. ledger: $Ledger"
