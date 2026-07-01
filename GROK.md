# Grok — start here

You are agent `grok` in this repo. Multiple AI agents share this codebase.

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

On start (after bootstrap): read `.agent-log/SUMMARY.md`, the latest `.agent-log/` entries, and
`git log --oneline -15`.
On end: commit with an `Agent: grok` trailer, write a `.agent-log/` session
note, and update `.agent-log/SUMMARY.md`.

See SYNC.md for the full contract and the exact text block that must live in *every* harness's global instructions (Claude, Codex, Gemini, future ents) so the "invisible commit" problem never recurs no matter the ent.
