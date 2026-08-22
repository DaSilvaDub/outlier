# Handoff Summary — 2026-08-22 — Grok (predictor accuracy)

**Agent:** Grok  
**Canonical master HEAD:** `ed76e08` (live pipeline; keep clean)  
**Feature branch HEAD:** `dc80c29` on `feat/predictor-accuracy-upgrades`  
**Feature worktree:** `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades`  
**PR:** https://github.com/DaSilvaDub/outlier/pull/104  

Sync when written: `REPORT STATUS: OK` / `RUN-NONCE: ae149bf96f9e49b4` / `head=ed76e08`

---

## Do this first (next agent)

1. **Do NOT merge PR #104.** Fresh `so_eval --require-gamelog-hash` after hash backfill:
   - `n=7`, market Brier **0.282**, independent Brier **0.403**, `prefer_independent=false`
   - Gamelog SO model is **worse than market** on current settled sample — sizing promotion must stay off master.
2. Keep running live packs on **master only** so more SO settlements accumulate.
3. Re-run eval after more days:
   ```powershell
   Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
   python -m outlier_scrapers.so_eval --db C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3 --require-gamelog-hash
   ```

---

## What just landed on the feature branch (`dc80c29`)

**Critical measurement fix:** feedback ledger now stores `projection_feature_hash` + `projection_quality_flags`.

Without this, `gamelog_rows` stayed 0 forever even though packs already had hashes.

Also added `scripts/backfill_projection_hashes.py` — already run against live DB for packs `2026-08-21` and `2026-08-22` (`updated: 13`).

### Prior feature-branch work (still unmerged)
- Promote gamelog SO into live Kelly (2u, signal-gated) — **blocked by eval**
- LM stale window 12h → 6h
- CLV close-join fallbacks
- Opponent K% (MLB Stats API SO/PA) + curated park SO factors
- WNBA minutes/PPM ESPN → pack **audit-only** (`projection_audit_wnba_minutes`)
- `so_eval` harness

### Tests
```powershell
Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
python -m pytest tests/test_feedback.py::test_capture_pack_persists_projection_feature_hash tests/test_predictor_gates.py tests/test_so_eval_and_context.py tests/test_context_fetchers.py tests/test_pitcher_so_features.py -q --tb=line
# 33 passed recently
```

---

## Master already has (do not redo)
`c8ca8c9` predictor gates, `1be5bc1` gamelog SO, `41358bb` empty-hash eligibility fix.

Live packs `2026-08-21`/`22` already write gamelog hashes into `candidates.csv`.

---

## Recommended next engineering (if not waiting)
1. Diagnose **why** gamelog SO Brier is poor (n=7): overconfident probs? thin samples? side errors?
2. Consider shadow-only mode for independent SO sizing until eval flips (or gate `promote_independent_so_sizing` behind env/config flag default off).
3. Optional: land **feedback hash persistence only** on master early (safe measurement infra) so future packs auto-record digests without waiting for full PR.
4. Keep portfolio calibration shadow; no `fit-blend` on old universe.

## Resume path
```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
# read this file
Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
git pull
git log -5 --oneline
```
