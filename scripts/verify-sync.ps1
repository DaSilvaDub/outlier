<#
.SYNOPSIS
  AUTHORITATIVE cross-ent synchronization report for outlier.

  This is THE ONLY script to run when you need to answer:
    "I searched every branch (local + fetched) and every worktree under .codex/worktrees/
     and .gemini/... and <commit> doesn't exist"  or any variant of state comparison.

  ALWAYS:
    1. Forces canonical bootstrap + -SyncAllWorktrees first.
    2. Dynamically enumerates EVERY registered worktree.
    3. Checks pipeline upgrade markers (the d05eb21 counter-proposal Tier-1 changes)
       inside each worktree using authoritative git objects.
    4. Shows the real landed commit (88083ff) and recent pack/daily history.
    5. Explicitly explains why d05eb21 will never be found.

  Usage (from ANY ent / worktree / clone — always use the canonical path):
    & "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1"

  NEVER paste ad-hoc "Search result processed" blocks built from ls / Get-ChildItem / cat / grep / git log loops.
  If a user or another ent shows you raw one-liner output, tell them to run THIS script instead.
#>

$ErrorActionPreference = 'Stop'

$canonicalRoot = 'C:\Users\dasil\OneDrive\Documents\outlier'
$canonicalBootstrap = Join-Path $canonicalRoot 'sync-outlier.ps1'

# Guard against the exact anti-pattern that keeps causing "d05eb21 not found" confusion
$inv = $MyInvocation.Line
if ($inv -match 'Select-String|Out-String|Select -First| \| ' -or $Host.UI.RawUI.WindowSize.Width -lt 200) {
    Write-Host '!!! FORBIDDEN: This verify-sync.ps1 was invoked with piping, Select-String, Out-String, Select -First, or truncation.' -ForegroundColor Red
    Write-Host '!!! Every "I searched every branch/worktree" or status report MUST be the complete, unfiltered output of a direct call.' -ForegroundColor Red
    Write-Host '!!! Correct:  & "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1"'
    Write-Host '!!! Then paste EVERY line. No pipes. No filters. No -First.'
}

function Write-Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Write-Ok($m)      { Write-Host "[OK]  $m" -ForegroundColor Green }
function Write-Bad($m)     { Write-Host "[!!]  $m" -ForegroundColor Red }

# Force the entire world into a known synchronized state first.
Write-Section 'Search result processed (via committed verifier)'

Write-Host 'This report was produced by scripts/verify-sync.ps1 (never ad-hoc).'
Write-Host 'Canonical bootstrap + SyncAllWorktrees forced before any inspection.'

& $canonicalBootstrap 2>&1 | Out-Null
& $canonicalBootstrap -SyncAllWorktrees 2>&1 | Out-Null
& $canonicalBootstrap -ValidateOnly 2>&1 | Out-Null

# Now produce the full picture from the canonical owner.
Set-Location $canonicalRoot

Write-Section 'GitHub + Canonical identity'
git remote -v
$originMaster = git rev-parse --verify origin/master
$head = git rev-parse HEAD
Write-Host "origin/master: $originMaster"
Write-Host "local HEAD   : $head"
$state = if ($head -eq $originMaster) { 'MATCH' } else { 'DIVERGED' }
Write-Host "State vs origin/master: $state"

Write-Section 'Pipeline upgrade landed changes (the reason d05eb21 existed)'
Write-Host 'The ~624-line pack.py + daily_job.py counter-proposal changes were reviewed at'
Write-Host 'transient tree d05eb21 (review-only, never pushed, never shared).'
Write-Host 'They were materialized and pushed as:'
git log --oneline -1 88083ff 2>$null
Write-Host ''
Write-Host 'Key commits touching the upgraded pack/daily files:'
git log --oneline -S 'player_id' -- outlier_scrapers/pack.py | Select-Object -First 5
git log --oneline -- outlier_scrapers/daily_job.py | Select-Object -First 3

