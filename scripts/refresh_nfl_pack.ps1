<#
.SYNOPSIS
  Same-day NFL pack refresh: optional pipeline, export nfl_only.csv, strip
  ruled-out players from nfl_only + companions (best_bets / alt_floors /
  matchup scripts / game scripts), publish to Google Drive dated + rolling folders.

.PARAMETER Date
  Slate date YYYY-MM-DD (default: current America/New_York calendar date).

.PARAMETER SkipPipeline
  Re-export from existing normalized JSON only (no live pipeline).

.PARAMETER SkipDrive
  Do not copy into G:\My Drive\today (useful for scratch / dry-run).

.PARAMETER ScratchOut
  Write the filtered CSV here instead of (or before checking) the repo export.
  When set, the repo data\NFL\exports\nfl_only.csv is still produced by export
  then filtered into ScratchOut; Drive publish is skipped unless -ForceDrive.

.PARAMETER ForceDrive
  Publish to Drive even when -ScratchOut is set (still blocked if empty+events).

.PARAMETER SkipInjuryFilter
  Export only; do not fetch ESPN / tape statuses.

.NOTES
  Idempotent and non-destructive to MLB/WNBA CSVs in Drive folders.
  Exits non-zero when the filtered export is empty after a live pipeline that
  found events (or when dated summary shows events_count > 0).
  Does NOT register scheduled tasks and does NOT git commit.
#>
[CmdletBinding()]
param(
    [string]$Date = "",
    [switch]$SkipPipeline,
    [switch]$SkipDrive,
    [string]$ScratchOut = "",
    [switch]$ForceDrive,
    [switch]$SkipInjuryFilter
)

$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\dasil\Dev\GitHub\outlier"
$driveToday = "G:\My Drive\today"
$python = "python"

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

function Get-EventsCount {
    param([Parameter(Mandatory = $true)][string]$DateStr)
    $summaryPath = Join-Path $repoRoot "data\NFL\normalized\summary_$DateStr.json"
    if (-not (Test-Path -LiteralPath $summaryPath)) { return $null }
    try {
        $s = Get-Content -Raw -LiteralPath $summaryPath | ConvertFrom-Json
        if ($null -ne $s.events_count) { return [int]$s.events_count }
    }
    catch { }
    return $null
}

