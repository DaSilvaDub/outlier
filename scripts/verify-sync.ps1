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
    & "C:\Users\dasil\Dev\GitHub\outlier\scripts\verify-sync.ps1"

  NEVER paste ad-hoc "Search result processed" blocks built from ls / Get-ChildItem / cat / grep / git log loops.
  If a user or another ent shows you raw one-liner output, tell them to run THIS script instead.
#>

[CmdletBinding()]
param(
  # Repo this report is ABOUT. Defaults to canonical; override only to target another
  # checkout (or to test this script without mutating canonical).
  [string]$RepoRoot
)

$ErrorActionPreference = 'Continue'

$CanonicalRoot = 'C:\Users\dasil\Dev\GitHub\outlier'
if (-not $RepoRoot) { $RepoRoot = $CanonicalRoot }
$canonicalRoot = $RepoRoot

# Invoke the bootstrap that ships ALONGSIDE this script, not the one at the canonical
# path. These two files are a versioned pair and must not be mixed across versions:
# a new verify-sync passing -RepoRoot to an old sync-outlier is a parameter-binding
# error. Which repo is *targeted* is decided by $RepoRoot above, so this is purely
# about which code runs -- script-relative is safe here in a way it is NOT for
# resolving the repo root (a copy of this tooling also lives under OneDrive).
$canonicalBootstrap = Join-Path (Split-Path -Parent $PSScriptRoot) 'sync-outlier.ps1'
if (-not (Test-Path -LiteralPath $canonicalBootstrap)) {
    $canonicalBootstrap = Join-Path $CanonicalRoot 'sync-outlier.ps1'
}

# Per-run nonce. Replaces the old "!!! FORBIDDEN" banner, which claimed to detect
# piping but actually fired on ANY terminal narrower than 200 columns
# ($Host.UI.RawUI.WindowSize.Width -lt 200) -- so it fired on clean, unpiped
# `& "...\report-sync.ps1"` calls and was pure noise. A script fundamentally cannot
# see what its caller pipes it into; $MyInvocation.Line only ever showed the wrapper's
# own invocation line. Training every ent to ignore the one anti-forgery signal was
# strictly worse than having none.
#
# Instead: emit an unforgeable-after-the-fact token as the FINAL line. A paste that
# is truncated, filtered, or edited will be missing it or carry a stale one, and the
# token can be checked against this run's HEAD and timestamp.
$runNonce = [guid]::NewGuid().ToString('N').Substring(0, 16)
$runUtc   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

function Write-Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Write-Ok($m)      { Write-Host "[OK]  $m" -ForegroundColor Green }
function Write-Bad($m)     { Write-Host "[!!]  $m" -ForegroundColor Red }
# Was called at L162 but never defined, so a lagging full clone raised
# "The term 'Write-Warn' is not recognized" instead of the intended warning.
function Write-Warn($m)    { Write-Host "[??]  $m" -ForegroundColor Yellow }

# Verdict accumulators, rendered as a single computed trailer at the end.
$vBootstrap = 'NOT-RUN'
$vValidate  = 'NOT-RUN'
$vState     = 'UNKNOWN'
$vMarkers   = '0/5'
$vWorktrees = 'NOT-RUN'
$vClones    = 'NOT-RUN'
$vFailures  = [System.Collections.Generic.List[string]]::new()

