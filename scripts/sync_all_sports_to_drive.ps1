<#
.SYNOPSIS
  Universal Google Drive Sync for all 5 Sports Betting Pipelines:
  - NCAA (College Football)
  - NBA (Basketball)
  - NFL (Football)
  - MLB (Baseball)
  - WNBA (Women's Basketball)
#>
[CmdletBinding()]
param(
    [string]$Date = "",
    [string]$DriveRoot = "",
    [double]$MinDiskSpaceGB = 20.0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "publication_guards.ps1")
$repoRoot = Split-Path -Parent $PSScriptRoot
Assert-PublicationHold $repoRoot
Assert-FreeSpace $repoRoot $MinDiskSpaceGB

try {
    $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $nowEt = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
    if (-not $Date) { $Date = $nowEt.ToString("yyyy-MM-dd") }
    $timestamp = $nowEt.ToString("yyyy-MM-dd HH:mm:ss")
}
catch {
    if (-not $Date) { $Date = (Get-Date).ToString("yyyy-MM-dd") }
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " STARTING ALL-SPORTS GOOGLE DRIVE SYNC ($timestamp ET)" -ForegroundColor Cyan
Write-Host " Slate Date: $Date" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# Resolve Google Drive root
$driveRoots = @(
    "G:\My Drive",
    "C:\Users\dasil\My Drive (dasilvadub@gmail.com)"
)
if (-not $DriveRoot) {
    foreach ($dr in $driveRoots) {
        if (Test-Path -LiteralPath $dr) {
            $driveRoot = $dr
            break
        }
    }
}

if (-not $driveRoot) {
    Write-Error "Google Drive root not found! Cannot sync."
    exit 1
}

$driveToday = Join-Path $driveRoot "today"
$driveBettingReports = Join-Path $driveRoot "BETTING REPORTS"
$driveNcaa = Join-Path $driveBettingReports "NCAA"
$driveNfl = Join-Path $driveBettingReports "NFL"
$driveNba = Join-Path $driveBettingReports "NBA"
$driveMlbWnba = Join-Path $driveBettingReports "MLB_WNBA"

New-Item -ItemType Directory -Force -Path $driveToday, $driveBettingReports, $driveNcaa, $driveNfl, $driveNba, $driveMlbWnba | Out-Null

# -------------------------------------------------------------------------
# 1. NCAA PIPELINE SYNC
# -------------------------------------------------------------------------
Write-Host "`n[1/4] Syncing NCAA (CFB) Pipeline..." -ForegroundColor Yellow
$cfbRefreshScript = Join-Path $PSScriptRoot "refresh_cfb_pack.ps1"
if (Test-Path -LiteralPath $cfbRefreshScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cfbRefreshScript -SkipPipeline -Date $Date
    if ($LASTEXITCODE -ne 0) { throw "Sport sync stage failed with exit code $LASTEXITCODE" }
} else {
    $ncaaScript = "C:\Users\dasil\Dev\GitHub\cfb-analytics\scripts\sync_to_drive.ps1"
    if (Test-Path -LiteralPath $ncaaScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ncaaScript -SlateDate $Date
        if ($LASTEXITCODE -ne 0) { throw "Sport sync stage failed with exit code $LASTEXITCODE" }
    } else {
        throw "NCAA sync script not found at $ncaaScript"
    }
}

# -------------------------------------------------------------------------
# 2. NBA PIPELINE SYNC
# -------------------------------------------------------------------------
Write-Host "`n[2/4] Syncing NBA Pipeline..." -ForegroundColor Yellow
$nbaRefreshScript = Join-Path $PSScriptRoot "refresh_nba_pack.ps1"
if (Test-Path -LiteralPath $nbaRefreshScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nbaRefreshScript -SkipPipeline -Date $Date
    if ($LASTEXITCODE -ne 0) { throw "Sport sync stage failed with exit code $LASTEXITCODE" }
} else {
    $nbaScript = "C:\Users\dasil\Dev\GitHub\nba-props-pipeline\scripts\sync_nba_to_drive.ps1"
    if (Test-Path -LiteralPath $nbaScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nbaScript
        if ($LASTEXITCODE -ne 0) { throw "Sport sync stage failed with exit code $LASTEXITCODE" }
    } else {
        throw "NBA sync script not found at $nbaScript"
    }
}

# -------------------------------------------------------------------------
# 3. NFL PIPELINE SYNC
# -------------------------------------------------------------------------
Write-Host "`n[3/4] Syncing NFL Pipeline..." -ForegroundColor Yellow
$nflRefreshScript = Join-Path $PSScriptRoot "refresh_nfl_pack.ps1"
if (Test-Path -LiteralPath $nflRefreshScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nflRefreshScript -SkipPipeline -Date $Date
    if ($LASTEXITCODE -ne 0) { throw "Sport sync stage failed with exit code $LASTEXITCODE" }
    # If today has no direct Sunday slate, also check tomorrow (e.g. Sunday)
    $nextDate = ([DateTime]::Parse($Date)).AddDays(1).ToString("yyyy-MM-dd")
    if (Test-Path "C:\Users\dasil\Dev\GitHub\outlier\data\NFL\normalized\nfl_best_bets_$nextDate.json") {
        Write-Host "Also syncing upcoming Sunday slate ($nextDate)..." -ForegroundColor Yellow
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nflRefreshScript -SkipPipeline -Date $nextDate
        if ($LASTEXITCODE -ne 0) { throw "Sport sync stage failed with exit code $LASTEXITCODE" }
    }
} else {
    throw "NFL refresh script not found at $nflRefreshScript"
}

# -------------------------------------------------------------------------
# 4. MLB & WNBA PIPELINE SYNC (Off-Season Guarded)
# -------------------------------------------------------------------------
Write-Host "`n[4/4] Syncing MLB & WNBA Pipeline..." -ForegroundColor Yellow
$todayPack = Join-Path $repoRoot "packs\$Date"
if (Test-Path -LiteralPath $todayPack) {
    # Direct MLB/WNBA sync is retired: run_daily_pipeline.ps1 owns current-run attestation
    # and publication for those sports. Skip rather than fail after NCAA/NBA/NFL succeeded.
    Write-Host "MLB/WNBA pack found for $Date; direct sync retired. Publish it via run_daily_pipeline.ps1." -ForegroundColor DarkGray
} else {
    Write-Host "No active MLB/WNBA pack found for $Date (off-season). Skipping." -ForegroundColor DarkGray
}

# -------------------------------------------------------------------------
# 5. MASTER SLATE SUMMARY GENERATION
# -------------------------------------------------------------------------
Write-Host "`nGenerating Universal SLATE_SUMMARY.md on Google Drive..." -ForegroundColor Yellow
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# Daily Multi-Sport Betting Intelligence Hub")
[void]$sb.AppendLine("**Last Synced:** $timestamp ET  |  **Slate Date:** $Date")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("All sports pipelines are continuously connected and synchronized to your Google Drive (G:\My Drive). Access this directory or specific subfolders from your laptop, mobile phone, or tablet anywhere.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## NCAA Football (CFB)")
[void]$sb.AppendLine("- Master Simulation Digest: ncaa_master_v2_digest_latest.md (and $Date-master-v2-digest.md)")
[void]$sb.AppendLine("- Over-Board Picks: $Date-over-board.txt")
[void]$sb.AppendLine("- Mispriced Lines: $Date-mispriced.txt")
[void]$sb.AppendLine("- Board Reasoning: $Date-board-reasoning.txt")
[void]$sb.AppendLine("- Extra Pack Data: extra_packs/ncaafb_only.csv")
[void]$sb.AppendLine("- Archive Folder: BETTING REPORTS/NCAA/")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## NFL Football")
[void]$sb.AppendLine("- Weekly Best Bets Card: nfl_best_bets.md")
[void]$sb.AppendLine("- Matchup Advantage Rankings: Top_10_Overall_Matchups.md, Matchup_Boost_Rankings.csv")
[void]$sb.AppendLine("- Positional Matchups: Top_5_QB_Matchups.md, Top_5_RB_Matchups.md, Top_5_WR_Matchups.md, Top_5_TE_Matchups.md")
[void]$sb.AppendLine("- Alternate Floors: Alt_Floors_latest.md, nfl_alt_floors.csv")
[void]$sb.AppendLine("- Extra Pack Data: extra_packs/nfl_only.csv")
[void]$sb.AppendLine("- Archive Folder: BETTING REPORTS/NFL/")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## NBA Basketball")
[void]$sb.AppendLine("- Prop Edges: nba_prop_edges.csv")
[void]$sb.AppendLine("- Graded Plays: nba_graded_plays.csv")
[void]$sb.AppendLine("- Projections & Analysis: ALL_TOP_PROPS.txt, GAME_TOTALS_PROJECTION.txt")
[void]$sb.AppendLine("- Outlier Shadow Candidates: nba_shadow_candidates.csv")
[void]$sb.AppendLine("- Extra Pack Data: extra_packs/nba_only.csv")
[void]$sb.AppendLine("- Live Data Watcher: Sports_Analytics/01_Active/NBA_Active/")
[void]$sb.AppendLine("- Archive Folder: BETTING REPORTS/NBA/")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## MLB & WNBA")
[void]$sb.AppendLine("- Playable Props Slate Report: playable_props_$Date.md (and playable_props.md)")
[void]$sb.AppendLine("- Daily Candidate Board: candidates.csv")
[void]$sb.AppendLine("- Game Totals & Team Totals: game_totals.csv, team_totals.csv, alt_team_totals.csv")
[void]$sb.AppendLine("- Alternate Pitcher Props: alt_player_props.csv, alt_player_props_parlays.csv")
[void]$sb.AppendLine("- Extra Pack Data: extra_packs/mlb_only.csv, extra_packs/wnba_only.csv")
[void]$sb.AppendLine("- Archive Folder: BETTING REPORTS/MLB_WNBA/")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("*Automated multi-pipeline synchronization active across all scheduled intraday triggers.*")

$summaryContent = $sb.ToString()
Set-Content -LiteralPath (Join-Path $driveToday "SLATE_SUMMARY.md") -Value $summaryContent -Encoding UTF8
Set-Content -LiteralPath (Join-Path $driveBettingReports "SLATE_SUMMARY.md") -Value $summaryContent -Encoding UTF8

Write-Host "`n=================================================================" -ForegroundColor Green
Write-Host " ALL-SPORTS GOOGLE DRIVE SYNC COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host " Available on Google Drive at: G:\My Drive\today and G:\My Drive\BETTING REPORTS" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
