# Multi-Agent Sync Protocol (MANDATORY FOR ALL AGENTS / ENTS)

**CRITICAL RULE**: No matter which harness ("ent") — Grok, Claude, Codex, Gemini/antigravity, or future — **every session starts by forcing identical state from GitHub + canonical**.

## HOUSE RULE — Never run reasoning models unless explicitly asked (ALL ENTS)

**Default: OFF.** Do **not** invoke the AI Research Desk / paid reasoning layer unless the user **explicitly** asks in this turn (e.g. "run the desk", "run reasoning", "run A/B/C/D/E", "call OpenAI/Gemini/Claude for the pack").

**Forbidden without that explicit ask:**
- `python -m outlier_scrapers.reasoning` (Prompt A / OpenAI)
- `python -m outlier_scrapers.gemini_research` (Prompt B)
- `python -m outlier_scrapers.c_research` (Prompt C)
- `python -m outlier_scrapers.claude_reasoning` (Prompt D)
- `python -m outlier_scrapers.claude_synthesis` (Prompt E)
- `python -m outlier_scrapers.run_desk` (full desk orchestrator)
- `python -m outlier_scrapers.daily_job --run-reasoning` (or any flag that triggers A–E)
- The `outlier-ai-desk` skill / any agent that would fire the above
- Live provider API calls for desk reasoning (OpenAI / Anthropic / Gemini grounded research)
- `pytest` (or other runners) that hit live reasoning providers or hang on network (e.g. `tests/test_reasoning.py`, `test_gemini_research.py`, `test_claude_reasoning.py`, `test_claude_synthesis.py`, `test_c_research.py` when they can leave the mock path)

**Allowed without asking:** pack build, scrapers, local unit tests that stay offline/mocked, code edits, PR/git work, docs, reading existing pack/report artifacts on disk.

**If unsure whether the user asked:** do **not** run reasoning — ask first. Cost, hangs, and quota burn are real.

This rule is duplicated in `docs/ENT-SYNC-GLOBAL-PROMPT.md` (harness system-prompt block), `CLAUDE.md`, `GROK.md`, and the desk skill. Keep them in sync when changing the rule.

## EXPORTED PROMPT HANDOFF (ALL ENTS)

When the user explicitly asks in the current turn to analyze generated Outlier prompts, route `Desk1_Automated` master prompts to `.agents/skills/analyze-outlier-generic-prompts/SKILL.md` and save under `BETTING REPORTS\GENERIC\YYYY-MM-DD`. Route `Desk2_Manual` or `paste_*.md` prompts to `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`, enforce Q → R → W → X → S, and save under `BETTING REPORTS\SEQUENTIAL\YYYY-MM-DD`. Use the current agent's native model. A prompt appearing on disk does not itself authorize reasoning.

### Mechanical enforcement (Claude Code) — and how to legitimately run the desk

For the Claude ent this rule is no longer prose-only. `.claude/settings.json` registers a
`PreToolUse` hook on `Bash|PowerShell` — [`.claude/hooks/block-reasoning.ps1`](.claude/hooks/block-reasoning.ps1) —
that **denies** the commands listed above instead of merely warning. It supersedes the old
`.claude/hookify.no-reasoning-unless-asked.local.md`, whose defect was `action: warn` —
advisory only, nothing actually stopped a paid command — and which also lacked `PowerShell`
and `run_desk2` coverage.

