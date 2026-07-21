<#
.SYNOPSIS
  MANDATORY bootstrap for all agents (Grok, Claude, Codex, Gemini/antigravity, etc.).
  Run at the VERY START of EVERY session in this repo. NO EXCEPTIONS.

  Goal: NO MATTER which "ent", worktree, clone, or harness you are in, you ALWAYS
  see the exact same source — pack.py + daily_job.py (the d05eb21 counter-proposal
  Tier-1 changes materialized at 88083ff and later), tests, prompts/C.md, and all history.

  GitHub is the cross-ent SSOT. Canonical owns the .git. Linked worktrees share objects.
  Use this to make "commit not found anywhere" or "closest commit doesn't touch pack.py"
  impossible.

  Usage:
    & "C:\Users\dasil\Dev\GitHub\outlier\sync-outlier.ps1"
    & "C:\Users\dasil\Dev\GitHub\outlier\sync-outlier.ps1" -ValidateOnly
    & "C:\Users\dasil\Dev\GitHub\outlier\sync-outlier.ps1" -SyncAllWorktrees   # from canonical: align every registered worktree
#>

[CmdletBinding()]
param(
  [switch]$ValidateOnly,
  [switch]$Force,
  [switch]$SyncAllWorktrees
)

$ErrorActionPreference = 'Continue'

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

$aiRunnersPath = 'C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners'
if ($here -eq $aiRunnersPath -or $here -like '*outlier-worktrees*ai-runners*') {
  Write-Info "NOTE: Running inside the ai-runners full clone. This is a secondary tracking clone. Canonical (C:\Users\dasil\Dev\GitHub\outlier) + GitHub remain SSOT. Always prefer invoking report-sync via the canonical path."
}

# Known sibling full clones (separate .git directories, not linked worktrees).
# These must be force-reset to origin/master during -SyncAllWorktrees from canonical
# so that ai-runners (and future full clones) never drift from GitHub SSOT.
$knownFullClones = @(
  $aiRunnersPath
)

# 1. Ensure we have a real GitHub remote (cross-ent glue). Prefer "origin".
$githubUrl = 'https://github.com/DaSilvaDub/outlier.git'
# NOTE: -or would coerce the URL to a boolean ('True'), which made this branch
# fire on EVERY run: remote remove deleted all origin/* refs each pass, and any
# transient fetch failure then surfaced as "No origin/master" (2026-07-15 incident).
$originUrl = git remote get-url origin 2>$null
if (-not $originUrl) { $originUrl = '' }
if (-not $originUrl -or $originUrl -notlike '*github.com*DaSilvaDub/outlier*') {
  Write-Info "Setting origin to GitHub (was '$originUrl')."
  git remote remove origin 2>$null | Out-Null
  git remote add origin $githubUrl
}
git remote set-url origin $githubUrl 2>$null | Out-Null

# 2. Fetch latest from the single source of truth
Write-Info "Fetching origin (GitHub)..."
git fetch origin --prune --tags 2>$null | Out-Null

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
  git reset --hard origin/master 2>$null
} else {
  Write-Warn "Dirty tree detected. Using checkout -B (safer). Pass -Force to hard reset."
  git checkout -B master origin/master 2>$null
}

# 4. Explicitly materialize the critical files that caused the original invisibility bug
#    Also materialize the sync tooling itself so every tree gets the latest bootstrap.
#    NOTE: the WHOLE outlier_scrapers package is materialized (not just pack.py/daily_job.py).
#    Partial materialization caused the 2026-07-17 ImportError: master pack.py imported
#    compute_historical_edge from sizing.py, but sizing.py was left at the branch version.
#    pack.py/daily_job.py transitively import ~29 of the 33 package modules, so the package
#    directory is the correct sync boundary; a curated file list rots on the next new import.
Write-Info "Materializing key files (outlier_scrapers package, tests, prompts, sync tooling)..."
git checkout -- outlier_scrapers tests/test_daily_job.py prompts/C.md sync-outlier.ps1 scripts/verify-sync.ps1 report-sync.ps1 SYNC.md docs/ENT-SYNC-GLOBAL-PROMPT.md 2>$null

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

