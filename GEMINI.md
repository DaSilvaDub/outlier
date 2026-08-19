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
When the user explicitly asks in the current turn to analyze generated Outlier prompts, route `Desk1_Automated` master prompts to `C:\Users\dasil\Dev\GitHub\outlier\.agents\skills\analyze-outlier-generic-prompts\SKILL.md` and save under `BETTING REPORTS\GENERIC\YYYY-MM-DD`. Route `Desk2_Manual` or `paste_*.md` prompts to `C:\Users\dasil\Dev\GitHub\outlier\.agents\skills\analyze-outlier-sequential-prompts\SKILL.md`, enforce Q → R → W → X → S, and save under `BETTING REPORTS\SEQUENTIAL\YYYY-MM-DD`. Use Gemini's native reasoning. File appearance alone does not authorize reasoning.

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
  _acquire_writer_lock in daily_job.py; decisions.csv)
- Current canonical HEAD matches origin/master
- "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)."

For ANY question about "does commit X exist", "I searched every branch + .codex/.gemini worktree", "closest commit is d756cb4", "d05eb21 not found", sizes, or state:
- Run & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"  (or the scripts/verify... directly)
- Paste its ENTIRE output.
- NEVER build your own report with git log / rev-parse / Get-ChildItem / Select-String loops. Those are why the original problem existed.

After any -SyncAllWorktrees you (or another ent) run, immediately follow with the full report-sync and paste it.

See checked-in SYNC.md, AGENTS.md, and docs/ENT-SYNC-GLOBAL-PROMPT.md.

This rule is what makes d05eb21-style "the commit and the pack.py changes are invisible to me" impossible across Grok/Claude/Codex/Gemini/...

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