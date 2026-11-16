<#
.SYNOPSIS
  Same-day NCAA (CFB) pack refresh: pre-flight host disk check, live cfb-analytics
  pipeline, export ncaafb_only.csv, stage Desktop Today locally, then (unless held or
  -SkipDrive) publish to Google Drive dated + rolling folders and BETTING REPORTS\NCAA.

  Fails closed: every pipeline stage, the export, and the freshness/empty-slate gates
  exit nonzero instead of warning, so a stale CSV can never be published under a
  new date. No injury filter exists for NCAA yet; rows are published as exported.

.PARAMETER Date
  Slate date YYYY-MM-DD (default: current America/New_York calendar date).

.PARAMETER SkipPipeline
  Re-export from existing data only (no live pipeline).

.PARAMETER SkipDrive
  Do not copy into Google Drive (local staging still runs).

.PARAMETER ScratchOut
  Write the CSV here instead of publishing from the repo export.

.PARAMETER ForceDrive
  Publish to Drive even when -ScratchOut is set.

.PARAMETER MinDiskSpaceGB
  Minimum required free GB on C:\ drive (default: 5.0 GB, optimal >= 10.0 GB).

.PARAMETER WindowDays
  Number of days in CFB slate window to model (default: 5).
#>
[CmdletBinding()]
param(
    [string]$Date = "",
    [switch]$SkipPipeline,
    [switch]$SkipDrive,
    [string]$ScratchOut = "",
    [switch]$ForceDrive,
    [double]$MinDiskSpaceGB = 5.0,
    [int]$WindowDays = 5
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "publication_guards.ps1")

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$cfbRepo = "C:\Users\dasil\Dev\GitHub\cfb-analytics"
$python = "C:\Users\dasil\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

# Pre-Flight Storage Capacity Gate
$cDrive = Get-PSDrive C
$freeGb = [math]::Round($cDrive.Free / 1GB, 2)
Write-Output "[$(Get-Date -Format 'o')] Host C: storage check: $freeGb GB free."

if ($freeGb -lt $MinDiskSpaceGB) {
    [Console]::Error.WriteLine("[NCAA-SYS-001 VIOLATION] Critical low disk space on C: ($freeGb GB free < $MinDiskSpaceGB GB minimum required). Aborting.")
    exit 2
} elseif ($freeGb -lt 10.0) {
    Write-Warning "[NCAA-SYS-001 WARNING] Host storage ($freeGb GB free) is below recommended 10.0 GB threshold."
}

# Resolve Drive root (prioritizing G:\My Drive)
$driveRoots = @(
    "G:\My Drive",
    "C:\Users\dasil\My Drive (dasilvadub@gmail.com)"
)
$driveRoot = $null
foreach ($dr in $driveRoots) {
    if (Test-Path -LiteralPath $dr) {
        $driveRoot = $dr
        break
    }
}
$driveToday = if ($driveRoot) { Join-Path $driveRoot "today" } else { "G:\My Drive\today" }

function Invoke-CheckedPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "python $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-CsvDataRowCount {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $n = 0
    Import-Csv -LiteralPath $Path | ForEach-Object { $n++ }
    return $n
}

# Runs the cfb exporter for one date into a temp file that must be written during this
# run, then swaps it over $OutCsv. A silent no-op export can never leave yesterday's rows
# behind under today's name, and a failed export leaves the last good file in place.
function Invoke-FreshExport {
    param([Parameter(Mandatory = $true)][string]$SlateDate, [Parameter(Mandatory = $true)][string]$OutCsv)
    $tmpCsv = "$OutCsv.tmp.csv"
    if (Test-Path -LiteralPath $tmpCsv) { Remove-Item -LiteralPath $tmpCsv -Force }
    $startedUtc = [DateTime]::UtcNow.AddSeconds(-2)
    Push-Location $cfbRepo
    try {
        # Out-Host keeps exporter stdout visible without polluting this function's return value.
        Invoke-CheckedPython -Arguments @($exportScript, "--date", $SlateDate, "--out", $tmpCsv) | Out-Host
    }
    finally {
        Pop-Location
    }
    Assert-RequiredFile $tmpCsv
    if ((Get-Item -LiteralPath $tmpCsv).LastWriteTimeUtc -lt $startedUtc) {
        throw "Stale export (not written this run): $tmpCsv"
    }
    Move-Item -LiteralPath $tmpCsv -Destination $OutCsv -Force
    return (Get-CsvDataRowCount -Path $OutCsv)
}

if (-not $Date) {
    $Date = Get-EasternSlateDate
}
Assert-SlateDate $Date

Set-Location -LiteralPath $repoRoot
Write-Output "refresh_ncaa_pack: date=$Date skipPipeline=$SkipPipeline skipDrive=$SkipDrive scratchOut=$ScratchOut"

if (-not (Test-Path -LiteralPath $cfbRepo)) {
    throw "cfb-analytics repo not found at $cfbRepo"
}
$exportScript = Join-Path $cfbRepo "scripts\export_ncaafb_extra_pack.py"
Assert-RequiredFile $exportScript