function Copy-FileSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Src,
        [Parameter(Mandatory = $true)][string]$Dst
    )
    if (-not (Test-Path -LiteralPath $Src)) { return $false }
    $dstDir = Split-Path -Parent $Dst
    if (-not (Test-Path -LiteralPath $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $Src -Destination $Dst -Force
    return $true
}

if (-not $Date) {
    $Date = Get-EasternDateString
}

Set-Location -LiteralPath $repoRoot

Write-Output "refresh_nfl_pack: date=$Date skipPipeline=$SkipPipeline skipDrive=$SkipDrive scratchOut=$ScratchOut"

$pipelineRan = $false
if (-not $SkipPipeline) {
    Write-Output "Running outlier_nfl.pipeline --date $Date ..."
    Invoke-CheckedPython @("-m", "outlier_nfl.pipeline", "--date", $Date)
    $pipelineRan = $true
}
else {
    Write-Output "Skipping pipeline (re-export from existing JSON)."
}

$exportCsv = Join-Path $repoRoot "data\NFL\exports\nfl_only.csv"
Write-Output "Exporting nfl_only.csv ..."
Invoke-CheckedPython @("scripts\export_nfl_extra_pack.py", "--date", $Date, "--out", $exportCsv)

$beforeRows = Get-CsvDataRowCount -Path $exportCsv
Write-Output "Export rows before injury filter: $beforeRows"

$filteredCsv = $exportCsv
$cachePath = Join-Path $repoRoot "data\NFL\cache\espn_injuries_filter_$Date.json"
$summaryJson = Join-Path $repoRoot "data\NFL\exports\nfl_only_filter_summary_$Date.json"

if ($ScratchOut) {
    $scratchDir = Split-Path -Parent $ScratchOut
    if ($scratchDir -and -not (Test-Path -LiteralPath $scratchDir)) {
        New-Item -ItemType Directory -Path $scratchDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $exportCsv -Destination $ScratchOut -Force
    $filteredCsv = $ScratchOut
}

if (-not $SkipInjuryFilter) {
    Write-Output "Filtering ruled-out players (ESPN + tape) + companions ..."
    $filterArgs = @(
        "scripts\filter_nfl_ruled_out.py",
        "--csv", $filteredCsv,
        "--out", $filteredCsv,
        "--nfl-dir", (Join-Path $repoRoot "data\NFL"),
        "--cache", $cachePath,
        "--summary-json", $summaryJson
    )

    $bestBetsJson = Join-Path $repoRoot "data\NFL\normalized\nfl_best_bets_$Date.json"
    if (Test-Path -LiteralPath $bestBetsJson) {
        $filterArgs += @("--best-bets-json", $bestBetsJson)
        $bestBetsMd = Join-Path $repoRoot "data\NFL\normalized\nfl_best_bets_$Date.md"
        if (Test-Path -LiteralPath $bestBetsMd) {
            $filterArgs += @("--best-bets-md", $bestBetsMd)
        }
    }

    $altCsvDated = Join-Path $repoRoot "data\NFL\exports\nfl_alt_floors_$Date.csv"
    $altCsvPlain = Join-Path $repoRoot "data\NFL\exports\nfl_alt_floors.csv"
    if (Test-Path -LiteralPath $altCsvDated) {
        $filterArgs += @("--alt-floors-csv", $altCsvDated)
    }
    elseif (Test-Path -LiteralPath $altCsvPlain) {
        $filterArgs += @("--alt-floors-csv", $altCsvPlain)
    }

    $matchupSrc = Join-Path $repoRoot "data\NFL\normalized\nfl_matchup_scripts_$Date.json"
    if (Test-Path -LiteralPath $matchupSrc) {
        $filterArgs += @("--matchup-json", $matchupSrc)
    }

    Get-ChildItem -Path (Join-Path $repoRoot "reports\NFL") -Filter "${Date}_*_Game_Script.md" -ErrorAction SilentlyContinue |
        ForEach-Object { $filterArgs += @("--game-script-md", $_.FullName) }

    Invoke-CheckedPython $filterArgs

    # After filtering dated alt floors, refresh undated export copy if both exist.
    if ((Test-Path -LiteralPath $altCsvDated) -and (Test-Path -LiteralPath $altCsvPlain)) {
        Copy-Item -LiteralPath $altCsvDated -Destination $altCsvPlain -Force
    }
}
else {
    Write-Output "Skipping injury filter."
}

# If ScratchOut was set, also refresh the repo export with the same filtered bytes
# only when ScratchOut IS the intended artifact and we are not dry-running away
# from the repo file. Keep repo export filtered when ScratchOut empty (normal path).
if (-not $ScratchOut -and -not $SkipInjuryFilter) {
    # filtered in place on exportCsv already
}
elseif ($ScratchOut -and -not $SkipInjuryFilter -and -not $SkipDrive) {
    # Leave repo export as pre-filter unless ForceDrive wants Drive from filtered.
    # Scratch is the dry-run artifact; repo nfl_only.csv stays as exported unless
    # we are doing a real publish without ScratchOut.
}

$afterRows = Get-CsvDataRowCount -Path $filteredCsv
Write-Output "Export rows after injury filter: $afterRows (before=$beforeRows)"

$eventsCount = Get-EventsCount -DateStr $Date
if ($null -eq $eventsCount) {
    Write-Output "No summary_$Date.json events_count (unknown event presence)."
}
else {
    Write-Output "summary events_count=$eventsCount"
}

$emptyAfterEvents = ($afterRows -eq 0) -and (
    ($pipelineRan -and ($null -eq $eventsCount -or $eventsCount -gt 0)) -or
    ($null -ne $eventsCount -and $eventsCount -gt 0)
)

if ($emptyAfterEvents) {
    Write-Error "Filtered nfl_only.csv is empty but events exist for $Date (events_count=$eventsCount). Refusing Drive publish."
    exit 2
}

$shouldPublish = -not $SkipDrive
if ($ScratchOut -and -not $ForceDrive) {
    $shouldPublish = $false
    Write-Output "ScratchOut set without -ForceDrive; skipping Drive publish."
}

if ($shouldPublish) {
    $datedDir = Join-Path $driveToday "extra_packs_${Date}_latest"
    $rollingDir = Join-Path $driveToday "extra_packs"

    if (-not (Test-Path -LiteralPath $datedDir)) {
        New-Item -ItemType Directory -Path $datedDir -Force | Out-Null
        Write-Output "Created $datedDir"
    }
    if (-not (Test-Path -LiteralPath $rollingDir)) {
        New-Item -ItemType Directory -Path $rollingDir -Force | Out-Null
        Write-Output "Created $rollingDir"
    }

    $publishSrc = $filteredCsv
    # On a real (non-scratch) run, keep repo export in sync with what we publish.
    if (-not $ScratchOut -and (Resolve-Path $filteredCsv).Path -ne (Resolve-Path $exportCsv).Path) {
        Copy-Item -LiteralPath $filteredCsv -Destination $exportCsv -Force
    }
    elseif (-not $ScratchOut) {
        # already same file
    }
    else {
        # Scratch dry-run with ForceDrive: publish scratch bytes, leave repo export alone.
    }

    $dstDated = Join-Path $datedDir "nfl_only.csv"
    $dstRolling = Join-Path $rollingDir "nfl_only.csv"
    Copy-Item -LiteralPath $publishSrc -Destination $dstDated -Force
    Copy-Item -LiteralPath $publishSrc -Destination $dstRolling -Force
    Write-Output "Published nfl_only.csv -> $dstDated"
    Write-Output "Published nfl_only.csv -> $dstRolling"

    # Optional same-date companions (copy if present; never wipe other sports).
    $gameScript = Join-Path $repoRoot "reports\NFL\${Date}_*_Game_Script.md"
    Get-ChildItem -Path (Join-Path $repoRoot "reports\NFL") -Filter "${Date}_*_Game_Script.md" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $datedDir $_.Name) -Force
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $rollingDir $_.Name) -Force
            Write-Output "Published game script $($_.Name)"
        }

    $matchupSrc = Join-Path $repoRoot "data\NFL\normalized\nfl_matchup_scripts_$Date.json"
    if (Test-Path -LiteralPath $matchupSrc) {
        Copy-Item -LiteralPath $matchupSrc -Destination (Join-Path $datedDir "nfl_matchup_scripts.json") -Force
        Copy-Item -LiteralPath $matchupSrc -Destination (Join-Path $rollingDir "nfl_matchup_scripts.json") -Force
        Write-Output "Published nfl_matchup_scripts.json"
    }

    $altCsv = Join-Path $repoRoot "data\NFL\exports\nfl_alt_floors_$Date.csv"
    if (-not (Test-Path -LiteralPath $altCsv)) {
        $altCsv = Join-Path $repoRoot "data\NFL\exports\nfl_alt_floors.csv"
    }
    if (Test-Path -LiteralPath $altCsv) {
        Copy-Item -LiteralPath $altCsv -Destination (Join-Path $datedDir "nfl_alt_floors.csv") -Force
        Copy-Item -LiteralPath $altCsv -Destination (Join-Path $rollingDir "nfl_alt_floors.csv") -Force
        Write-Output "Published nfl_alt_floors.csv"
    }

    $bestMd = Join-Path $repoRoot "data\NFL\normalized\nfl_best_bets_$Date.md"
    if (Test-Path -LiteralPath $bestMd) {
        Copy-Item -LiteralPath $bestMd -Destination (Join-Path $datedDir "nfl_best_bets.md") -Force
        Copy-Item -LiteralPath $bestMd -Destination (Join-Path $rollingDir "nfl_best_bets.md") -Force
        Write-Output "Published nfl_best_bets.md"
    }

    # Matchup Advantage Rankings markdown reports (dated + rolling)
    Get-ChildItem -Path (Join-Path $repoRoot "reports\NFL") -Filter "${Date}_*Matchups.md" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $datedDir $_.Name) -Force
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $rollingDir $_.Name) -Force
            Write-Output "Published matchup report $($_.Name)"
        }
    Get-ChildItem -Path (Join-Path $repoRoot "reports\NFL") -Filter "Top_*_Matchups.md" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $datedDir $_.Name) -Force
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $rollingDir $_.Name) -Force
            Write-Output "Published rolling matchup report $($_.Name)"
        }

    # Matchup Advantage Rankings CSV
    $rankCsvDated = Join-Path $repoRoot "reports\NFL\${Date}_Matchup_Boost_Rankings.csv"
    $rankCsvRolling = Join-Path $repoRoot "reports\NFL\Matchup_Boost_Rankings.csv"
    if (Test-Path -LiteralPath $rankCsvDated) {
        Copy-Item -LiteralPath $rankCsvDated -Destination (Join-Path $datedDir "${Date}_Matchup_Boost_Rankings.csv") -Force
        Copy-Item -LiteralPath $rankCsvDated -Destination (Join-Path $rollingDir "Matchup_Boost_Rankings.csv") -Force
        Write-Output "Published Matchup_Boost_Rankings.csv"
    }
    elseif (Test-Path -LiteralPath $rankCsvRolling) {
        Copy-Item -LiteralPath $rankCsvRolling -Destination (Join-Path $datedDir "Matchup_Boost_Rankings.csv") -Force
        Copy-Item -LiteralPath $rankCsvRolling -Destination (Join-Path $rollingDir "Matchup_Boost_Rankings.csv") -Force
        Write-Output "Published Matchup_Boost_Rankings.csv"
    }
}
else {
    Write-Output "Drive publish skipped."
}

Write-Output "refresh_nfl_pack done: before=$beforeRows after=$afterRows date=$Date"
exit 0



