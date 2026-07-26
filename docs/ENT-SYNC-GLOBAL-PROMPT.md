# Multi-Ent Sync Global Prompt Snippet (for EVERY harness)

Copy-paste the block below **verbatim** into the *global / system / pre-instructions* for this repo in every AI harness you use (Claude Code project settings, Codex rules, Grok custom instructions, Gemini/antigravity workspace prompt, future ents). 

This is the mechanism that guarantees no matter which ent, the agent sees identical pack.py/daily_job.py and history.

```
You are working in the outlier repo (https://github.com/DaSilvaDub/outlier.git). MULTI-ENT SYNC IS MANDATORY.

HOUSE RULE — NEVER RUN REASONING MODELS UNLESS THE USER EXPLICITLY ASKS THIS TURN:
Default OFF. Do not invoke the AI Research Desk / paid reasoning layer unless the user clearly requests it (e.g. "run the desk", "run reasoning", "run A/B/C/D/E").
Forbidden without that ask: python -m outlier_scrapers.reasoning | gemini_research | c_research | claude_reasoning | claude_synthesis | run_desk; daily_job --run-reasoning; the outlier-ai-desk skill; live OpenAI/Anthropic/Gemini desk calls; pytest/network paths that hit live reasoning providers (test_reasoning, test_gemini_research, test_claude_reasoning, test_claude_synthesis, test_c_research when not fully offline-mocked).
Allowed without asking: pack/scrapers, offline unit tests, code/docs/git work, reading existing pack artifacts.
If unsure whether the user asked: do NOT run reasoning — ask first.
Full rule text: AGENTS.md section "HOUSE RULE — Never run reasoning models unless explicitly asked".

EXPORTED PROMPT HANDOFF (all ents):
When the user explicitly asks in the current turn to analyze generated Outlier prompts, route `Desk1_Automated` master prompts to `C:\Users\dasil\Dev\GitHub\outlier\.agents\skills\analyze-outlier-generic-prompts\SKILL.md` and save under `C:\Users\dasil\OneDrive\Desktop\BETTING REPORTS\GENERIC\YYYY-MM-DD`. Route `Desk2_Manual` or `paste_*.md` prompts to `C:\Users\dasil\Dev\GitHub\outlier\.agents\skills\analyze-outlier-sequential-prompts\SKILL.md`, enforce Q → R → W → X → S, and save under `C:\Users\dasil\OneDrive\Desktop\BETTING REPORTS\SEQUENTIAL\YYYY-MM-DD`. Use the current agent's native model. File appearance alone does not authorize reasoning.

STEP 0 (ABSOLUTE FIRST ACTION — before reading any files, git log, planning, or running commands):

NO MATTER which ent (Grok/Claude/Codex/Gemini), which starting CWD (worktree, ai-runners full clone, or stray dir), or how the harness launches you:

ALWAYS execute EXACTLY (full canonical path; do not use a local ./report-sync or relative path):

& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"

# Thin wrapper -> canonical bootstrap + -SyncAllWorktrees (which now also hard-resets ai-runners) + full verify report.
# Never pipe | Select-String | Out-String | Select -First | grep etc. Paste it whole.

cwd no longer matters — the script targets canonical explicitly and refuses loudly if that
path is not a readable git repo. Report the *full* untruncated console output, through the
final RUN-NONCE line.

You may only continue when the trailer reads:
- "REPORT STATUS: OK"

That verdict is computed, and is the only thing you need to check:

```
REPORT STATUS: OK
  bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=2/2  fullclones=3/3 (+1 unreadable)

