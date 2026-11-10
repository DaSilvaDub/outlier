<#
.SYNOPSIS
  Same-day NCAA (CFB) pack refresh: pre-flight host disk check, live pipeline execution,
  export ncaafb_only.csv, strip ruled-out players, and publish to Google Drive
  dated + rolling folders and BETTING REPORTS\NCAA.

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
    [switch]$SkipInjuryFilter,
    [double]$MinDiskSpaceGB = 5.0,
    [int]$WindowDays = 5
)

$ErrorActionPreference = "Stop"

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
    Write-Error "[NCAA-SYS-001 VIOLATION] Critical low disk space on C: ($freeGb GB free < $MinDiskSpaceGB GB minimum required). Aborting."
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
Write-Output "refresh_ncaa_pack: date=$Date skipPipeline=$SkipPipeline skipDrive=$SkipDrive scratchOut=$ScratchOut"

if (-not $SkipPipeline) {
    if (Test-Path -LiteralPath $cfbRepo) {
        Write-Output "Running NCAA CFB pipeline updates in $cfbRepo..."
        Push-Location $cfbRepo
        try {
            # 1. Run daily ingest and market update
            Write-Output "Running cfb_analytics daily with props..."
            Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "daily", "--with-props")

            # 2. Generate board, mispriced and over-board outputs
            $reportDir = Join-Path $cfbRepo "data\reports\$Date"
            if (-not (Test-Path -LiteralPath $reportDir)) {
                New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
            }

            Write-Output "Generating CFB board, mispriced and over-board..."
            try {
                Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "board", "--date", $Date, "--with-reasoning")
            } catch { Write-Warning "board generation warning: $_" }

            try {
                Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "mispriced", "--date", $Date, "--min-edge", "0.01")
            } catch { Write-Warning "mispriced generation warning: $_" }

            try {
                Invoke-CheckedPython -Arguments @("-m", "cfb_analytics.cli", "over-board", "--date", $Date)
            } catch { Write-Warning "over-board generation warning: $_" }

            # 3. Run master V2 simulation engine
            $simScript = Join-Path $cfbRepo "scripts\run_master_v2_pipeline.py"
            if (Test-Path -LiteralPath $simScript) {
                Write-Output "Running master V2 simulation engine..."
                try {
                    Invoke-CheckedPython -Arguments @($simScript, "--date", $Date, "--window-days", $WindowDays)
                } catch { Write-Warning "master v2 simulation warning: $_" }
            }
        }
        finally {
            Pop-Location
        }
    } else {
        Write-Warning "cfb-analytics repo not found at $cfbRepo; skipping live pipeline run."
    }
}

$exportCsv = Join-Path $repoRoot "data\NCAAFB\exports\ncaafb_only.csv"
$exportDir = Split-Path -Parent $exportCsv
if (-not (Test-Path -LiteralPath $exportDir)) {
    New-Item -ItemType Directory -Path $exportDir -Force | Out-Null
}

$exportScript = Join-Path $cfbRepo "scripts\export_ncaafb_extra_pack.py"
if (Test-Path -LiteralPath $exportScript) {
    Write-Output "Exporting ncaafb_only.csv from cfb-analytics..."
    Push-Location $cfbRepo
    try {
        Invoke-CheckedPython -Arguments @($exportScript, "--date", $Date, "--out", $exportCsv)
    }
    finally {
        Pop-Location
    }
} else {
    Write-Warning "export_ncaafb_extra_pack.py not found at $exportScript"
}

$beforeRows = Get-CsvDataRowCount -Path $exportCsv
Write-Output "Export rows before injury filter: $beforeRows"

# If current date has 0 rows (e.g. Monday), check next slate in window to ensure active extra pack
if ($beforeRows -eq 0 -and (Test-Path -LiteralPath $exportScript)) {
    $nextDate = [DateTime]::Parse($Date).AddDays(1).ToString("yyyy-MM-dd")
    Write-Output "Checking next slate date ($nextDate) for upcoming game props..."
    Push-Location $cfbRepo
    try {
        $tempOut = Join-Path $exportDir "ncaafb_only_${nextDate}.csv"
        Invoke-CheckedPython -Arguments @($exportScript, "--date", $nextDate, "--out", $tempOut)
        $nextRows = Get-CsvDataRowCount -Path $tempOut
        if ($nextRows -gt 0) {
            Write-Output "Found $nextRows upcoming props for $nextDate. Staging into extra pack."
            Copy-Item -LiteralPath $tempOut -Destination $exportCsv -Force
            $beforeRows = $nextRows
        }
    }
    catch {
        Write-Warning "Next slate check skipped: $_"
    }
    finally {
        Pop-Location
    }
}

$filteredCsv = $exportCsv
if ($ScratchOut) {
    $scratchDir = Split-Path -Parent $ScratchOut
    if ($scratchDir -and -not (Test-Path -LiteralPath $scratchDir)) {
        New-Item -ItemType Directory -Path $scratchDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $exportCsv -Destination $ScratchOut -Force
    $filteredCsv = $ScratchOut
}

$afterRows = Get-CsvDataRowCount -Path $filteredCsv
Write-Output "Export rows after injury filter: $afterRows (before=$beforeRows)"

$shouldPublish = -not $SkipDrive
if ($ScratchOut -and -not $ForceDrive) {
    $shouldPublish = $false
    Write-Output "ScratchOut set without -ForceDrive; skipping Drive publish."
}

if ($shouldPublish -and $driveRoot) {
    $datedDir = Join-Path $driveToday "extra_packs_${Date}_latest"
    $rollingDir = Join-Path $driveToday "extra_packs"
    $driveNcaa = Join-Path $driveRoot "BETTING REPORTS\NCAA"

    New-Item -ItemType Directory -Force -Path $datedDir, $rollingDir, $driveNcaa | Out-Null

    $publishSrc = $filteredCsv
    if (-not $ScratchOut -and (Resolve-Path $filteredCsv).Path -ne (Resolve-Path $exportCsv).Path) {
        Copy-Item -LiteralPath $filteredCsv -Destination $exportCsv -Force
    }

    $dstDated = Join-Path $datedDir "ncaafb_only.csv"
    $dstRolling = Join-Path $rollingDir "ncaafb_only.csv"
    Copy-Item -LiteralPath $publishSrc -Destination $dstDated -Force
    Copy-Item -LiteralPath $publishSrc -Destination $dstRolling -Force
    Write-Output "Published ncaafb_only.csv -> $dstDated"
    Write-Output "Published ncaafb_only.csv -> $dstRolling"

    # Also publish to BETTING REPORTS\NCAA
    $dstNcaaDated = Join-Path $driveNcaa "ncaafb_only_${Date}.csv"
    $dstNcaaLatest = Join-Path $driveNcaa "ncaafb_only_latest.csv"
    Copy-Item -LiteralPath $publishSrc -Destination $dstNcaaDated -Force
    Copy-Item -LiteralPath $publishSrc -Destination $dstNcaaLatest -Force

    # Sync CFB simulation reports if present
    $cfbReportDir = Join-Path $cfbRepo "data\reports\$Date"
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

# Sync to Desktop Today organized subfolders
$organizerScript = Join-Path $repoRoot "scripts\organize_desktop_today.py"
if (Test-Path -LiteralPath $organizerScript) {
    Write-Output "Organizing Desktop Today folders for NCAA..."
    Invoke-CheckedPython -Arguments @($organizerScript, "--sport", "NCAA", "--date", $Date)
}

Write-Output "refresh_ncaa_pack done: before=$beforeRows after=$afterRows date=$Date"
exit 0
