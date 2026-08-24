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
