<#
.SYNOPSIS
  Sync the outlier scrapers sources to the canonical shared state.
  Run this at the START of EVERY agent session for this project.

  Goal: no matter which AI env / worktree / clone you are in, you end up with the same code.

.USAGE
  From any dir:
    & C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners\sync-outlier.ps1
    # or after copying to a global location

  Or cd to the ai-runners (this dir) or central and run .\sync-outlier.ps1
#>

[CmdletBinding()]
param(
  [string]$Canonical = 'C:\Users\dasil\OneDrive\Documents\outlier',
  [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

function Write-Info($msg) { Write-Host "[sync] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[sync] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[sync] $msg" -ForegroundColor Red }

$here = (Get-Location).Path
Write-Info "Current location: $here"
Write-Info "Canonical: $Canonical"

if (-not (Test-Path $Canonical)) {
  Write-Err "Canonical not found at $Canonical. Fix the path or create the clone."
  exit 1
}

# 1. Make sure we can talk to the canonical
$remoteName = 'canonical'
try {
  git remote get-url $remoteName 2>$null | Out-Null
} catch {
  Write-Info "Adding remote '$remoteName' pointing to canonical..."
  git remote add $remoteName $Canonical 2>$null | Out-Null
}

# 2. Fetch everything the canonical knows
Write-Info "Fetching from canonical..."
git fetch $remoteName --prune --tags 2>&1 | Out-Null

# 3. Get the latest master from canonical
$target = git -C $Canonical rev-parse --verify master 2>$null
if (-not $target) {
  Write-Err "Canonical has no master?"
  exit 1
}

if ($VerifyOnly) {
  $current = git rev-parse HEAD
  Write-Info "Current HEAD: $current"
  Write-Info "Canonical master: $target"
  if ($current -eq $target) {
    Write-Info "Already at canonical master."
  } else {
    Write-Warn "Diverged or behind."
  }
  # Also check key file
  $pack = (Get-Item outlier_scrapers\pack.py -ErrorAction SilentlyContinue).Length
  Write-Info "pack.py size here: $pack"
  exit 0
}

# 4. Move to canonical master (or merge if you have local work)
Write-Info "Checking out canonical master..."
git checkout -B master $target 2>&1 | Out-Null

# 5. If we are in a stray full clone (own .git dir), also overlay any missing tree changes
#    (worktrees linked to the canonical already have the objects after fetch)
if ((Test-Path .git) -and (Test-Path .git -PathType Container)) {
  Write-Info "Detected full clone (own .git dir). Ensuring tree matches canonical for key files..."
  git checkout $target -- outlier_scrapers/pack.py outlier_scrapers/daily_job.py tests/test_daily_job.py prompts/C.md 2>&1 | Out-Null
}

# 6. Verify critical artifacts
$packSize = (Get-Item outlier_scrapers\pack.py).Length
$dailySize = (Get-Item outlier_scrapers\daily_job.py).Length
Write-Info "pack.py size: $packSize (expect ~25-26k after fixes)"
Write-Info "daily_job.py size: $dailySize (expect 8916)"

if ($packSize -lt 25000) {
  Write-Warn "pack.py looks small. You may need to pull more objects or the central itself is not up to date."
}

Write-Info "Done. You should now see the same sources as other agents that followed this script."
Write-Info "If you made changes here, commit them, then from the canonical do: git fetch <this-path-or-remote> ; git merge ... or cherry-pick."

# Optional: show the commit
git log --oneline -1 | Write-Info

exit 0
