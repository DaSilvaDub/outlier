# Handoff Report: Specification Mining for Outlier NCAAFB Props & Insights

**Agent:** `spec_miner_survey_3`  
**Working Directory:** `C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3`  
**Target Worktree:** `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Branch:** `feat/outlier-props-insights`  
**Handoff Type:** Hard  
**Timestamp:** 2026-09-06T15:37:00Z  

---

## 1. Observation

1. **Step 0 Multi-Ent Synchronization:**
   Executed `& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"`.
   Full report ended with verbatim output:
   ```
   === VERDICT ===
   REPORT STATUS: OK
     bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)

   RUN-NONCE: 681be614a2d74aa2  utc=2026-09-06T15:27:17Z  head=6b5181c  status=OK
   ```

2. **R1 Discovery Probe & Fixtures:**
   - Git log in `cfb-analytics-worktrees/outlier-props-insights`:
     Commit `a9371c3` landed `feat(discovery): R1 discovery probe script, fixtures, and findings`.
   - File `docs/probes/2026-09-05-ncaafb-props-discovery.md` lines 14–19 records:
     ```markdown
     - **Milestone M0 Gate Status:** PARTIAL_PASS
     - **Gamelines Resolution:** CONFIRMED_FUNCTIONAL
     - **Team Props Resolution:** UNOFFERED
     - **Player Props Resolution:** UNOFFERED
     - **Insights Endpoint Status:** EMPTY_200
     ```
   - Payloads in `tests/fixtures/outlier/`:
     - `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_TEAM_PROP.json`: `{"markets": []}`
     - `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_PLAYER_PROP.json`: `{"markets": []}`
     - `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_insights.json`: `{"insights": []}`
     - `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_GAMELINE.json`: 25 SPREAD cards, 26 TOTAL cards across 3 games, median 13 books per game.

3. **Database Migration State:**
   - File `cfb_analytics/db.py:512-522`:
     ```python
     MIGRATIONS: tuple[tuple[int, str, str, Callable[[sqlite3.Connection], None] | None], ...] = (
         (1, "outlier_ingestion_core", MIGRATION_001, None),
         (2, "games_football_date", MIGRATION_002, _backfill_football_date),
         (3, "market_consensus_and_movement", MIGRATION_003, None),
         (4, "cfbd_team_history", MIGRATION_004, None),
         (5, "cfbd_fundamentals", MIGRATION_005, None),
         (6, "feature_rows", MIGRATION_006, None),
         (7, "game_venue_id_and_weather", MIGRATION_007, _backfill_game_venue_id),
         (8, "players_and_passing", MIGRATION_008, None),
         (9, "internal_team_ratings", MIGRATION_009, None),
     )
     ```
     `git diff origin/master...HEAD -- cfb_analytics/db.py` is empty. The latest migration on both `master` and `HEAD` is 9. Next available is **Migration 10**.

4. **CHECK Constraint on `odds_snapshots`:**
   - File `cfb_analytics/db.py:106`:
     ```sql
     market TEXT NOT NULL CHECK (market IN ('ML','SPREAD','TOTAL')),
     ```
     Prevents storing props without a table rebuild.

5. **Existing `snapshot_id` Formula:**
   - File `cfb_analytics/sources/outlier.py:71-76`:
     ```python
     @property
     def snapshot_id(self) -> str:
         return stable_id(
             self.source, self.game_id, self.book, self.market, self.side,
             self.line, self.price_american, self.captured_utc,
         )
     ```
     Uses 8 positional arguments joined by `\x1f`.

6. **Test Suite Baseline & Tooling:**
   - `pytest`: 491 passed in 34.18s.
   - `ruff check .`: `All checks passed!`.
   - `mypy cfb_analytics`: `Success: no issues found in 40 source files`.
   - `pyright cfb_analytics`: 1 typing issue in `store.py:45:55` (`"__name__" is not a known attribute of "None"`).
   - Test coverage on outlier modules: `outlier_ingest.py` 99%, `outlier.py` 73% (overall 83%).

---

## 2. Logic Chain

1. **Migration Number:**
   - Observation 3 shows `MIGRATIONS` has exactly 9 elements (1 through 9).
   - No migration 10 exists in `db.py` or git history.
   - Therefore, the next migration is **Migration 10** (`MIGRATION_010`).

