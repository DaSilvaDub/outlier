# Repository Synchronization Contract (for ALL agents / environments)

**GOAL**: No matter which agent harness / "ent" (Grok, Claude Code, Codex, Gemini/antigravity, ...) you are in, you ALWAYS see the **exact same** source for outlier_scrapers/ (pack.py ~624-line Tier-1 changes, daily_job.py, tests, prompts/C.md, etc.).

The commit d05eb21 (and its materialization 88083ff etc.) and all follow-ups must be visible identically.

## The Single Sources of Truth (use both)

1. **GitHub (cross-ent, cross-machine glue)**: `https://github.com/DaSilvaDub/outlier.git`
   - Every ent that can reach the internet MUST be able to fetch from here.
   - This is what makes "d05eb21 lineage" visible to a Gemini in the cloud or a fresh Codex session.

2. **Canonical (local fast path + worktree owner)**: `C:\Users\dasil\OneDrive\Documents\outlier`
   - Owns the real .git directory.
   - All local Codex/Gemini worktrees under `.codex/worktrees/.../outlier` and `.gemini/...` are **linked** to it (share objects/refs).
   - **WARNING**: Still on OneDrive. Hydration, CRLF, and placeholder issues are the #1 recurring cause of "commit invisible" and size mismatches. Set the folder to "Always keep on this device".

**Rule**: GitHub is the arbiter. If two ents disagree, the one that did `git fetch origin; git reset --hard origin/master` from GitHub wins.

## MANDATORY First Action — Every Session, Every Ent (no exceptions)

**STEP 0 from ANY directory / ent / harness**:

```powershell
& "C:\Users\dasil\OneDrive\Documents\outlier\report-sync.ps1"
```

Always use the *canonical full path*. Do not rely on CWD, local copy in ai-runners, or relative ./report-sync.ps1.

This single command:
- cds to canonical internally where needed
- runs the bootstrap (GitHub remote, fetch --prune, reset/checkout to origin/master)
- runs -SyncAllWorktrees (now also force-resets the ai-runners full clone + materializes sync scripts)
- produces the authoritative report with marker validation, worktree list, full clone status, and the baked-in d05eb21 explanation

Then paste the *entire* output. Only proceed if you see VALIDATE: OK + MATCH + all markers.

For state/search questions ("commit not found", "searched all worktrees") you MUST use report-sync.ps1 (or scripts/verify-sync.ps1) and paste full. Ad-hoc git log / Get-ChildItem inside a 90ca8c3 worktree or lagged ai-runners clone is forbidden and will be rejected.

See also docs/ENT-SYNC-GLOBAL-PROMPT.md (the block to inject into every harness).

## For Linked Worktrees (Codex / Gemini on this box)

They share the canonical .git. After the canonical (or any worktree) does a successful sync + push/fetch, simply:

```powershell
cd C:\Users\dasil\.codex\worktrees\XXXX\outlier   # or gemini equivalent
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"
```

If still stale after that: the worktree list on canonical is out of date or OneDrive has not propagated objects. Re-run from the canonical once, then from the worktree.

## End of Session

```powershell
git add -A
git commit -m "..."
# From canonical (or a tree with push rights):
git push origin HEAD
```

## Creating New Isolated Work (use worktree, never stray clone)

From canonical:

```powershell
cd C:\Users\dasil\OneDrive\Documents\outlier
git worktree add ..\..\.codex\worktrees\my-task\outlier -b my-task
```

Tell the agent harness to use that path.

**Never** `git clone` a second full copy for an agent unless it is completely isolated and you immediately run the bootstrap against GitHub. Full clones are the historical source of d05eb21-only-visible-here disasters.

## Current Known Problem Areas (as of 2026-07-01)

- Many `.codex/worktrees/*/outlier` and gemini worktrees were left on detached old commits (a318709, 8a700c9, ...). They were realigned by running fetch+reset from the canonical.
- `ai-runners` (this full stray clone) — now slaved to GitHub via the script. It must not be treated as another source of truth.
- OneDrive hydration gaps on pack.py / daily_job.py after reset.

## How to Verify "Everyone Sees the Same" (use the script)

```powershell
& "...\sync-outlier.ps1" -ValidateOnly
git log --oneline -1
# Authoritative (what other ents will see after their fetch):
git show origin/master:outlier_scrapers/pack.py | Select-String -Pattern 'player_id|CANDIDATES_HEADER|round_robin' | Select -First 3
```

All ents must report the same HEAD (or descendant) and the markers present.

## If "d05eb21 (or the pack changes) doesn't exist" ever happens again

