# WNBA Independent Projection Coverage Expansion

## Outcome

Expand the existing WNBA shadow projection lane from points-only to the player
markets already admitted by the pipeline: points (`PTS`), rebounds (`REB`),
assists (`AST`), points+rebounds (`PR`), points+assists (`PA`),
rebounds+assists (`RA`), and points+rebounds+assists (`PRA`). Preserve the
current fail-closed recommendation, probability, Kelly, and sizing contracts.

The attached limitation report measured independent-model coverage at 24 of 223
player opportunities and projection means at 40 of 223. Those figures are an
input snapshot, not a live acceptance target. The pipeline will now emit its own
per-market coverage counts so future claims can be made from the dated artifact.

## Current production seam

1. `refresh --projections` calls `projections.export_projections()`.
2. The exporter reads the dated normalized props feed and writes
   `<league>_projections_latest.json`.
3. `pack.index_projections()` joins by outcome identity and
   `apply_shadow_projection()` validates event, market, sport, line, and side.
4. Audit-only feature hashes expose distribution summaries but do not populate
   `independent_model_prob`, blended probability, edge, Kelly, units, or
   actionability.

The previous WNBA adapter parsed only minutes and points, filtered out every
other market before fetching, and used the audit-only
`wnba-minutes-ppm-v1` hash.

## Implemented first slice

- Parse aligned minutes, points, rebounds, and assists from the existing ESPN
  WNBA athlete gamelog adapter, accepting normalized name aliases.
- Build per-player, per-minute features once per slate and reuse them across all
  priced outcomes and supported market families.
- Create distribution-first shadow records for `PTS`, `REB`, `AST`, `PR`, `PA`,
  `RA`, and `PRA`. Combination means are composed from their base-stat rates;
  the betting line is never a model feature.
- Mark every record with `wnba-gamelog-stat-rates-v2`, which remains explicitly
  audit-only.
- Emit artifact-local coverage telemetry with supported and projected counts by
  market family.
- Keep legacy points helper functions as compatibility wrappers.

## Validation and promotion roadmap

### Phase 1 — shadow collection (implemented)

Collect dated distributions and coverage without changing executable
probabilities or stakes. Fail closed on unresolved players, fewer than three
aligned games, invalid minutes, missing base stats, unsupported markets, stale
slate dates, duplicate outcome IDs, or join mismatches.

### Phase 2 — walk-forward datasets

Extend the existing projection training framework rather than creating a second
ledger. Build one pregame sample per player/game/market family using only games
strictly before the target game. Store feature schema hash, as-of time, player
identity, opponent/context inputs, projected minutes, component rates, realized
stat, and source provenance. Do not use market price, line, recent hit rate, or
future games as features.

### Phase 3 — market-family calibration

Fit and validate separate WNBA artifacts for `PTS`, `REB`, `AST`, and
combination families. Use chronological holdouts and report sample count,
log-loss/Brier score at actual historical lines, calibration gap, interval
coverage, mean absolute error, and performance against market consensus.
Combination markets require their own validation because summing marginal rates
does not establish a calibrated joint distribution.

### Phase 4 — guarded promotion

Promotion must be explicit, artifact-backed, and market-family scoped. A market
remains audit-only when its artifact is missing, stale, schema-incompatible,
insufficient, or fails validation. Promotion may allow
`independent_model_prob` to participate in the existing learned blend; it must
not bypass liquidity, predictive-signal, projection-conflict, portfolio, or
actionability gates.

## Acceptance criteria

- Every supported normalized WNBA market is either projected or counted as an
  uncovered opportunity in the dated artifact.
- One player with multiple markets performs one identity lookup and one gamelog
  fetch per season/slate run.
- Missing or malformed stats produce no projection for the affected market.
- New WNBA hashes leave independent probability, blended probability, edge,
  Kelly, units, and actionability unchanged.
- Existing projection identity/date validation and projection-conflict behavior
  remain intact.
- Focused projection and pack tests, Ruff, MyPy, and the broader offline test
  suite pass before promotion or merge.
