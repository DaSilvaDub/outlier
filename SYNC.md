# Repository Synchronization Contract (for ALL agents / environments)

**GOAL**: No matter which agent harness you are (Grok, Claude Code, Codex, Gemini/antigravity, etc.), you ALWAYS see the exact same source for outlier_scrapers/ (pack.py, daily_job.py, c_research.py, run_desk.py, tests, etc.).

## The One True Source

- **Canonical repo (single .git owner)**: `C:\Users\dasil\OneDrive\Documents\outlier`
  - All Codex worktrees (under `C:\Users\dasil\.codex\worktrees\*\outlier`) and Gemini worktrees (under `C:\Users\dasil\.gemini\antigravity\worktrees\...`) are linked to this .git.
  - This is currently the "central" that agent worktrees attach to.

**WARNING**: This canonical is still on OneDrive. Long-term we should move it to a pure local path (e.g. `C:\Users\dasil\dev\outlier`).

## Mandatory Start-of-Session Steps (EVERY agent, every time)

```powershell
# 1. cd to a checkout that shares the canonical (or the canonical itself)
cd C:\Users\dasil\OneDrive\Documents\outlier
# or for a Codex/Gemini worktree:
# cd C:\Users\dasil\.codex\worktrees\XXXX\outlier

# 2. Bring in everything from the central objects + any pushes
git fetch --all --prune

# 3. Make sure you are on the latest shared master (or the agreed feature branch)
git checkout -B master origin/master   # or the branch you are collaborating on
# If you were on detached HEAD (common for agent worktrees):
# git checkout -B master origin/master

git status
```

## End-of-Session (before you stop or switch agents)

```powershell
git add -A
git commit -m "type: short description

Longer context. Reference counter-proposal or issue."
# If a real remote exists:
# git push origin HEAD
```

If only file:// "origin" (current setup), pushing updates the central folder refs. Other worktrees still need an explicit `fetch` to see it.

## How to Create / Use Worktrees (isolation without divergence)

From the canonical:

```powershell
cd C:\Users\dasil\OneDrive\Documents\outlier
git worktree add C:\Users\dasil\.codex\worktrees\my-new-task\outlier -b my-new-task
```

Then tell the harness "use this directory as the project root".

Never `git clone` again into a new random location. That creates an independent object store and guarantees the exact problem described (d05eb21 only visible in one tree).

## Current Known Stray / Independent Clones

- `C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners` (had its own full .git + d05eb21 locally)
  - Action: treat as throw-away or re-clone it from canonical after moves. Do not commit here expecting other agents to see it.

## How to Verify "Everyone Sees the Same"

Run in your tree:

```powershell
git rev-parse HEAD
(Get-Item outlier_scrapers/pack.py).Length
(Get-Item outlier_scrapers/daily_job.py).Length
# Look for upgrade markers:
Select-String -Path outlier_scrapers/pack.py -Pattern 'def select_date|def _acquire_pack_lock' -SimpleMatch
```

All agents should report the same lengths and the presence of Tier 1/2 symbols after a fetch + checkout.

## OneDrive Gotchas (why this keeps happening)

- Python sees different file presence/hydration than `cmd` / Explorer / git sometimes.
- .git directories get partially synced or conflicted.
- Worktrees registered in one clone's .git are invisible to a second independent clone.
- **Rule**: Only the canonical folder + its registered worktrees share objects/refs reliably.

## Long-Term Ideal Setup (to eliminate the problem forever)

1. Move canonical to `C:\Users\dasil\dev\outlier` (or D: drive).
2. Add a real GitHub/GitLab remote.
3. All harnesses configured to start from the new canonical path.
4. Update this file + AGENTS.md + user's global Agents.md.
5. Add a tiny bootstrap script `scripts\agent-bootstrap.ps1` that every agent skill/command calls first.

## If You See "commit X doesn't exist" again

1. Identify which .git the complaining agent is using (`git rev-parse --git-dir`).
2. From that tree: `git remote -v` (see if it's pointing at the central or a stray).
3. `git fetch <the-central-path-or-real-remote>`.
4. If still missing: the commit lives only in a stray clone's object store — use `git remote add temp-stray <stray-path>; git fetch temp-stray; git branch temp-import temp-stray/master`.
5. Materialize files or merge as needed, then commit on the canonical lineage.

Last updated: 2026-07-01 (after syncing d05eb21 / counter-proposal pack+daily changes into central as 88083ff).