1. Run `sync-outlier.ps1` (or the ValidateOnly variant). It will set the GitHub remote and hard reset.
2. `git remote -v` — must show the github url for origin.
3. `git fetch origin; git reset --hard origin/master; git checkout -- outlier_scrapers/pack.py`
4. If still wrong: the ent is using a completely separate clone whose origin is an old GitHub fetch or a local file remote. Delete it and start fresh from GitHub or run the bootstrap.
5. For the canonical machine: also run the alignment commands on every registered worktree (see the script comments and past session notes).

## OneDrive Mitigation (do this now)

In Explorer: right-click the `outlier` folder (and .codex / .gemini worktree parents if possible) → OneDrive → "Always keep on this device".

This has been the root cause of "file size different", "Python can't see it", and "commit exists in git log but checkout shows old pack.py".

## Global Harness Prompt Template (paste this at the top of every ent's system instructions)

```
You are working in the outlier repo. MULTI-ENT SYNC IS MANDATORY.

STEP 0 (ABSOLUTE FIRST, before reading files, git log, or planning):
Execute exactly:
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"
# or for gate:
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -ValidateOnly

Report the *full* console output. You may only continue when the output shows VALIDATE: OK, recent HEAD match, and all pipeline upgrade markers (player_id / CANDIDATES_HEADER / round_robin / decisions.csv / locks). If not OK, re-run and/or escalate.

This is the only way to guarantee you see the same pack.py + daily_job.py (~624 line changes) and history as every other agent (Grok/Claude/Codex/Gemini/...).
See repo AGENTS.md + SYNC.md for details and -SyncAllWorktrees.
```

## For Linked Worktrees (Codex / Gemini on this box) + Safe alignment for feature branches

They share the canonical .git. After a successful sync + push from canonical or any:

```powershell
cd C:\Users\dasil\.codex\worktrees\XXXX\outlier   # or gemini equivalent
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"
```

**From canonical, align / materialize all registered worktrees in one shot (recommended after any push of core changes):**

```powershell
cd C:\Users\dasil\OneDrive\Documents\outlier
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -SyncAllWorktrees
```

The -SyncAllWorktrees uses safe `checkout origin/master -- <files>` so feature branches (codex/prompt-*, gemini fix-*) keep their HEAD/branch but receive the blessed versions of pack.py, daily_job.py, the sync script, etc.

## If "d05eb21 (or the pack changes) doesn't exist" ever happens again

1. Run the canonical bootstrap (or -ValidateOnly). It will set GitHub remote + hard reset + materialize.
2. `git remote -v` — must show the github url for origin.
3. From canonical: `& "...\sync-outlier.ps1" -SyncAllWorktrees` (fastest way to push the state to all local linked worktrees).
4. `git fetch origin; git reset --hard origin/master; git checkout -- outlier_scrapers/pack.py ... sync-outlier.ps1 SYNC.md`
5. If still wrong for a full clone like ai-runners: cd into it and run the bootstrap line (it self-slaves via GitHub).
6. Update the global instructions for that ent with the exact bootstrap + "report output" rule from the template in this file.

## OneDrive Mitigation (do this now)

In Explorer: right-click the `outlier` folder (and .codex / .gemini worktree parents if possible) → OneDrive → "Always keep on this device".

This has been the root cause of "file size different", "Python can't see it", and "commit exists in git log but checkout shows old pack.py".

## Recurrence Prevention (make "d05eb21 invisible" impossible)

- All development of changes that touch pack.py / daily_job / the C profile go through GitHub PRs or direct pushes from canonical after bootstrap.
- New worktrees created only on canonical, immediately bootstrapped.
- Every global agent/system prompt for this repo *starts* with the template above.
- Canonical owner periodically (or before handing off) runs `-SyncAllWorktrees`.
- Cloud or remote ents always clone from the github URL then bootstrap.
- The bootstrap materializes the sync files themselves.
- Only 2 full clones (canonical + ai-runners). ai-runners is kept slaved by running the script inside it.

## Long-Term (to make the problem impossible)

- Move canonical off OneDrive to `C:\Users\dasil\dev\outlier` (or fast local disk).
- Update all global agent configs + this doc.
- Make every skill / command for this project start by invoking the bootstrap + validate.

Last updated: 2026-07-01 (re-applied: -SyncAllWorktrees with safe non-destructive file materialize for branches; STEP 0 enforcement in AGENTS/CLAUDE/GROK.md; global template; self-materialization of tooling; recurrence checklist. GitHub + canonical + bootstrap = synchronized no matter the ent).