if (-not $SkipPipeline) {
    Write-Output "Running NCAA CFB pipeline updates in $cfbRepo..."
    Push-Location $cfbRepo
    try {
        Write-Output "Running cfb_analytics daily with props..."
        Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "daily", "--with-props")

        $reportDir = Join-Path $cfbRepo "data\reports\$Date"
        if (-not (Test-Path -LiteralPath $reportDir)) {
            New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
        }

        # --with-reasoning is cfb_analytics' offline MultiFactorReasoningEngine, not an LLM call.
        Write-Output "Generating CFB board, mispriced and over-board..."
        Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "board", "--date", $Date, "--with-reasoning")
        Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "mispriced", "--date", $Date, "--min-edge", "0.01")
        Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "over-board", "--date", $Date)

        $simScript = Join-Path $cfbRepo "scripts\run_master_v2_pipeline.py"
        if (Test-Path -LiteralPath $simScript) {
            Write-Output "Running master V2 simulation engine..."
            Invoke-CheckedPython -Arguments @($simScript, "--date", $Date, "--window-days", $WindowDays)
        }
        else {
            Write-Output "master V2 simulation script not present; skipping (optional stage)."
        }
    }
    finally {
        Pop-Location
    }
}

$exportDir = Join-Path $repoRoot "data\NCAAFB\exports"
if (-not (Test-Path -LiteralPath $exportDir)) {
    New-Item -ItemType Directory -Path $exportDir -Force | Out-Null
}

$exportCsv = Join-Path $exportDir "ncaafb_only.csv"
Write-Output "Exporting ncaafb_only.csv for $Date from cfb-analytics..."
$exportRows = Invoke-FreshExport -SlateDate $Date -OutCsv $exportCsv
$publishDate = $Date
$publishCsv = $exportCsv
Write-Output "Export rows for ${Date}: $exportRows"

# No games today (e.g. Monday): look one day ahead, but publish that slate under its
# own date. Never relabel tomorrow's rows as today's pack.
if ($exportRows -eq 0) {
    $nextDate = [DateTime]::ParseExact($Date, "yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture).AddDays(1).ToString("yyyy-MM-dd")
    Write-Output "No rows for $Date; checking next slate date $nextDate..."
    $nextCsv = Join-Path $exportDir "ncaafb_only_${nextDate}.csv"
    $nextRows = Invoke-FreshExport -SlateDate $nextDate -OutCsv $nextCsv
    if ($nextRows -gt 0) {
        Write-Output "Found $nextRows props for $nextDate; publishing them under $nextDate."
        $publishDate = $nextDate
        $publishCsv = $nextCsv
        $exportRows = $nextRows
    }
}

if ($exportRows -eq 0) {
    [Console]::Error.WriteLine("ncaafb_only.csv is empty for $Date and the next slate. Refusing to stage or publish an empty pack.")
    exit 2
}

if ($ScratchOut) {
    $scratchDir = Split-Path -Parent $ScratchOut
    if ($scratchDir -and -not (Test-Path -LiteralPath $scratchDir)) {
        New-Item -ItemType Directory -Path $scratchDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $publishCsv -Destination $ScratchOut -Force
    $publishCsv = $ScratchOut
}

$shouldPublish = -not $SkipDrive
if ($ScratchOut -and -not $ForceDrive) {
    $shouldPublish = $false
    Write-Output "ScratchOut set without -ForceDrive; skipping Drive publish."
}

# Local Desktop staging runs before the Drive publish so an active publication
# hold (which throws below) never blocks local staging.
$organizerScript = Join-Path $repoRoot "scripts\organize_desktop_today.py"
Assert-RequiredFile $organizerScript
Write-Output "Organizing Desktop Today folders for NCAA (local only)..."
Invoke-CheckedPython -Arguments @($organizerScript, "--sport", "NCAA", "--date", $publishDate, "--skip-drive")

if ($shouldPublish -and $driveRoot) {
    Assert-PublicationHold $repoRoot
    $datedDir = Join-Path $driveToday "extra_packs_${publishDate}_latest"
    $rollingDir = Join-Path $driveToday "extra_packs"
    $driveNcaa = Join-Path $driveRoot "BETTING REPORTS\NCAA"

    New-Item -ItemType Directory -Force -Path $datedDir, $rollingDir, $driveNcaa | Out-Null

    foreach ($dst in @(
        (Join-Path $datedDir "ncaafb_only.csv"),
        (Join-Path $rollingDir "ncaafb_only.csv"),
        (Join-Path $driveNcaa "ncaafb_only_${publishDate}.csv"),
        (Join-Path $driveNcaa "ncaafb_only_latest.csv")
    )) {
        Copy-Item -LiteralPath $publishCsv -Destination $dst -Force
        Write-Output "Published ncaafb_only.csv -> $dst"
    }

    $cfbReportDir = Join-Path $cfbRepo "data\reports\$publishDate"
    if (Test-Path -LiteralPath $cfbReportDir) {
        Get-ChildItem -LiteralPath $cfbReportDir -File | Where-Object { $_.Name -match "\.(md|txt|json)$" } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $driveToday $_.Name) -Force
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $driveNcaa $_.Name) -Force
            Write-Output "Published CFB report: $($_.Name)"
        }
    }
}
elseif ($shouldPublish) {
    Write-Warning "Google Drive root not found; skipping Drive copy."
}
else {
    Write-Output "Drive publish skipped."
}

Write-Output "refresh_ncaa_pack done: rows=$exportRows slate=$publishDate requested=$Date"
exit 0