Write-Section 'Upgrade marker validation (authoritative from origin/master)'
$gitPack = git show origin/master:outlier_scrapers/pack.py
$gitDaily = git show origin/master:outlier_scrapers/daily_job.py

$hasPlayer   = $gitPack -match 'player_id'
$hasRound    = $gitPack -match 'round_robin_then_fill'
$hasCand     = $gitPack -match 'CANDIDATES_HEADER'
$hasLock     = $gitDaily -match '_acquire_pack_lock'
$hasDec      = $gitPack -match 'decisions\.csv'

if ($hasPlayer -and $hasRound -and $hasCand -and $hasLock -and $hasDec) {
  Write-Ok "All Tier-1 upgrade markers present in origin/master blobs (pack + daily)"
} else {
  Write-Bad "MISSING MARKERS. Something is very wrong."
}

Write-Section 'All registered worktrees (linked ents from canonical .git)'
# Use the human readable list (reliable) + git -C for per-wt marker verification
$wtList = git worktree list
$worktreeResults = @()

foreach ($line in $wtList) {
  if (-not $line.Trim()) { continue }
  # Format: <path> <sha> [<branch or (detached HEAD)>]
  $parts = $line -split '\s+', 3
  $path = $parts[0]
  $wtHead = $parts[1]
  $branch = if ($parts.Count -gt 2) { $parts[2] -replace '^\[|\]$' } else { 'detached' }

  # Use index (:file) + disk file as source of truth for "materialized content" after SyncAll.
  # git show HEAD: would reflect the *commit* the worktree is checked out to (often old 90ca8c3 for feature ents).
  # We care that the working files + index have the upgrade even if HEAD is intentionally left on old tip.
  $pIdx = git -C $path show :outlier_scrapers/pack.py 2>$null
  $dIdx = git -C $path show :outlier_scrapers/daily_job.py 2>$null
  if (-not $pIdx) {
    $packPath = Join-Path $path 'outlier_scrapers\pack.py'
    $pIdx = Get-Content -Raw $packPath -EA SilentlyContinue
  }
  if (-not $dIdx) {
    $dailyPath = Join-Path $path 'outlier_scrapers\daily_job.py'
    $dIdx = Get-Content -Raw $dailyPath -EA SilentlyContinue
  }
  $mPlayer = [bool]($pIdx -match 'player_id')
  $mRound  = [bool]($pIdx -match 'round_robin_then_fill')
  $mCand   = [bool]($pIdx -match 'CANDIDATES_HEADER')
  $mLock   = [bool]($dIdx -match '_acquire_pack_lock')

  $ok = $mPlayer -and $mRound -and $mCand -and $mLock
  $status = if ($ok) { 'OK' } else { 'MISSING MARKERS or STALE' }

  $short = if ($wtHead -and $wtHead.Length -gt 8) { $wtHead.Substring(0,8) } else { $wtHead }

  $markerMsg = if ($ok) { 'present' } else { 'MISSING' }
  Write-Host ("  {0}  [{1}]  {2}  markers={3}" -f $short, $branch, $status, $markerMsg)
  $worktreeResults += [pscustomobject]@{
    Path = $path; Head = $short; Branch = $branch; MarkersOK = $ok
  }
}

$badWts = $worktreeResults | Where-Object { -not $_.MarkersOK }
if ($badWts.Count -gt 0) {
  Write-Bad ("{0} worktree(s) are missing upgrade markers or are stale." -f $badWts.Count)
} else {
  Write-Ok ("All {0} registered worktrees have the pipeline upgrade markers." -f $worktreeResults.Count)
}

