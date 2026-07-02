# Claude — start here

You are agent **`claude`** in this repo. Multiple AI agents share this codebase.

**STEP 0 — DO THIS FIRST, BEFORE READING AGENTS.md OR ANY CODE OR LOGS:**
**Canonical path, every ent, every start dir:**

```powershell
& "C:\Users\dasil\OneDrive\Documents\outlier\report-sync.ps1"
```

Runs full bootstrap + SyncAll (now covers ai-runners) + authoritative report (includes explicit d05eb21 section).

Paste the ENTIRE output. Only continue when it shows the "produced by scripts/verify-sync.ps1" header + VALIDATE: OK + MATCH + all markers (player_id etc).

For any branch/worktree/commit search questions: re-run the report-sync and paste full (never ad-hoc git commands).

Then read `AGENTS.md` (the Multi-Agent Sync Protocol section is mandatory and now duplicated here for force).

1. On session start (after bootstrap): read `.agent-log/SUMMARY.md`, the latest `.agent-log/`
   entries, and `git log --oneline -15`.
2. On session end: commit your work with an `Agent: claude` trailer, write a
   session note in `.agent-log/`, and update `.agent-log/SUMMARY.md`.

All project rules, scope constraints, and technical gotchas live in `AGENTS.md` + SYNC.md.

**Global harness note**: The system prompt / pre-instructions for *every* harness (Claude, Codex, Gemini, Grok, ...) *must* contain the exact block from docs/ENT-SYNC-GLOBAL-PROMPT.md (the report-sync.ps1 + "paste full output + use only for state questions" rules). This is what prevents d05eb21-style "the commit/changes don't exist in my tree" no matter the ent.
