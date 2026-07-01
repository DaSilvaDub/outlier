# Claude — start here

You are agent **`claude`** in this repo. Multiple AI agents share this codebase.

**STEP 0 — DO THIS FIRST, BEFORE READING AGENTS.md OR ANY CODE OR LOGS:**

```powershell
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1"
# Preferred for verification:
& "C:\Users\dasil\OneDrive\Documents\outlier\sync-outlier.ps1" -ValidateOnly
```

Paste the complete [sync] output into your first response. Proceed only if you see:
- VALIDATE: OK
- State vs origin/master: MATCH (or close after fetch)
- All upgrade markers present (player_id, CANDIDATES_HEADER, round_robin_then_fill, decisions.csv, lock functions)
- Recent HEAD (the one that has the pack.py + daily_job changes)

Then read `AGENTS.md` (the Multi-Agent Sync Protocol section is mandatory and now duplicated here for force).

1. On session start (after bootstrap): read `.agent-log/SUMMARY.md`, the latest `.agent-log/`
   entries, and `git log --oneline -15`.
2. On session end: commit your work with an `Agent: claude` trailer, write a
   session note in `.agent-log/`, and update `.agent-log/SUMMARY.md`.

All project rules, scope constraints, and technical gotchas live in `AGENTS.md` + SYNC.md.

**Global harness note**: When configuring the Claude agent for this repo, the system prompt / pre-instructions *must* contain the exact STEP 0 bootstrap commands above + "report the output and only continue on VALIDATE OK". This is what prevents d05eb21-style "the commit/changes don't exist in my tree" across ents.
