# Handoff Report: CFB Outlier Props & Insights TDD Test Suite

**Agent**: `test_writer_1`  
**Working Directory**: `C:\Users\dasil\Dev\GitHub\outlier\.agents\test_writer_1`  
**Target Worktree**: `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Branch**: `feat/outlier-props-insights`  
**Target Test File**: `tests/test_outlier_props.py`

---

## 1. Observation

- **Step 0 Multi-Agent Sync**:
  Command executed: `& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"`
  Output:
  ```text
  REPORT STATUS: OK
    bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)
  RUN-NONCE: cbcba79ae13543ce  utc=2026-09-06T15:38:07Z  head=6b5181c  status=OK
  ```
- **Requirements & Specifications**:
  Authoritative request read from `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` and architecture from `C:\Users\dasil\Dev\GitHub\outlier\.agents\orchestrator_1\PROJECT.md`.
- **Target Worktree & Isolation Boundaries**:
  Branch verified as `feat/outlier-props-insights`.
  Zero changes made to production code (`cfb_analytics/*`).
  Zero touches to Elo files (`cfb_analytics/features/elo_ratings.py`, `team_ratings.py`, `asof.py`, `qb.py`, `build_market.py`, `market.py`, `models/ridge.py`, `shrinkage.py`, `linalg.py`, `devig.py`, `cfb_analytics/backtest/*`, `cfb_analytics/sources/cfbd.py`, `cfb_analytics/ingest/cfbd_*`).
  Zero modifications made to existing test files (`tests/test_outlier_parsing.py`, `tests/test_ingest.py`, `tests/test_db_and_store.py`, `tests/test_market.py`).
- **New Test Suite Created**:
  `tests/test_outlier_props.py` created containing 33 test cases spanning 9 test classes.
- **Linter Execution**:
  Command: `ruff check tests/test_outlier_props.py`
  Result: `All checks passed!`
- **New Test Suite Run**:
  Command: `pytest tests/test_outlier_props.py`
  Result: `18 failed, 15 passed in 3.97s`
  - 15 tests passed immediately (Traps 1 & 2 on existing parser logic, drop of unwhitelisted proposition strings, empty fixture degradation, consensus math).
  - 18 tests failed as expected under TDD red state (requiring implementer to add Migration 10, whitelist frozensets, team prop attribution with `team_id`, and `with_props=True` in `ingest_slate`).
- **Existing Test Suite Run**:
  Command: `pytest tests/test_outlier_parsing.py tests/test_ingest.py tests/test_db_and_store.py tests/test_market.py`
  Result: `83 passed in 4.91s` (zero regressions).

---

## 2. Logic Chain

1. **Trap 1 Verification**:
   In fixture `tests/fixtures/outlier/event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json`, in market `2688bed6285c63715d286293a2d2802c93b88c51` outcome 0, `outcome["books"][0]` is `"THESCOREBET"` but `outcome["odds"][0]["book"]` is `"DRAFTKINGS"` with price `+625`. Naive index-zipping attributes `+625` to `"THESCOREBET"`. When parsed by `parse_odds_rows`, `"DRAFTKINGS"` is correctly assigned `+625` and `"THESCOREBET"` is assigned `+750` (matching its true odds entry at index 4). Tested in `TestTrap1BookAttribution`.
2. **Trap 2 Verification**:
   In the same fixture, `SPREAD` spans 7 market cards. Individual cards quote between 3 and 13 books. Union across cards captures all 13 distinct books. Deduplication on `(book, market, side, line)` ensures that cards repeating identical book/line combinations produce a strictly deduplicated set of `OddsRow`s. Tested in `TestTrap2MultiCardUnionAndDeduplication`.
