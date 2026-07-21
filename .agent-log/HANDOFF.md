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