function Write-Verdict {
    param([int]$ExitCode)
    # Defence in depth: never render OK while failures are recorded. A caller passing a
    # mis-derived 0 (see the LASTEXITCODE-unset case below) must not be able to mint a
    # green attestation.
    if ($ExitCode -eq 0 -and $vFailures.Count -gt 0) { $ExitCode = 1 }
    $status = if ($ExitCode -eq 0) { 'OK' } else { 'FAILED' }
    Write-Section 'VERDICT'
    Write-Host "REPORT STATUS: $status"
    Write-Host ("  bootstrap={0}  validate={1}  state={2}  markers={3}  worktrees={4}  fullclones={5}" -f `
        $vBootstrap, $vValidate, $vState, $vMarkers, $vWorktrees, $vClones)
    if ($vFailures.Count -gt 0) {
        Write-Host '  failures:'
        foreach ($f in $vFailures) { Write-Host "    - $f" }
    }
    if ($ExitCode -ne 0) {
        Write-Bad 'This report is NOT a valid sync attestation. Do not quote it as proof of state.'
    }
    $headNow = git -C $canonicalRoot rev-parse --short HEAD 2>$null
    if (-not $headNow) { $headNow = 'unknown' }
    Write-Host ''
    Write-Host "RUN-NONCE: $runNonce  utc=$runUtc  head=$headNow  status=$status"
    exit $ExitCode
}

# Force the entire world into a known synchronized state first.
Write-Section 'Search result processed (via committed verifier)'

Write-Host 'This report was produced by scripts/verify-sync.ps1 (never ad-hoc).'
Write-Host 'Canonical bootstrap + SyncAllWorktrees forced before any inspection.'

# The repo root is passed EXPLICITLY. Previously the bootstrap resolved it from cwd,
# so invoking this from an unreadable directory made all three calls no-op while the
# sections below -- which address canonical directly -- still produced a confident
# report. Every invocation's exit code is now checked and is fatal.
# Hashtable splatting, not array splatting: array elements bind positionally, so
# '-RepoRoot' arrived as a value rather than a parameter name and the call failed with
# "A positional parameter cannot be found that accepts argument ...". Hashtable
# splatting binds by name unambiguously.
$bootstrapSteps = @(
    @{ Name = 'bootstrap';        Args = @{ RepoRoot = $canonicalRoot } },
    @{ Name = 'SyncAllWorktrees'; Args = @{ RepoRoot = $canonicalRoot; SyncAllWorktrees = $true } },
    @{ Name = 'ValidateOnly';     Args = @{ RepoRoot = $canonicalRoot; ValidateOnly = $true } }
)

foreach ($step in $bootstrapSteps) {
    # @($step.Args) is an array SUBEXPRESSION, not splatting -- it passes the whole
    # array as one positional argument. Splatting needs @<variablename>.
    $stepArgs = $step.Args
    $global:LASTEXITCODE = $null
    try {
        & $canonicalBootstrap @stepArgs
        $code = $LASTEXITCODE
    } catch {
        Write-Bad "  bootstrap threw before it could set an exit code: $($_.Exception.Message)"
        $code = 1
    }
    # A throw (e.g. parameter binding failure) leaves LASTEXITCODE unset. Treating
    # $null/'' as 0 is exactly the empty-string truthiness bug this PR exists to kill:
    # it reported STATUS: OK for a run that never executed.
    if ($null -eq $code -or "$code" -eq '') { $code = 1 }
    $code = [int]$code
    if ($code -ne 0) {
        Write-Bad "ABORT: bootstrap step '$($step.Name)' failed with exit code $code."
        switch ($code) {
            1 { Write-Bad "  -> canonical is not a git repo git can read: $canonicalRoot" }
            2 { Write-Bad '  -> upgrade-marker validation failed; the tree is stale or wrong.' }
            3 { Write-Bad '  -> git fetch failed; origin/master cannot be trusted.' }
            default { Write-Bad '  -> see the [sync] lines above for the cause.' }
        }
        Write-Bad '  Refusing to continue to the identity/marker sections. Those address canonical'
        Write-Bad '  explicitly and would otherwise print a MATCH derived from a fetch that never ran.'
        $vBootstrap = "FAILED($($step.Name)/exit$code)"
        $vFailures.Add("bootstrap step '$($step.Name)' exited $code")
        Write-Verdict -ExitCode $code
    }
    if ($step.Name -eq 'ValidateOnly') { $vValidate = 'OK' }
}
$vBootstrap = 'OK'

# Now produce the full picture from the canonical owner.
Set-Location $canonicalRoot
python "$canonicalRoot\scripts\sync_agent_docs.py"

Write-Section 'GitHub + Canonical identity'
git remote -v
$originMaster = git rev-parse --verify origin/master 2>$null
$head = git rev-parse HEAD 2>$null
Write-Host "origin/master: $originMaster"
Write-Host "local HEAD   : $head"
# Reachable only after a bootstrap that fetched successfully (exit 3 otherwise), so
# this comparison is against a freshly-updated origin/master rather than a stale ref.
if (-not $originMaster -or -not $head) {
    $state = 'UNKNOWN'
    $vFailures.Add('could not resolve origin/master or HEAD at canonical')
} else {
    $state = if ($head -eq $originMaster) { 'MATCH' } else { 'DIVERGED' }
}
$vState = $state
Write-Host "State vs origin/master: $state"
if ($state -eq 'DIVERGED') {
    # The bootstrap above force-aligns canonical to origin/master (reset --hard, or
    # checkout -B when the tree is dirty), so reaching here means that alignment did
    # not take -- e.g. the checkout hit a conflict, or another session moved HEAD
    # mid-run. Either way the tree is not what this report would otherwise imply.
    $branchNow = git rev-parse --abbrev-ref HEAD 2>$null
    Write-Bad "  canonical HEAD ($head, branch '$branchNow') != origin/master ($originMaster)"
    Write-Bad '  The bootstrap should have aligned these. Check for a concurrent session moving HEAD.'
    $vFailures.Add("canonical DIVERGED from origin/master (branch '$branchNow')")
}

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

$markerHits = @($hasPlayer, $hasRound, $hasCand, $hasLock, $hasDec) | Where-Object { $_ }
$vMarkers = "$($markerHits.Count)/5"

if ($hasPlayer -and $hasRound -and $hasCand -and $hasLock -and $hasDec) {
  Write-Ok "All Tier-1 upgrade markers present in origin/master blobs (pack + daily)"
} else {
  # Name the offenders. "Something is very wrong" gave the reader nothing to act on.
  $absent = @()
  if (-not $hasPlayer) { $absent += 'player_id (pack.py)' }
  if (-not $hasRound)  { $absent += 'round_robin_then_fill (pack.py)' }
  if (-not $hasCand)   { $absent += 'CANDIDATES_HEADER (pack.py)' }
  if (-not $hasLock)   { $absent += '_acquire_pack_lock (daily_job.py)' }
  if (-not $hasDec)    { $absent += 'decisions.csv (pack.py)' }
  Write-Bad ("MISSING MARKERS ({0}/5): {1}" -f $markerHits.Count, ($absent -join ', '))
  $vFailures.Add("origin/master missing markers: $($absent -join ', ')")
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

$badWts = @($worktreeResults | Where-Object { -not $_.MarkersOK })
$vWorktrees = "{0}/{1}" -f ($worktreeResults.Count - $badWts.Count), $worktreeResults.Count
if ($badWts.Count -gt 0) {
  Write-Bad ("{0} worktree(s) are missing upgrade markers or are stale." -f $badWts.Count)
  foreach ($b in $badWts) { $vFailures.Add("worktree missing markers: $($b.Path)") }
} else {
  Write-Ok ("All {0} registered worktrees have the pipeline upgrade markers." -f $worktreeResults.Count)
}

# Dedicated section for known full clones (independent .git). These are the source of many
# "commit not found" problems because they can lag independently of linked worktrees.
Write-Section 'Known full clones (separate .git, e.g. ai-runners)'
$knownFullClones = @(
  'C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners',
  'C:\Users\dasil\OneDrive\Documents\outlier',
  'C:\Users\dasil\OneDrive\Documents\outlier-mirror',
  'C:\Users\dasil\My Drive (dasilvadub@gmail.com)\Sports_Analytics\outlier'
)
# Every row used to be hardcoded as "ai-runners:" regardless of which path it described,
# so four different clones reported under one name. And Test-Path on .git is satisfied by
# a OneDrive placeholder that git cannot open, which emitted the phantom blank row
# ("HEAD= origin/master= markers=MISSING") sitting among healthy ones -- an empty-string
# truthiness bug of the same family as PR #41. Label by actual path, and probe with git
# rather than trusting Test-Path.
$cloneOk = 0
$cloneTotal = 0
$cloneUnreadable = 0
foreach ($fc in $knownFullClones) {
  # Leaf alone is ambiguous: Documents\outlier and My Drive\...\outlier both render as
  # "outlier". Use the last two segments.
  $leaf   = Split-Path -Leaf $fc
  $parent = Split-Path -Leaf (Split-Path -Parent $fc)
  $label  = "$parent/$leaf"

  if (-not (Test-Path (Join-Path $fc '.git'))) {
    Write-Host ("  {0,-28} : not present" -f $label)
    continue
  }

  git -C $fc rev-parse --git-dir 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    # Reported, but deliberately NOT a hard failure. The OneDrive mirror is
    # permanently unreadable by design (placeholder .git), so failing the verdict on
    # it would print FAILED on every single run -- and a status that is always red
    # teaches everyone to ignore it, which is precisely how the old FORBIDDEN banner
    # became noise. Only readable-but-stale clones fail the report.
    Write-Warn ("  {0,-28} : UNREADABLE (.git exists but git cannot open it - OneDrive placeholder?) at {1}" -f $label, $fc)
    $cloneUnreadable++
    continue
  }

  $cloneTotal++
  $fcHead   = (git -C $fc rev-parse --short HEAD 2>$null)
  $fcOrigin = (git -C $fc rev-parse --short origin/master 2>$null)
  $fcPack   = git -C $fc show origin/master:outlier_scrapers/pack.py 2>$null   # from origin for truth
  $fcDaily  = git -C $fc show origin/master:outlier_scrapers/daily_job.py 2>$null
  $mP = [bool]($fcPack -match 'player_id')
  $mR = [bool]($fcPack -match 'round_robin_then_fill')
  $mC = [bool]($fcPack -match 'CANDIDATES_HEADER')
  $mL = [bool]($fcDaily -match '_acquire_pack_lock')
  $ok = $mP -and $mR -and $mC -and $mL

  # An empty HEAD is a failed probe, not a healthy clone at "".
  if (-not $fcHead) {
    Write-Bad ("  {0,-12} : could not resolve HEAD at {1}" -f $label, $fc)
    $vFailures.Add("full clone HEAD unresolved: $fc")
    continue
  }

  $status = if ($ok) { 'OK' } else { 'MISSING' }
  Write-Host ("  {0,-28} : HEAD={1} origin/master={2} markers={3}" -f $label, $fcHead, $fcOrigin, $status)
  if ($ok) { $cloneOk++ } else { $vFailures.Add("full clone missing markers: $fc") }
  if ($fcHead -ne $fcOrigin) {
    Write-Warn ("  {0} is not at origin/master tip; re-run report-sync from canonical." -f $label)
  }
}
$vClones = "$cloneOk/$cloneTotal"
if ($cloneUnreadable -gt 0) { $vClones += " (+$cloneUnreadable unreadable)" }

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
Write-Host '1. From canonical (C:\Users\dasil\Dev\GitHub\outlier):'
Write-Host '     & "C:\Users\dasil\Dev\GitHub\outlier\sync-outlier.ps1" -SyncAllWorktrees'
Write-Host '2. In the affected ent/worktree, run:'
Write-Host '     & "C:\Users\dasil\Dev\GitHub\outlier\scripts\verify-sync.ps1"'
Write-Host '3. If still bad: the ent is on a non-linked full clone — delete it and start from'
Write-Host '   git clone https://github.com/DaSilvaDub/outlier.git then run the script.'

Write-Host ''
Write-Ok 'Report complete. This is the synchronized view for all ents.'
Write-Host 'Rule: every "I searched..." or "commit not visible" discussion must start with the'
Write-Host 'full output of the command above (the canonical verify-sync.ps1 path), and the'
Write-Host 'paste must end with the RUN-NONCE line below. No nonce = not a valid attestation.'

# Previously an unconditional `exit 0`: the report could describe a total failure and
# still succeed. The verdict is computed from what actually happened.
$exitCode = 0
if ($vState -ne 'MATCH')   { $exitCode = 1 }
if ($vMarkers -ne '5/5')   { $exitCode = 1 }
if ($vFailures.Count -gt 0) { $exitCode = 1 }
Write-Verdict -ExitCode $exitCode
