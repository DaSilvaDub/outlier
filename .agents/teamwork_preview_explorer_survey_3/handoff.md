# Handoff Report — Explorer 3: NFL Market Coverage, Normalization, and Verification Strategy

## 1. Observation
1. **Repository Sync & HEAD**: Executed `& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"` in Step 0. Output verified: `REPORT STATUS: OK`, `head=a3ad900`, `RUN-NONCE: 3602dcb5deee4c1e`.
2. **Existing Normalizer Coupling**:
   - `outlier_scrapers/normalizer.py:20-29`: `ALLOWED_MLB_PLAYER_PROPS = frozenset({"SO"})`, `ALLOWED_MLB_TEAM_PROPS = frozenset({"R", "TOTAL"})`.
   - `outlier_scrapers/normalizer.py:441-443`: MLB player props hard-filtered: `if config.league_id == "MLB": if market not in ALLOWED_MLB_PLAYER_PROPS: continue`.
   - `outlier_scrapers/normalizer.py:687-689`: MLB team props hard-filtered: `if config.league_id == "MLB" and market_type == "TEAM_PROP": if canonical_market not in ALLOWED_MLB_TEAM_PROPS: continue`.
3. **Existing Registry Taxonomy**:
   - `outlier_scrapers/registry.py:317-347`: `SPORTS` dict contains only `"MLB"` and `"WNBA"` enabled by default; `"NBA"` is disabled. No NFL entry exists.
   - `outlier_scrapers/team_totals.py:12-19`: `TEAM_TOTAL_PROPOSITIONS` covers `"MLB"`, `"WNBA"`, `"NBA"`, and `"NHL"`, but lacks `"NFL"`.
4. **Outlier API Odds Structures**:
   - `sample_market.json:32-60`: Shows raw market outcomes containing `position: "OVER"`, `line: 1.5`, and `odds: [{"book": "UNDERDOG", "american": "-102", "decimal": 1.9764}]`.
   - `outlier_scrapers/normalizer.py:324-375`: Demonstrates that props use `outcome.get("bookOdds")` while games use `outcome.get("odds")`.
5. **Codebase Quirks**:
   - `outlier_scrapers/normalizer.py:66-76`: `implied_probability(american_odds)` calculates probability as a percentage (e.g. `52.5`), requiring `/ 100.0` for standard unit probabilities.
   - `outlier_scrapers/game_totals.py:90-109`: Demonstrates push capability flags and divergence thresholds. Key numbers in football (3, 7) require `(1.0 - push_prob)` adjustment on integer spreads and totals.
   - `.agents/AGENTS.md:20-22`: `json.loads()` on Outlier API responses requires `strict=False`.
6. **Testing Suite**:
   - Running `python -m pytest tests/test_api_pagination.py` succeeded with exit code 0 (`5 passed in 0.57s`, Python 3.13.12, pytest-9.0.2).
   - Fixtures in `tests/fixtures/` (`mlb_player_props.json`, `mlb_schedule.json`, etc.) demonstrate standard offline test patterns.

## 2. Logic Chain
1. *From Observation 2 and 3*: The existing `outlier_scrapers` codebase is specifically hardcoded with MLB and WNBA filtering logic (e.g., pitcher SO-only whitelist, runs/strikeouts team prop limits). In addition, city abbreviations like `DET`, `SF`, `ARI`, and `MIA` correspond to different franchises in baseball vs football. Therefore, per requirement R1, attempting to shoehorn NFL into `outlier_scrapers` would risk regression of existing MLB/WNBA invariants. Creating `outlier_nfl` as a standalone module is the architecturally sound and necessary solution.
2. *From Observation 4 and Requirement R3*: Outlier organizes betting markets into `GAMELINE` (game totals, spreads, moneyline), `TEAM_PROP` (team totals), and `PLAYER_PROP`. The normalizer for `outlier_nfl` must support all three market types while unifying the differing odds formats (`bookOdds` vs `odds`).
3. *From Observation 5*: Because football margins cluster heavily on 3 and 7, push probabilities on integer spreads (`-3.0`, `+3.0`, `-7.0`, `+7.0`) and integer totals (`44.0`, `47.0`) are substantial (8–15%). Failing to adjust conditional probabilities via `(1.0 - push_prob)` will produce erroneous edges and Kelly unit recommendations.
4. *From Observation 6 and Acceptance Criteria*: Repository tests are structured to run completely offline without hitting live networks or LLM providers. By creating synthetic fixtures (`nfl_schedule.json`, `nfl_player_props.json`, `nfl_games.json`) in `tests/fixtures/`, comprehensive unit tests (`tests/test_nfl_*.py`) and the standalone verification script (`verify_nfl_pipeline.py`) can validate all market parsing, schema adherence, and end-to-end execution offline and deterministically.

## 3. Caveats
1. No live Outlier NFL API probe was executed in this turn to avoid hitting network or unauthorized endpoints; taxonomy is derived from Outlier API documentation, `sample_market.json`, prior NCAAF probe findings in `docs/plans/2026-08-31-ncaaf-analytics-pipeline.md`, and standard sportsbook conventions.
2. The exact availability of specific niche player props (e.g. Defensive Tackles or Kicking Points) varies by bookmaker and season timing; core passing, rushing, receiving, and TD props are universally available.

## 4. Conclusion
The NFL market coverage, normalization architecture, and verification strategy are fully investigated and detailed in `report.md`. The design satisfies requirements R1, R3, and Acceptance Criteria without impacting MLB/WNBA code paths.

## 5. Verification Method
1. Inspect report: View `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_3\report.md`.
2. Inspect test suite baseline: Run `python -m pytest tests/test_api_pagination.py` (passes 5/5).
3. Invalidation Conditions: Any design that imports or alters `outlier_scrapers/normalizer.py` MLB whitelists, fails to normalize 32 NFL teams, omits push probability adjustments on integer spreads, or requires live network access during `pytest` runs is invalid.
