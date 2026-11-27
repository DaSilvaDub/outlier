<#
.SYNOPSIS
  Same-day NBA pack refresh: pre-flight host disk check, live pipeline execution,
  export nba_only.csv, strip ruled-out players, and publish to Google Drive
  dated + rolling folders and BETTING REPORTS\NBA.

.PARAMETER Date
  Slate date YYYY-MM-DD (default: current America/New_York calendar date).

.PARAMETER SkipPipeline
  Re-export from existing data only (no live pipeline).

.PARAMETER SkipDrive
  Do not copy into G:\My Drive\today (useful for scratch / dry-run).

.PARAMETER ScratchOut
  Write the filtered CSV here instead of the repo export.

.PARAMETER ForceDrive
  Publish to Drive even when -ScratchOut is set.

.PARAMETER SkipInjuryFilter
  Deprecated: rejected because publication requires game availability verification.

.PARAMETER MinDiskSpaceGB
  Minimum required free GB on C:\ drive (default: 5.0 GB, optimal >= 10.0 GB).

.NOTES
  Enforces Invariants NBA-SYS-001, NBA-PROP-010, and Fail-Closed Architecture.
#>
[CmdletBinding()]
param(
    [string]$Date = "",
    [switch]$SkipPipeline,
    [switch]$SkipDrive,
    [string]$ScratchOut = "",
    [string]$PropSource = "",
    [string]$Registry = "",
    [string]$NbaPipelineRepo = $(if ($env:NBA_PIPELINE_REPO) { $env:NBA_PIPELINE_REPO } else { "C:\Users\dasil\Dev\GitHub\nba-props-pipeline" }),
    [string]$NbaDataRoot = "",
    [string]$AvailabilityReport = "",
    [string]$AvailabilityReportUrl = "",
    [switch]$ForceDrive,
    [switch]$SkipInjuryFilter,
    [switch]$UseRotoGrinders,
    [double]$MinDiskSpaceGB = 5.0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "publication_guards.ps1")

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\dasil\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

# ==============================================================================
# INVARIANT NBA-SYS-001: Pre-Flight Storage Capacity Gate
# ==============================================================================
$cDrive = Get-PSDrive C
$freeGb = [math]::Round($cDrive.Free / 1GB, 2)
Write-Output "[$(Get-Date -Format 'o')] Host C: storage check: $freeGb GB free."