3. **Whitelist & Side Enforcement**:
   Per R2 §48-61, `ALLOWED_MARKET_TYPES` must be `frozenset({"GAMELINE", "TEAM_PROP"})`, `ALLOWED_MARKETS` must be `frozenset({"SPREAD", "TOTAL", "ML", "POINTS", "OFFENSIVE_YARDS", "RECEIVING_YARDS", "RUSHING_YARDS"})`, `ALLOWED_SCOPES` must be `frozenset({"full_game", "first_half"})`. Derivatives (`DOUBLE_RESULT`, `MONEYLINE_THREE_WAY`, `WINNING_MARGIN`, quarters, player props, game props) are asserted to return `[]`. Invalid sides (`SPREAD` with `OVER`, `TOTAL` with `HOME`) are asserted to be dropped. Tested in `TestWhitelistFiltering`.
4. **Team Prop Attribution**:
   Team props (`POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, `RUSHING_YARDS`) must attribute to `home_team_id` or `away_team_id` (`t-home` / `t-away`), restrict sides strictly to `OVER` and `UNDER`, and generate distinct `snapshot_id` values when differing only by `team_id`. Tested in `TestTeamPropsAttribution`.
5. **Graceful Degradation**:
   Empty payloads (`event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_TEAM_PROP.json` and `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_insights.json`) parse to empty lists without crashing. An event raising an HTTP error on prop fetch does not abort gamelines or other games on the slate, recording the failure in `source_health` and `IngestSummary.market_failures`. Tested in `TestGracefulDegradation`.
6. **Schema Migration 10**:
   Migration 10 table-rebuild upgrades a v9 database containing existing gameline `odds_snapshots` rows, preserving their exact original `snapshot_id` values and setting `market_type='GAMELINE'`, `scope='full_game'`, `team_id=NULL`, `player_id=NULL`. The widened CHECK constraint admits team props and rejects unwhitelisted markets. The new `prop_consensus` table enforces primary key `(game_id, team_id, market, line, side, as_of_utc)` and foreign keys. Tested in `TestSchemaMigration10`.
7. **Idempotency**:
   Inserting identical team prop rows with `store.insert_odds` produces rowcount 0 on the second pass. Re-running `ingest_slate(conn, client, SLATE, with_props=True)` produces identical total rows. Tested in `TestIdempotency`.
8. **Prop Consensus Floor**:
   Quoting under 3 books triggers `FLAG_THIN_MARKET`. Quoting 3 or more distinct books produces consensus without `FLAG_THIN_MARKET`. Tested in `TestPropConsensus`.
9. **Ingestion Summary**:
   `ingest_slate(..., with_props=True)` populates `prop_rows`, unions distinct books into `summary.books`, logs failures into `summary.market_failures`, and includes prop counts in `summary.as_text()`. Tested in `TestIngestionSummary`.

---

## 3. Caveats

- Tests for unbuilt features (Migration 10, whitelist frozensets, team prop attribution, `with_props=True` flag) fail as expected under TDD until the implementing agent builds them.
- No network requests were made; all tests operate strictly in replay mode (`CFB_HTTP_MODE=replay`) or with fake offline clients and committed fixtures under `tests/fixtures/outlier/`.
- No Elo project files were inspected, modified, or executed.

---

## 4. Conclusion

The comprehensive, opaque-box TDD test suite in `tests/test_outlier_props.py` is fully authored, clean under `ruff check`, aligned with all specifications in `ORIGINAL_REQUEST.md` and `PROJECT.md`, and ready to drive Milestone 2–4 implementation.

---

## 5. Verification Method

To independently verify the test suite:

1. **Verify git status is clean except the new test file**:
   ```powershell
   git status
   ```
2. **Verify linter passes**:
   ```powershell
   ruff check tests/test_outlier_props.py
   ```
3. **Verify new test suite runs**:
   ```powershell
   pytest tests/test_outlier_props.py
   ```
   *Expected: 15 passed, 18 failed (unbuilt features).*
4. **Verify existing test suite passes without regression**:
   ```powershell
   pytest tests/test_outlier_parsing.py tests/test_ingest.py tests/test_db_and_store.py tests/test_market.py
   ```
   *Expected: 83 passed in ~5s.*
