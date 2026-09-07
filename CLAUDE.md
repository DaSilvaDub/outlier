# Claude — start here

You are agent **`claude`** in this repo. Multiple AI agents share this codebase.

**STEP 0 — DO THIS FIRST, BEFORE READING AGENTS.md OR ANY CODE OR LOGS:**
**Canonical path, every ent, every start dir:**

```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```

Runs full bootstrap + SyncAll (now covers ai-runners) + authoritative report (includes explicit d05eb21 section).

Paste the ENTIRE output, up to and including the final `RUN-NONCE:` line.

**Only continue when the trailer reads `REPORT STATUS: OK`.** That verdict is computed from
what actually ran (`bootstrap=/validate=/state=/markers=/worktrees=/fullclones=`), so a
skipped sync now says `FAILED` and exits non-zero instead of quietly omitting a line. A
missing `RUN-NONCE:` line means the paste was truncated or edited — treat it as invalid.

The individual gates ("produced by scripts/verify-sync.ps1" + VALIDATE: OK + MATCH + all 5
markers) are still shown, but you should not have to hunt for an absent one.

For any branch/worktree/commit search questions: re-run the report-sync and paste full (never ad-hoc git commands).

Cloud/sandbox agents (Linux, no PowerShell): see AGENTS.md "Cloud / Sandbox Agents" for the equivalent procedure.

Then read `AGENTS.md` (the Multi-Agent Sync Protocol section is mandatory and now duplicated here for force).

**HOUSE RULE (all ents):** Never run reasoning models / the AI Research Desk (A–E, `run_desk`, `daily_job --run-reasoning`, live OpenAI/Anthropic/Gemini desk calls, or provider-hitting reasoning tests) unless the user **explicitly asks this turn**. Default offline. If unsure, ask first. Full text: `AGENTS.md` → "Never run reasoning models unless explicitly asked".

**EXPORTED PROMPT HANDOFF:** Route explicitly requested `Desk1_Automated` master prompts to `.agents/skills/analyze-outlier-generic-prompts/SKILL.md` and `Desk2_Manual`/`paste_*.md` prompts to `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`. Generic reports go under `BETTING REPORTS\GENERIC`; sequential Q → R → W → X → S reports go under `BETTING REPORTS\SEQUENTIAL`. Use Claude's native reasoning. File appearance alone does not authorize reasoning.

1. On session start (after bootstrap): read `.agent-log/HANDOFF.md` to see where the last agent left off, and `git log --oneline -15`.
2. On session end: commit your work to your **feature branch** (see AGENTS.md "Feature Branch Workflow"), push it, open a PR to `master` via `gh pr create`, then write a brief handoff summary to `.agent-log/HANDOFF.md` (last commit, PR link, next steps). Direct commits to `master` are allowed only for coordination files (AGENTS.md, CLAUDE.md, .agent-log/).

All project rules, scope constraints, and technical gotchas live in `AGENTS.md` + SYNC.md.

**Global harness note**: The system prompt / pre-instructions for *every* harness (Claude, Codex, Gemini, Grok, ...) *must* contain the exact block from docs/ENT-SYNC-GLOBAL-PROMPT.md (report-sync.ps1 + "no reasoning unless asked" + paste-full-output rules). This is what prevents d05eb21-style "the commit/changes don't exist in my tree" and accidental paid desk runs no matter the ent.

## Common Commands
- Run tests: `pytest`
- Run ruff linter: `python -m ruff check`
- Run MyPy type checking: `python -m mypy outlier_scrapers` or `make typecheck`
- Run Pyright type checking: `pyright outlier_scrapers`

HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:
1. MLB Player Prop Allowed Markets (strict whitelist, full-game only; canonical codes in normalizer ALLOWED_MLB_PLAYER_PROPS): Pitcher Strikeouts (SO) only. Hits (H), Total Bases (TB), Outs (OUTS), Doubles (2B), HRR (Hits+Runs+RBIs), Earned Runs (ER), Batting Walks (BB), batter strikeouts (BSO), and every other player prop (including HR, HA/Hits Allowed, RBI, 1B/Singles, 3B/Triples, BF/Batters Faced, PT/Pitches Thrown, BBA/Walks Allowed) are dropped at generation.
2. MLB Team Props (strict whitelist, full-game only; ALLOWED_MLB_TEAM_PROPS): Runs (R) and Total (TOTAL) only — team run totals. Hits (H), Strikeouts (SO), Walks (BB), and all other TEAM_PROP markets are dropped. Game lines (GAMELINE: Moneyline, Spread, Game Total) stay in the games feed — not subject to prop whitelists.
3. Side Restrictions: MLB alt player props are pitcher SO OVER only and must be parlayed across different games. MLB alt game/team totals are OVER runs only and must be parlayed across different games. Home Runs (HR) remain pack-excluded entirely (desk hard ban via EXCLUDED_MARKETS).
5. Candidate Actionable & Identity Invariants: actionable is "true" ONLY if board == "A", units > 0, edge_pct > 0, and no suspect/disqualifying flags exist. Rows with suspect flags or blank units are actionable = "false" and board = "A_FLAGGED". build_row() must auto-infer missing team/opponent from matchup.
6. Drive/OneDrive File Copy Resiliency: organize_today_run2.py must use safe_copy() retry loops to handle transient cloud sync file locks ([WinError 32]).
7. Totals & Alternate Team Totals Invariants: is_team_total_record() matches sport-specific scoring tokens ("RUNS", "R", "TOTAL_RUNS", "GOALS", "POINTS", "TOTAL", "TEAM_TOTAL"). build_alt_team_totals() exports alternate lines to alt_team_totals.csv, tests/test_game_totals.py verifies multi-sport eligibility, and generate_prompts.py loads prompts/Totals_Analysis.md (falling back to A.md if missing).
8. Daily Structural Integrity Audit: (a) Non-whitelisted MLB player/team props strictly excluded at generation (ALLOWED_MLB_PLAYER_PROPS / ALLOWED_MLB_TEAM_PROPS); pack also hard-excludes HR and Walks Allowed tokens; (b) Scheduled slate games in context.events never receive UNINDEXED_SLATE_GAME; (c) Non-actionable rows (actionable=false) clear recommended_units_pre_news to ""; (d) Briefing totals deduplicated against candidate market IDs; (e) Integer totals derived via +0.5/-0.5 ladder lines or flagged push_capable_no_prob.
9. Whitelisted ≠ Recommendable (generation scope vs. desk-recommendation scope; ruled 2026-08-12, see docs/plans/2026-08-12-structured-ai-verdicts.md): ALLOWED_MLB_PLAYER_PROPS / ALLOWED_MLB_TEAM_PROPS govern what enters a pack at generation only — they do not by themselves make a market desk-recommendable. Pitcher SO, game totals, and team run totals are the only MLB prop families this pipeline is built to recommend. If a stale HR, HRR, or player BB row appears, the desk may never BET it.
10. Structured desk verdicts (see docs/plans/2026-08-12-structured-ai-verdicts.md): PackIndex is the sole pack truth for the gate. Readers outside the publish path resolve `packs/<date>/verdicts/desk_snapshot.json`, never a pass-level `current.json`. `verdicts/` is never named in `DERIVED_PACK_OUTPUTS` and must not be `unlink()`ed on rebuild. No-E fallback requires A+D+B quorum; unanimous BET uses min stake and is labeled `synthesis_source: fallback_no_e`. `verdict_records` is keyed by `(decision_id, pass, publication_id, record_id)`. Legacy `C_verdict` is CONTRADICTS > CONFIRMS > NEUTRAL, else empty. Never invoke paid/live reasoning unless the user explicitly asks this turn.