
## Codebase Quirk: `normalizer.implied_probability()`
When working with `outlier_scrapers`, note that `normalizer.implied_probability(price)` returns a **percentage** (e.g., `52.5` for -110), NOT a decimal probability (e.g., `0.525`). If you are passing this value into a field that expects a standard `[0, 1]` probability (such as those checked by `feedback.py`), you must divide the result by 100.

## Codebase Quirk: Push Probability Adjustments
When working with probabilities in `pack.py`, `game_totals.py`, or similar projection modules, consensus probabilities derived from devigged line odds (e.g., `devig_decimal` or `p_over_headline`) are conditional on a definitive win/loss (i.e., assuming no push occurs). To determine the true absolute win probability for a given side, you must multiply the conditional probability by `(1.0 - push_prob)`. Ensure this adjustment is made before populating probability output columns (like `model_prob` or `market_consensus_prob`) or feeding them into final sizing/edge calculations, otherwise the sizing logic will dangerously overestimate the edge on push-capable lines.

## Codebase Quirk: Outlier API JSON Control Characters
When using `json.loads()` to parse raw responses from the Outlier API (e.g., in `api.py`), ALWAYS pass `strict=False`. The upstream API occasionally returns invalid unescaped control characters in string fields, which will crash the standard `json.loads()` parser if strict mode is enforced.
