# Shadow settle — 2026-09-20 (Week 2, 1pm Tier-1)

| Metric | Value |
|---|---|
| predictions | 328 |
| settled | 237 (W 171 / L 65 / P 1) |
| hit rate | 0.725 |
| Brier (market) | **0.209** |
| logloss (market) | 0.611 |
| Brier (model_p Laplace α=2) | **0.205** |
| logloss (model_p) | 0.606 |
| CLV | ok vs snapshot (mean 0.0); `close_source=pregame_snapshot_best_odds` — **not** book close |

## model_p comparison (same 236 scored rows)

| Variant | model Brier | vs market |
|---|---|---|
| Raw empirical L10 (`empirical_hit_rate`) | 0.238 | +0.029 worse |
| Laplace α=2 (`empirical_hit_rate_laplace`) | **0.205** | **−0.005 better** |
| Hierarchy (nflverse gamelog → Laplace) | 0.217 | +0.008 worse |

Default α=2 is pre-specified (add-2). Grid on this slate alone: α∈[1.5,12] beat market; α=4 min (~0.199) — not locked without more Sundays.

Projection coverage on pack: 229/328 `projection_nflverse_rate` (only Week 1 prior); remainder Laplace. Hierarchy does **not** beat pure Laplace yet on this thin sample.

## How produced

```bash
python -m outlier_nfl.enrich_close \
  --predictions artifacts/nfl/nfl_high_prob_props_2026-09-20_1pm.json \
  --out artifacts/nfl/nfl_high_prob_props_2026-09-20_1pm_laplace.json \
  --mode snapshot_best --attach-model-p empirical_hit_rate_laplace --alpha 2.0 --overwrite-model-p

python -m outlier_nfl.settle \
  --predictions artifacts/nfl/nfl_high_prob_props_2026-09-20_1pm_laplace.json \
  --boxscores artifacts/nfl/boxscores_2026-09-20_nflverse.json \
  --out-json docs/nfl/artifacts/shadow_settle_2026-09-20.json \
  --out-md docs/nfl/artifacts/shadow_settle_2026-09-20.md
```
