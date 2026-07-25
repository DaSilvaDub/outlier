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


