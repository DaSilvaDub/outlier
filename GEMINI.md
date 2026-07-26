# Outlier multi-ent global instructions (auto-installed from docs/ENT-SYNC-GLOBAL-PROMPT.md)

**Scope:** Apply fully when the working tree is the outlier repo
(`C:\Users\dasil\Dev\GitHub\outlier` or any clone/worktree of
https://github.com/DaSilvaDub/outlier.git). Outside that repo, ignore STEP 0 /
report-sync; still obey the reasoning-off default if you are about to invoke
`outlier_scrapers.reasoning|run_desk|...` by accident.

Source of truth in-repo: `docs/ENT-SYNC-GLOBAL-PROMPT.md` (last synced 2026-07-12).

---
You are working in the outlier repo (https://github.com/DaSilvaDub/outlier.git). MULTI-ENT SYNC IS MANDATORY.

HOUSE RULE — NEVER RUN REASONING MODELS UNLESS THE USER EXPLICITLY ASKS THIS TURN:
Default OFF. Do not invoke the AI Research Desk / paid reasoning layer unless the user clearly requests it (e.g. "run the desk", "run reasoning", "run A/B/C/D/E").
Forbidden without that ask: python -m outlier_scrapers.reasoning | gemini_research | c_research | claude_reasoning | claude_synthesis | run_desk; daily_job --run-reasoning; the outlier-ai-desk skill; live OpenAI/Anthropic/Gemini desk calls; pytest/network paths that hit live reasoning providers (test_reasoning, test_gemini_research, test_claude_reasoning, test_claude_synthesis, test_c_research when not fully offline-mocked).
Allowed without asking: pack/scrapers, offline unit tests, code/docs/git work, reading existing pack artifacts.
If unsure whether the user asked: do NOT run reasoning — ask first.
Full rule text: AGENTS.md section "HOUSE RULE — Never run reasoning models unless explicitly asked".

EXPORTED PROMPT HANDOFF (all ents):
When the user explicitly asks in the current turn to analyze a generated Outlier prompt, read and follow `C:\Users\dasil\Dev\GitHub\outlier\.agents\skills\analyze-outlier-prompt\SKILL.md`. Use Gemini's native reasoning, then save the provenance-labeled report in `C:\Users\dasil\OneDrive\Desktop\BETTING REPORTS`. A prompt appearing on disk does not itself authorize reasoning.

STEP 0 (ABSOLUTE FIRST ACTION — before reading any files, git log, planning, or running commands):

NO MATTER which ent (Grok/Claude/Codex/Gemini), which starting CWD (worktree, ai-runners full clone, or stray dir), or how the harness launches you:

ALWAYS execute EXACTLY (full canonical path; do not use a local ./report-sync or relative path):

& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"

# Thin wrapper -> canonical bootstrap + -SyncAllWorktrees (which now also hard-resets ai-runners) + full verify report.
# Never pipe | Select-String | Out-String | Select -First | grep etc. The guard will flag it.

cd 'C:\Users\dasil\Dev\GitHub\outlier' first if you like, then the command above.
Report the *full* untruncated console output (every line from [sync] through the final Rule).

You may only continue when the computed trailer reads:
- "REPORT STATUS: OK"   (anything else, or a non-zero exit, means NOT a valid attestation)

and the paste ends with the per-run "RUN-NONCE:" line (missing it = truncated/edited).

Underlying gates, still printed:
- "VALIDATE: OK"
- "State vs origin/master: MATCH"
- All 5 upgrade markers (player_id, round_robin_then_fill, CANDIDATES_HEADER in pack.py;
  _acquire_pack_lock in daily_job.py; decisions.csv)
- Current canonical HEAD matches origin/master
- "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)."

For ANY question about "does commit X exist", "I searched every branch + .codex/.gemini worktree", "closest commit is d756cb4", "d05eb21 not found", sizes, or state:
- Run & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"  (or the scripts/verify... directly)
- Paste its ENTIRE output.
- NEVER build your own report with git log / rev-parse / Get-ChildItem / Select-String loops. Those are why the original problem existed.

After any -SyncAllWorktrees you (or another ent) run, immediately follow with the full report-sync and paste it.

See checked-in SYNC.md, AGENTS.md, and docs/ENT-SYNC-GLOBAL-PROMPT.md.

This rule is what makes d05eb21-style "the commit and the pack.py changes are invisible to me" impossible across Grok/Claude/Codex/Gemini/...

