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
  Export only; do not filter ruled-out / inactive players.

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
    [switch]$ForceDrive,
    [switch]$SkipInjuryFilter,
    [double]$MinDiskSpaceGB = 5.0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "publication_guards.ps1")

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$nbaPipelineRepo = "C:\Users\dasil\Dev\GitHub\nba-props-pipeline"
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
    Write-Error "[NBA-SYS-001 VIOLATION] Critical low disk space on C: ($freeGb GB free < $MinDiskSpaceGB GB minimum required). Aborting to prevent Errno 28 capture failure."
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

$needsPipelineRun = -not $SkipPipeline
if ($SkipPipeline) {
    $foundFresh = $false
    foreach ($peDir in $propEdgeSources) {
        $cand = Join-Path $peDir "prop_edges_latest.csv"
        if (Test-Path -LiteralPath $cand) {
            $ageHours = ((Get-Date) - (Get-Item $cand).LastWriteTime).TotalHours
            if ($ageHours -le 12.0) {
                $foundFresh = $true
                break
            }
        }
    }
    if (-not $foundFresh) {
        Write-Warning "[NBA-PROP-010] Candidate prop edges are older than 12 hours. Overriding SkipPipeline to force live refresh."
        $needsPipelineRun = $true
    }
}

if ($needsPipelineRun) {
    if (Test-Path -LiteralPath $nbaPipelineRepo) {
        Write-Output "Running NBA props pipeline updates in $nbaPipelineRepo..."
        Push-Location $nbaPipelineRepo
        try {
            # 1. Update lineups and injury reports
            $lineupsScript = Join-Path $nbaPipelineRepo "scripts\refresh_lineups_injuries.ps1"
            if (Test-Path -LiteralPath $lineupsScript) {
                Write-Output "Refreshing NBA lineups and injuries..."
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $lineupsScript -Date $Date -Wait
                if ($LASTEXITCODE -ne 0) { throw "refresh_lineups_injuries.ps1 failed with exit code $LASTEXITCODE" }
            }

            # 2. Run data watcher check cycle
            $watcherScript = Join-Path $nbaPipelineRepo "pipeline\nba_data_watcher.py"
            if (Test-Path -LiteralPath $watcherScript) {
                Write-Output "Running nba_data_watcher --once..."
                Invoke-CheckedPython -Arguments @($watcherScript, "--once")
            }

            # 3. Generate updated prop edges and playbook grades
            $edgesScript = Join-Path $nbaPipelineRepo "pipeline\generate_prop_edges.py"
            if (Test-Path -LiteralPath $edgesScript) {
                Write-Output "Generating prop edges..."
                Invoke-CheckedPython -Arguments @($edgesScript, "--allow-stale-hardrock")
            }

            $gradesScript = Join-Path $nbaPipelineRepo "pipeline\generate_playbook_grades.py"
            if (Test-Path -LiteralPath $gradesScript) {
                Write-Output "Generating playbook grades..."
                Invoke-CheckedPython -Arguments @($gradesScript)
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Warning "NBA pipeline repo not found at $nbaPipelineRepo; skipping live pipeline run."
    }
}

$exportCsv = Join-Path $repoRoot "data\NBA\exports\nba_only.csv"
$exportDir = Split-Path -Parent $exportCsv
if (-not (Test-Path -LiteralPath $exportDir)) {
    New-Item -ItemType Directory -Path $exportDir -Force | Out-Null
}

$exportScript = Join-Path $repoRoot "scripts\export_nba_extra_pack.py"
$exportArgs = @($exportScript, "--date", $Date, "--out", $exportCsv)
Write-Output "Exporting nba_only.csv ..."
Invoke-CheckedPython $exportArgs

$beforeRows = Get-CsvDataRowCount -Path $exportCsv
Write-Output "Export rows before injury filter: $beforeRows"

$filteredCsv = $exportCsv
$summaryJson = Join-Path $repoRoot "data\NBA\exports\nba_only_filter_summary_$Date.json"

if ($ScratchOut) {
    $scratchDir = Split-Path -Parent $ScratchOut
    if ($scratchDir -and -not (Test-Path -LiteralPath $scratchDir)) {
        New-Item -ItemType Directory -Path $scratchDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $exportCsv -Destination $ScratchOut -Force
    $filteredCsv = $ScratchOut
}

$filterScript = Join-Path $repoRoot "scripts\filter_nba_ruled_out.py"
if ($SkipInjuryFilter) {
    Write-Warning "Skipping injury filter (-SkipInjuryFilter set): pack is NOT filtered for ruled-out players."
}
elseif (-not (Test-Path -LiteralPath $filterScript)) {
    # Fail closed: never publish an unfiltered pack unless the caller asked for it.
    throw "Injury filter not found at $filterScript. Pass -SkipInjuryFilter to publish an unfiltered pack."
}
else {
    Write-Output "Filtering ruled-out players..."
    Invoke-CheckedPython @(
        $filterScript,
        "--csv", $filteredCsv,
        "--out", $filteredCsv,
        "--summary-json", $summaryJson
    )
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

# Local Desktop staging runs before the Drive publish so an active publication
# hold (which throws below) never blocks local staging.
$organizerScript = Join-Path $repoRoot "scripts\organize_desktop_today.py"
if (Test-Path -LiteralPath $organizerScript) {
    Write-Output "Organizing Desktop Today folders for NBA (local only)..."
    Invoke-CheckedPython -Arguments @($organizerScript, "--sport", "NBA", "--date", $Date, "--skip-drive")
}

if ($shouldPublish -and $driveRoot) {
    Assert-PublicationHold $repoRoot
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
    Copy-Item -LiteralPath $publishSrc -Destination $dstDated -Force
    Copy-Item -LiteralPath $publishSrc -Destination $dstRolling -Force
    Write-Output "Published nba_only.csv -> $dstDated"
    Write-Output "Published nba_only.csv -> $dstRolling"

    # Sync companions from nba-props-pipeline or drive
    foreach ($peDir in $propEdgeSources) {
        if (Test-Path -LiteralPath $peDir) {
            $csv = Join-Path $peDir "prop_edges_latest.csv"
            if (Test-Path -LiteralPath $csv) {
                Copy-Item -LiteralPath $csv -Destination (Join-Path $driveToday "nba_prop_edges.csv") -Force
                Copy-Item -LiteralPath $csv -Destination (Join-Path $driveNba "nba_prop_edges_latest.csv") -Force
                Copy-Item -LiteralPath $csv -Destination (Join-Path $driveNba "nba_prop_edges_${Date}.csv") -Force
                Write-Output "Published nba_prop_edges.csv"
                break
            }
        }
    }

    foreach ($peDir in $propEdgeSources) {
        if (Test-Path -LiteralPath $peDir) {
            $graded = Join-Path $peDir "nba_graded_plays_latest.csv"
            if (Test-Path -LiteralPath $graded) {
                Copy-Item -LiteralPath $graded -Destination (Join-Path $driveToday "nba_graded_plays.csv") -Force
                Copy-Item -LiteralPath $graded -Destination (Join-Path $driveNba "nba_graded_plays.csv") -Force
                Copy-Item -LiteralPath $graded -Destination (Join-Path $driveNba "nba_graded_plays_${Date}.csv") -Force
                Write-Output "Published nba_graded_plays.csv"
                break
            }
        }
    }

    $txtFiles = @(
        "$nbaPipelineRepo\ALL_TOP_PROPS.txt",
        "$nbaPipelineRepo\GAME_TOTALS_PROJECTION.txt",
        "$nbaPipelineRepo\SAS_OKC_GAME_TOTALS.txt",
        "$nbaPipelineRepo\SAS_OKC_INJURY_ANALYSIS.txt",
        "$nbaPipelineRepo\SAS_OKC_TOP_PROPS.txt"
    )

    foreach ($tf in $txtFiles) {
        if (Test-Path -LiteralPath $tf) {
            $name = Split-Path -Leaf $tf
            Copy-Item -LiteralPath $tf -Destination (Join-Path $driveToday $name) -Force
            Copy-Item -LiteralPath $tf -Destination (Join-Path $driveNba $name) -Force
            Write-Output "Published $name"
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
