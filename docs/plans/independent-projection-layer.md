# Independent MLB and WNBA Projection Layer

## Summary

Build a deterministic statistical-modeling layer between normalized data collection and cards/pack generation. It creates full discrete outcome distributions, preserves bookmaker consensus as a separate baseline, and initially runs in shadow mode so existing ranking and sizing remain unchanged.

The implementation starts from synchronized `origin/master` in a new feature worktree. It does not invoke the AI Research Desk or paid reasoning providers.

## Data and model architecture

- Add canonical player, team, event, market, and scope IDs at normalization. Unresolved player identities may be recorded with quality flags but cannot produce live-eligible projections.
- Add cached provider adapters with source timestamps, provenance, retries, rate limits, atomic snapshots, and manual JSON/CSV overrides:
  - MLB Statcast and park data from [Baseball Savant](https://baseballsavant.mlb.com/csv-docs) and [park factors](https://baseballsavant.mlb.com/leaderboard/statcast-park-factors?type=year).
  - Weather from the [NWS API](https://www.weather.gov/documentation/services-web-api), with explicit overrides for roofs and unsupported venues.
  - WNBA minutes, usage, lineups, on/off, opponent and pace data from [official WNBA stats](https://stats.wnba.com/players/traditional/), [usage views](https://stats.wnba.com/players/usage/), and [team advanced data](https://stats.wnba.com/teams/advanced/).
  - Injury, lineup, umpire, roof, bullpen, and teammate-availability overrides remain auditable inputs when no reliable structured source is available.
- Build a three-season-plus-current historical backfill with exponential recency weighting and date-based walk-forward splits. Raw datasets remain ignored; compact versioned JSON coefficients, calibration maps, feature-schema hashes, and training manifests are committed.
- Use `numpy`/`scipy` for inference and optional `pandas`/`statsmodels` training dependencies. Artifacts remain JSON rather than pickle.
- Every projection contains a bounded PMF, tail mass, mean, variance, quantiles, model version, training cutoff, feature snapshot hash, coverage percentage, missing/stale features, and deterministic simulation seed.

## Market models and probability semantics

### MLB

- Pitcher strikeouts: NB2/Poisson-gamma count model with projected batters faced, innings/pitches, K/BB rates, handedness, opponent K profile, expected lineup, and pitch-mix interactions.
- Hits allowed: NB2 using projected workload, contact quality, handedness, lineup quality, park, and environment.
- Batter total bases: compound plate-appearance model with an explicit zero hurdle and categorical 1B/2B/3B/HR outcomes.
- Game and team totals: correlated team-run distributions using negative-binomial marginals and a shared game-level latent factor; include starters, bullpens, lineups, park, weather, umpire, travel, and rest.
- NRFI/YRFI: first-inning bivariate count model from top-of-order lineups, starter first-time-through performance, and game environment. Normalize it as `GAME_PROP/FIRST_INNING_RUN` with `first_inning` scope. If no priced Outlier market exists, emit probability-only output with `NO_MARKET_PRICE`.

### WNBA

- Model a bounded minutes mixture for active, normal, foul-trouble, and blowout scripts using starting status, availability, rotation depth, rest, and schedule density.
- Simulate pace and team opportunities, then model points as compound scoring events, rebounds/assists as opportunity-share beta-binomial counts, and 3PM from attempts and conversion rate.
- Use shared pace, role, and game-script factors plus fitted residual correlation so PTS, REB, AST, and 3PTS are joint draws. Derive PR, PA, RA, and PRA from those same draws.
- Defer steals, blocks, turnovers, fantasy scores, and DD/TD markets from v1.

Betting lines and odds are never predictive inputs. WNBA expected margin/blowout probability comes from the internal team-strength model.

For discrete outcome `X` and line `L`, store unconditional `P(win)`, `P(push)`, and `P(loss)=1-win-push`; integer-line push is `P(X=L)`. Over/under tails come directly from the PMF.

## Pipeline and public contracts

- Add `python -m outlier_scrapers.projections` commands:
  - `backfill --sport --from --to`
  - `train --sport --as-of`
  - `project --sport --date`
  - `validate --sport --artifact`
- Add projection-feature and projection steps after normalized props/games and before cards. Shadow-mode source failures do not block the existing pack; live-enabled market families fail closed individually.
- Write timestamped and `latest` projection/status artifacts under each league and freeze pack-local `projections.jsonl`.
- Extend candidate and totals schemas with `independent_push_prob`, `independent_edge_pct`, model/distribution identifiers, mean/variance/quantiles, feature coverage/hash, and projection-quality flags.
  - `market_consensus_prob` remains bookmaker-derived.
  - `independent_model_prob` is the model’s unconditional selected-side win probability.
  - `final_blended_prob`, `edge_pct`, `push_prob`, and sizing remain consensus-driven in shadow mode; when manually promoted, they use the independent probability partition.
- Add selection channel C for promoted independent-model edges. A/B behavior remains intact; overlapping opportunities are deduplicated and annotated with all qualifying channels. The model quota defaults to the existing EV quota, and the promoted minimum edge defaults to 3%.
- Preserve the current game/team-total market ladder as the market baseline. Add independent over/under/push fields without changing the legacy `projected_over_prob` meaning.
- Migrate feedback storage to freeze model version, training cutoff, feature/distribution hashes, quantiles, and PMF snapshot identity. Never rewrite historical projections.
- Existing reasoning request hashes already include candidates and totals ledgers; because every decision-visible projection summary and provenance field is added to those CSVs, model changes invalidate reasoning caches automatically. `projections.jsonl` is audit-only and is not passed to reasoning runners.

## Test and rollout plan

- Distribution tests: nonnegative mass, sum-to-one including tail, monotonic tails, valid win/push/loss partitions, stable seeds, and finite parameters.
- MLB tests: workload sensitivity, handedness/pitch-mix effects, total-bases compound outcomes, correlated run totals, integer pushes, and NRFI/YRFI complementarity.
- WNBA tests: minutes bounds, 200 regulation team-minute conservation, scratch/starting/blowout changes, opportunity conservation, and combo props matching joint samples.
- Data tests: canonical ID joins, source provenance, override precedence, stale critical-feature rejection, historical cutoff leakage prevention, and incompatible artifact rejection.
- Integration tests: daily ordering, shadow mode producing no ranking/sizing regression, fixture-enabled channel C, totals compatibility, runner hash changes, immutable feedback capture, and probability-only NRFI rows.
- Validate with time-based backtests using log loss, Brier score, calibration curves, CRPS/count error, and coverage by sport/market family. Promotion remains a manual config change after review of these reports.
- Default all market families to shadow mode. Missing critical starter, lineup, minutes, or availability inputs make a projection ineligible for live use; optional missing context uses a documented neutral value plus a quality flag.

## Assumptions and defaults

- v1 covers both sports, with MLB strikeouts/hits allowed/total bases/game totals/NRFI-YRFI and WNBA PTS/REB/AST/3PTS plus their combinations.
- Data acquisition uses public/official adapters plus cached/manual overrides; no licensed provider is required for the initial implementation.
- Historical fitting is reproducible and walk-forward; raw source data and local caches are not committed.
- Shadow mode is the default, with manual promotion per sport and market family.
- All existing pregame, freshness, fetch-error, and push-aware sizing gates remain authoritative.
