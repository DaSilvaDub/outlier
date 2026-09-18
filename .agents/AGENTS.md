
## Codebase Quirk: `normalizer.implied_probability()`
When working with `outlier_scrapers`, note that `normalizer.implied_probability(price)` returns a **percentage** (e.g., `52.5` for -110), NOT a decimal probability (e.g., `0.525`). If you are passing this value into a field that expects a standard `[0, 1]` probability (such as those checked by `feedback.py`), you must divide the result by 100.

## Codebase Quirk: Push Probability Adjustments
When working with probabilities in `pack.py`, `game_totals.py`, or similar projection modules, consensus probabilities derived from devigged line odds (e.g., `devig_decimal` or `p_over_headline`) are conditional on a definitive win/loss (i.e., assuming no push occurs). To determine the true absolute win probability for a given side, you must multiply the conditional probability by `(1.0 - push_prob)`. Ensure this adjustment is made before populating probability output columns (like `model_prob` or `market_consensus_prob`) or feeding them into final sizing/edge calculations, otherwise the sizing logic will dangerously overestimate the edge on push-capable lines.

## Codebase Quirk: Outlier API JSON Control Characters
When using `json.loads()` to parse raw responses from the Outlier API (e.g., in `api.py`), ALWAYS pass `strict=False`. The upstream API occasionally returns invalid unescaped control characters in string fields, which will crash the standard `json.loads()` parser if strict mode is enforced.

## Codebase Quirk: NFL Consensus Line Selection vs. Alternate Ladders
When extracting player props from `data/NFL/normalized/nfl_props_*.json`, the Outlier dataset often contains multiple alternate ladder lines (e.g., +750 to -900 odds) and exchange quotes for the same player and market. Never pick the consensus line solely by `sum(len(books))`, as alternate ladders can aggregate large book counts across non-standard lines. Always enforce a balanced two-way market check: both `OVER` and `UNDER` must exist within normal betting juice (`-220 <= odds <= +180`), minimizing the deviation from -110. For touchdown markets (`ANYTIME_TD`), select `line == 0.5` and `position == 'OVER'`.

## NFL Game Script Calibration Heuristics (Learned from BUF 41 - DET 31)
1. **Deficit-Risk Discount on Road Underdog RB Rushing Lines:** If a team is a road underdog (+4.5 or greater) facing a high-scoring favorite (Team Total >= 28.0), apply a **15% downward volume haircut** to the running back's projected rushing attempts and yardage. When an underdog falls behind by two scores, run volume collapses; pivot exposure to **Anytime TD** or **Receiving Props**, which remain active in catch-up mode.
2. **Two-High Shell Target Divergence in Comeback Mode:** When a favorite establishes a multi-score lead, defenses play deep two-high Cover-2/Cover-4 shells. This neutralizes vertical perimeter deep threats (aDOT >= 14.0, e.g. Jameson Williams), while funneling increased target volume (+20%) to intermediate slot receivers (e.g. Amon-Ra St. Brown) and pass-catching tight ends (e.g. Sam LaPorta).
3. **Empirical Hit Rate Priority (L5/L10):** Props passing strict filters of L5 Hit Rate = 100% and L10 Hit Rate >= 80% across multi-book consensus consistently outperform pure algorithmic projection models; allocate Tier-1 staking priority to these anchors.

## Windows PowerShell Python Execution & UTF-8 Console Encoding
1. **Avoid Complex Quotes in Terminal One-Liners:** In Windows PowerShell, running inline Python scripts (`python -c "..."`) with nested quotes or `$()` frequently fails with parser errors. Write scratch scripts to `<appDataDir>\brain\<conversation-id>/scratch/` instead.
2. **Stdout Encoding Constraint:** Python on Windows defaults stdout to `cp1252`, which raises `UnicodeEncodeError` when printing Unicode characters like `↳` (`\u21b3`) or em-dashes `—`. Scripts that print formatted output must call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` and favor standard ASCII text.

