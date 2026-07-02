# Multi-Ent Sync Global Prompt Snippet (for EVERY harness)

Copy-paste the block below **verbatim** into the *global / system / pre-instructions* for this repo in every AI harness you use (Claude Code project settings, Codex rules, Grok custom instructions, Gemini/antigravity workspace prompt, future ents). 

This is the mechanism that guarantees no matter which ent, the agent sees identical pack.py/daily_job.py and history.

```
You are working in the outlier repo (https://github.com/DaSilvaDub/outlier.git). MULTI-ENT SYNC IS MANDATORY.

STEP 0 (ABSOLUTE FIRST ACTION — before reading any files, git log, planning, or running commands):

NO MATTER which ent (Grok/Claude/Codex/Gemini), which starting CWD (worktree, ai-runners full clone, or stray dir), or how the harness launches you:

ALWAYS execute EXACTLY (full canonical path; do not use a local ./report-sync or relative path):

& "C:\Users\dasil\OneDrive\Documents\outlier\report-sync.ps1"

# Thin wrapper -> canonical bootstrap + -SyncAllWorktrees (which now also hard-resets ai-runners) + full verify report.
# Never pipe | Select-String | Out-String | Select -First | grep etc. The guard will flag it.

cd 'C:\Users\dasil\OneDrive\Documents\outlier' first if you like, then the command above.
Report the *full* untruncated console output (every line from [sync] through the final Rule).

You may only continue when the output contains:
- "VALIDATE: OK"
- "State vs origin/master: MATCH"
- "Upgrade markers present" (player_id, round_robin_then_fill, CANDIDATES_HEADER, _acquire_pack_lock, decisions.csv)
- Current canonical HEAD matches origin/master
- "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)."

For ANY question about "does commit X exist", "I searched every branch + .codex/.gemini worktree", "closest commit is d756cb4", "d05eb21 not found", sizes, or state:
- Run & "C:\Users\dasil\OneDrive\Documents\outlier\report-sync.ps1"  (or the scripts/verify... directly)
- Paste its ENTIRE output.
- NEVER build your own report with git log / rev-parse / Get-ChildItem / Select-String loops. Those are why the original problem existed.

After any -SyncAllWorktrees you (or another ent) run, immediately follow with the full report-sync and paste it.

See checked-in SYNC.md, AGENTS.md, and docs/ENT-SYNC-GLOBAL-PROMPT.md.

This rule is what makes d05eb21-style "the commit and the pack.py changes are invisible to me" impossible across Grok/Claude/Codex/Gemini/...
```

## Usage instructions for harness owners

- Claude Code: Put in the Project or Global instructions / Claude.md equivalent at the very top.
- Codex / other: Equivalent "always-run" or system prompt prefix.
- After updating a harness config, start a fresh session in the outlier checkout and verify it obeys STEP 0 by reporting full bootstrap output.
- When handing off between ents: the sender runs `& "...\sync-outlier.ps1" -SyncAllWorktrees` from canonical, then tells the receiver the exact bootstrap command + "run verify-sync for any state query".

## Why this exists

The d05eb21 (~624 line pipeline upgrade touching pack.py + daily_job.py) was a transient review tree that never got pushed to origin or other worktrees. Later materialization (88083ff+) + these scripts + enforced bootstrap = synchronized state for all ents.

Run `& "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1"` yourself to see the live authoritative cross-worktree report (lists all ~16 worktrees + marker status + explicit d05eb21 explanation).

Last updated: 2026-07-01
