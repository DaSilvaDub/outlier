<#
.SYNOPSIS
  Continuous 4-way sync for the outlier repo across:
    1) Laptop local   (C:\Users\dasil\Dev\GitHub\outlier)  <- CANONICAL work tree
    2) OneDrive       (C:\Users\dasil\OneDrive\Documents\outlier or outlier-mirror)
    3) Google My Drive (C:\Users\dasil\My Drive (...)\Sports_Analytics\outlier)
    4) GitHub         (https://github.com/DaSilvaDub/outlier.git)   <- SSOT for commits

.DESCRIPTION
  GitHub is the single source of truth for committed code.
  - Canonical (Laptop local disk): fetch; push ahead commits; pull only when clean and behind.
    NEVER hard-resets a dirty tree (protects WIP). DO REAL WORK HERE.
  - OneDrive + My Drive + ai-runners: tracking mirrors. Always force-reset to origin/master.
    Do not develop on these mirrors.
  - Also optionally runs -SyncAllWorktrees from the laptop canonical.

  Install (once):
    & "C:\Users\dasil\Scripts\install_outlier_quad_sync.ps1"

  Run once now:
    & "C:\Users\dasil\Scripts\sync_outlier_quad.ps1"
#>

[CmdletBinding()]
param(
  [switch]$SkipWorktrees,
  [switch]$SkipMirrors,
  [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$GithubUrl = 'https://github.com/DaSilvaDub/outlier.git'
$Canonical = 'C:\Users\dasil\Dev\GitHub\outlier'
# Prefer primary OneDrive path only if it is a *valid* git repo; else outlier-mirror
$OneDrivePrimary = 'C:\Users\dasil\OneDrive\Documents\outlier'
$OneDriveAlt     = 'C:\Users\dasil\OneDrive\Documents\outlier-mirror'
function Test-ValidGitRepo([string]$Path) {
  if (-not (Test-Path (Join-Path $Path '.git'))) { return $false }
  $prev = Get-Location
  try {
    Set-Location $Path
    $null = git rev-parse --git-dir 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
  finally { Set-Location $prev }
}
$OneDrive = if (Test-ValidGitRepo $OneDrivePrimary) {
  $OneDrivePrimary
} elseif (Test-ValidGitRepo $OneDriveAlt) {
  $OneDriveAlt
} else {
  # Prefer alt for clone (primary may be a locked/broken leftover tree)
  $OneDriveAlt
}
$MyDrive   = 'C:\Users\dasil\My Drive (dasilvadub@gmail.com)\Sports_Analytics\outlier'
$AiRunners = 'C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners'
$LogPath   = 'C:\Users\dasil\Scripts\outlier_quad_sync.log'
$StatusJson = 'C:\Users\dasil\Scripts\outlier_quad_sync_status.json'
$LockPath  = 'C:\Users\dasil\Scripts\outlier_quad_sync.lock'

# Mirrors: always match origin/master (read-only tracking clones)
$MirrorPaths = @($OneDrive, $MyDrive, $AiRunners)

function Write-Log([string]$msg, [string]$level = 'INFO') {
  $line = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $level, $msg
  Add-Content -Path $LogPath -Value $line -Encoding utf8
  $color = switch ($level) {
    'ERROR' { 'Red' }
    'WARN'  { 'Yellow' }
    'OK'    { 'Green' }
    default { 'Cyan' }
  }
  Write-Host $line -ForegroundColor $color
}

function Get-GitExe {
  $g = Get-Command git -ErrorAction SilentlyContinue
  if (-not $g) { throw 'git not found on PATH' }
  return $g.Source
}

function Invoke-Git {
  param(
    [string]$Repo,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GitArgs
  )
  $git = Get-GitExe
  $prev = Get-Location
  try {
    Set-Location $Repo
    $out = & $git @GitArgs 2>&1
    $code = $LASTEXITCODE
    return [pscustomobject]@{
      ExitCode = $code
      Output   = ($out | Out-String).Trim()
    }
  } finally {
    Set-Location $prev
  }
}

function Ensure-RepoClone {
  param([string]$Path, [string]$Role)
  if (Test-ValidGitRepo $Path) {
    Write-Log "$Role already cloned: $Path"
    return $true
  }
  if (Test-Path (Join-Path $Path '.git')) {
    Write-Log "$Role has a broken .git at $Path - will not clone over it" 'ERROR'
    return $false
  }
  if (Test-Path $Path) {
    $items = @(Get-ChildItem $Path -Force -ErrorAction SilentlyContinue)
    if ($items.Count -gt 0) {
      Write-Log "$Role path exists but is not a git repo and is non-empty: $Path" 'ERROR'
      return $false
    }
  }
  $parent = Split-Path $Path -Parent
  if (-not (Test-Path $parent)) {
    if ($DryRun) {
      Write-Log "DRYRUN would mkdir $parent"
    } else {
      New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
  }
  Write-Log "Cloning $Role from GitHub -> $Path"
  if ($DryRun) { return $true }
  $git = Get-GitExe
  $parentDir = Split-Path $Path -Parent
  $name = Split-Path $Path -Leaf
  $prev = Get-Location
  try {
    Set-Location $parentDir
    # Shallow-ish but full history is preferred for agents; use full clone.
    & $git clone --branch master --single-branch $GithubUrl $name 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
      # fallback without --single-branch
      & $git clone $GithubUrl $name 2>&1 | Out-Null
    }
    if (-not (Test-Path (Join-Path $Path '.git'))) {
      Write-Log "Clone failed for $Role at $Path" 'ERROR'
      return $false
    }
  } finally {
    Set-Location $prev
  }
  Write-Log "Clone OK: $Role" 'OK'
  return $true
}

function Ensure-Origin {
  param([string]$Path)
  $r = Invoke-Git $Path remote get-url origin
  if ($r.ExitCode -ne 0 -or $r.Output -notlike '*github.com*DaSilvaDub/outlier*') {
    Write-Log "Fixing origin on $Path (was: $($r.Output))" 'WARN'
    if (-not $DryRun) {
      Invoke-Git $Path remote remove origin | Out-Null
      Invoke-Git $Path remote add origin $GithubUrl | Out-Null
    }
  } else {
    if (-not $DryRun) {
      Invoke-Git $Path remote set-url origin $GithubUrl | Out-Null
    }
  }
}

function Get-ShortSha([string]$Path, [string]$Ref = 'HEAD') {
  $r = Invoke-Git $Path rev-parse --short $Ref
  if ($r.ExitCode -eq 0 -and $r.Output) { return $r.Output.Trim() }
  return 'UNKNOWN'
}

function Test-DirtyTree([string]$Path) {
  $r = Invoke-Git $Path status --porcelain
  return ($r.Output -and $r.Output.Trim().Length -gt 0)
}

function Sync-Mirror {
  param([string]$Path, [string]$Role)
  if (-not (Ensure-RepoClone -Path $Path -Role $Role)) {
    return [pscustomobject]@{ Role = $Role; Path = $Path; Status = 'CLONE_FAIL'; Head = '' }
  }
  Ensure-Origin $Path
  Write-Log "Mirror sync ${Role}: fetch + hard reset to origin/master"
  if ($DryRun) {
    return [pscustomobject]@{ Role = $Role; Path = $Path; Status = 'DRYRUN'; Head = '' }
  }
  $fetch = Invoke-Git $Path fetch origin --prune --tags
  if ($fetch.ExitCode -ne 0) {
    Write-Log "Fetch failed for $Role : $($fetch.Output)" 'ERROR'
    return [pscustomobject]@{ Role = $Role; Path = $Path; Status = 'FETCH_FAIL'; Head = Get-ShortSha $Path }
  }
  $target = Invoke-Git $Path rev-parse --verify origin/master
  if ($target.ExitCode -ne 0) {
    Write-Log "No origin/master for $Role" 'ERROR'
    return [pscustomobject]@{ Role = $Role; Path = $Path; Status = 'NO_MASTER'; Head = '' }
  }
  # Detach or master - always land on origin/master tip for mirrors
  Invoke-Git $Path checkout -B master origin/master | Out-Null
  Invoke-Git $Path reset --hard origin/master | Out-Null
  Invoke-Git $Path clean -fd | Out-Null
  # Materialize key files for OneDrive/Google Drive hydration issues
  Invoke-Git $Path checkout -- outlier_scrapers tests prompts sync-outlier.ps1 scripts/verify-sync.ps1 report-sync.ps1 SYNC.md 2>$null | Out-Null
  $head = Get-ShortSha $Path
  $origin = Get-ShortSha $Path 'origin/master'
  $status = if ($head -eq $origin) { 'MATCH' } else { 'DIVERGED' }
  Write-Log "Mirror $Role HEAD=$head origin/master=$origin -> $status" $(if ($status -eq 'MATCH') { 'OK' } else { 'WARN' })
  return [pscustomobject]@{ Role = $Role; Path = $Path; Status = $status; Head = $head; Origin = $origin }
}

function Sync-Canonical {
  param([string]$Path)
  if (-not (Test-Path (Join-Path $Path '.git'))) {
    Write-Log "Canonical missing or not a git repo: $Path" 'ERROR'
    return [pscustomobject]@{ Role = 'Laptop-Canonical'; Path = $Path; Status = 'MISSING'; Head = '' }
  }
  Ensure-Origin $Path
  Write-Log 'Canonical: fetch origin'
  if ($DryRun) {
    return [pscustomobject]@{ Role = 'Laptop-Canonical'; Path = $Path; Status = 'DRYRUN'; Head = '' }
  }
  $fetch = Invoke-Git $Path fetch origin --prune --tags
  if ($fetch.ExitCode -ne 0) {
    Write-Log "Canonical fetch failed: $($fetch.Output)" 'ERROR'
    return [pscustomobject]@{ Role = 'Laptop-Canonical'; Path = $Path; Status = 'FETCH_FAIL'; Head = Get-ShortSha $Path }
  }

  $dirty = Test-DirtyTree $Path
  $head = Get-ShortSha $Path
  $origin = Get-ShortSha $Path 'origin/master'
  $branch = (Invoke-Git $Path branch --show-current).Output.Trim()

  # Ahead/behind counts vs origin/master
  $ab = Invoke-Git $Path rev-list --left-right --count "HEAD...origin/master"
  $ahead = 0; $behind = 0
  if ($ab.ExitCode -eq 0 -and $ab.Output -match '(\d+)\s+(\d+)') {
    $ahead = [int]$Matches[1]
    $behind = [int]$Matches[2]
  }

  $action = 'NONE'
  $status = 'UNKNOWN'
  if ($dirty) {
    Write-Log "Canonical DIRTY - will not hard-reset. ahead=$ahead behind=$behind HEAD=$head origin=$origin branch=$branch" 'WARN'
  }

  # Push commits even when WIP exists (push does not require a clean tree).
  if ($ahead -gt 0 -and $behind -eq 0) {
    Write-Log "Canonical AHEAD by $ahead - pushing commits to GitHub (WIP left local)"
    $push = Invoke-Git $Path push origin HEAD:master
    if ($push.ExitCode -ne 0) {
      Write-Log "Push failed: $($push.Output)" 'ERROR'
      $status = 'PUSH_FAIL'
      $action = 'PUSH_FAIL'
    } else {
      $action = if ($dirty) { 'PUSHED_WITH_WIP' } else { 'PUSHED' }
      $status = if ($dirty) { 'DIRTY_MATCH_TIP' } else { 'MATCH' }
      Write-Log 'Push OK' 'OK'
    }
  } elseif ($behind -gt 0 -and $ahead -eq 0) {
    if ($dirty) {
      Write-Log "Canonical BEHIND by $behind but DIRTY - skip reset; commit/stash WIP first" 'WARN'
      $status = 'DIRTY_BEHIND'
      $action = 'PROTECTED_WIP'
    } else {
      Write-Log "Canonical clean and BEHIND by $behind - hard reset to origin/master"
      if ($branch -ne 'master') {
        Invoke-Git $Path checkout master | Out-Null
      }
      Invoke-Git $Path reset --hard origin/master | Out-Null
      $action = 'RESET_TO_ORIGIN'
      $status = 'MATCH'
    }
  } elseif ($ahead -gt 0 -and $behind -gt 0) {
    Write-Log "Canonical DIVERGED (ahead=$ahead behind=$behind) - manual reconcile required" 'ERROR'
    $status = 'DIVERGED'
    $action = 'NEEDS_MANUAL'
  } else {
    if ($dirty) {
      $status = 'DIRTY_MATCH_TIP'
      $action = 'PROTECTED_WIP'
      Write-Log "Canonical tip MATCH origin/master ($head) with local WIP" 'OK'
    } else {
      $status = 'MATCH'
      $action = 'ALREADY_SYNCED'
      Write-Log "Canonical already MATCH origin/master ($head)" 'OK'
    }
  }

  # Pin folder Always keep on this device (OneDrive)
  try {
    if (Test-Path $Path) {
      & attrib.exe +P /S /D $Path 2>$null | Out-Null
    }
  } catch {}

  $head = Get-ShortSha $Path
  $origin = Get-ShortSha $Path 'origin/master'
  return [pscustomobject]@{
    Role     = 'Laptop-Canonical'
    Path     = $Path
    Status   = $status
    Head     = $head
    Origin   = $origin
    Dirty    = $dirty
    Ahead    = $ahead
    Behind   = $behind
    Action   = $action
    Branch   = $branch
  }
}

function Sync-Worktrees {
  $script = Join-Path $Canonical 'sync-outlier.ps1'
  if (-not (Test-Path $script)) {
    Write-Log "sync-outlier.ps1 missing at canonical - skip worktrees" 'WARN'
    return
  }
  Write-Log 'Running canonical SyncAllWorktrees (agent worktrees + known full clones)'
  if ($DryRun) { return }
  $prev = Get-Location
  try {
    Set-Location $Canonical
    & $script -SyncAllWorktrees 2>&1 | ForEach-Object { Write-Log "  worktree: $_" }
  } catch {
    Write-Log "SyncAllWorktrees error: $_" 'WARN'
  } finally {
    Set-Location $prev
  }
}

# ---- lock (prevent overlapping scheduled runs) ----
if (Test-Path $LockPath) {
  $ageMin = ((Get-Date) - (Get-Item $LockPath).LastWriteTime).TotalMinutes
  if ($ageMin -lt 25) {
    Write-Log "Another sync holds the lock (age $($ageMin.ToString('N1'))m). Exiting." 'WARN'
    exit 0
  }
  Write-Log "Stale lock ($($ageMin.ToString('N1'))m) - removing" 'WARN'
  Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
}
'locked' | Set-Content $LockPath -Encoding utf8

$results = @()
$failed = 0
try {
  Write-Log '======== QUAD SYNC START ========'
  Write-Log "GitHub SSOT: $GithubUrl"

  # 1) Laptop canonical first (may push new commits to GitHub)
  $canon = Sync-Canonical -Path $Canonical
  $results += $canon
  if ($canon.Status -in @('MISSING', 'FETCH_FAIL', 'PUSH_FAIL', 'DIVERGED')) { $failed++ }

  # 2) Mirrors: OneDrive + My Drive + ai-runners track origin/master after any push
  if (-not $SkipMirrors) {
    Write-Log "OneDrive mirror target: $OneDrive"
    foreach ($pair in @(
      @{ Path = $OneDrive; Role = 'OneDrive-Mirror' },
      @{ Path = $MyDrive; Role = 'Google-MyDrive' },
      @{ Path = $AiRunners; Role = 'ai-runners' }
    )) {
      if (-not (Test-Path $pair.Path) -and $pair.Role -eq 'ai-runners') {
        Write-Log 'ai-runners not present - skip'
        continue
      }
      $m = Sync-Mirror -Path $pair.Path -Role $pair.Role
      $results += $m
      if ($m.Status -notin @('MATCH', 'DRYRUN')) { $failed++ }
    }
  }

  # 3) Align linked agent worktrees from existing bootstrap
  if (-not $SkipWorktrees) {
    Sync-Worktrees
  }

  # 4) Status file for dashboards / agents
  $payload = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    github       = $GithubUrl
    failed       = $failed
    locations    = @(
      foreach ($r in $results) {
        [ordered]@{
          role   = $r.Role
          path   = $r.Path
          status = $r.Status
          head   = $r.Head
          origin = $r.Origin
          dirty  = $r.Dirty
          action = $r.Action
        }
      }
    )
  }
  if (-not $DryRun) {
    ($payload | ConvertTo-Json -Depth 6) | Set-Content -Path $StatusJson -Encoding utf8
  }

  Write-Log "======== QUAD SYNC DONE failed=$failed ========" $(if ($failed -eq 0) { 'OK' } else { 'WARN' })
  foreach ($r in $results) {
    Write-Log ("  {0,-22} {1,-16} head={2}" -f $r.Role, $r.Status, $r.Head)
  }
} catch {
  Write-Log "FATAL: $($_.Exception.Message) [line $($_.InvocationInfo.ScriptLineNumber)]" 'ERROR'
  $failed++
} finally {
  Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
}

exit $(if ($failed -eq 0) { 0 } else { 2 })
