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

## MANDATORY First Action — Every Session, Every Ent

From inside any checkout (worktree or clone):

```powershell
# Preferred: use the committed bootstrap (updated to be GitHub-first + marker validation)
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"

# Or with validation gate (agents can call this and fail the session if not OK)
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -ValidateOnly
```

The script:
- Forces origin -> GitHub.
- `fetch --prune`.
- `reset --hard origin/master` (or checkout -B).
- Materializes the critical files that were the source of the original bug.
- Touches files to help OneDrive hydrate.
- Reports **authoritative sizes from git show** (immune to FS lies) + disk view.
- **Hard-validates** the upgrade markers (player_id in header, round_robin_then_fill, _acquire_pack_lock, decisions.csv, CANDIDATES_HEADER).
- If markers missing → you will see a warning / non-zero from -ValidateOnly.

Run this **before** reading any code or running any scraper / test.

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

## Long-Term (to make the problem impossible)

- Move canonical off OneDrive to `C:\Users\dasil\dev\outlier` (or fast local disk).
- Update all global agent configs + this doc.
- Make every skill / command for this project start by invoking the bootstrap + validate.

Last updated: 2026-07-01 (GitHub is primary; bootstrap hardened with marker validation + git-show authoritative checks; worktree alignment + ai-runners slaved).

