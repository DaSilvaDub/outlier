<#
.SYNOPSIS
    SessionStart guard: verifies outlier sync state WITHOUT mutating anything.

.DESCRIPTION
    AGENTS.md STEP 0 mandates running report-sync.ps1 as the absolute first
    action of every session, but nothing enforced it -- and its failure mode is
    near-silent. Launched from a directory git cannot read (e.g. the OneDrive
    mirror, where .git is a OneDrive placeholder) the bootstrap stage no-ops,
    prints "[sync] Not inside a git repo", and the report STILL renders
    "State vs origin/master: MATCH". Only the absence of "VALIDATE: OK"
    distinguishes a real sync from a skipped one.

    This hook does not call report-sync.ps1, because that script force-runs
    bootstrap + -SyncAllWorktrees before it will inspect anything, which
    hard-aligns the ai-runners full clone. Doing that automatically on every
    session start could overwrite another ent's uncommitted work with no human
    in the loop. So: read-only checks, and a loud instruction to run STEP 0 by
    hand when something is actually off.

    Read-only operations only: rev-parse, fetch (remote-tracking refs), and
    file reads. No checkout, no reset, no branch writes.

.NOTES
    Always exits 0. A SessionStart hook that fails must not block the session.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$Canonical = if ($PSScriptRoot) {
    $candidate = Split-Path (Split-Path $PSScriptRoot)  # hooks -> .claude -> repo root
    if (Test-Path (Join-Path $candidate '.git')) { $candidate } else { 'C:\Users\dasil\Dev\GitHub\outlier' }
} else { 'C:\Users\dasil\Dev\GitHub\outlier' }
$OriginRegex  = 'DaSilvaDub/outlier(\.git)?$'
$Step0        = '& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"'

# Tier-1 pipeline upgrade markers (CLAUDE.md / AGENTS.md). Absence means the
# tree predates 88083ff and is the d05eb21-class "changes invisible to me" bug.
#
# The marker -> file mapping matters and is easy to get wrong: despite its name,
# _acquire_pack_lock lives in daily_job.py, not pack.py. Authority is
# scripts/verify-sync.ps1 (~L81), which checks it against the *daily* blob:
#     $hasLock = $gitDaily -match '_acquire_pack_lock'
$Markers = @{
    'player_id'              = 'outlier_scrapers/pack.py'
    'round_robin_then_fill'  = 'outlier_scrapers/pack.py'
    'CANDIDATES_HEADER'      = 'outlier_scrapers/pack.py'
    'decisions.csv'          = 'outlier_scrapers/pack.py'
    '_acquire_pack_lock'     = 'outlier_scrapers/daily_job.py'
}

$findings = [System.Collections.Generic.List[string]]::new()
$problems = [System.Collections.Generic.List[string]]::new()

function Invoke-Git {
    # Returns trimmed stdout, or $null on any failure. Never throws.
    param([string]$RepoPath, [string[]]$GitArgs)
    try {
        $out = & git -C $RepoPath @GitArgs 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        if ($null -eq $out) { return '' }
        return ($out -join "`n").Trim()
    } catch { return $null }
}

