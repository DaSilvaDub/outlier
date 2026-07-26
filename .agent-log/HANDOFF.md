# Handoff Summary

## PR #40 independent projection layer

**Last Commit SHA**: `d194ff9898606439677a45b363d0f92eec26a841`

**Files Touched**:
- `outlier_scrapers/normalizer.py`
- `outlier_scrapers/pack.py`
- `outlier_scrapers/projections.py`
- `tests/test_normalizer.py`
- `tests/test_pack.py`
- `tests/test_projections.py`

**Summary of Work**:
- Rebuilt the contaminated PR #40 branch from current `master` as a six-file projection-only diff.
- Resolved tail-mass, first-inning token/side, non-MLB cardinality, and shadow-pack integration blockers.
- Squash-merged PR #40 after hosted tests, typecheck, and Codacy passed.

**Next Steps**:
- Continue the independent projection plan with cached MLB feature/provider adapters.

---

**Last Commit SHA**: `4fe871d0760315167579cfd89c23bad88690b3e7` (plus this handoff commit)

**Files Touched**:
- `outlier_scrapers/utils.py` (NEW)
- `outlier_scrapers/alt_team_totals.py`
- `outlier_scrapers/feedback.py`
- `outlier_scrapers/game_totals.py`
- `outlier_scrapers/pack.py`
- `tests/test_pack.py` (indirectly affected by imports)

**Summary of Work**:
- Completed merging PR #39 after resolving the remaining AI reviewer comments.
- Extracted common utilities (`_write_csv`, `_local_date`, `drop_locked_events`, `_american_to_decimal`, `_decimal_to_american`, and `_price_text`) into a new `utils.py` module to eliminate logic duplication and break the circular import dependency between `alt_team_totals.py` and `pack.py`.
- Formatted the positive American odds strings to always include the `+` prefix correctly.
- Left the "naive" SGP probability calculation mathematically independent but explicitly tagged it as `(uncorrelated)` on the UI markdown to provide transparency to users.
- Pruned stale git worktrees and correctly merged everything down to `master`.