(That rule *is* distributed, contrary to what an earlier version of this section claimed:
`.gitignore:43` `.claude/*.local.md` matches it, but the file was already tracked when
`b070caa` added that rule, and `.gitignore` never untracks. So it reaches every ent and every
fresh clone — it just never blocked anything. It is now redundant with the deny hook; if you
want `b070caa`'s intent to actually take effect, it needs `git rm --cached`, as was done for
the sqlite file in #55.)

**When the user HAS explicitly asked this turn, append the token `DESK_OK` to the command:**

```powershell
python -m outlier_scrapers.run_desk   # DESK_OK
```

Without that token the command is refused, so do not silently retry a blocked desk command —
either the user asked (add the token) or they did not (do something offline instead, or ask).
Every block and every bypass is appended to `~/.claude/reasoning-guard.log`, so an
unexplained bypass is auditable after the fact.

A second hook, [`.claude/hooks/check-sync.ps1`](.claude/hooks/check-sync.ps1), runs on
`SessionStart` and verifies sync state **read-only** (no fetch-reset, no `-SyncAllWorktrees`).
It does not replace STEP 0 below — it tells you when STEP 0 is needed, and in particular it
catches being launched from a directory git cannot read, where a sync silently no-ops while
still printing a `MATCH` line. See [`.claude/hooks/README.md`](.claude/hooks/README.md).

d05eb21 (the isolated "pipeline upgrade counter-proposal" with ~624 lines to pack.py + daily_job.py, player_id/CANDIDATES_HEADER etc) only existed in one stray full clone at one time. It was invisible on GitHub, other branches, .codex worktrees, and .gemini worktrees until explicitly materialized (88083ff) and bootstrapped. Similar-titled commits (d756cb4/4eea080) touched different files (c_research/run_desk) and are not the pack upgrade. We never want "commit not found in my tree" again no matter the ent.

## STEP 0 — ABSOLUTE FIRST ACTION (before any read, log, plan, or edit)
**No matter the ent or starting directory** (ai-runners full clone, a .codex worktree at 90ca8c3, gemini tree, or canonical):

```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```

Use the canonical absolute path always. This runs bootstrap + -SyncAllWorktrees (which now hard-aligns ai-runners too) + produces the complete standardized report.

- Paste the ENTIRE output.
- **The single line that matters is the trailer: `REPORT STATUS: OK`.** It is computed from
  what actually executed, so you no longer have to notice that an expected line is *absent*:

  ```
  REPORT STATUS: OK
    bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=2/2  fullclones=3/3 (+1 unreadable)

  RUN-NONCE: 2bf030bbd1b24567  utc=2026-07-26T03:28:39Z  head=1d701f1  status=OK
  ```

  `REPORT STATUS: FAILED` (or a non-zero exit) means the report is **not** a valid sync
  attestation — do not quote it as proof of state. The `failures:` list names the cause.
- The paste must END with the `RUN-NONCE:` line. No nonce = truncated or edited = not valid.
  The nonce is per-run, so a re-pasted old nonce is detectable against its `utc`/`head`.
- The individual gates are still printed and still meaningful: VALIDATE: OK,
  State vs origin/master: MATCH, all 5 upgrade markers (player_id, round_robin_then_fill,
  CANDIDATES_HEADER in pack.py; _acquire_writer_lock in daily_job.py; decisions.csv), and
  "This report was produced by scripts/verify-sync.ps1 (never ad-hoc)".
- For any "searched every... d05eb21 not found" question the *only* acceptable answer is a fresh full paste of the above command.

After you or any other ent executes -SyncAllWorktrees, immediately run the report-sync command again and share the full result.

See SYNC.md and docs/ENT-SYNC-GLOBAL-PROMPT.md. The global prompt template there must be the prefix for *every* harness.

## For new worktrees / new ents / cloud sessions
- Create worktrees only from canonical: `git worktree add ...`
- Fresh clones (remote ents): `git clone https://github.com/DaSilvaDub/outlier.git` then immediately run the bootstrap line above (it forces the GitHub remote and materializes).
- Never start an agent session on a path that has not just been bootstrapped.

## End of Session & Handoff Protocol
Agents must ensure the worktree is completely clean before ending their session (excluding the single log generated by the post-commit hook for the current HEAD). Do not track large blobs or .tmp.driveupload/.

**MANDATORY**: Before ending your session, you MUST write a brief handoff summary to `.agent-log/HANDOFF.md`. This replaces the old "paste the whole session" review pattern. 
The handoff file must include:
1. **Last Commit SHA**: The exact commit you ended on.
2. **Files Touched**: A brief list of the main files modified.
3. **Next Steps**: What the next agent should pick up (or open questions/blockers).

See SYNC.md for full contract, OneDrive notes, "if d05eb21-like problem recurs" steps, and the global prompt template you must paste into every harness's system instructions.

### Tracking coordination files that live under an ignored path
`.agent-log/` is listed in `.gitignore` (to keep noisy per-session logs out of history), but files this protocol mandates you commit — `.agent-log/HANDOFF.md` and any other required coordination file — still need to make it into git. A plain `git add .agent-log/HANDOFF.md` will be rejected with "paths are ignored". Use `git add -f .agent-log/HANDOFF.md` (or the specific file) to force-stage it.

## Feature Branch Workflow (MANDATORY for all non-trivial work)

**Never commit new feature or fix work directly to `master`.** Direct-to-master is reserved for infra-only changes (AGENTS.md, CLAUDE.md, .agent-log/).

### Starting a session
After bootstrapping and reading HANDOFF.md, create a branch before touching any code:

```powershell
git checkout -b fix/<descriptive-slug>   # for bug fixes
git checkout -b feat/<descriptive-slug>  # for new features
```

Slugs should be short and lowercase-hyphenated, e.g. `fix/lm-403-retry`, `feat/prop-longshot-filter`.

### Ending a session
1. Commit all work to your feature branch with an `Agent: <name>` trailer.
2. Push the branch: `git push -u origin <branch-name>`
3. Open a PR to `master`: `gh pr create --base master --title "..." --body "..."`
4. Write your handoff to `.agent-log/HANDOFF.md` (last commit, PR link, next steps).

### Infra-only exceptions
Changes to AGENTS.md, CLAUDE.md, GROK.md, .githooks/, .agent-log/, or report-sync.ps1 may be committed directly to master — these are coordination files, not product code.

HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:
1. MLB Player Prop Allowed Markets (strict whitelist, full-game only; canonical codes in normalizer ALLOWED_MLB_PLAYER_PROPS): Strikeouts (SO), Hits (H), Total Bases (TB), Outs (OUTS), Doubles (2B), HRR (Hits+Runs+RBIs), Earned Runs (ER), Batting Walks (BB). Any other player prop (including HR, HA/Hits Allowed, RBI, 1B/Singles, 3B/Triples, BF/Batters Faced, PT/Pitches Thrown, BBA/Walks Allowed) is dropped at generation.
2. MLB Team Props (strict whitelist, full-game only; ALLOWED_MLB_TEAM_PROPS): Hits (H), Strikeouts (SO), Walks (BB), Runs (R), Total (TOTAL). All other TEAM_PROP markets are dropped. Game lines (GAMELINE: Moneyline, Spread, Game Total) are always preserved — not subject to prop whitelists.
3. Side Restrictions: Doubles (2B) are UNDER-only — drop 2B OVER at generation. Home Runs (HR) are pack-excluded entirely (desk hard ban via EXCLUDED_MARKETS).
5. Candidate Actionable & Identity Invariants: actionable is "true" ONLY if board == "A", units > 0, edge_pct > 0, and no suspect/disqualifying flags exist. Rows with suspect flags or blank units are actionable = "false" and board = "A_FLAGGED". build_row() must auto-infer missing team/opponent from matchup.
6. Drive/OneDrive File Copy Resiliency: organize_today_run2.py must use safe_copy() retry loops to handle transient cloud sync file locks ([WinError 32]).
7. Totals & Alternate Team Totals Invariants: is_team_total_record() matches sport-specific scoring tokens ("RUNS", "R", "TOTAL_RUNS", "GOALS", "POINTS", "TOTAL", "TEAM_TOTAL"). build_alt_team_totals() exports alternate lines to alt_team_totals.csv, tests/test_game_totals.py verifies multi-sport eligibility, and generate_prompts.py loads prompts/Totals_Analysis.md (falling back to A.md if missing).
8. Daily Structural Integrity Audit: (a) Non-whitelisted MLB player/team props strictly excluded at generation (ALLOWED_MLB_PLAYER_PROPS / ALLOWED_MLB_TEAM_PROPS); pack also hard-excludes HR and Walks Allowed tokens; (b) Scheduled slate games in context.events never receive UNINDEXED_SLATE_GAME; (c) Non-actionable rows (actionable=false) clear recommended_units_pre_news to ""; (d) Briefing totals deduplicated against candidate market IDs; (e) Integer totals derived via +0.5/-0.5 ladder lines or flagged push_capable_no_prob.