2. **Table-Rebuild Requirement:**
   - Observation 4 shows `odds_snapshots.market` has `CHECK (market IN ('ML','SPREAD','TOTAL'))`.
   - SQLite does not support modifying a table's CHECK constraint via `ALTER TABLE`.
   - Any attempt to insert `market = 'POINTS'` fails with a SQLite integrity violation.
   - Therefore, Migration 10 must use SQLite's table rebuild procedure (create `odds_snapshots_v2`, copy data, drop old, rename).

3. **`snapshot_id` Preservation:**
   - Observation 5 shows `snapshot_id` is computed via `stable_id` using 8 specific fields.
   - Changing the argument order or adding default fields to the tuple for existing gamelines would change the sha256 digest.
   - Therefore, for gamelines where `team_id` is `None`, the 8 arguments must remain unchanged. For team props, `team_id` must be appended conditionally to avoid primary key collisions between Home and Away team props.

4. **Dedicated `prop_consensus` vs `market_consensus`:**
   - `market_consensus` has `PRIMARY KEY (game_id, market, line, side, as_of_utc)`.
   - A game has two opposing teams that can have identical lines/sides for `POINTS` (e.g. 28.5 OVER).
   - Without `team_id` in the primary key, one team's consensus would overwrite the other's.
   - Adding `team_id` to `market_consensus` would require rebuilding `market_consensus` and risks breaking consumers (`board`, `backtest`).
   - Therefore, creating a dedicated `prop_consensus` table with `PRIMARY KEY (game_id, team_id, market, line, side, as_of_utc)` is the cleanest, lowest-risk design.

5. **Graceful Degradation for Unoffered Feeds:**
   - Observation 2 proves `TEAM_PROP`, `PLAYER_PROP`, and `insights` return empty lists in live/replay fixtures.
   - Ingestion must not crash or fail when encountering empty payloads.
   - Each event's prop fetch must be isolated in a `try...except SourceError` block and recorded in `source_health`.

---

## 3. Caveats

1. **Player Props Out of Scope:** While the Outlier API has a `PLAYER_PROP` token, R2 §61 explicitly designates NCAAF player props out of scope. Player props must be dropped at parser level and not admitted to `odds_snapshots`.
2. **Current Feed Empty State:** Because the live Outlier NCAAFB feed currently returns `{"markets": []}` for `TEAM_PROP` on the sampled 2026-09-05 slate, unit tests for team prop parsing and consensus must use synthetic test fixtures adhering to Outlier's documented payload structure (as demonstrated in `tests/test_outlier_parsing.py`).
3. **Pyright Single Warning:** `pyright cfb_analytics` identified 1 typing issue in `cfb_analytics/ingest/store.py:45` regarding `exc_type.__name__`. This is a pre-existing 1-line typing fix for the implementation team.

---

## 4. Conclusion

The specification for Outlier NCAAFB Props & Insights is complete, unambiguous, and ready for implementation:
- **R2:** Whitelist defined as module-level frozensets (`ALLOWED_MARKET_TYPES`, `ALLOWED_MARKETS`, `ALLOWED_SCOPES`, `ALLOWED_SIDES_BY_MARKET`). Out-of-scope markets (`PLAYER_PROP`, `DOUBLE_RESULT`, etc.) are dropped.
- **R3:** Migration 10 rebuilds `odds_snapshots` to widen the CHECK constraint and add `team_id`, `player_id`, `scope`, `market_type`. Dedicated `prop_consensus` table created. Existing `snapshot_id` generation preserved for gamelines.
- **R4:** Traps 1 and 2 are fully analyzed with fixture evidence. Books are parsed directly from `odds[].book`; cards are unioned and deduplicated on `(book, market, side, line)`.
- **R5:** `ingest_slate()` accepts `with_props=False` and `with_insights=False` (default off), isolates errors per event, records to `source_health`, and enforces `min_books_for_consensus = 3`.
- **Quality Gates:** 491 tests pass, `ruff` and `mypy` are clean. Implementation must achieve `>=80%` test coverage on modified modules.

Detailed architectural specifications, DDL, and code blueprints are written in `C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\analysis.md`.

---

## 5. Verification Method

To independently verify these findings:
1. Check migration state in target worktree:
   ```powershell
   git -C "C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights" log -n 5 --oneline -- cfb_analytics/db.py
   ```
2. Verify test baseline and ruff/mypy:
   ```powershell
   cd "C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights"
   pytest
   ruff check .
   mypy cfb_analytics
   ```
3. Inspect discovery probe report:
   ```powershell
   Get-Content "C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights\docs\probes\2026-09-05-ncaafb-props-discovery.md" -Head 50
   ```
4. Read comprehensive specification document:
   `C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\analysis.md`
