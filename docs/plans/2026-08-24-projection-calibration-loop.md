# Projection calibration loop and funnel width (Tier 3, items 8–11)

Closes the four Tier-3 audit findings that kept the independent projection layer
from producing anything a bet could be sized from.

## 8 — Projections run for every sport with a model

`refresh --projections` and the `daily_job` step already existed for MLB. WNBA
projections were computed inline inside `pack` only, so `wnba_projections_latest.json`
was never written and the shadow points model left no artifact to audit.

- `export_projections()` now handles MLB (starter pitcher SO from game logs) and
  WNBA (minutes × points-per-minute scaffold), writing
  `data/<LEAGUE>/normalized/<league>_projections_latest.json` before `pack`.
- `build_wnba_points_projections()` fetches features once per player, and only
  for points rows, so a slate costs one ESPN call per player rather than one per
  priced outcome.

## 9 — `backfill` / `train` / `validate` are implemented

`outlier_scrapers/projection_training.py` replaces the `scaffold-ready` stubs.

```bash
# 1. Supervised dataset: one row per start, features from earlier starts only.
python -m outlier_scrapers.projections backfill --sport MLB --from 2024-03-20 --to 2026-08-20

# 2. Walk-forward fit; writes an UNPROMOTED artifact.
python -m outlier_scrapers.projections train --sport MLB --as-of 2026-08-01

# 3. Score it on samples after the training cutoff.
python -m outlier_scrapers.projections validate --sport MLB

# 4. Manual, audited promotion — the only step that changes live inference.
python -m outlier_scrapers.projections promote --sport MLB --actor "<name>"
```

Artifacts live under `calibration/projections/`. The backfilled dataset
(`MLB_so_samples.jsonl`) is gitignored — it is large and fully regenerable. The
fitted `MLB_so_model.json` and `MLB_so_validation.json` are small and carry the
promotion audit trail, so they can be committed.

**Backfill.** Crawls full-season rosters and pitching game logs from the free
`statsapi.mlb.com` endpoints the projection layer already uses. Each start
becomes one sample whose features come from `compute_starter_so_features_from_logs`
over *earlier* starts only — the same function inference calls, so the fit sees
exactly the features the daily pack will feed it, and outcome leakage is
structurally impossible. A dead endpoint costs one pitcher, not the run.

**Train.** Fits the parameters that were previously hand-set: league K rate and
starter workload (estimated directly), the rate-shrinkage prior and BF prior
(grid, selected on a later chronological slice), and the two workload-dispersion
parameters (grid, scored with the full mixture PMF under a sample cap). Staged
rather than jointly gridded because only dispersion needs the expensive PMF.
The artifact records `model_version`, `feature_schema_hash`, `trained_through`,
`n_train`, fitted vs. baseline NLL, and `promoted: false`.

**Validate.** Scores the artifact only on samples dated on/after its training
cutoff: count NLL, MAE, RMSE, CRPS, PIT coverage at q10–q90, and over/under
Brier + log loss across the 3.5–8.5 line ladder, each against the shipped
defaults. Verdict is `pass` only when the fit beats baseline on both NLL and
Brier and mean PIT deviation is within tolerance; too few holdout samples yields
`insufficient_data`, never `pass`. Schema or feature-hash mismatch is an error.

**Promotion.** `promote` refuses an artifact without a matching passing
validation report, and appends a who/when/version audit record. Inference reads
the artifact through `load_promoted_so_model()`, which ignores anything not
promoted, not `trained`, or schema-incompatible — so an unpromoted or corrupt
artifact silently leaves the shipped priors in place. A promoted model re-labels
the projection as `so-starter-calibrated-<model_version>`, which is
independent-eligible.

## 10 — The blend refits itself, and can be promoted

`blend_weights.json` sat at `eligible_samples: 72` because `feedback fit-blend`
was a manual command nobody ran.

- `daily_job.refit_blend_weights()` refits from the settled ledger on every run,
  before the pack, and is non-fatal (a failure keeps the previous artifact).
  `--skip-blend-refit` is the diagnostic escape hatch; the manifest records the
  outcome.
- `config/blend_promotion.json` gates whether `final_blended_prob` drives sizing:
  `mode: live` **and** `eligible_samples >= min_eligible_samples` (default 1000)
  **and** any pinned `model_version` matching. Shipped in `shadow`, so today's
  behavior is unchanged and the blend stays audit-only.
- When promoted, `apply_learned_probability_blend()` resizes the row from the
  blended probability (`model_prob_source: blended_market_model`) *before* every
  downstream suppression gate, and never revives a stake that was already
  withheld.

Sample count is the binding constraint, not code: the ledger has to accumulate
thousands of settled rows before `mode: live` is defensible.

## 11 — Funnel widened

`--top-ev-n` default 15 → 40 (`--top-signal-n` unchanged at 10). Portfolio caps
are enforced (`config/portfolio_risk.json` `mode: enforce`), so the risk
allocator prunes the board; the old default cut candidates before any cap saw
them.
