# Project: cfb-analytics Outlier Props & Insights Integration

## Architecture
- **Database & Storage (`cfb_analytics/db.py`)**:
  - SQLite ledger with forward migration chain (Schema version 9 -> 10).
  - Table-rebuild migration for `odds_snapshots` widening with `team_id`, `player_id`, `scope`, `market_type` and expanding the market CHECK constraint to allow team props (`POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, `RUSHING_YARDS`).
  - Dedicated `prop_consensus` table keyed by `(game_id, team_id, market, line, side, as_of_utc)` to avoid primary key collisions with gamelines.
  - Deterministic `snapshot_id` generation preserved for gamelines, deterministic ID for team props.
- **Source & Parser (`cfb_analytics/sources/outlier.py`)**:
  - `OutlierClient` methods for fetching markets by `marketType` (`GAMELINE`, `TEAM_PROP`, `PLAYER_PROP`, `GAME_PROP`) and `/insights`.
  - Whitelist enforcement: `ALLOWED_MARKET_TYPES`, `ALLOWED_MARKETS`, `ALLOWED_SCOPES`, `ALLOWED_SIDES_BY_MARKET`.
  - Feed Trap 1 resolution: Books read strictly from `odds[].book`, never `outcome.books`.
  - Feed Trap 2 resolution: Multi-card proposition spanning unioned and deduplicated by `(book, market, side, line)`.
  - Team prop attribution matching team IDs and name substring.
  - Graceful degradation: empty payloads `{"markets": []}` and `{"insights": []}` yield empty collections without raising errors.
- **Ingestion & Orchestration (`cfb_analytics/ingest/outlier_ingest.py`, `cfb_analytics/cli.py`)**:
  - Extended `ingest_slate(..., with_props=False, with_insights=False)`.
  - Per-event failure isolation logging to `source_health` and `IngestSummary.market_failures`.
  - Prop consensus calculation with `min_books_for_consensus = 3` floor.
  - CLI flags `--with-props` and `--with-insights` defaulting to False.
- **Isolated Boundaries (FORBIDDEN ELO FILES)**:
  - DO NOT TOUCH: `cfb_analytics/features/elo_ratings.py`, `team_ratings.py`, `asof.py`, `qb.py`, `build_market.py`, `market.py`, `models/ridge.py`, `shrinkage.py`, `linalg.py`, `devig.py`, `cfb_analytics/backtest/*`, `cfb_analytics/sources/cfbd.py`, `cfb_analytics/ingest/cfbd_*`, and corresponding Elo/backtest/cfbd test files.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | R1 Discovery Probe & Fixtures | Document probe findings, commit replay fixtures, verify empty props/insights payloads and traps | M1 | ORIGINAL_REQUEST §R1 |
| 2 | R2 Whitelist & Scopes | Define explicit frozensets for market_type, market, scope, allowed sides, drop unwhitelisted | M2 | ORIGINAL_REQUEST §R2 |
| 3 | R3 Migration 010 & Schema | 12-step table rebuild for odds_snapshots, new prop_consensus table, preserve gameline snapshot_ids | M2 | ORIGINAL_REQUEST §R3 |
| 4 | R4 Parser Trap 1 (Book Order) | Read book identity from odds[].book, never outcome.books | M3 | ORIGINAL_REQUEST §R4 |
| 5 | R4 Parser Trap 2 (Multi-Card Union) | Union and deduplicate market cards across propositions by (book, market, side, line) | M3 | ORIGINAL_REQUEST §R4 |
| 6 | R4 Team Prop Parsing & Attribution | Parse team props (POINTS, YARDS) and attribute to home/away team_id | M3 | ORIGINAL_REQUEST §R4 |
| 7 | R4 Insights Handling | Fetch insights endpoint, handle empty 200 payload gracefully, do not synthesize mock insights | M3 | ORIGINAL_REQUEST §R4 |
| 8 | R5 Ingestion & Summary Reporting | Ingest props/insights behind flags (default off), isolate event failures, build IngestSummary | M4 | ORIGINAL_REQUEST §R5 |
| 9 | R5 Prop Consensus Floor | Enforce min_books_for_consensus = 3 on prop consensus | M4 | ORIGINAL_REQUEST §R5 |
| 10 | R5 CLI Integration | Expose --with-props and --with-insights on ingest CLI command | M4 | ORIGINAL_REQUEST §R5 |
| 11 | Quality Gates & PR Delivery | Zero Elo touches, 100% replay tests pass, coverage >=80% on touched files, ruff/mypy/pyright clean, 1 PR | M5 | ORIGINAL_REQUEST Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Discovery Verification | Verify R1 probe findings, cleanliness, and fixture replay readiness | none | DONE |
| 2 | Schema Migration 010 & Whitelist | Implement Migration 010, prop_consensus, and whitelist definitions in db.py / models | M1 | IN_PROGRESS |
| 3 | Outlier Props & Insights Parser | Implement parser in outlier.py handling traps 1 & 2, team props, and empty insights | M2 | PLANNED |
| 4 | Ingestion Pipeline & CLI Integration | Implement ingest_slate flags, failure isolation, prop consensus, CLI options | M3 | PLANNED |
| 5 | Verification, Quality Gates & PR | Run full suite, coverage check, ruff/mypy/pyright, git push, open PR | M4 | PLANNED |

## Interface Contracts
### `cfb_analytics/db.py` (Migration 010)
- `MIGRATION_010`: Rebuilds `odds_snapshots` to widen columns:
  - `market_type TEXT NOT NULL DEFAULT 'GAMELINE'`
  - `scope TEXT NOT NULL DEFAULT 'full_game'`
  - `team_id INTEGER NULL REFERENCES teams(team_id)`
  - `player_id TEXT NULL`
  - CHECK constraint: `market IN ('ML','SPREAD','TOTAL','POINTS','OFFENSIVE_YARDS','RECEIVING_YARDS','RUSHING_YARDS')`
- `prop_consensus` table:
  - `PRIMARY KEY (game_id, team_id, market, line, side, as_of_utc)`
  - Foreign keys to `games(game_id)` and `teams(team_id)`.
- `compute_snapshot_id(game_id, book, market, side, line, price, as_of_utc, team_id=None)`:
  - For gamelines (team_id is None): identical 8-tuple hash as before.
  - For team props (team_id is not None): incorporates team_id deterministically.

### `cfb_analytics/sources/outlier.py`
- Constants:
  - `ALLOWED_MARKET_TYPES = frozenset({"GAMELINE", "TEAM_PROP"})`
  - `ALLOWED_MARKETS = frozenset({"SPREAD", "TOTAL", "ML", "POINTS", "OFFENSIVE_YARDS", "RECEIVING_YARDS", "RUSHING_YARDS"})`
  - `ALLOWED_SCOPES = frozenset({"full_game", "first_half"})`
  - `ALLOWED_SIDES = {"ML": frozenset({"HOME", "AWAY"}), "SPREAD": frozenset({"HOME", "AWAY"}), "TOTAL": frozenset({"OVER", "UNDER"}), ...}`
- Methods:
  - `fetch_event_markets(event_id, market_type="GAMELINE") -> dict`
  - `fetch_event_insights(event_id) -> list[dict]`
  - `parse_market_odds(payload, game_context) -> list[OddsSnapshot]`
  - `parse_insights(payload) -> list[dict]`

### `cfb_analytics/ingest/outlier_ingest.py`
- `ingest_slate(session, date_str, with_props=False, with_insights=False) -> IngestSummary`:
  - `with_props`: if True, probes `TEAM_PROP` for each event on slate.
  - `with_insights`: if True, probes `/insights` for each event on slate.
  - Returns `IngestSummary` with prop rows by family, distinct books, and failures.

## Code Layout
- Working worktree: `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`
- Database: `cfb_analytics/db.py`
- Sources: `cfb_analytics/sources/outlier.py`
- Ingest: `cfb_analytics/ingest/outlier_ingest.py`
- CLI: `cfb_analytics/cli.py`
- Tests: `tests/test_outlier_props.py`, `tests/test_outlier_ingest.py`, `tests/test_db_migrations.py`
- Docs: `docs/probes/2026-09-05-ncaafb-props-discovery.md`
