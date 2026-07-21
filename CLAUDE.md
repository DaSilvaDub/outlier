# Claude — start here

You are agent **`claude`** in this repo. Multiple AI agents share this codebase.

**STEP 0 — DO THIS FIRST, BEFORE READING AGENTS.md OR ANY CODE OR LOGS:**
**Canonical path, every ent, every start dir:**

```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```

Runs full bootstrap + SyncAll (now covers ai-runners) + authoritative report (includes explicit d05eb21 section).

Paste the ENTIRE output. Only continue when it shows the "produced by scripts/verify-sync.ps1" header + VALIDATE: OK + MATCH + all markers (player_id etc).

For any branch/worktree/commit search questions: re-run the report-sync and paste full (never ad-hoc git commands).

Then read `AGENTS.md` (the Multi-Agent Sync Protocol section is mandatory and now duplicated here for force).

**HOUSE RULE (all ents):** Never run reasoning models / the AI Research Desk (A–E, `run_desk`, `daily_job --run-reasoning`, live OpenAI/Anthropic/Gemini desk calls, or provider-hitting reasoning tests) unless the user **explicitly asks this turn**. Default offline. If unsure, ask first. Full text: `AGENTS.md` → "Never run reasoning models unless explicitly asked".

1. On session start (after bootstrap): read `.agent-log/HANDOFF.md` to see where the last agent left off, and `git log --oneline -15`.
2. On session end: commit your work to your **feature branch** (see AGENTS.md "Feature Branch Workflow"), push it, open a PR to `master` via `gh pr create`, then write a brief handoff summary to `.agent-log/HANDOFF.md` (last commit, PR link, next steps). Direct commits to `master` are allowed only for coordination files (AGENTS.md, CLAUDE.md, .agent-log/).

All project rules, scope constraints, and technical gotchas live in `AGENTS.md` + SYNC.md.

**Global harness note**: The system prompt / pre-instructions for *every* harness (Claude, Codex, Gemini, Grok, ...) *must* contain the exact block from docs/ENT-SYNC-GLOBAL-PROMPT.md (report-sync.ps1 + "no reasoning unless asked" + paste-full-output rules). This is what prevents d05eb21-style "the commit/changes don't exist in my tree" and accidental paid desk runs no matter the ent.

## Common Commands
- Run tests: `pytest`
- Run ruff linter: `python -m ruff check`
- Run MyPy type checking: `python -m mypy outlier_scrapers` or `make typecheck`
- Run Pyright type checking: `pyright outlier_scrapers`