try {
    $cwd = (Get-Location).Path

    # ---- 0. Relevance gate --------------------------------------------------
    # This hook is also registered at user scope (~/.claude/settings.json) so it
    # fires regardless of cwd -- including in projects that have nothing to do
    # with outlier. Without this gate every unrelated session would pay for a
    # network fetch against the outlier remote. Exit silently unless this session
    # plausibly concerns outlier: either the path says so (covers the OneDrive
    # mirror, whose .git git cannot read) or the checkout's remote says so.
    $looksOutlier = $cwd -match '(?i)outlier'
    if (-not $looksOutlier) {
        $probeRemote = Invoke-Git -RepoPath $cwd -GitArgs @('remote', 'get-url', 'origin')
        if ($probeRemote -match $OriginRegex) { $looksOutlier = $true }
    }
    if (-not $looksOutlier) { exit 0 }

    # ---- 1. Is the session's own directory a usable outlier checkout? --------
    $cwdRoot   = Invoke-Git -RepoPath $cwd -GitArgs @('rev-parse', '--show-toplevel')
    $cwdRemote = Invoke-Git -RepoPath $cwd -GitArgs @('remote', 'get-url', 'origin')

    if (-not $cwdRoot) {
        $problems.Add("Session cwd is NOT a git repository git can read: $cwd")
        $problems.Add('If this is the OneDrive mirror, its .git is a OneDrive placeholder. Every sync/verify run from here silently skips bootstrap while still printing a MATCH line.')
    } elseif ($cwdRemote -notmatch $OriginRegex) {
        $problems.Add("Session cwd is a git repo but origin is not the outlier remote: $cwdRemote")
    } else {
        $findings.Add("cwd checkout: $cwdRoot")
    }

    # ---- 2. Canonical identity (read-only) ----------------------------------
    if (-not (Test-Path $Canonical)) {
        $problems.Add("Canonical checkout missing at $Canonical")
    } else {
        # Updates remote-tracking refs only; touches no local branch or file.
        Invoke-Git -RepoPath $Canonical -GitArgs @('fetch', 'origin', '--quiet') | Out-Null

        $branch   = Invoke-Git -RepoPath $Canonical -GitArgs @('branch', '--show-current')
        $head     = Invoke-Git -RepoPath $Canonical -GitArgs @('rev-parse', '--short', 'HEAD')
        $originM  = Invoke-Git -RepoPath $Canonical -GitArgs @('rev-parse', '--short', 'origin/master')

        if (-not $originM) {
            $problems.Add('Could not resolve origin/master in canonical (fetch may have failed).')
        } else {
            $findings.Add("canonical: branch=$(if ($branch) { $branch } else { '(detached)' }) HEAD=$head origin/master=$originM")

            $counts = Invoke-Git -RepoPath $Canonical -GitArgs @('rev-list', '--left-right', '--count', "origin/master...HEAD")
            if ($counts -match '^(\d+)\s+(\d+)$') {
                $behind = [int]$Matches[1]
                $ahead  = [int]$Matches[2]
                if ($behind -gt 0) {
                    $problems.Add("Canonical HEAD is $behind commit(s) BEHIND origin/master.")
                } elseif ($ahead -gt 0) {
                    if ($branch -eq 'master') {
                        # Ahead on master means unpushed commits on the trunk itself.
                        # AGENTS.md reserves direct-to-master for coordination files,
                        # so this is worth surfacing rather than waving through.
                        $findings.Add("canonical master has $ahead UNPUSHED commit(s) vs origin/master - confirm they are coordination-file-only per AGENTS.md, and push them.")
                    } else {
                        $findings.Add("canonical is $ahead commit(s) ahead of origin/master (normal on feature branch '$branch').")
                    }
                } else {
                    $findings.Add('canonical matches origin/master.')
                }
            }
        }
    }

    # ---- 3. Upgrade markers on disk in whichever tree this session will edit -
    $markerRoot = if ($cwdRoot) { $cwdRoot } else { $Canonical }
    $missing    = [System.Collections.Generic.List[string]]::new()

    foreach ($marker in $Markers.Keys) {
        $file = Join-Path $markerRoot $Markers[$marker]
        if (-not (Test-Path $file)) {
            $missing.Add("$marker (file missing: $($Markers[$marker]))")
            continue
        }
        $content = Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue
        if ($null -eq $content -or -not $content.Contains($marker)) {
            $missing.Add($marker)
        }
    }

    if ($missing.Count -gt 0) {
        $problems.Add("Upgrade markers MISSING in ${markerRoot}: $($missing -join ', ')")
    } else {
        $findings.Add("upgrade markers present (all $($Markers.Count)) in $markerRoot")
    }

    # ---- 4. Verdict ---------------------------------------------------------
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('[outlier sync check - read-only, no reset performed]')
    foreach ($f in $findings) { $lines.Add("  ok   $f") }

    if ($problems.Count -eq 0) {
        $lines.Add('  VERDICT: sync state looks consistent. STEP 0 still governs any')
        $lines.Add('  "does commit X exist / I searched every branch" question - answer those')
        $lines.Add("  only with a full untruncated run of: $Step0")
    } else {
        foreach ($p in $problems) { $lines.Add("  FAIL $p") }
        $lines.Add('')
        $lines.Add('  VERDICT: DRIFT OR UNREADABLE STATE. Before reading code, planning, or')
        $lines.Add('  editing, run STEP 0 from the canonical directory and paste the ENTIRE output:')
        $lines.Add("      Set-Location '$Canonical'")
        $lines.Add("      $Step0")
        $lines.Add('  Require in that output: VALIDATE: OK, State vs origin/master: MATCH,')
        $lines.Add('  all upgrade markers, and the verify-sync.ps1 provenance line.')
        $lines.Add('  Note: report-sync also -SyncAllWorktrees (hard-aligns ai-runners). Confirm no')
        $lines.Add('  other ent has uncommitted work there before running it.')
    }

    $context = $lines -join "`n"

    $out = @{
        hookSpecificOutput = @{
            hookEventName    = 'SessionStart'
            additionalContext = $context
        }
    }
    if ($problems.Count -gt 0) {
        $out.systemMessage = "outlier sync check found $($problems.Count) problem(s) - STEP 0 sync needed. See session context."
    }

    $out | ConvertTo-Json -Depth 6 -Compress
} catch {
    @{
        hookSpecificOutput = @{
            hookEventName     = 'SessionStart'
            additionalContext = "[outlier sync check] hook error: $($_.Exception.Message). Run STEP 0 manually: $Step0"
        }
    } | ConvertTo-Json -Depth 6 -Compress
}

exit 0