**Next Steps**:
- The user has been doing bottom-up merges of PRs (started with #23, then #34, #36, and now #39). The next agent should await the user's instructions for the next task (likely the next PR in the queue).

---

## PR #52 learned probability blending

**Last Product Commit SHA**: `a783d15576fd1d83d0598828bb0820220e68950c` (plus the handoff-only commit that records this summary)

**Files Touched**:
- `outlier_scrapers/probability_blend.py`
- `outlier_scrapers/pack.py`
- `outlier_scrapers/feedback.py`
- `outlier_scrapers/game_totals.py`
- `tests/test_probability_blend.py`
- `tests/test_pack.py`
- `tests/test_feedback.py`
- `tests/test_game_totals.py`
- `docs/probability-blending.md`

**Summary of Work**:
- Added Brier-loss market/model blend fitting with learned weights for league, market
  type, odds range, time before game, and data-quality tier.
- Added versioned artifact loading, cold-start and historical-cutoff safeguards,
  pack sizing integration, ledger provenance, and v4 segment migration.
- Opened PR #52: https://github.com/DaSilvaDub/outlier/pull/52
- Fixed the inherited integer-total serialization failure by leaving market, model,
  and blended probabilities blank whenever push mass is unknown.
- Reconciled PR #52 with the post-PR50 revert/current master and resolved the
  `game_totals.py` conflict without reintroducing the reverted implementation.
- Verified the exact offline suite (517 tests), the focused blend/totals suite
  (161 tests), full mypy and pyright, touched-file Ruff, and diff checks.

**Next Steps**:
- Review and merge PR #52 after the refreshed hosted checks pass.
- Continue capturing and settling rows; run `feedback fit-blend` once each desired
  segment has enough eligible pregame history.

---

## Canonical path migration: Laptop is now CANONICAL (not OneDrive)

**Date**: 2026-07-21 (session continued from 2026-07-20)
**Agent**: Grok
**Product commit SHA**: `3b5e2764a38525ea67215d9eb941ac9e3566146b`
  (`chore(sync): make laptop Dev\GitHub\outlier the canonical path`)

### Layout (authoritative)

| Role | Path |
|------|------|
| **CANONICAL (do real work here)** | `C:\Users\dasil\Dev\GitHub\outlier` |
| **GitHub SSOT for commits** | `https://github.com/DaSilvaDub/outlier.git` |
| **OneDrive mirror** | `C:\Users\dasil\OneDrive\Documents\outlier-mirror` |
| **Google My Drive mirror** | `C:\Users\dasil\My Drive (dasilvadub@gmail.com)\Sports_Analytics\outlier` |
| **ai-runners full clone** | `C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners` |

### Continuous sync

- Task: **`Outlier-Quad-Sync`** (every 10 min + logon)
- Engine: `C:\Users\dasil\Scripts\sync_outlier_quad.ps1`
- Installer: `C:\Users\dasil\Scripts\install_outlier_quad_sync.ps1`
- Log: `C:\Users\dasil\Scripts\outlier_quad_sync.log`
- Status JSON: `C:\Users\dasil\Scripts\outlier_quad_sync_status.json`
- Docs: `C:\Users\dasil\Scripts\OUTLIER_QUAD_SYNC.md`
- Behavior:
  - Laptop canonical: fetch; **push** commits when ahead; **never hard-reset** if dirty (WIP protected)
  - OneDrive + My Drive + ai-runners: force-reset to `origin/master` every cycle (read-only mirrors)

### Agent STEP 0 (new path — mandatory)

```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```

Updated in-repo: `report-sync.ps1`, `sync-outlier.ps1`, `scripts/verify-sync.ps1`,
`AGENTS.md`, `CLAUDE.md`, `GROK.md`, `GEMINI.md`, `SYNC.md`, `README.md`,
`docs/ENT-SYNC-GLOBAL-PROMPT.md`.

Also updated global harness files:
- `C:\Users\dasil\.grok\Agents.md`
- `C:\Users\dasil\.claude\Claude.md`
- `C:\Users\dasil\Agents.md`

### What happened during the move

1. User requested 4-way always-on sync (Laptop + My Drive + OneDrive + GitHub).
2. User then required **Laptop** as canonical, not OneDrive.
3. A full physical move of the OneDrive tree failed (folder locked by OneDrive /
   open processes). A partial `robocopy /MOVE` **corrupted** both the OneDrive and
   intermediate laptop `.git` directories.
4. Recovery used the healthy pre-move laptop mirror backup
   (`outlier.mirror-bak` → promoted to `Dev\GitHub\outlier`).
5. WIP was rescued from the broken OneDrive working tree into laptop:
   - `outlier_scrapers/{pack,daily_job,games,refresh}.py`
   - `dashboard.html`, `calibration/feedback.sqlite3`
   - `scripts/run_wnba_specific.py`
6. Path canonicalization committed and pushed as `3b5e276`.
7. OneDrive primary path `C:\Users\dasil\OneDrive\Documents\outlier` remains a
   **broken leftover** (invalid `.git`). Sync uses **`outlier-mirror`** instead.
   Delete/rename the broken folder when unlocked (reboot if needed).

### Worktrees

- Old Codex/Gemini linked worktrees under `.codex/worktrees/*/outlier` were tied to
  the broken OneDrive main `.git` and are **orphaned**.
- Create new worktrees only from laptop canonical:

```powershell
cd C:\Users\dasil\Dev\GitHub\outlier
git worktree add <path> -b <branch>
```

### Next agent rules

- [ ] **Develop only** in `C:\Users\dasil\Dev\GitHub\outlier`
- [ ] **Never** treat OneDrive or My Drive mirrors as write targets (they hard-reset)
- [ ] STEP 0 always uses the **Dev\GitHub** `report-sync.ps1` path
- [ ] Optional cleanup: remove locked `OneDrive\Documents\outlier` broken tree when
      Windows unlocks it; keep `outlier-mirror` as the OneDrive tracking clone
- [ ] Optional: re-create needed agent worktrees from laptop canonical
- [ ] Local WIP may still be dirty on laptop (scraper edits / sqlite / dashboard) —
      commit when ready so mirrors receive it via the next push cycle

---

## Track C — calibration, uncertainty, drawdown (C1–C4)

**Date**: 2026-07-25  
**Agent**: Grok  
**Branch**: `risk-opt/c1-stake-calibration`  
**Commit SHA**: `6061fe9`  
**Base**: `origin/master` at `538a093` (includes B3 settlement ops, B4 portfolio replay, A0b ledger path)

### Prior session context (Antigravity handoff)

- B3 settlement ingestion: fixed on `risk-opt/b3-settlement-operations` → landed as `7cf4a14` on master.
- B4 chronological portfolio replay: already on master as `89f6380`.
- A0b authoritative ledger path: `538a093`.
- Next work requested: **Ticket C** (full Track C: C1→C4).

### What landed

| Ticket | Module / surface | Status |
|--------|------------------|--------|
| **C1** | `outlier_scrapers/stake_calibration.py` — `fit_stake_calibration`, `resolve_stake_calibration`, artifact load/write/validate | Done (library + CLI) |
| **C2** | Same module — `resolve_probability_uncertainty`, Wilson one-sided LB, `uncertainty_multiplier` | Done |
| **C3** | `outlier_scrapers/drawdown.py` — `compute_drawdown_state`, tier resolution, state load/write | Done |
| **C4** | `outlier_scrapers/learned_multipliers.py` — `apply_learned_multipliers` (shadow-neutral by default) | Done (integration hooks; pack enforce path not activated) |

Supporting:

- `outlier_scrapers/feedback.py`: CLI `fit-stake-calibration`, `compute-drawdown`; `settled_at` in `_joined_rows`.
- `config/portfolio_risk.json`: force-added (normally under `config/*` gitignore) with Track C blocks disabled / shadow neutral.
- Tests: `tests/test_stake_calibration.py`, `tests/test_drawdown.py`, `tests/test_learned_multipliers.py`.

### Invariants implemented

- Multipliers ∈ [0, 1]; never raise stakes above raw Kelly.
- Calibration uses Kelly ratio, not hit-rate stake scalar.
- Never raise calibrated probability above source for staking (better-than-predicted reported via raw factor but multiplier capped at 1.0).
- Pushes excluded from binary fit; push mass retained for Kelly recomputation.
- Artifacts reject future cutoffs and wrong `source_probability_column`.
- Cold-start / missing artifact → neutral multipliers in shadow.
- Drawdown uses settled **placed** PnL only; PROPOSED/PLACED/future rows ignored.
- `shadow_multipliers_neutral=true` forces cal/uncertainty/drawdown multipliers to 1.0 while still computing diagnostic probabilities.

### Verification

```text
pytest tests/test_stake_calibration.py tests/test_drawdown.py tests/test_learned_multipliers.py  → 23 passed
pytest tests (full offline suite) → 560 passed
python -m outlier_scrapers.feedback fit-stake-calibration --help  OK
python -m outlier_scrapers.feedback compute-drawdown --help       OK
```

No paid reasoning / live desk paths run.

### Explicitly NOT done (remaining gates)

- [ ] Wire `apply_learned_multipliers` into pack `write_pack` / allocator pre-cap path (still shadow library only).
- [ ] Sidecar fields for calibration/uncertainty/drawdown artifact versions in `portfolio_risk.json` pack output.
- [ ] Activate learned multipliers (`shadow_multipliers_neutral: false` + policy enable flags) — requires sample thresholds, OOS review, product sign-off.
- [ ] Drawdown tier thresholds product sign-off (defaults are provisional).
- [ ] A9 enforce flip still blocked on 14-day shadow window + A0a + placed-exposure policy.
- [ ] Branch not pushed / no PR opened yet (await user).

### How to operate offline

```powershell
cd C:\Users\dasil\Dev\GitHub\outlier

# Fit stake calibration from ledger (writes calibration/stake_calibration.json)
python -m outlier_scrapers.feedback fit-stake-calibration `
  --db calibration/feedback.sqlite3 `
  --min-samples 30 --prior-strength 30 --confidence-level 0.80 `
  --as-of 2026-07-25T00:00:00+00:00

# Compute drawdown equity state (writes calibration/drawdown_state.json)
python -m outlier_scrapers.feedback compute-drawdown `
  --db calibration/feedback.sqlite3 `
  --as-of 2026-07-25T00:00:00+00:00
```

### Next agent steps

1. Open PR from `risk-opt/c1-stake-calibration` (or split C2/C3/C4 branches if desired — currently one commit for whole Track C library).
2. Optional next coding: pack shadow integration of learned multipliers + sidecar provenance (still non-authoritative until activation).
3. Or resume A9 enforce path once shadow window / A0a gates close.
4. Do not enable Track C in enforce without checklist items in the final execution plan §12.

---

## Ticket A9 review (2026-07-25) — BLOCKED, not approved

**Agent**: Grok  
**Reviewed commit**: `fca9e28` Implement Ticket A9: Enforce Flip  
**Verdict**: **DO NOT merge / do not leave enforce active**  
**Safety fix**: PR #58 merged (`2a9dfc3`) — `config/portfolio_risk.json` mode forced back to **`shadow`**

### Already on master before review
`fca9e28` was already the tip of `origin/master` (branch `risk-opt/a9-enforce` identical). No clean PR merge path; review was of landed code.

### Critical production bug
Enforce path in `write_pack` zeros rows without `stable_wager_id`. Pack candidates never set that field → **all recommended units become 0.0** under mode=enforce. Allocator also requires `units` (not `recommended_units_pre_news`) and `board == "A"`.

### Entry gates unmet
- No 14-day shadow window evidence (only `packs/2026-07-07`)
- No immutable second-enforce refusal / reserved exposure wiring
- `prompts/desk2/S_claude.md` still has 20%/30% same-event discount
- A0a / product cap sign-off / book policy sign-off not evidenced
- No focused A9 tests for fail-closed identity or second pack write

### Partial credit kept on master (under shadow)
- Ledger provenance columns for policy/portfolio units
- A/B/D/E prompt discount removed + stake-contract sentence
- write_pack enforce hook (inert while mode=shadow)

### Required before re-attempting A9
1. A3 identity on pack rows (`stable_wager_id` etc.)
2. Correct allocator input mapping from pack fields
3. Placed-exposure **or** immutable single-pack + required test
4. Finish S_claude prompt cleanup
5. Gate checklist §12 with evidence
6. Only then flip `mode` to enforce

---

## Ticket A9 v2 review (2026-07-25) — APPROVED for shadow; do not flip yet

**Agent**: Grok  
**Walkthrough**: `C:\Users\dasil\.gemini\antigravity\brain\55b45821-bb5d-489f-b152-64131643484c\walkthrough.md`  
**Branch**: `risk-opt/a9-enforce-v2`  
**Commit**: `b86f947` feat: Implement Ticket A9 (Enforce Flip v2) without final flip  
**Merge status**: **Already on `origin/master`** (master == branch tip `b86f947`). No further merge action required.

### Review against prior blockers
| Prior blocker | v2 status |
|---------------|-----------|
| Missing `stable_wager_id` / identity | **Fixed** — `project_risk_identity` + mapping in `write_pack` |
| Allocator field mismatch | **Fixed** — maps `recommended_units_pre_news` → `units`, uses projected identity |
| Immutable second enforce pack | **Fixed** — raises if enforce sidecar exists; tested |
| 14-day shadow gate | **Fixed** — enforce refused if &lt;14 distinct snapshot days; tested |
| S_claude 20%/30% residual | **Fixed** — fallback heuristic removed |
| Final mode flip to enforce | **Correctly blocked** — stays `shadow` |

### Verification (this review)
- Offline suite: **562 passed**
- `config/portfolio_risk.json` mode: **`shadow`**
- Ledger distinct capture days: **6 / 14** (need ~8 more complete days)
- Identity smoke: `project_risk_identity` produces `stable_wager_id` for a candidate prop

### Residual follow-ups **before** flipping to enforce
1. Register `portfolio_risk.json` in `DERIVED_PACK_OUTPUTS` (plan A7/A9: remove before regen).
2. On enforce path, write allocated units into `recommended_units_pre_news` (currently sets `portfolio_units` only; shadow preserves legacy columns correctly).
3. Prefer counting **shadow-mode** days (policy_mode/portfolio_mode) rather than raw snapshot days if mixed history appears.
4. Keep accumulating consecutive complete shadow days until 14; then product re-check §12 before `mode: enforce`.

### Operating guidance
- Continue daily packs in **shadow**.
- Do **not** edit `mode` to `enforce` until 14-day gate is green.
- When ready: flip only `config/portfolio_risk.json` `"mode": "enforce"` after checklist re-review.

---

## R2–R5 Desk Contradictions, Proxy Devig Rules & Mandatory Stand-Down Reconciliation

**Date**: 2026-07-25  
**Agent**: Gemini 3.6 Flash  
**Branch**: `fix/r2-r5-desk-integrity-and-totals-reconciliation`  
**Last Commit SHA**: `ea9ac64` (`fix(pack): refine plus_money_speculative_edge scope to proxy_market_devig rows`)

### Files Touched:
- `outlier_scrapers/game_totals.py`
- `outlier_scrapers/pack.py`
- `prompts/desk2/Q_chatgpt.md`

### Summary of Work:
1. **Game Totals & Side Resolution Alignment**:
   - Updated `game_totals.py` to flag `UNINDEXED_SLATE_GAME` when a game is missing from candidate indexing, and `SIDE_RESOLUTION_CONFLICT` + `SOURCE_INTEGRITY_FLAG` when candidate headline side conflicts with the book ladder side (e.g. candidate OVER vs board UNDER).
2. **Candidate Actionable & Sizing Invariants**:
   - Added `edge_suspect_thin_liquidity` flag for small edges (edge_pct <= 0.035) on `thin_liquidity` markets, withholding pre-news units.
   - Tagged `plus_money_speculative_edge` on `proxy_market_devig` rows with plus-money price and near coin-flip probability (~0.50-0.525).
3. **Desk Instruction Guardrails (`ROLE_BLOCK` & `Q_chatgpt.md`)**:
   - Updated `ROLE_BLOCK` in `outlier_scrapers/pack.py` and `prompts/desk2/Q_chatgpt.md` to mandate `PASS` / stand-down on any non-actionable or quality-flagged row (`movement_line_mismatch`, `ev_line_fallback`, `edge_suspect_stale_line`, `SOURCE_INTEGRITY_FLAG`, `UNINDEXED_SLATE_GAME`, `SIDE_RESOLUTION_CONFLICT`).
   - Explicitly prohibited double-counting `proxy_market_devig` as independent model support.
   - Added quantitative capping for thin-liquidity edges and plus-money speculative edges in Phase Q.
4. **Verification**:
   - Full offline unit test suite passed: **565 passed**.

### Next Steps:
- Open PR for `fix/r2-r5-desk-integrity-and-totals-reconciliation` to merge into `master`.

---

## Single Master Generic Prompt Export for Desk 1

**Date**: 2026-07-25  
**Agent**: Gemini 3.6 Flash  
**Branch**: `feat/single-master-generic-prompts`  
**Last Commit SHA**: `49959a1`  
**PR**: https://github.com/DaSilvaDub/outlier/pull/60  

### Files Touched:
- `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`
- `scripts/organize_today_run2.py`

### Summary of Work:
1. **Single Master Generic Prompt**: Updated `generate_prompts.py` so that Desk 1 generic prompt export generates a single master file (`1_Master_Generic_pack_{date}.txt`) instead of 5 identical model copies.
2. **Desk 2 Phase-Specific Prompts Intact**: Preserved phase/model-specific prompts for Desk 2 (`Desk2_Manual/`), where individual phases require distinct prompt instructions.
3. **Resilient Cloud Storage Clean-up**: Added `safe_rmtree()` with retry logic to avoid `PermissionError` when clearing `prompts/` directories in cloud-synced folders (OneDrive and Google Drive).
4. **Organize Script Updated**: Updated `organize_today_run2.py` startswith matcher to properly route `1_Master` generic prompt files into `generic_prompts_{date}` output subdirectories.

### Next Steps:
- Review and merge PR #60 into `master`.

---

## Codex command reference document (2026-07-25)

**Last Commit SHA**: `5e164464db4145831feb18ba0cc64a3c5d8afbbc`

**Files Touched**:
- `C:\Users\dasil\OneDrive\Desktop\Codex Commands - Outlier Pipeline Map.docx` (user artifact; outside the repo)
- `.agent-log/HANDOFF.md` (this ignored coordination note)

**Summary of Work**:
- Created a 12-page Word reference covering current desktop shortcuts, IDE and CLI slash commands, CLI top-level commands, and all installed user-invocable skills.
- Added an Outlier relationship only where a concrete pipeline or repository workflow connection exists; unrelated relationship cells are blank.
- Included the explicit-ask-only A/B/C/D/E Research Desk safety boundary and an Outlier command key.
- Rendered the document through Microsoft Word and visually inspected every page; corrected the terminal shortcut rendering.

**Next Steps**:
- No repository work remains. Refresh the document after a material Codex command/skill inventory change.


---

## Split cross-agent prompt report skills (2026-07-26)

**Last Product Commit SHA**: `aaffb2cca7da7680b32e3d558e2a55507ff5d26a` (plus the handoff-only commit recording this summary)

**PR**: https://github.com/DaSilvaDub/outlier/pull/66 (follow-up because PR #64 merged before the split-skill correction was pushed)

**Files Touched**:
- `.agents/skills/analyze-outlier-generic-prompts/`
- `.agents/skills/analyze-outlier-sequential-prompts/`
- `.agents/skills/_shared/resolve_prompt_report.py`
- `.agents/skills/export-manual-outlier-packs/`
- `AGENTS.md`, `CLAUDE.md`, `GROK.md`, `GEMINI.md`
- `docs/ENT-SYNC-GLOBAL-PROMPT.md`
- `tests/test_outlier_prompt_report_skills.py`

**Summary of Work**:
- Replaced the combined prompt skill with two explicit skills: generic Desk 1 master prompts and ordered Desk 2 phases.
- Generic reports go to `C:\Users\dasil\OneDrive\Desktop\BETTING REPORTS\GENERIC\YYYY-MM-DD`.
- Sequential reports go to `C:\Users\dasil\OneDrive\Desktop\BETTING REPORTS\SEQUENTIAL\YYYY-MM-DD` with hard Q → R → W → X → S predecessor and agent-assignment gates.
- Added one shared collision-safe resolver that returns predecessor report paths to each downstream phase and keeps generic outputs outside the Desk 2 chain.
- Wired the existing manual exporter to route each prompt family to its matching skill.
- Installed thin prompt-handoff pointers in all live Grok, Claude, Codex, and Gemini global instruction files. These machine-local edits are not part of the PR.
- Created the live `BETTING REPORTS\GENERIC` and `BETTING REPORTS\SEQUENTIAL` folders.
- Verified both skill validators, 9 focused tests, Ruff, and Pyright. No reasoning model or scraper was run.

**Next Steps**:
- Review and merge PR #66. PR #64 contains the superseded combined-skill version already on master.
- Start fresh agent sessions after merge so each harness reloads its instruction/skill context.
- Use the generic skill for `Desk1_Automated`; use the sequential skill for `Desk2_Manual` or `paste_*.md`. Prompt creation alone remains non-authorizing.

---

## Automated cross-agent prompt reports (2026-07-26)

**Last Product Commit SHA**: `321ad33ac661b1dc101eacc1e886e4acac9ad288`

**Publication**: The background canonical sync advanced `origin/master` to the product
commit before the feature-branch PR could be created. GitHub rejected a duplicate PR with
`No commits between master and feat/automated-prompt-cli-reports`.

**Files Touched**:
- `.agents/skills/_shared/run_prompt_workflow.py`
- `.agents/skills/analyze-outlier-generic-prompts/SKILL.md`
- `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`
- `.agents/skills/export-manual-outlier-packs/SKILL.md`
- `tests/test_outlier_prompt_report_skills.py`

**Summary of Work**:
- Added a dry-run-by-default subprocess runner for the installed Codex, Claude, Gemini,
  and Grok CLIs.
- Generic mode discovers all latest Desk 1 master prompts, runs the selected agent set,
  and saves collision-safe reports under `BETTING REPORTS/GENERIC/YYYY-MM-DD`.
- Sequential mode runs Q (Codex), R (Claude), W (Gemini), X (Grok), and S (Claude) in
  strict order, injecting every saved predecessor report and stopping on the first failure.
- Live calls require both `--execute` and `--authorization DESK_OK`; prompt discovery alone
  cannot spend quota. Reports are written atomically and never overwrite existing output.
- Verified 15 focused offline tests, Ruff, Pyright, both skill validators, installed CLI
  executability, and real dry-run plans for 12 generic and 5 sequential jobs. No live model
  call was made.

**Next Steps**:
- To preview either workflow, run the command documented in its `SKILL.md` without
  `--execute`.
- To make live calls, explicitly authorize them in the current turn and use the documented
  `--execute --authorization DESK_OK` form.
- Monitor the first authorized live run for provider authentication/quota failures; those
  provider-dependent paths were intentionally not exercised during implementation.

---

## Claude automation hardening: sync tooling, hooks, typecheck fix, Claude Code config (2026-07-25 to 2026-07-26)

**Agent**: Claude (Sonnet 5 / Opus 5)
**Branches** (all merged and deleted, local + remote): `feat/claude-hooks-reasoning-guard`,
`fix/hookify-rationale-correction`, `fix/sync-silent-noop`, `fix/portfolio-report-typing`,
`docs/sync-ent-global-prompt`, `feat/claude-subagents-and-skills`,
`feat/mcp-config-and-skill-symlinks`
**Last Commit SHA**: `d34b06a` (canonical fast-forwarded to this; plus this handoff commit)
**PRs** (all merged, squash): [#56](https://github.com/DaSilvaDub/outlier/pull/56),
[#57](https://github.com/DaSilvaDub/outlier/pull/57),
[#61](https://github.com/DaSilvaDub/outlier/pull/61),
[#62](https://github.com/DaSilvaDub/outlier/pull/62),
[#63](https://github.com/DaSilvaDub/outlier/pull/63),
[#65](https://github.com/DaSilvaDub/outlier/pull/65),
[#67](https://github.com/DaSilvaDub/outlier/pull/67)

### Files Touched (representative — see each PR for full diffs)
- `.claude/hooks/block-reasoning.ps1`, `.claude/hooks/check-sync.ps1`, `.claude/hooks/README.md`, `.claude/settings.json`
- `sync-outlier.ps1`, `scripts/verify-sync.ps1`, `report-sync.ps1`
- `outlier_scrapers/portfolio_report.py`, `outlier_scrapers/portfolio.py`, `outlier_scrapers/drawdown.py`
- `AGENTS.md`, `CLAUDE.md`, `GROK.md`, `GEMINI.md`, `SYNC.md`, `docs/ENT-SYNC-GLOBAL-PROMPT.md`
- `.claude/agents/registry-alias-auditor.md`, `.claude/agents/untyped-module-reviewer.md`
- `.claude/skills/sync-report/SKILL.md`, `.claude/skills/handoff/SKILL.md`
- `.mcp.json`
- `.claude/skills/{analyze-outlier-generic-prompts,analyze-outlier-sequential-prompts,export-manual-outlier-packs,outlier-ai-desk,synthesize-outlier-pack}` (symlinks → `.agents/skills/...`)
- `.claude/agents/{outlier-bug-checker-agent,outlier-data-validator-agent,outlier-desk-agent}.md` (symlinks → `plugins/outlier/agents/...`)
- Outside the repo (personal machine config, not tracked): `~/.claude/CLAUDE.md`, `~/.grok/AGENTS.md`, `~/.codex/AGENTS.md`, `~/AGENTS.md`, `~/.gemini/GEMINI.md`, `~/.gemini/AGENTS.md`, `~/.claude/settings.json`, `~/.claude/hooks/outlier-*.ps1`

### Summary of Work

**#56/#57 — Reasoning house rule + sync check as hooks, not prose.** The old guard
(`.claude/hookify.no-reasoning-unless-asked.local.md`) was `action: warn` (never actually
blocked anything) and covered neither the `PowerShell` tool nor `run_desk2`. Replaced with a
`PreToolUse` deny hook on `Bash|PowerShell` (escape hatch: append `DESK_OK`; every block/bypass
logged to `~/.claude/reasoning-guard.log`). Added a `SessionStart` hook that verifies sync state
read-only and tells the agent to run STEP 0 by hand on drift — it does not auto-mutate, since
canonical is frequently mid-work on a `risk-opt/*` branch. #57 is a docs-only correction: #56
claimed the old hookify rule "protected exactly one machine" because `.gitignore` matched it —
false. The file was already tracked when the ignore rule was added, so `.gitignore` never took
effect; it's committed and reaches every ent, it just never blocked anything.

**#61 — The sync tooling's own silent-no-op bug.** `sync-outlier.ps1` resolved the repo from
cwd; run from `OneDrive\Documents\outlier` (whose `.git` is a placeholder) it printed
`Not inside a git repo` and exited, but `verify-sync.ps1` never checked that exit code and
produced a confident `State vs origin/master: MATCH` anyway. Fixed: repo resolution takes an
explicit `-RepoRoot` (default canonical, never cwd), all three bootstrap invocations are now
fatal on failure, and the report ends with a computed `REPORT STATUS: OK|FAILED` trailer plus a
per-run `RUN-NONCE` (replacing the old `!!! FORBIDDEN` banner, which actually triggered on
terminal width, not piping, and was pure noise). Also fixed a phantom blank full-clone row
(hardcoded "ai-runners:" label + `Test-Path` satisfied by an unreadable placeholder) and a
missing `Write-Warn` function that had never been defined.

**#62 — `typecheck` had been red on `master` since 2026-07-25T16:31Z** (13+ runs, from
`9347adb`). 23 mypy errors across `portfolio_report.py` (20 — a heterogeneous dict literal
inferred as `dict[str, object]`), `portfolio.py` (2 missing `defaultdict` annotations, which
unmasked 4 more real errors once fixed — `r` was reused for two unrelated types in one
function), and `drawdown.py` (1 — an over-wide `Mapping` annotation). Verified with a
differential test (old vs new `run_report` over synthetic inputs) that output is byte-identical.

**#63 — Propagated #61's refreshed global-prompt block** to all six installed harness files
(`~/.claude/CLAUDE.md`, `~/.grok/AGENTS.md`, `~/.codex/AGENTS.md`, `~/AGENTS.md`,
`~/.gemini/GEMINI.md`, `~/.gemini/AGENTS.md`). Found three had silently drifted onto the
unreadable OneDrive mirror path; corrected to canonical.

**#65 — First real `.claude/agents/` and `.claude/skills/` content.** `registry-alias-auditor`
(cross-checks `registry.py`'s paired alias/display tables — the exact bug class behind the WNBA
"Tempo"/"Fire" incidents) and `untyped-module-reviewer` (manually reviews the 11
mypy/pyright-excluded modules, including the two documented silent-wrong-number quirks:
`implied_probability()` returns a percentage not `[0,1]`, and the `(1 - push_prob)` adjustment).
Both read-only. Plus `sync-report` and `handoff` skills (both `disable-model-invocation: true`)
wrapping STEP 0 and the end-of-session protocol correctly.

**#67 — `.mcp.json` + symlinks.** Context7 and GitHub as remote-HTTP MCP servers, deliberately
with no hardcoded token/API-key header (a header pointing at an unset env var degrades or breaks
silently — exactly the failure class the rest of this work was fixing; both endpoints work
without one). Symlinked the 5 existing cross-ent skills under `.agents/skills/` and 3 existing
agents under `plugins/outlier/agents/` into `.claude/` as per-item relative symlinks (not a
whole-directory swap — `.claude/agents/` and `.claude/skills/` already held #65's real content).
Verified `core.symlinks=true` and a real git round-trip (store as mode `120000`, fresh checkout
stays a real symlink, not a text file containing the path) before committing, then re-verified
the blob modes directly on GitHub after push.

**Repo hygiene**: after each merge, deleted the corresponding local + remote feature branch and
`git worktree remove`d its worktree — all seven confirmed `MERGED` via `gh pr view` first (this
matters because squash-merges make `git branch -d`/`--is-ancestor` report "not merged" even
though they are; `-D` was used only after independent confirmation). Canonical was fast-forwarded
from `4164f95` to `d34b06a` to write this handoff; the fast-forward did not touch the one
unrelated file already dirty on canonical (`.github/workflows/pytest.yml` — not this work, left
untouched, belongs to whoever has that in progress).

### Next Steps / Open Items

- **This skill file's own instructions are wrong.** `.claude/skills/handoff/SKILL.md` step 4
  says to *overwrite* `.agent-log/HANDOFF.md` — but every prior entry in this file (Track C, the
  A9 reviews, the prompt-report skills work) appended a new `## <title>` section instead,
  preserving cross-agent history. Followed the real convention (append) for this entry, not the
  skill's literal wording. The skill needs a one-line fix; not done yet since it's an
  unrequested addition to this task — flagging so the next session doesn't have to rediscover it.
- `.claude/hookify.no-reasoning-unless-asked.local.md` is now redundant with the #56 deny hook.
  Left in place deliberately (hookify rules may still be consumed by non-Claude ents, which the
  `PreToolUse` hook doesn't cover). `git rm --cached` would also fix the original `.gitignore`
  intent from `b070caa` if ever wanted.
- The symlinks in #67 depend on Developer Mode / `core.symlinks` being enabled wherever the repo
  is checked out. Verified fine on this machine (all four harnesses run here); would silently
  degrade to plain-text stand-ins on a fresh clone elsewhere without that.
- `~/AGENTS.md` has a duplicated `## Heavy OMX Runtime` section (identical text, back to back) —
  noticed in passing, pre-existing, unrelated to this work, not fixed.
- Canonical currently has one unrelated uncommitted change
  (`.github/workflows/pytest.yml`, switching to `dariocurr/pytest-summary@v2.6`) that is not
  part of this handoff — whoever owns it should commit or discard it; it was left untouched here.

---

## Fix handoff skill: append, not overwrite (2026-07-26)

**Agent:** claude
**Branch(es):** `fix/handoff-skill-append-not-overwrite`
**Last Commit SHA:** `80a4bc7`
**PR:** https://github.com/DaSilvaDub/outlier/pull/68

### Files Touched
- `.claude/skills/handoff/SKILL.md`

### Summary of Work
- The first "Next Steps" item in the entry directly above this one flagged that the `handoff`
  skill's own step 4 said to *overwrite* `.agent-log/HANDOFF.md`, when every real entry in this
  file (including that one) appends a new dated section instead. Fixed it: step 4 now instructs
  appending a `---`-separated section matching the field names already in use here (`Agent`,
  `Branch(es)`, `Last Commit SHA`, `PR`, then `Files Touched` / `Summary of Work` / `Next Steps`),
  and to check the file's tail first since field naming has drifted slightly entry to entry
  across sessions/agents. Also tightened step 5 to explicitly say stage only the handoff file,
  never a blanket `-A` — canonical routinely has unrelated dirty files from concurrent sessions.
- Docs-only change to the skill's own instructions; no code or repo behavior affected.
- This entry itself is written by following the corrected skill.

### Next Steps
- None outstanding from this fix. The open items listed in the entry above (redundant hookify
  rule, symlink Developer Mode dependency, duplicated OMX section in `~/AGENTS.md`, the unrelated
  `pytest.yml` change on canonical) are still open and unrelated to this change.

---

## Normalized prop identity contract fix (2026-07-26)

**Agent:** Codex
**Branch(es):** `fix/normalized-prop-identity`
**Last Commit SHA:** `578aa6b`
**PR:** https://github.com/DaSilvaDub/outlier/pull/71

### Files Touched
- `outlier_scrapers/normalizer.py`
- `outlier_scrapers/schema.py`
- `outlier_scrapers/cards.py`
- `tests/fixtures/mlb_player_props.json`
- `tests/fixtures/wnba_player_props.json`
- `tests/test_normalizer.py`
- `tests/test_schema.py`

### Summary of Work
- Confirmed the live raw MLB payload contained `position` and `outcomeId` on every outcome;
  the warning came from an internal normalized-contract mismatch.
- Promoted both fields to top-level normalized keys while preserving `side` and nested
  `sport_context.outcome_id` compatibility aliases.
- Dropped source rows without stable outcome identity and made normalized contract violations
  abort the refresh instead of continuing after warning-only validation.
- Updated cards to prefer canonical identity with legacy fallback.
- Verified 183 focused offline tests, Ruff, Pyright, compile checks, and a live-payload smoke
  covering 23,854 rows with zero schema errors or identity mismatches.

### Next Steps
- Review and merge PR #71 after hosted checks pass.
- Re-run the local pipeline after merge to regenerate props artifacts without the prior schema
  warning flood.

