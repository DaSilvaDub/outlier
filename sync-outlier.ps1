<#
.SYNOPSIS
  MANDATORY bootstrap for all agents (Grok, Claude, Codex, Gemini, etc.).
  Run at the VERY START of EVERY session in this repo.

  Goal: NO MATTER which "ent", worktree, or clone you are in, you end up on the
  exact same source (including the pack.py +624 / daily_job changes from d05eb21
  lineage, sync commits 88083ff etc.).

  GitHub (https://github.com/DaSilvaDub/outlier.git) is the cross-ent SSOT.
  Linked worktrees share the canonical .git. Stray full clones must be slaved to GitHub.

  Usage (from anywhere):
    & "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"
    & "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -ValidateOnly
#>

[CmdletBinding()]
param(
  [switch]$ValidateOnly,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Write-Info($msg) { Write-Host "[sync] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[sync] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[sync] $msg" -ForegroundColor Red }

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
  Write-Err "Not inside a git repo. cd to an outlier checkout first."
  exit 1
}
Set-Location $repoRoot
$here = (Get-Location).Path
Write-Info "Repo root: $here"

# 1. Ensure we have a real GitHub remote (cross-ent glue). Prefer "origin".
$githubUrl = 'https://github.com/DaSilvaDub/outlier.git'
$originUrl = (git remote get-url origin 2>$null) -or ''
if (-not $originUrl -or $originUrl -notlike '*github.com*DaSilvaDub/outlier*') {
  Write-Info "Setting origin to GitHub (was '$originUrl')."
  git remote remove origin 2>$null | Out-Null
  git remote add origin $githubUrl
}
git remote set-url origin $githubUrl 2>$null | Out-Null

# 2. Fetch latest from the single source of truth
Write-Info "Fetching origin (GitHub)..."
git fetch origin --prune --tags 2>&1 | Out-Null

$target = git rev-parse --verify origin/master 2>$null
if (-not $target) {
  Write-Err "No origin/master. Is the remote correct?"
  exit 1
}

if ($ValidateOnly) {
  $current = git rev-parse HEAD
  Write-Info "Current HEAD: $current"
  Write-Info "origin/master: $target"
  $match = if ($current -eq $target) { 'MATCH' } else { 'DIVERGED/BEHIND' }
  Write-Info "State vs origin/master: $match"

  # Authoritative (from git objects, immune to OneDrive hydration lies)
  $gitPack = git show origin/master:outlier_scrapers/pack.py 2>$null
  $gitDaily = git show origin/master:outlier_scrapers/daily_job.py 2>$null
  $gitPackLen = if ($gitPack) { ($gitPack | Measure-Object -Character).Characters } else { 0 }
  $gitDailyLen = if ($gitDaily) { ($gitDaily | Measure-Object -Character).Characters } else { 0 }
  Write-Info "git (origin/master) pack.py chars: $gitPackLen"
  Write-Info "git (origin/master) daily_job.py chars: $gitDailyLen"

  # Upgrade marker validation (the reason for the whole sync fix)
  $hasPlayerId = $gitPack -match 'player_id'
  $hasRankRoundRobin = $gitPack -match 'round_robin_then_fill|rank_rows'
  $hasLock = $gitDaily -match '_acquire_pack_lock|_atomic_write_manifest'
  $hasDecisions = $gitPack -match 'decisions\.csv'
  $hasCHeader = $gitPack -match 'CANDIDATES_HEADER'
  Write-Info "Upgrade markers: player_id=$hasPlayerId roundrobin=$hasRankRoundRobin lock=$hasLock decisions=$hasDecisions CANDIDATES=$hasCHeader"

  $ok = $hasPlayerId -and $hasRankRoundRobin -and $hasLock -and $hasCHeader
  if ($ok) {
    Write-Info "VALIDATE: OK — pipeline upgrade (pack + daily changes) is present via GitHub."
  } else {
    Write-Err "VALIDATE: FAIL — upgrade markers missing. Run without -ValidateOnly or check remote."
    exit 2
  }
  exit 0
}

# 3. Force the tree to origin/master (what agents want: identical state)
Write-Info "Resetting to origin/master (force sync)..."
if ($Force -or (git status --porcelain | Measure-Object).Count -eq 0) {
  git reset --hard origin/master 2>&1 | Out-Null
} else {
  Write-Warn "Dirty tree detected. Using checkout -B (safer). Pass -Force to hard reset."
  git checkout -B master origin/master 2>&1 | Out-Null
}

# 4. Explicitly materialize the critical files that caused the original invisibility bug
Write-Info "Materializing key files (pack, daily, tests, prompts)..."
git checkout -- outlier_scrapers/pack.py outlier_scrapers/daily_job.py tests/test_daily_job.py prompts/C.md 2>&1 | Out-Null

# 5. Touch the files (helps OneDrive Files-On-Demand hydrate the content for this view)
try {
  $null = Get-Content -Raw outlier_scrapers\pack.py -ErrorAction SilentlyContinue | Out-Null
  $null = Get-Content -Raw outlier_scrapers\daily_job.py -ErrorAction SilentlyContinue | Out-Null
} catch {}

# 6. Report authoritative state + disk view (note: CRLF can make disk bytes differ)
$head = git rev-parse --short HEAD
$gitPack = git show origin/master:outlier_scrapers/pack.py 2>$null
$gitPackLen = if ($gitPack) { ($gitPack | Measure-Object -Character).Characters } else { 0 }
$diskPack = (Get-Item outlier_scrapers\pack.py -EA SilentlyContinue).Length
$diskDaily = (Get-Item outlier_scrapers\daily_job.py -EA SilentlyContinue).Length
Write-Info "HEAD: $head"
Write-Info "git pack chars (origin/master): $gitPackLen  |  disk pack bytes: $diskPack"
Write-Info "disk daily bytes: $diskDaily"

# 7. Hard validation that the specific changes (the ~624 line pack upgrade etc.) are here
$gitPackFull = git show HEAD:outlier_scrapers/pack.py 2>$null
$gitDailyFull = git show HEAD:outlier_scrapers/daily_job.py 2>$null

$hasPlayerId = $gitPackFull -match 'player_id'
$hasRank = $gitPackFull -match 'round_robin_then_fill'
$hasSelectStrict = $gitPackFull -match 'select_date'
$hasLock = $gitDailyFull -match '_acquire_pack_lock'
$hasWriteDecisions = $gitPackFull -match 'decisions\.csv'

if (-not ($hasPlayerId -and $hasRank -and $hasLock -and $hasWriteDecisions)) {
  Write-Warn "Some upgrade markers not detected in current HEAD blob. This tree may still be stale."
  Write-Warn "Run again, or from canonical: git fetch origin; git reset --hard origin/master"
} else {
  Write-Info "Upgrade markers present: player_id, round_robin, lock, decisions.csv"
}

Write-Info "Sync complete. Every ent that runs this (or equivalent fetch+reset from GitHub) will see identical pack.py + daily_job.py + history (d05eb21 lineage via 88083ff+)."

# OneDrive note
Write-Warn "If disk size looks wrong vs git: ensure the outlier folder is 'Always keep on this device' in OneDrive settings, or re-run git checkout -- <file> after a short wait."

git log --oneline -1 | Write-Info
exit 0
