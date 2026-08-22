# Handoff Summary — 2026-08-22 — Grok

**Master HEAD:** `2be43bb` (merged PR #104)  
**Decision:** Ship accuracy upgrades to live pipeline with **independent SO Kelly promotion OFF**.

---

## What just shipped to master

Merged https://github.com/DaSilvaDub/outlier/pull/104

### Live now
- **Gamelog SO v2**: empirical-Bayes shrink + thin-sample soften (`so-starter-gamelog-v2`)
- **Feedback** stores `projection_feature_hash` / `projection_quality_flags`
- Opponent batter K% + curated park SO factors on probable enrich
- WNBA minutes/PPM pack **audit-only** (not independent-eligible)
- LM stale window 6h; CLV close-join fallbacks; `so_eval` harness

### Explicitly NOT live
- Independent SO → Kelly sizing stays **disabled** unless:
  ```powershell
  $env:OUTLIER_PROMOTE_INDEPENDENT_SO = "1"
  ```
- Verified on master: `ENABLE_INDEPENDENT_SO_SIZING is False`, hash `so-starter-gamelog-v2`

---

## Why this is best
v1 gamelog independents were overconfident (7/7 more extreme than market; Brier 0.40 vs 0.28).  
v2 shrinkage belongs on master so new packs get better audit probs and measurable hashes.  
Live restaking on independent must wait until `so_eval --require-gamelog-hash` prefers independent on **v2** settlements.

---

## Next agent / next run
1. Run normal master pipeline (`daily_job --analysis-profile local`) so v2 hashes land in ledger.
2. After settlements accumulate:
   ```powershell
   python -m outlier_scrapers.so_eval --db calibration/feedback.sqlite3 --require-gamelog-hash
   ```
3. Only if `prefer_independent=true` (and preferably `gamelog_v2_rows` healthy): enable `OUTLIER_PROMOTE_INDEPENDENT_SO=1` or flip the default.
4. Feature worktree still at `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades` for follow-on experiments.

## Sync
Last report-sync before merge: `RUN-NONCE: 56bb057309b64f6d` head=`263709f`. Master now `2be43bb`.