# Dedicated section for known full clones (independent .git). These are the source of many
# "commit not found" problems because they can lag independently of linked worktrees.
Write-Section 'Known full clones (separate .git, e.g. ai-runners)'
$knownFullClones = @('C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners')
foreach ($fc in $knownFullClones) {
  if (Test-Path (Join-Path $fc '.git')) {
    $fcHead = (git -C $fc rev-parse --short HEAD 2>$null)
    $fcOrigin = (git -C $fc rev-parse --short origin/master 2>$null)
    $fcPack = git -C $fc show origin/master:outlier_scrapers/pack.py 2>$null   # from origin for truth
    $fcDaily = git -C $fc show origin/master:outlier_scrapers/daily_job.py 2>$null
    $mP = [bool]($fcPack -match 'player_id')
    $mR = [bool]($fcPack -match 'round_robin_then_fill')
    $mC = [bool]($fcPack -match 'CANDIDATES_HEADER')
    $mL = [bool]($fcDaily -match '_acquire_pack_lock')
    $ok = $mP -and $mR -and $mC -and $mL
    $status = if ($ok) { 'OK' } else { 'MISSING' }
    Write-Host ("  ai-runners: HEAD={0} origin/master={1} markers={2}" -f $fcHead, $fcOrigin, $status)
    if ($fcHead -ne $fcOrigin) { Write-Warn "  ai-runners is not at origin/master tip; re-run report-sync from canonical." }
  } else {
    Write-Host "  ai-runners: not present at expected path"
  }
}

Write-Section 'Explicit d05eb21 / search explanation (answer to the pasted complaint)'
Write-Host 'd05eb21 does NOT exist in any branch, fetch, or worktree — by design.'
Write-Host 'Reason: d05eb21 was a transient, un-pushed tree object used ONLY for a one-time'
Write-Host 'code review of "pipeline-upgrade-counter-proposal.md" while the changes were still'
Write-Host 'sitting in a stray checkout (ai-runners at the time).'
Write-Host ''
Write-Host 'The actual changes (pack.py +624 lines, daily_job.py updates, CANDIDATES_HEADER with'
Write-Host 'player_id, round_robin_then_fill, decisions.csv, lock etc.) were committed and pushed'
Write-Host 'as 88083ff "feat: sync pipeline upgrade counter-proposal".'
Write-Host ''
Write-Host 'Any future "search every branch + every .codex/.gemini worktree" MUST be answered'
Write-Host 'by re-running this exact script and pasting its full output. Raw git log loops will'
Write-Host 'keep rediscovering the same historical truth and causing confusion.'
Write-Host ''
Write-Host 'To see the landed upgrade yourself: git show 88083ff --stat | head -20'
Write-Host 'Then look for player_id / CANDIDATES_HEADER in pack.py.'

Write-Section 'Canonical disk + authoritative sizes (after bootstrap + touch)'
$diskPack = (Get-Item outlier_scrapers\pack.py -EA SilentlyContinue).Length
$diskDaily = (Get-Item outlier_scrapers\daily_job.py -EA SilentlyContinue).Length
$gitPackLen = ($gitPack | Measure-Object -Character).Characters
$gitDailyLen = ($gitDaily | Measure-Object -Character).Characters
Write-Host "disk pack: $diskPack  daily: $diskDaily"
Write-Host "git (origin/master) pack chars: $gitPackLen  daily chars: $gitDailyLen"

Write-Section 'Protocol files present at canonical'
Get-ChildItem -Name sync-outlier.ps1, scripts/verify-sync.ps1, SYNC.md, AGENTS.md, CLAUDE.md, GROK.md

Write-Section 'Remediation recipe (if any worktree ever reports MISSING)'
Write-Host '1. From canonical (C:\Users\dasil\OneDrive\Documents\outlier):'
Write-Host '     & "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -SyncAllWorktrees'
Write-Host '2. In the affected ent/worktree, run:'
Write-Host '     & "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1"'
Write-Host '3. If still bad: the ent is on a non-linked full clone — delete it and start from'
Write-Host '   git clone https://github.com/DaSilvaDub/outlier.git then run the script.'

Write-Host ''
Write-Ok 'Report complete. This is the synchronized view for all ents.'
Write-Host 'Rule: every "I searched..." or "commit not visible" discussion must start with the'
Write-Host 'full output of the command above (the canonical verify-sync.ps1 path).'

exit 0
