# Handoff Report: Outlier NCAAFB Props & Insights Implementation

**Agent**: `worker_1`  
**Working Directory**: `C:\Users\dasil\Dev\GitHub\outlier\.agents\worker_1`  
**Target Worktree**: `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Branch**: `feat/outlier-props-insights`  

---

## 1. Observation

- **Step 0 Multi-Agent Sync**:
  Command: `& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"`
  Output trailer:
  ```text
  REPORT STATUS: OK
    bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)
  RUN-NONCE: 8755a863d02a46f5  utc=2026-09-06T15:50:02Z  head=6b5181c  status=OK
  ```
- **Initial Test State (TDD Red State)**:
  Command: `pytest tests/test_outlier_props.py`
  Result: `18 failed, 15 passed in 3.32s`
  Errors: Missing `ALLOWED_*` whitelist frozensets, missing `team_id`/`scope`/`market_type` on `OddsRow`, missing Migration 010, missing `with_props` keyword argument on `ingest_slate`.
- **Files Modified**:
  - `cfb_analytics/db.py`:
    Lines 512-588: Implemented `MIGRATION_010` with 12-step table rebuild for `odds_snapshots` widening schema (`team_id TEXT REFERENCES teams(team_id)`, `player_id TEXT REFERENCES players(player_id)`, `scope TEXT NOT NULL DEFAULT 'full_game' CHECK (scope IN ('full_game', 'first_half'))`, `market_type TEXT NOT NULL DEFAULT 'GAMELINE' CHECK (market_type IN ('GAMELINE', 'TEAM_PROP'))`) and expanding CHECK constraint to allow `'ML', 'SPREAD', 'TOTAL', 'POINTS', 'OFFENSIVE_YARDS', 'RECEIVING_YARDS', 'RUSHING_YARDS'`. Created `prop_consensus` table with `PRIMARY KEY (game_id, team_id, market, line, side, as_of_utc)`. Registered `(10, "props_and_insights_schema", MIGRATION_010, None)` in `MIGRATIONS`.
  - `cfb_analytics/sources/outlier.py`:
    Lines 43-85: Added module-level frozensets `ALLOWED_MARKET_TYPES`, `ALLOWED_MARKETS`, `ALLOWED_SCOPES`, `ALLOWED_SIDES_BY_MARKET`. Updated `PROPOSITION_TO_MARKET` to include team props. Widened `OddsRow` with `team_id`, `player_id`, `scope`, `market_type`, and updated `snapshot_id` property to append `team_id` deterministically while preserving exact 8-part hash for gamelines (`team_id is None`).
    Lines 150-165: Added `fetch_event_insights()` method to `OutlierClient` and added `parse_insights()`.
    Lines 170-320: Updated `parse_odds_rows()` to handle Traps 1 & 2, drop unwhitelisted market types/scopes/propositions/sides, and resolve team props to `home_team_id` or `away_team_id` via `teamId` or team name/alias matching.
  - `cfb_analytics/ingest/store.py`:
    Lines 350-395: Updated `insert_odds()` to dynamically detect widened columns via `PRAGMA table_info(odds_snapshots)` and insert `team_id, player_id, scope, market_type` while remaining backward-compatible with v9 schemas. Fixed `exc_type` handling in `RunRecorder.__exit__` for pyright type checking.
  - `cfb_analytics/ingest/outlier_ingest.py`:
    Lines 25-180: Extended `IngestSummary` with `prop_rows`, `prop_rows_by_family`, `insights_rows`, `prop_books`, `prop_failures`, `insight_failures`. Extended `ingest_slate()` with `with_props=False` and `with_insights=False` keyword arguments. Implemented `_ingest_props()` and `_ingest_insights()` with per-event failure isolation and `source_health` reporting.
  - `cfb_analytics/cli.py`:
    Lines 80-86: Passed `with_props` and `with_insights` in `_cmd_ingest`.
    Lines 509-514: Added `--with-props` and `--with-insights` options to `ingest` CLI command.
  - `tests/test_client_and_cli.py`:
    Lines 176-210: Added 3 unit tests verifying CLI argument parsing and execution with `--with-props` and `--with-insights`.
- **Zero Touches to Forbidden Files**:
  Verified via `git status` and `git diff --stat`: zero modifications to any Elo engine code (`cfb_analytics/features/elo_ratings.py`, `team_ratings.py`, `asof.py`, `qb.py`, `build_market.py`, `market.py`, `models/ridge.py`, `shrinkage.py`, `linalg.py`, `devig.py`, `cfb_analytics/backtest/*`, `cfb_analytics/sources/cfbd.py`, `cfb_analytics/ingest/cfbd_*`, and `tests/test_elo_*`, `test_backtest_*`, `test_cfbd_*`).
- **Post-Implementation Verification Results**:
  1. `pytest tests/test_outlier_props.py`: `33 passed in 2.99s` (100% pass).
  2. Full test suite: `pytest`: `524 passed in 111.97s` (zero regressions across all 524 tests).
  3. `ruff check .`: `All checks passed!` (0 errors).
  4. `mypy cfb_analytics`: `Success: no issues found in 40 source files`.
  5. `pyright cfb_analytics`: `0 errors, 0 warnings, 0 informations`.
  6. Code coverage (`pytest --cov=cfb_analytics`):
     - `cfb_analytics/db.py`: 92% (target: >=80%)
     - `cfb_analytics/ingest/outlier_ingest.py`: 90% (target: >=80%)
     - `cfb_analytics/sources/outlier.py`: 83% (target: >=80%)

---

## 2. Logic Chain

