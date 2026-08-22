# Gamelog SO miscalibration diagnosis (2026-08-22)

## Sample
Settled rows with `projection_feature_hash = so-starter-gamelog-v1`: **n=7**  
Script: `scripts/diagnose_so_gamelog_calibration.py`  
JSON dump: `docs/plans/2026-08-22-so-gamelog-calibration-diagnosis.json`

## Headline
| Metric | Market | Independent (raw gamelog) |
|---|---|---|
| Brier | 0.282 | **0.403** |
| Hit rate | 2/7 | **1/7** |
| More extreme than market | — | **7/7** |

## Mechanism
1. **Extreme projected means from thin recent starts**  
   Example: Cam Schlittler OVER 5.5 had distribution mean **K≈8.25** → win_prob **0.782**. Actual K=4.
2. **Dispersion too tight** for L3–L8 samples (default workload_dispersion=20), so probability mass did not admit large misses.
3. **No league prior / reliability shrink**, so noisy rates translated directly into near-certain OVER/UNDER calls.
4. Not primarily a side-disagreement bug: only 1/7 disagreed with market direction (Yamamoto); the damage is **overconfidence when wrong**.

## Examples
- Schlittler OVER 5.5: market 0.59 / indep 0.78 / actual 4 → both wrong; indep much worse Brier
- Melton OVER 4.5 (PLAY 1u): market 0.55 / indep 0.68 / actual 3
- Peralta UNDER 5.5: market 0.56 / indep 0.72 / actual 6
- Rodriguez UNDER 5.5: only clear indep win (actual 4)

## Fix on feature branch
In `mlb_so_projection_record` gamelog path:
1. `shrink_starter_so_features` — empirical Bayes BF/K toward league (`SO_RATE_PRIOR_BF=90`, `SO_BF_PRIOR_STARTS=4`)
2. Wider `workload_dispersion` when starts are thin
3. `soften_so_win_probability` — shrink decisive win/loss mass toward 0.5 by sample reliability

Replay of Schlittler-like inputs: raw 0.782 → shrunk **0.541** (no longer more extreme than market 0.59).

## Still blocked for merge
Even after this fix, **do not merge live Kelly promotion** until a fresh settled sample with the shrunk model shows `prefer_independent=true` (or user override). Re-run after next packs settle under the new code (or offline replay once features are stored).
