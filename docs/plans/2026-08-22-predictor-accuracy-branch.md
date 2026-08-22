# Predictor accuracy upgrades (feature branch)

**Branch:** `feat/predictor-accuracy-upgrades`  
**Worktree:** `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades`  
**Base:** `41358bb` on `origin/master`  
**Purpose:** Continue accuracy upgrades without touching the live `master` pipeline checkout.

## Do not use this worktree for daily_job

Run the production slate from:

```text
C:\Users\dasil\Dev\GitHub\outlier   (branch master)
```

This worktree is for development/tests only until explicitly merged.

## Landed on this branch

1. **Promote gamelog SO into live sizing** (`slate_quality.promote_independent_so_sizing`)
   - Requires `projection_feature_hash == so-starter-gamelog-v1`, independent prob, predictive signal
   - Caps at 2u; sets `model_prob_source=independent_gamelog_so`
2. **Align LM stale window to 6h** (matches `feed_health.MAX_SOURCE_AGE_HOURS`)
3. **CLV join fallbacks** in `results._latest_local_close` (any-book, then market_id+selection)
4. **SO offline eval harness** `python -m outlier_scrapers.so_eval` (Brier vs market; no portfolio enable)
5. **Richer SO context hooks** (`opponent_k_rate`, `park_k_factor` optional blends)
6. **Opponent K% fetcher** — `fetch_team_batter_k_rate` via MLB Stats API team hitting SO/PA; attached in `enrich_probable_with_so_features`
7. **Park K factor** — curated `HOME_PARK_K_FACTORS` by home team; applied from pitcher's venue (`home_away`)
8. **WNBA minutes/PPM scaffold wired into pack** — ESPN search + gamelog → audit-only projection (`projection_audit_wnba_minutes`); hash is **not** independent-eligible
9. **Pack UNSAFE refusal** — already present on master (`build_feed_health_by_league` raises); documented, no change needed

## Still open before merge

- Refresh park factors from live Savant CSV when a stable endpoint is available
- Run `so_eval` once enough gamelog-settled rows exist; only then consider merging live independent SO sizing
- Keep `config/portfolio_risk.json` in shadow until post-whitelist recalibration
- Full projection layer (hits allowed, totals, NRFI, channel C) remains plan-level

## Verify

```powershell
Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
python -m pytest tests/test_predictor_gates.py tests/test_so_eval_and_context.py tests/test_pitcher_so_features.py -q --tb=line
python -m outlier_scrapers.so_eval
```
