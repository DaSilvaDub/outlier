
## Codebase Quirk: `normalizer.implied_probability()`
When working with `outlier_scrapers`, note that `normalizer.implied_probability(price)` returns a **percentage** (e.g., `52.5` for -110), NOT a decimal probability (e.g., `0.525`). If you are passing this value into a field that expects a standard `[0, 1]` probability (such as those checked by `feedback.py`), you must divide the result by 100.

## Codebase Quirk: Push Probability Adjustments
When working with probabilities in `game_totals.py` or similar projection modules, consensus probabilities (e.g., `p_over_headline`) derived from devigged line odds are conditional on a definitive win/loss (i.e., assuming no push occurs). To determine the true absolute win probability for a given side, you must multiply the conditional probability by `(1.0 - push_prob)`. Ensure this adjustment is made before populating probability output columns (like `market_consensus_prob`) or using them in final sizing/edge calculations.
