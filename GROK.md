# Grok — start here

You are agent `grok` in this repo. Multiple AI agents share this codebase.

**STEP 0 — DO THIS FIRST, BEFORE READING AGENTS.md OR ANY CODE OR LOGS:**
**Use the canonical path no matter the ent or CWD you landed in.**

```powershell
& "C:\Users\dasil\OneDrive\Documents\outlier\report-sync.ps1"
```

(This does bootstrap + -SyncAllWorktrees (including ai-runners full clone) + the complete standardized report with d05eb21 explanation.)

Paste the *entire* output. Proceed only if you see:
- "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)"
- VALIDATE: OK
- State vs origin/master: MATCH
- All upgrade markers present (player_id, round_robin_then_fill, CANDIDATES_HEADER, _acquire_pack_lock, decisions.csv)
- Current HEAD on canonical == origin/master

Then read `AGENTS.md` (the Multi-Agent Sync Protocol section is mandatory and now duplicated here for force).

**HOUSE RULE (all ents):** Never run reasoning models / the AI Research Desk (A–E, `run_desk`, `daily_job --run-reasoning`, live OpenAI/Anthropic/Gemini desk calls, or provider-hitting reasoning tests) unless the user **explicitly asks this turn**. Default offline. If unsure, ask first. Full text: `AGENTS.md` → "Never run reasoning models unless explicitly asked".

On start (after bootstrap): read `.agent-log/SUMMARY.md`, the latest `.agent-log/` entries, and
`git log --oneline -15`.
On end: commit with an `Agent: grok` trailer, write a `.agent-log/` session
note, and update `.agent-log/SUMMARY.md`.

See SYNC.md for the full contract and the exact text block that must live in *every* harness's global instructions (Claude, Codex, Gemini, future ents) so the "invisible commit" problem never recurs no matter the ent. That block now also includes the permanent **no reasoning unless asked** rule.
