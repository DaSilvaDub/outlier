# Grok — start here

You are agent `grok` in this repo. Multiple AI agents share this codebase.

**STEP 0 — DO THIS FIRST, BEFORE READING AGENTS.md OR ANY CODE OR LOGS:**
**Use the canonical path no matter the ent or CWD you landed in.**

```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```

(This does bootstrap + -SyncAllWorktrees (including ai-runners full clone) + the complete standardized report with d05eb21 explanation.)

Paste the *entire* output, through the final "RUN-NONCE:" line. Proceed only if:
- The trailer reads "REPORT STATUS: OK" (computed; FAILED also exits non-zero)
- The paste ends with "RUN-NONCE:" (missing it = truncated/edited = invalid)

Underlying gates, still printed:
- "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)"
- VALIDATE: OK
- State vs origin/master: MATCH
- All 5 upgrade markers (player_id, round_robin_then_fill, CANDIDATES_HEADER in pack.py;
  _acquire_pack_lock in daily_job.py; decisions.csv)
- Current HEAD on canonical == origin/master

Then read `AGENTS.md` (the Multi-Agent Sync Protocol section is mandatory and now duplicated here for force).

**HOUSE RULE (all ents):** Never run reasoning models / the AI Research Desk (A–E, `run_desk`, `daily_job --run-reasoning`, live OpenAI/Anthropic/Gemini desk calls, or provider-hitting reasoning tests) unless the user **explicitly asks this turn**. Default offline. If unsure, ask first. Full text: `AGENTS.md` → "Never run reasoning models unless explicitly asked".

**EXPORTED PROMPT HANDOFF:** Route explicitly requested `Desk1_Automated` master prompts to `.agents/skills/analyze-outlier-generic-prompts/SKILL.md` and `Desk2_Manual`/`paste_*.md` prompts to `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`. Generic reports go under `BETTING REPORTS\GENERIC`; sequential Q → R → W → X → S reports go under `BETTING REPORTS\SEQUENTIAL`. Use Grok's native reasoning. File appearance alone does not authorize reasoning.

On start (after bootstrap): read `.agent-log/SUMMARY.md`, the latest `.agent-log/` entries, and
`git log --oneline -15`.
On end: commit with an `Agent: grok` trailer, write a `.agent-log/` session
note, and update `.agent-log/SUMMARY.md`.

See SYNC.md for the full contract and the exact text block that must live in *every* harness's global instructions (Claude, Codex, Gemini, future ents) so the "invisible commit" problem never recurs no matter the ent. That block now also includes the permanent **no reasoning unless asked** rule.

HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:
1. MLB Player Prop Allowed Markets: Home Runs (Over-only), Strikeouts, Hits, Total Bases, HRR, Hits Allowed, Earned Runs, Outs, RBIs, Singles/Doubles/Triples, Batting Walks, Batters Faced, Pitches Thrown.
2. MLB Team Props: All 18 team props allowed.
3. Side Restrictions: Home Runs and "To Record X..." milestone lines are Over-only. Drop Home Runs Under and milestone Under lines.
5. Candidate Actionable & Identity Invariants: actionable is "true" ONLY if board == "A", units > 0, edge_pct > 0, and no suspect/disqualifying flags exist. Rows with suspect flags or blank units are actionable = "false" and board = "A_FLAGGED". build_row() must auto-infer missing team/opponent from matchup.
6. Drive/OneDrive File Copy Resiliency: organize_today_run2.py must use safe_copy() retry loops to handle transient cloud sync file locks ([WinError 32]).
7. Totals & Alternate Team Totals Invariants: is_team_total_record() matches sport-specific scoring tokens ("RUNS", "GOALS", "POINTS", "TOTAL"). build_alt_team_totals() exports alternate lines to alt_team_totals.csv, and generate_prompts.py builds 5 Desk 3 Totals prompt files using prompts/Totals_Analysis.md.
8. Daily Structural Integrity Audit: (a) Prohibited markets (Walks Allowed, Total Bases, Hits Allowed) strictly excluded at generation; (b) Scheduled slate games in context.events never receive UNINDEXED_SLATE_GAME; (c) Non-actionable rows (actionable=false) clear recommended_units_pre_news to ""; (d) Briefing totals deduplicated against candidate market IDs; (e) Integer totals derived via +0.5/-0.5 ladder lines or flagged push_capable_no_prob.