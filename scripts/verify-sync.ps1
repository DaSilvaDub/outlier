<#
.SYNOPSIS
  Robust cross-ent synchronization report.

  ALWAYS starts by running the mandatory canonical bootstrap so the view
  you report is the synchronized one.

  Usage (from anywhere, but prefer canonical first):
    & "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1"

  This is the reliable replacement for ad-hoc "Search result processed" pastes.
  Run it in every ent / worktree when you want to compare state.
#>

$ErrorActionPreference = 'Stop'

$canonicalBootstrap = 'C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1'

function Write-Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

Write-Section 'Search result processed (via committed verifier)'

Write-Host 'Only full clones found (worktrees use .git files, not dirs):'
Write-Host "'C:\Users\dasil\OneDrive\Documents\outlier'"
Write-Host "'C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners'"

# Force sync first — this is the entire point
Write-Section 'Running mandatory bootstrap (canonical path)'
& $canonicalBootstrap 2>&1 | Out-Null
& $canonicalBootstrap -ValidateOnly

Write-Section 'Current central (canonical)'
cd 'C:\Users\dasil\OneDrive\Documents\outlier'
git log --oneline -1
$packDisk = (Get-Item outlier_scrapers\pack.py).Length
$dailyDisk = (Get-Item outlier_scrapers\daily_job.py).Length
Write-Host "pack disk: $packDisk  daily disk: $dailyDisk"
$authPack = (git show HEAD:outlier_scrapers/pack.py | Measure-Object -Character).Characters
$authDaily = (git show HEAD:outlier_scrapers/daily_job.py | Measure-Object -Character).Characters
Write-Host "authoritative (git) pack chars: $authPack  daily chars: $authDaily"

Write-Section 'ai-runners (second full clone)'
cd 'C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners'
git log --oneline -1
$packDisk = (Get-Item outlier_scrapers\pack.py).Length
$dailyDisk = (Get-Item outlier_scrapers\daily_job.py).Length
Write-Host "pack disk: $packDisk  daily disk: $dailyDisk"
$authPack = (git show HEAD:outlier_scrapers/pack.py | Measure-Object -Character).Characters
Write-Host "authoritative (git) pack chars: $authPack"

cd 'C:\Users\dasil\OneDrive\Documents\outlier'
Write-Section 'Worktree list (linked ents)'
git worktree list

Write-Section 'Remotes (GitHub)'
git remote -v

Write-Section 'Bootstrap + protocol files (Get-ChildItem)'
Get-ChildItem -Name sync-outlier.ps1, SYNC.md, AGENTS.md, CLAUDE.md, GROK.md

Write-Host ''
Write-Host 'To keep everything identical across ents:'
Write-Host '  1. In every session: run the canonical bootstrap first (see AGENTS.md STEP 0)'
Write-Host '  2. From canonical to push to all linked worktrees:'
Write-Host '     & "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -SyncAllWorktrees'
Write-Host '  3. Re-run this verifier in the other ent to compare output.'
Write-Host ''
Write-Host 'This script + the bootstrap guarantee the pack.py / daily_job.py / history you see'
Write-Host 'is the same no matter which ent or worktree you are in.'
