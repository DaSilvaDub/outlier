# Multi-Ent Sync Global Prompt Snippet (for EVERY harness)

Copy-paste the block below **verbatim** into the *global / system / pre-instructions* for this repo in every AI harness you use (Claude Code project settings, Codex rules, Grok custom instructions, Gemini/antigravity workspace prompt, future ents). 

This is the mechanism that guarantees no matter which ent, the agent sees identical pack.py/daily_job.py and history.

```
You are working in the outlier repo (https://github.com/DaSilvaDub/outlier.git). MULTI-ENT SYNC IS MANDATORY.

STEP 0 (ABSOLUTE FIRST ACTION — before reading any files, git log, planning, or running commands):

Execute EXACTLY this (use the full canonical path even from worktrees or other clones):

& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"
# Preferred verification gate:
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -ValidateOnly

Report the *full* console output (every [sync] line).

You may only continue when the output contains:
- "VALIDATE: OK"
- "State vs origin/master: MATCH"
- "Upgrade markers present" (player_id, round_robin_then_fill / rank_rows, CANDIDATES_HEADER, _acquire_pack_lock, decisions.csv)
- Recent HEAD (e.g. b2c79d4 or later)
- "This report was produced by scripts/verify-sync.ps1" when a search of branches/worktrees is needed

For ANY question about "does commit X exist", "search every branch + every .codex/.gemini worktree", or state comparison:
- ALWAYS run: & "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1"
- Paste its ENTIRE output.
- NEVER build reports from raw git log / Get-ChildItem / Select-String / ls / cat / grep loops.

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