# 8. Optional: from canonical, force-align every linked worktree (materialize critical files from origin/master).
#    Safe for feature branches (e.g. codex/* , gemini fix-*): only updates the key files via checkout from origin/master,
#    does not move HEAD or change branch. Master/detached worktrees also get the files synced.
#    This is the practical hammer against "d05eb21 only visible in one tree".
if ($SyncAllWorktrees) {
  Write-Info "=== SyncAllWorktrees: materializing core files (outlier_scrapers package, sync tooling) from origin/master into all registered worktrees ==="
  $porcelain = git worktree list --porcelain 2>$null
  $currentWt = $here
  $aligned = 0
  $lines = $porcelain -split "`n"
  $wtPath = $null
  foreach ($line in $lines) {
    if ($line -match '^worktree (.+)') {
      $wtPath = $matches[1]
    } elseif ($line -match '^HEAD ' -and $wtPath) {
      $normWt = ($wtPath -replace '\\','/').TrimEnd('/')
      $normHere = ($currentWt -replace '\\','/').TrimEnd('/')
      if ($normWt -ne $normHere) {
        Write-Info "Aligning worktree: $wtPath"
        git -C $wtPath fetch origin --prune --tags 2>$null
        # Non-destructive for the files we care about (the ones that were invisible before).
        # Uses the tree at origin/master so even feature-branch worktrees see the blessed pack/daily/sync versions.
        # Whole outlier_scrapers package: syncing pack.py without its imports (sizing.py etc.)
        # caused the 2026-07-17 "cannot import name 'compute_historical_edge'" ImportError.
        git -C $wtPath checkout origin/master -- outlier_scrapers tests/test_daily_job.py prompts/C.md sync-outlier.ps1 scripts/verify-sync.ps1 report-sync.ps1 SYNC.md docs/ENT-SYNC-GLOBAL-PROMPT.md 2>$null
        try {
          $null = Get-Content -Raw (Join-Path $wtPath 'outlier_scrapers\pack.py') -EA SilentlyContinue | Out-Null
        } catch {}
        $newHead = git -C $wtPath rev-parse --short HEAD 2>$null
        Write-Info "  -> files updated from origin/master (HEAD remains $newHead)"
        $aligned++
      }
      $wtPath = $null
    }
  }
  Write-Info "SyncAllWorktrees complete. Materialized into $aligned additional worktree(s)."
  Write-Info "Note: feature worktrees keep their branch; run bootstrap inside them for any local reset needs."

  # Align known full clones (ai-runners etc.). These have independent .git so must be
  # hard-reset to the SSOT. This eliminates the case where ai-runners lags and ad-hoc
  # searches inside it find "d05eb21 not present".
  foreach ($fc in $knownFullClones) {
    if (Test-Path (Join-Path $fc '.git')) {
      Write-Info "Aligning full clone: $fc"
      git -C $fc fetch origin --prune --tags 2>$null
      git -C $fc reset --hard origin/master 2>$null
      git -C $fc checkout -- outlier_scrapers tests/test_daily_job.py prompts/C.md sync-outlier.ps1 scripts/verify-sync.ps1 report-sync.ps1 SYNC.md docs/ENT-SYNC-GLOBAL-PROMPT.md 2>$null
      try {
        $null = Get-Content -Raw (Join-Path $fc 'outlier_scrapers\pack.py') -EA SilentlyContinue | Out-Null
      } catch {}
      $fcHead = git -C $fc rev-parse --short HEAD 2>$null
      Write-Info "  -> full clone reset to origin/master (HEAD now $fcHead)"
      $aligned++
    }
  }
  if ($aligned -gt 0) {
    Write-Info "(includes full clones; total actions: $aligned)"
  }
}

# OneDrive note
Write-Warn "If disk size looks wrong vs git: ensure the outlier folder is 'Always keep on this device' in OneDrive settings, or re-run git checkout -- <file> after a short wait."

git log --oneline -1 | Write-Info
Write-Info "To align EVERYTHING from canonical in future: & '...\sync-outlier.ps1' -SyncAllWorktrees"
exit 0
