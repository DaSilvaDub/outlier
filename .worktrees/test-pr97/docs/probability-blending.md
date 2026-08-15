# Learned market/model probability blending

Outlier combines separate market and independent probabilities through a versioned
artifact learned from settled feedback rows:

```text
final_probability = market_weight * market_probability
                  + model_weight  * independent_probability
model_weight = 1 - market_weight
```

The fitter minimizes Brier loss on settled, pregame W/L snapshots. It learns a global
weight and sample-size-shrunk weights separately for league, market type, American-odds
range, time before game, and data-quality tier. Every applied weight, segment, source,
and artifact version is retained in the pack and permanent ledger.

## Safety contract

- Missing, invalid, or insufficient-history artifacts mean 100% market weight.
- Push-capable rows are fit on conditional non-push probabilities.
- Post-start snapshots, pushes, missing probabilities, event starts, or segment values
  are excluded from training.
- Projection join errors remain fail-closed.
- Pack generation only reads frozen JSON; it never fits the live ledger.
- An artifact generated after the row's `as_of` time is rejected, preventing historical
  rebuilds from using future results.
- The v4 migration recovers segment metadata from retained pack artifacts without
  changing finalized decisions or historical probabilities.

## Fit and activate weights

```powershell
python -m outlier_scrapers.feedback fit-blend `
  --db calibration/feedback.sqlite3 `
  --output calibration/blend_weights.json `
  --min-samples 30 `
  --prior-strength 30
```

Pack generation reads `calibration/blend_weights.json` by default; use
`--blend-weights` for an alternate artifact. Larger minimum samples are more
conservative for noisy markets, while prior strength controls shrinkage toward the
global weight.