RUN-NONCE: 2bf030bbd1b24567  utc=2026-07-26T03:28:39Z  head=1d701f1  status=OK
```

- "REPORT STATUS: FAILED", or a non-zero exit code, means NOT a valid sync attestation.
  The "failures:" list names the cause. Do not quote a FAILED report as proof of state.
- The paste must END with "RUN-NONCE:". Missing it = truncated/edited = invalid.
- Underlying gates still printed: "VALIDATE: OK", "State vs origin/master: MATCH",
  all 5 upgrade markers (player_id, round_robin_then_fill, CANDIDATES_HEADER in pack.py;
  _acquire_pack_lock in daily_job.py; decisions.csv), canonical HEAD == origin/master,
  and "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)."

For ANY question about "does commit X exist", "I searched every branch + .codex/.gemini worktree", "closest commit is d756cb4", "d05eb21 not found", sizes, or state:
- Run & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"  (or the scripts/verify... directly)
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

## Installed locations (machine: dasil, refreshed 2026-07-26)

The fenced block above was installed (or refreshed) into every harness global surface on this machine:

| Harness | Global instruction file |
|---------|-------------------------|
| Grok | `C:\Users\dasil\.grok\AGENTS.md` |
| Claude Code | `C:\Users\dasil\.claude\CLAUDE.md` |
| Codex | `C:\Users\dasil\.codex\AGENTS.md` (outlier section prepended) + `C:\Users\dasil\AGENTS.md` (appended section) |
| Gemini / Antigravity | `C:\Users\dasil\.gemini\GEMINI.md` + `C:\Users\dasil\.gemini\AGENTS.md` |
| Repo project files | `AGENTS.md`, `CLAUDE.md`, `GROK.md`, `GEMINI.md` (in-repo) |

**Enforcement hook (Claude/Grok hookify):**  
`~/.claude/hookify.no-reasoning-unless-asked.local.md` and  
`outlier/.claude/hookify.no-reasoning-unless-asked.local.md` — warns on bash that would invoke A–E / `run_desk` / `--run-reasoning` / provider-hitting reasoning tests.

When the fenced block in this file changes, re-run the install (or re-paste) into every path in the table above.

**2026-07-26 refresh:** re-installed after PR #61 (REPORT STATUS/RUN-NONCE verdict,
skipped-bootstrap-is-fatal, FORBIDDEN banner replaced). While refreshing, found that
`.codex\AGENTS.md` and both `.gemini\*.md` files had drifted onto
`C:\Users\dasil\OneDrive\Documents\outlier` instead of the canonical path — that
mirror's `.git` is a OneDrive placeholder git cannot open, which is precisely the
condition PR #61 makes report-sync.ps1 refuse loudly rather than silently no-op on.
Corrected to canonical in all three during this refresh.

## Why this exists

The d05eb21 (~624 line pipeline upgrade touching pack.py + daily_job.py) was a transient review tree that never got pushed to origin or other worktrees. Later materialization (88083ff+) + these scripts + enforced bootstrap = synchronized state for all ents.

Run `& "C:\Users\dasil\Dev\GitHub\outlier\scripts\verify-sync.ps1"` yourself to see the live authoritative cross-worktree report (lists all ~16 worktrees + marker status + explicit d05eb21 explanation).

Last updated: 2026-07-26 (shared exported-prompt report handoff; REPORT STATUS/RUN-NONCE verdict from PR #61; re-installed
into all 6 harness global files and corrected 3 that had drifted to the unreadable
OneDrive mirror path). Previously: 2026-07-12 (added permanent "no reasoning models
unless asked" house rule).

HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:
1. MLB Player Prop Allowed Markets: Home Runs (Over-only), Strikeouts, Hits, Total Bases, HRR, Hits Allowed, Earned Runs, Outs, RBIs, Singles/Doubles/Triples, Batting Walks, Batters Faced, Pitches Thrown.
2. MLB Team Props: All 18 team props allowed.
3. Side Restrictions: Home Runs and "To Record X..." milestone lines are Over-only. Drop Home Runs Under and milestone Under lines.
5. Candidate Actionable & Identity Invariants: actionable is "true" ONLY if board == "A", units > 0, edge_pct > 0, and no suspect/disqualifying flags exist. Rows with suspect flags or blank units are actionable = "false" and board = "A_FLAGGED". build_row() must auto-infer missing team/opponent from matchup.
6. Drive/OneDrive File Copy Resiliency: organize_today_run2.py must use safe_copy() retry loops to handle transient cloud sync file locks ([WinError 32]).
7. Totals & Alternate Team Totals Invariants: is_team_total_record() matches sport-specific scoring tokens ("RUNS", "R", "TOTAL_RUNS", "GOALS", "POINTS", "TOTAL", "TEAM_TOTAL"). build_alt_team_totals() exports alternate lines to alt_team_totals.csv, tests/test_game_totals.py verifies multi-sport eligibility, and generate_prompts.py loads prompts/Totals_Analysis.md (falling back to A.md if missing).
8. Daily Structural Integrity Audit: (a) Prohibited markets (Walks Allowed, Total Bases, Hits Allowed) strictly excluded at generation; (b) Scheduled slate games in context.events never receive UNINDEXED_SLATE_GAME; (c) Non-actionable rows (actionable=false) clear recommended_units_pre_news to ""; (d) Briefing totals deduplicated against candidate market IDs; (e) Integer totals derived via +0.5/-0.5 ladder lines or flagged push_capable_no_prob.