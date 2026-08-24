# Handoff Summary — 2026-08-24 — Claude (projection calibration loop / Tier-3 items 8–11)

**Branch:** `claude/goal-resolution-dra9j0`
**PR:** https://github.com/DaSilvaDub/outlier/pull/114
**Plan:** `docs/plans/2026-08-24-projection-calibration-loop.md`

## What this slice did

1. `export_projections()` now covers WNBA as well as MLB, so
   `wnba_projections_latest.json` is written before `pack` instead of the points
   scaffold living only inline.
2. Real `backfill` / `train` / `validate` / `promote` in
   `outlier_scrapers/projection_training.py`, replacing the `scaffold-ready`
   stubs. Walk-forward samples from free `statsapi.mlb.com` game logs; staged
   parameter fit; holdout scoring (NLL/MAE/RMSE/CRPS/PIT/Brier) against the
   shipped defaults; manual audited promotion.
3. `daily_job.refit_blend_weights()` refits blend weights from the settled
   ledger every run (non-fatal, `--skip-blend-refit` opts out).
   `config/blend_promotion.json` gates whether `final_blended_prob` drives
   sizing — shipped `shadow`, needs `mode: live` plus 1000 eligible samples.
4. `--top-ev-n` default 15 → 40 so the enforced portfolio caps prune the board.

Merged `origin/master` (PR #105 SO restaking/promotion gate). A promoted
calibrated model emits `so-starter-calibrated-<version>` instead of
`so-starter-gamelog-v2`, so `is_current_gamelog_so_hash()` now backs the hash
checks in `slate_quality` and `so_eval` — otherwise promotion would silently
drop those rows out of restaking and out of the promotion gate.

## Explicitly NOT done

- No backfill/train run: no `statsapi.mlb.com` egress from the remote session.
- No blend refit run: no feedback ledger present. Both are covered by offline
  tests with injected fetchers.
- No projection model promoted; `promoted: false` everywhere, blend stays shadow.
- No paid desk / reasoning.

## Next

1. On a machine with network + ledger: `projections backfill --sport MLB --from
   2024-03-20 --to <today>`, then `train`, then `validate`; `promote` only on a
   passing report.
2. Let `daily_job` accumulate settled rows until `blend_weights.json`
   `eligible_samples` clears `config/blend_promotion.json`'s
   `min_eligible_samples` before considering `mode: live`.
3. PR #105's SO promotion gate still needs settled v2 rows (see previous entry).

## Sync

`report-sync.ps1` could not run in this session (Windows PowerShell path, Linux
container). Branch state verified with `git fetch origin master` + merge.

---

# Handoff Summary — 2026-08-22 — Grok (SO restaking / promotion gate)

**Branch:** `feat/predictor-accuracy-upgrades` (`d17fc80`)  
**PR:** https://github.com/DaSilvaDub/outlier/pull/105  
**Worktree:** `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades`  
**Master:** leave live pipeline alone; promotion still OFF.

---

## What this slice did

Hardened **restaking units from independent SO** without flipping live Kelly:

1. Offline replay on settled **v1** gamelog rows (n=7, diagnostic only):
   - market_brier **0.282** / raw indep **0.403** / soft **0.310** / tempered(raw) **~0.30**
   - still prefer market; **no settled v2 rows yet** (`--promotion-gate` → n=0, ready=false)
2. New `config/so_promotion.json` + `so_promotion.py` + leaf `so_sizing_blend.py`
   - Force: `OUTLIER_PROMOTE_INDEPENDENT_SO=1`
   - Auto: `OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO=1` only when readiness clears
   - Gate scores **v2-only**; soft prefer alone cannot unlock; live Kelly = `temper(raw, market)` and **requires** `market_consensus_prob`
3. Promote path Kelly-sizes tempered prob; flags `independent_so_market_tempered`
4. `so_eval --include-tempered` / `--promotion-gate`
5. Tests: `tests/test_so_promotion.py` (+ predictor gates) — **31 passed** with related suites

## Explicitly NOT done

- Did **not** enable live independent SO sizing (`enabled False`, `promotion.ready false`)
- No paid desk / reasoning
- No merge to master yet (open PR from this branch)

## Next

1. Keep running master `daily_job` so **v2** hashes settle into the ledger.
2. Re-check:
   ```powershell
   python -m outlier_scrapers.so_eval --db calibration/feedback.sqlite3 --promotion-gate
   ```
3. Only when `promotion.ready=true`: force-env or set `auto_promote` / merge with eyes open.
4. Optional: Savant park-factor refresh; portfolio_risk still shadow.

## Sync
Last report-sync: `RUN-NONCE: 8320481bc18a4b1b` utc=2026-08-22T16:35:38Z head=`f125169` status=OK