1. **Migration 010 Integrity**:
   - In SQLite, table constraints cannot be altered in-place. A 12-step table rebuild (`odds_snapshots_v2` -> copy -> drop -> rename) was implemented to widen the `market` CHECK constraint to include team props (`POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, `RUSHING_YARDS`) and add `team_id`, `player_id`, `scope`, and `market_type`.
   - Existing gameline rows default to `team_id=NULL`, `player_id=NULL`, `scope='full_game'`, `market_type='GAMELINE'`. Because `OddsRow.snapshot_id` computes the exact same 8-tuple hash when `team_id` is None and `scope` is `'full_game'`, every pre-existing snapshot ID is preserved 100% hash-identical across Migration 010. Verified by `TestSchemaMigration10.test_migration_10_applies_forward_from_v9_preserving_snapshot_ids`.
   - Dedicated `prop_consensus` table with `PRIMARY KEY (game_id, team_id, market, line, side, as_of_utc)` prevents collisions between Home and Away team props sharing identical lines. Verified by `TestSchemaMigration10.test_migration_10_prop_consensus_table_schema`.
2. **Parser Trap 1 (Non-Parallel Book Attribution)**:
   - Reading `entry["book"]` from inside each element of `outcome["odds"]` rather than indexing into `outcome["books"]` guarantees that sportsbooks are attributed their genuine quotes even when Outlier's `books` list is in arbitrary order. Verified against live fixture in `TestTrap1BookAttribution`.
3. **Parser Trap 2 (Multi-Card Union & Deduplication)**:
   - Iterating all cards for a proposition and deduplicating by `(book, market, side, line, team_id, scope)` unions all sportsbooks across partitioned market cards while eliminating duplicate quotes. Verified in `TestTrap2MultiCardUnionAndDeduplication`.
4. **Target Whitelist & Side Enforcement**:
   - Market types outside `{"GAMELINE", "TEAM_PROP"}` (e.g. `PLAYER_PROP`, `GAME_PROP`), propositions outside `ALLOWED_MARKETS` (e.g. `DOUBLE_RESULT`, `MONEYLINE_THREE_WAY`, `WINNING_MARGIN`), scopes outside `{"full_game", "first_half"}`, and sides not in `ALLOWED_SIDES_BY_MARKET` are strictly dropped. Verified in `TestWhitelistFiltering`.
5. **Team Prop Attribution**:
   - For `TEAM_PROP`, candidates are matched against `home` and `away` team IDs, schools, aliases, and names. Quotes with positions other than `OVER` and `UNDER` or for unrecognized teams are dropped. Distinct `snapshot_id`s are generated for Home vs Away props with identical lines. Verified in `TestTeamPropsAttribution`.
6. **Graceful Degradation & Failure Isolation**:
   - Empty payloads `{"markets": []}` and `{"insights": []}` return empty collections without error.
   - When fetching props for an event raises `SourceError`, the error is recorded in `source_health` and `summary.market_failures`, while the remainder of the slate and gameline odds continue without interruption. Verified in `TestGracefulDegradation`.
7. **Consensus Floor & Ingestion Summary**:
   - `build_consensus(..., min_books_for_consensus=3)` flags thin markets with `< 3` books and computes full consensus with `>= 3` books.
   - `IngestSummary` tracks `prop_rows`, `prop_rows_by_family`, distinct books, and failures, formatted cleanly in `as_text()`. Verified in `TestPropConsensus` and `TestIngestionSummary`.

---

## 3. Caveats

- Outlier currently does not publish NCAAF player props (`PLAYER_PROP` returns `{"markets": []}`); per R2 §61 player props are explicitly out of scope and dropped.
- Outlier's `/insights` endpoint currently returns HTTP 200 with empty list `{"insights": []}` for NCAAFB; per R1 and R4 findings, no synthetic insights are generated and empty payloads degrade cleanly.
- No caveats regarding implementation integrity: all logic is genuine, deterministic, stdlib-only, and fully verified against replay fixtures.

---

## 4. Conclusion

All acceptance criteria and tasks for Milestone 2–5 are 100% satisfied:
- Migration 010 implemented and verified.
- Outlier props & insights parser implemented with Traps 1 & 2 resolved, whitelist frozensets enforced, and team props attributed.
- Slate ingestion pipeline extended with `--with-props` and `--with-insights` flags, per-event error isolation, and prop metrics.
- All 33 tests in `tests/test_outlier_props.py` pass.
- All 524 tests in full test suite pass with 0 regressions.
- `ruff check .`, `mypy cfb_analytics`, and `pyright cfb_analytics` pass with zero errors.
- Coverage on touched modules exceeds the 80% threshold (`db.py` 92%, `outlier_ingest.py` 90%, `outlier.py` 83%).
- Zero touches to Elo engine code.

---

## 5. Verification Method

To independently reproduce and verify all results:

1. **Verify git status shows only allowed files modified and no forbidden files touched**:
   ```powershell
   cd C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights
   git status
   ```
2. **Run new props & insights test suite (all 33 tests pass)**:
   ```powershell
   pytest tests/test_outlier_props.py
   ```
3. **Run full test suite (all 524 tests pass, zero regressions)**:
   ```powershell
   pytest
   ```
4. **Run linters and typecheckers**:
   ```powershell
   ruff check .
   mypy cfb_analytics
   pyright cfb_analytics
   ```
5. **Run coverage check on touched modules**:
   ```powershell
   pytest --cov=cfb_analytics tests/test_outlier_props.py tests/test_outlier_parsing.py tests/test_ingest.py tests/test_db_and_store.py tests/test_client_and_cli.py
   ```
