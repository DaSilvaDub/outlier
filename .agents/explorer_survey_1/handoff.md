# Handoff Report: cfb-analytics Worktree Architecture Survey

**Agent:** `explorer_survey_1`  
**Working Directory:** `C:\Users\dasil\Dev\GitHub\outlier\.agents\explorer_survey_1`  
**Target Repository:** `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Branch:** `feat/outlier-props-insights` (HEAD: `a9371c3`)  
**Type:** Hard (Task complete)  

---

## 1. Observation

1. **Git State & Recent Commits:**
   - Command `git log -n 15 --oneline` inside `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights` returned:
     ```
     a9371c3 feat(discovery): R1 discovery probe script, fixtures, and findings
     d6d1102 tune(ridge): retune ridge_lambda from 25 to 0.5, re-validate live
     e934283 feat(models): recency weighting and early-season shrinkage prior for ridge
     ffeb0f5 feat(backtest): wire the Elo-only baseline into the moneyline backtest
     9848a9c fix(db): checkpoint WAL on close; refuse to publish an empty database
     ...
     ```
   - Commit `a9371c3` landed `docs/probes/2026-09-05-ncaafb-props-discovery.md`, `scripts/probe_ncaafb_outlier.py`, raw JSON replay fixtures in `tests/fixtures/outlier/`, and `tests/test_fixtures_cleanliness.py`.

2. **R1 Discovery Probe Findings (`docs/probes/2026-09-05-ncaafb-props-discovery.md`):**
   - Lines 14–19:
     ```markdown
     - **Milestone M0 Gate Status:** PARTIAL_PASS
     - **Gamelines Resolution:** CONFIRMED_FUNCTIONAL
     - **Team Props Resolution:** UNOFFERED
     - **Player Props Resolution:** UNOFFERED
     - **Insights Endpoint Status:** EMPTY_200
     - **Recommendation for M1:** Proceed to M1 with baseline gamelines and graceful degradation for unoffered props/insights; do not synthesize mock feeds.
     ```
   - Probed tokens: `GAMELINE` returns active markets with median 13 books on `SPREAD`/`TOTAL` and 9 on `MONEYLINE`. `TEAM_PROP`, `PLAYER_PROP`, `GAME_PROP` returned empty envelopes (`{"markets": []}`). `/insights` returned empty envelopes (`{"insights": []}`).
   - Trap 1 (Non-Parallel Books) verified: in event `d3c27ca7c2343f149a695c5a810549c9cc4d6426`, `outcome.books[0]` was `MIDNITE` while `outcome.odds[0].book` was `DRAFTKINGS`.
   - Trap 2 (Multi-Row Proposition Spanning) verified: `SPREAD` spanned 25 market cards; `TOTAL` spanned 26 market cards per event.

3. **Database Schema & Migrations (`cfb_analytics/db.py`):**
   - Lines 512–522: Migrations tuple contains 9 migrations:
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
   - Lines 100–113 (`odds_snapshots` DDL):
     ```sql
     CREATE TABLE IF NOT EXISTS odds_snapshots (
         snapshot_id     TEXT PRIMARY KEY,
         game_id         TEXT NOT NULL REFERENCES games(game_id),
         market_id       TEXT,
         book            TEXT NOT NULL,
         captured_utc    TEXT NOT NULL,
         market          TEXT NOT NULL CHECK (market IN ('ML','SPREAD','TOTAL')),
         side            TEXT,
         line            REAL,
         price_american  INTEGER,
         price_decimal   REAL,
         is_primary      INTEGER,
         source          TEXT NOT NULL
     );
     ```
   - Lines 175–193 (`market_consensus` DDL): PK is `(game_id, market, line, side, as_of_utc)`.
   - Deterministic ID generator in `cfb_analytics/utils.py:84-93`:
     ```python
     def stable_id(*parts: Any) -> str:
         payload = "\x1f".join("" if p is None else str(p) for p in parts)
         return hashlib.sha256(payload.encode("utf-8")).hexdigest()
     ```
   - `OddsRow.snapshot_id` property (`cfb_analytics/sources/outlier.py:71-75`):
     ```python
     @property
     def snapshot_id(self) -> str:
         return stable_id(
             self.source, self.game_id, self.book, self.market, self.side,
             self.line, self.price_american, self.captured_utc,
         )
     ```

4. **Outlier Client & Ingestion Pipeline (`cfb_analytics/sources/outlier.py`, `cfb_analytics/ingest/outlier_ingest.py`, `cfb_analytics/cli.py`):**
   - Outlier client: `fetch_schedule`, `fetch_event_markets(event_id, market_type="GAMELINE")`, `fetch_team_injuries(team_id)`.
   - `parse_odds_rows` currently only handles `MONEYLINE`, `SPREAD`, `TOTAL` and discards all other propositions.
   - `ingest_slate` accepts `(conn, client, slate_date, *, with_odds=True, with_injuries=True, limit=None)`.
   - CLI `ingest` subcommand accepts `--date`, `--no-odds`, `--no-injuries`, `--limit`.

5. **Test Suite Status (`pytest -q`):**
   - All tests pass (exit code 0): 500+ assertions passed in under 10 seconds.
   - `tests/conftest.py:8-22` enforces hermetic testing: `CFB_DATA_DIR=tmp_path / "data"`, `CFB_HTTP_MODE=replay`, `CFBD_API_KEY=""`.

---

## 2. Logic Chain

1. **Gate Evaluation:**
   - Observation 2 demonstrates that Milestone M0 (R1) discovery probe is fully executed and committed in `a9371c3`.
   - The probe verified that `GAMELINE` is functional with depth across 13+ sportsbooks, while `TEAM_PROP`, `PLAYER_PROP`, and `/insights` are currently unoffered (empty 200) for `NCAAFB`.
   - Therefore, the project proceeds to Milestone M1 under the R1 verdict: implement props ingestion infrastructure with strict whitelist dropping and graceful degradation for empty feeds, without creating synthetic mock data.

2. **Schema & Migration Planning:**
   - Observation 3 shows the highest registered migration is `version = 9` (`internal_team_ratings`).
   - Therefore, the next migration is **Migration 010**.
   - `odds_snapshots` has a strict SQLite CHECK constraint: `CHECK (market IN ('ML','SPREAD','TOTAL'))`.
   - Inserting any row with `market='POINTS'` or other props will trigger an `IntegrityError`.
   - Because SQLite does not support `ALTER TABLE ... DROP CONSTRAINT`, Migration 010 must execute a table-rebuild migration or instantiate dedicated prop tables (`prop_odds_snapshots`, `prop_consensus`).
   - If rebuilding `odds_snapshots`, existing rows copied into the new table must compute identical `snapshot_id` values to ensure complete backward compatibility.

3. **Ingestion & CLI Plumbing:**
   - Observation 4 shows `ingest_slate` and `_cmd_ingest` only expose `--no-odds` and `--no-injuries`.
   - Wiring props and insights requires adding `with_props: bool = False` and `with_insights: bool = False` to `ingest_slate`, and `--with-props` and `--with-insights` flags to the `ingest` CLI command.
   - Failures during prop fetching must be caught, recorded in `source_health` table, and added to `IngestSummary.market_failures` without interrupting gameline ingestion.

4. **Elo Engine Boundary Protection:**
   - Observation 1 shows extensive recent work on `master` for ridge ratings, weekly CFBD Elo backfills, and walk-forward backtesting.
   - Touching any of the 20 modeling/backtest/CFBD modules or their 14 corresponding test files risks breaking the user's calibrated engine.
   - All prop ingestion and schema work must be confined strictly to `cfb_analytics/db.py`, `cfb_analytics/sources/outlier.py`, `cfb_analytics/ingest/outlier_ingest.py`, `cfb_analytics/ingest/store.py`, `cfb_analytics/cli.py`, and new/extended outlier tests.

---

## 3. Caveats

1. **Live Network Probing Outside Worktree:**
   - Investigation was performed read-only in hermetic replay mode (`CFB_HTTP_MODE=replay`) using fixtures committed in `a9371c3`. No live network calls were made, complying with offline and reasoning-off rules.
2. **Prop Offerings Timing:**
   - R1 confirmed that Outlier offered no team props on the sampled `2026-09-05` slate. If Outlier introduces college football player/team props closer to kickoff or later in the season, the parser whitelist will be ready to ingest them without breaking when they are empty.
3. **Elo Files Prohibition:**
   - Elo and ridge backtest files were inspected read-only to map boundaries; no modifications were made or proposed to those files.

---

## 4. Conclusion

1. **Worktree Status:** Ready for implementation. The discovery gate (R1) is satisfied with committed evidence.
2. **Next Database Version:** Migration 010. Must perform a table rebuild of `odds_snapshots` (or introduce dedicated prop tables) to allow whitelisted markets (`POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, `RUSHING_YARDS`).
3. **Whitelist Enforcement:** Strict whitelist frozenset must drop all non-whitelisted props at the parser boundary.
4. **Boundary Isolation:** Exactly 20 production files and 14 test files are designated as **FORBIDDEN ELO FILES** and are strictly out of bounds.

---

## 5. Verification Method

1. **Run Full Test Suite:**
   ```powershell
   cd C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights
   pytest -q
   ```
   *Expected outcome:* All 500+ tests pass with exit code 0.

2. **Verify Fixture Cleanliness:**
   ```powershell
   pytest tests/test_fixtures_cleanliness.py
   ```
   *Expected outcome:* Passes with 0 assertion failures, confirming no JWT tokens or credentials exist in fixtures.

3. **Verify Git Branch and Status:**
   ```powershell
   git status
   git log -n 3 --oneline
   ```
   *Expected outcome:* Branch is `feat/outlier-props-insights`, working tree clean, HEAD is `a9371c3`.

4. **Verify Detailed Analysis Document:**
   Inspect `C:\Users\dasil\Dev\GitHub\outlier\.agents\explorer_survey_1\analysis.md` for full architectural breakdown and forbidden files inventory.