if ($freeGb -lt $MinDiskSpaceGB) {
    [Console]::Error.WriteLine("[NBA-SYS-001 VIOLATION] Critical low disk space on C: ($freeGb GB free < $MinDiskSpaceGB GB minimum required). Aborting to prevent Errno 28 capture failure.")
    exit 2
} elseif ($freeGb -lt 10.0) {
    Write-Warning "[NBA-SYS-001 WARNING] Host storage ($freeGb GB free) is below recommended 10.0 GB threshold."
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

function Get-EasternDateString {
    try {
        $tz = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
        return [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz).ToString("yyyy-MM-dd")
    }
    catch {
        return (Get-Date).ToString("yyyy-MM-dd")
    }
}

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

if (-not $Date) {
    $Date = Get-EasternDateString
}

Set-Location -LiteralPath $repoRoot
Write-Output "refresh_nba_pack: date=$Date skipPipeline=$SkipPipeline skipDrive=$SkipDrive scratchOut=$ScratchOut"

# ==============================================================================
# INVARIANT NBA-PROP-010: Prop Edge Staleness Detection & Live Re-Trigger
# ==============================================================================
$propEdgeSources = @(
    "G:\My Drive\Sports_Analytics\01_Active\NBA_Active\PROP EDGES",
    "C:\Users\dasil\Dev\GitHub\Sports_Analytics\Sports_Analytics\01_Active\NBA_Active\PROP EDGES",
    "$nbaPipelineRepo\output\PROP EDGES"
)

$exportCsv = if ($ScratchOut) { $ScratchOut } else { Join-Path $repoRoot "data\NBA\exports\nba_only.csv" }
$exportDir = Split-Path -Parent $exportCsv
New-Item -ItemType Directory -Path $exportDir -Force | Out-Null
$upstreamDir = Join-Path $exportDir "upstream_$Date"
if (-not $SkipPipeline) {
    if (-not $NbaDataRoot) {
        $NbaDataRoot = if ($env:NBA_ROOT) { $env:NBA_ROOT } elseif ($driveRoot) { Join-Path $driveRoot "Sports_Analytics\01_Active\NBA_Active" } else { Join-Path $nbaPipelineRepo "output" }
    }
    $upstreamArgs = @( (Join-Path $repoRoot "scripts\refresh_nba_upstream.py"), "--pipeline-repo", $nbaPipelineRepo, "--date", $Date, "--output-dir", $upstreamDir, "--data-root", $NbaDataRoot )
    if ($UseRotoGrinders) { $upstreamArgs += "--use-rg" }
    if ($AvailabilityReport) { $upstreamArgs += @("--report-pdf", $AvailabilityReport) }
    if ($AvailabilityReportUrl) { $upstreamArgs += @("--report-url", $AvailabilityReportUrl) }
    try { Invoke-CheckedPython -Arguments $upstreamArgs }
    catch { Write-Warning "Upstream evidence refresh failed. The publication gates will check the collected inputs." }
    # An attempted current refresh always selects its own output, never a retained Drive feed.
    $PropSource = Join-Path $upstreamDir "prop_edges_latest.csv"
    if (-not $Registry) { $Registry = Join-Path $upstreamDir "nba_availability_registry.json" }
    $propEdgeSources = @($upstreamDir)
}

$exportScript = Join-Path $repoRoot "scripts\export_nba_extra_pack.py"
$exportArgs = @($exportScript, "--date", $Date, "--out", $exportCsv, "--no-allow-latest-fallback", "--no-auto-refresh")
if ($PropSource) { $exportArgs += @("--src", $PropSource) }
Write-Output "Exporting nba_only.csv ..."
Invoke-CheckedPython $exportArgs

$beforeRows = Get-CsvDataRowCount -Path $exportCsv
Write-Output "Export rows before injury filter: $beforeRows"

$filteredCsv = $exportCsv
$summaryJson = Join-Path $exportDir "nba_only_filter_summary_$Date.json"


$filterScript = Join-Path $repoRoot "scripts\filter_nba_ruled_out.py"
if ($SkipInjuryFilter) {
    throw "Skipping injury verification cannot authorize NBA publication."
}
elseif (-not (Test-Path -LiteralPath $filterScript)) {
    throw "Required injury verification missing: $filterScript"
}
else {
    Write-Output "Filtering ruled-out players..."
    $filterArgs = @($filterScript, "--csv", $filteredCsv, "--date", $Date, "--out", $filteredCsv, "--summary-json", $summaryJson)
    if ($Registry) { $filterArgs += @("--registry", $Registry) }
    Invoke-CheckedPython -Arguments $filterArgs
}

$afterRows = Get-CsvDataRowCount -Path $filteredCsv
Write-Output "Export rows after injury filter: $afterRows (before=$beforeRows)"

if ($afterRows -eq 0) {
    Write-Warning "[FAIL-CLOSED NOTICE] Final filtered pack contains 0 candidate rows. All pregame prop markets stood down."
}

$shouldPublish = -not $SkipDrive
if ($ScratchOut -and -not $ForceDrive) {
    $shouldPublish = $false
    Write-Output "ScratchOut set without -ForceDrive; skipping Drive publish."
}

# Build companions exclusively from the final authorized selections.
$bundleScript = Join-Path $repoRoot "scripts\nba_publication_bundle.py"
$bundleArgs = @($bundleScript, "--date", $Date, "--csv", $filteredCsv, "--summary", $summaryJson, "--out-dir", $exportDir)
foreach ($kind in @(@("prop_edges_latest.csv", "--edge-source"), @("nba_graded_plays_latest.csv", "--grade-source"))) {
    foreach ($peDir in $propEdgeSources) {
        $candidate = Join-Path $peDir $kind[0]
        if (Test-Path -LiteralPath $candidate) { $bundleArgs += @($kind[1], $candidate); break }
    }
}
Invoke-CheckedPython -Arguments $bundleArgs

function Copy-VerifiedNbaCsv {
    param([string]$Source, [string]$Destination, [string]$Summary = "")
    $copyArgs = @($bundleScript, "--date", $Date, "--csv", $Source, "--destination", $Destination)
    if ($Summary) { $copyArgs += @("--summary", $Summary) }
    Invoke-CheckedPython -Arguments $copyArgs
}

# Scratch runs never write the live Desktop tree.
if (-not $ScratchOut) {
    $organizerScript = Join-Path $repoRoot "scripts\organize_desktop_today.py"
    Write-Output "Organizing verified NBA bundle on Desktop..."
    Invoke-CheckedPython -Arguments @($organizerScript, "--sport", "NBA", "--date", $Date, "--skip-drive")
}

if ($shouldPublish -and $driveRoot) {
    Assert-PublicationHold $repoRoot
    Assert-PublicationHold "C:\Users\dasil\Dev\GitHub\outlier"
    $datedDir = Join-Path $driveToday "extra_packs_${Date}_latest"
    $rollingDir = Join-Path $driveToday "extra_packs"
    $driveNba = Join-Path $driveRoot "BETTING REPORTS\NBA"

    New-Item -ItemType Directory -Force -Path $datedDir, $rollingDir, $driveNba | Out-Null

    $publishSrc = $filteredCsv
    if (-not $ScratchOut -and (Resolve-Path $filteredCsv).Path -ne (Resolve-Path $exportCsv).Path) {
        Copy-Item -LiteralPath $filteredCsv -Destination $exportCsv -Force
    }

    $dstDated = Join-Path $datedDir "nba_only.csv"
    $dstRolling = Join-Path $rollingDir "nba_only.csv"
    Copy-VerifiedNbaCsv $publishSrc $dstDated $summaryJson
    Copy-VerifiedNbaCsv $publishSrc $dstRolling $summaryJson
    Write-Output "Published nba_only.csv -> $dstDated"
    Write-Output "Published nba_only.csv -> $dstRolling"

    foreach ($name in @("nba_prop_edges", "nba_graded_plays")) {
        $csv = Join-Path $exportDir "$name.csv"
        Copy-VerifiedNbaCsv $csv (Join-Path $driveToday "$name.csv")
        Copy-VerifiedNbaCsv $csv (Join-Path $driveNba "$name.csv")
        Copy-VerifiedNbaCsv $csv (Join-Path $driveNba "${name}_${Date}.csv")
    }
    Copy-VerifiedNbaCsv (Join-Path $exportDir "nba_prop_edges.csv") (Join-Path $driveNba "nba_prop_edges_latest.csv")

    $legacyTxtFiles = @(
        "SAS_OKC_GAME_TOTALS.txt",
        "SAS_OKC_INJURY_ANALYSIS.txt",
        "SAS_OKC_TOP_PROPS.txt"
    )
    foreach ($legacyName in $legacyTxtFiles) {
        foreach ($dir in @($driveToday, $driveNba)) {
            $stalePath = Join-Path $dir $legacyName
            if (Test-Path -LiteralPath $stalePath) {
                Remove-Item -LiteralPath $stalePath -Force -ErrorAction SilentlyContinue
                Write-Output "Removed legacy stale file: $stalePath"
            }
        }
    }

    $txtFiles = @(
        "$nbaPipelineRepo\GAME_TOTALS_PROJECTION.txt",
        "$nbaPipelineRepo\MATCHUP_FOCUS.txt"
    )

    # Prop narratives are rebuilt from the verified publication status.
    $status = Get-Content -LiteralPath ([IO.Path]::ChangeExtension($filteredCsv, ".status.json")) -Raw | ConvertFrom-Json
    $propNotice = "NBA $Date - status=$($status.status); actionable_rows=$afterRows. See verified nba_only.csv. Prior raw top-prop narratives are retired."
    foreach ($dir in @($driveToday, $driveNba)) {
        Set-Content -LiteralPath (Join-Path $dir "ALL_TOP_PROPS.txt") -Value $propNotice -Encoding UTF8
    }
    foreach ($tf in $txtFiles) {
        $name = Split-Path -Leaf $tf
        $notice = "NBA $Date - RESEARCH REFERENCE ONLY. Awaiting verified executable totals prices; zero actionable totals and no price-based EV. Projection gaps are not betting edges."
        $body = ""
        if ((Test-Path -LiteralPath $tf) -and ((Get-Item -LiteralPath $tf).Length -gt 0)) {
            $candidateText = Get-Content -LiteralPath $tf -Raw
            if ($candidateText.Contains($Date)) { $body = $candidateText }
        }
        foreach ($dir in @($driveToday, $driveNba)) {
            Set-Content -LiteralPath (Join-Path $dir $name) -Value "$notice`n$body" -Encoding UTF8
        }
    }
}
elseif ($shouldPublish) {
    Write-Warning "Google Drive root not found; skipping Drive copy."
}
else {
    Write-Output "Drive publish skipped."
}

Write-Output "refresh_nba_pack done: before=$beforeRows after=$afterRows date=$Date"
exit 0
