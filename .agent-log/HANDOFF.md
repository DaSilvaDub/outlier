# Handoff Summary — 2026-08-22 — Grok (SO gamelog calibration dig)

**Agent:** Grok  
**Master:** `4b685af` (live pipeline)  
**Feature branch:** `dab83c8` on `feat/predictor-accuracy-upgrades`  
**Worktree:** `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades`  
**PR:** https://github.com/DaSilvaDub/outlier/pull/104  

---

## Diagnosis (why the 7 rows were bad)

Settled `so-starter-gamelog-v1` rows: **n=7**

| Finding | Evidence |
|---|---|
| Systematic overconfidence | **7/7** independent probs more extreme than market |
| Hit rates | market 2/7, independent **1/7** |
| Brier | market 0.282 vs independent **0.403** |
| Root cause | Thin recent-start means too extreme (e.g. Schlittler mean K **≈8.25** on a 5.5 line; actual 4) + dispersion too tight |

Not mainly side-disagreement (only Yamamoto disagreed with market direction). Damage is **being too sure when wrong**.

Full write-up: feature-branch `docs/plans/2026-08-22-so-gamelog-calibration-diagnosis.md`  
Repro: `python scripts/diagnose_so_gamelog_calibration.py`

---

## Fix landed on feature branch (`dab83c8`) — not merged

Gamelog path now:
1. **Empirical-Bayes shrink** BF/K toward league priors
2. **Wider workload dispersion** when starts are thin
3. **Soften win_prob toward 0.5** by sample reliability
4. Hash bumped to **`so-starter-gamelog-v2`** (so old v1 settled rows stay distinguishable)

Replay example: Schlittler-like 0.782 → **0.541** (no longer more extreme than market ~0.59).

Tests: **35 passed** including `tests/test_so_shrinkage.py`.

---

## Merge gate (unchanged policy)

**Do not merge PR #104** until settled **v2** sample shows independent ≤ market Brier (or user overrides).

Next eval after new packs under v2 (needs master to run code — so either wait for merge of *projection-only* shrink without Kelly promotion, or continue shadow measurement after a targeted master cherry-pick of shrinkage without `promote_independent_so_sizing`).

### Practical options for next agent
1. **Preferred:** Cherry-pick / split PR so master gets **v2 shrinkage only** (audit independent probs improve) while **live Kelly promotion stays off**.
2. Keep collecting settlements; re-run:
   ```powershell
   Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
   python -m outlier_scrapers.so_eval --db C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3 --require-gamelog-hash
   ```
3. Live pipeline remains `C:\Users\dasil\Dev\GitHub\outlier` on `master`.
