# Architectural Survey & Pipeline Analysis: NCAAF Props & Insights Integration

**Author:** `explorer_survey_1`  
**Date:** 2026-09-06  
**Repository Worktree:** `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Branch:** `feat/outlier-props-insights` (tracking origin/master base `d6d1102`)  
**Scope:** Read-only architectural investigation and boundary definition  

---

## 1. Executive Summary

This report delivers a comprehensive architectural survey of the `cfb-analytics` repository worktree at `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights` to prepare for implementing Outlier player/team props and insights ingestion.

### Key Highlights
1. **R1 Discovery Spike Already Completed in `a9371c3`:**
   - Commit `a9371c35529c90d71551e726c387414500f1247f` completed the R1 discovery spike on the reference slate (`2026-09-05`).
   - Findings documented in `docs/probes/2026-09-05-ncaafb-props-discovery.md`:
     - `GAMELINE` is active and healthy (median 13 books on `SPREAD` / `TOTAL`, median 9 books on `MONEYLINE`).
     - `TEAM_PROP` returned empty envelopes (`{"markets": []}`) on the sampled reference slate (status: `UNOFFERED`).
     - `PLAYER_PROP` returned empty envelopes (`{"markets": []}`) on the sampled reference slate (status: `UNOFFERED`).
     - `/insights` endpoint returned empty envelopes (`{"insights": []}`) with HTTP 200 (status: `EMPTY_200`), triggering the R1 stop condition.
     - Hard gate verdict: **`PARTIAL_PASS`**. Baseline gamelines work; unoffered props/insights must degrade gracefully without mocking or synthesizing feeds.
2. **Database Migration State:**
   - Current schema is at **Migration 009** (`internal_team_ratings`).
   - The next available migration is **Migration 010**.
   - `odds_snapshots` currently enforces `CHECK (market IN ('ML','SPREAD','TOTAL'))` at the SQLite DDL level. To admit props (`POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, `RUSHING_YARDS`), a table rebuild migration (Migration 010) is required.
   - `snapshot_id` generation is strictly deterministic via `cfb_analytics.utils.stable_id(source, game_id, book, market, side, line, price_american, captured_utc)`.
3. **Ingestion Entry Points & CLI:**
   - `cfb_analytics/ingest/outlier_ingest.py` has `ingest_slate(...)` currently accepting `with_odds: bool = True` and `with_injuries: bool = True`.
   - Ingestion entry points need `with_props: bool = False` and `with_insights: bool = False` parameters (defaulting to False).
   - CLI in `cfb_analytics/cli.py` (`_cmd_ingest`) currently exposes `--date`, `--no-odds`, `--no-injuries`, `--limit`. It requires wiring for `--with-props` and `--with-insights`.
4. **Strict Isolation of Elo Codes (FORBIDDEN ELO FILES):**
   - The repository contains an internal Elo and ridge modeling engine, CFBD fundamentals backfill, and walk-forward backtest harnesses.
   - Exactly **20 production files and 14 test files** are identified as **FORBIDDEN ELO FILES** and must NOT be edited.
5. **Test Suite Health:**
   - 100% of existing tests pass cleanly (`CFB_HTTP_MODE=replay` by default in `conftest.py`). Zero network calls during tests.

---

## 2. Git History & Commit Lineage

### 2.1 Branch & Commit Graph
- **Current Branch:** `feat/outlier-props-insights`
- **Worktree Path:** `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`
- **Recent Git Log:**
  - `a9371c3` (HEAD) `feat(discovery): R1 discovery probe script, fixtures, and findings`
  - `d6d1102` (master base) `tune(ridge): retune ridge_lambda from 25 to 0.5, re-validate live`
  - `e934283` `feat(models): recency weighting and early-season shrinkage prior for ridge`
  - `ffeb0f5` `feat(backtest): wire the Elo-only baseline into the moneyline backtest`
  - `9848a9c` `fix(db): checkpoint WAL on close; refuse to publish an empty database`
  - `a472be6` `ci(daily-ingest): add a one-off weekly-Elo backfill dispatch input`
  - `6754193` `fix(fundamentals): weekly CFBD Elo rows were silently dropped, never inserted`
  - `c525163` `feat(backtest): walk-forward moneyline backtest harness for the ridge model`
  - `a607aef` `feat(models): internal ridge team-strength ratings, leakage-safe integration`

### 2.2 Artifacts Committed in `a9371c3`
Commit `a9371c3` landed the complete Milestone M0 (R1) discovery probe:
- `docs/probes/2026-09-05-ncaafb-props-discovery.md`: Formal probe findings report.
- `scripts/probe_ncaafb_outlier.py`: Discovery probe script supporting live and offline replay.
- `tests/fixtures/outlier/`:
  - `schedule_ncaafb.json` (3,111 lines)
  - Raw JSON replay fixtures for 3 sampled events on `2026-09-05`:
    - `d3c27ca7c2343f149a695c5a810549c9cc4d6426` (Cardinals @ Buckeyes)
    - `e04493d90a41b698b3c38d344c417330478d6d9d` (Mean Green @ Hoosiers)
    - `4a8966e71bd7d4f2fd73daa3e80a97c2943b7593` (Thundering Herd @ Nittany Lions)
    - Fixture variants per event: `GAMELINE`, `GAME_PROP`, `PLAYER_PROP`, `TEAM_PROP`, `insights`.
- `tests/test_fixtures_cleanliness.py`: Automated audit ensuring zero JWT tokens, cookies, or auth keys in committed fixtures.

---

## 3. Database Architecture, Schemas, & Migrations (`cfb_analytics/db.py`)

### 3.1 Migration Mechanism
- **Table:** `schema_migrations`
  ```sql
  CREATE TABLE IF NOT EXISTS schema_migrations (
      version     INTEGER PRIMARY KEY,
      name        TEXT NOT NULL,
      applied_utc TEXT NOT NULL
  );
  ```
- **Execution:** `cfb_analytics.db.migrate(conn)` inspects applied versions, executes unapplied SQL scripts in order, runs optional Python hooks in the same transaction, records the applied version into `schema_migrations`, and commits.
- **Connection Management:** `cfb_analytics.db.open_db()` establishes a WAL-mode connection with `PRAGMA foreign_keys=ON`, `PRAGMA synchronous=NORMAL`, auto-migrates if `migrate_on_open=True`, and executes `PRAGMA wal_checkpoint(TRUNCATE)` on close.

### 3.2 Existing Migrations Registry (1 to 9)
| Ver | Migration Identifier | Primary Tables / Changes | Python Hook |
|---|---|---|---|
| **1** | `outlier_ingestion_core` | `runs`, `venues`, `teams`, `games`, `odds_snapshots`, `availability`, `source_health` | None |
| **2** | `games_football_date` | Adds `games.football_date` column | `_backfill_football_date` |
| **3** | `market_consensus_and_movement` | `market_consensus`, `line_movement` | None |
| **4** | `cfbd_team_history` | `team_seasons`, `team_aliases` | None |
| **5** | `cfbd_fundamentals` | `team_ratings`, `team_season_advanced`, `returning_production`, `team_talent` | None |
| **6** | `feature_rows` | `feature_rows` | None |
| **7** | `game_venue_id_and_weather` | Adds `games.venue_id`, creates `weather` table | `_backfill_game_venue_id` |
| **8** | `players_and_passing` | `players`, `player_seasons`, `player_game_passing` | None |
| **9** | `internal_team_ratings` | `internal_team_ratings` (ridge model ratings) | None |

**Conclusion:** The next migration must be **Migration 010**.

### 3.3 Schema Analysis: `odds_snapshots`
Current DDL from Migration 001 (`cfb_analytics/db.py:100-116`):
```sql
CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id     TEXT PRIMARY KEY,  -- deterministic hash: re-ingesting is idempotent
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
CREATE INDEX IF NOT EXISTS idx_odds_game_market ON odds_snapshots(game_id, market, captured_utc);
CREATE INDEX IF NOT EXISTS idx_odds_book ON odds_snapshots(book);
```

#### Bottlenecks & Required Changes
1. **CHECK Constraint:** `CHECK (market IN ('ML','SPREAD','TOTAL'))` strictly rejects all team props (`POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, `RUSHING_YARDS`).
2. **Missing Granularity Columns:** Current table has no `market_type` (`GAMELINE` vs `TEAM_PROP`), no `scope` (`full_game` vs `first_half`), and no `player_id` (nullable).
3. **Table-Rebuild Migration Strategy:**
   Because SQLite does not allow altering or dropping CHECK constraints directly, Migration 010 must execute a table rebuild:
   - Create temporary table `odds_snapshots_v10` with expanded CHECK constraint or broadened text definition, plus any nullable columns (`market_type`, `scope`, `player_id`).
   - Copy all existing rows from `odds_snapshots` into `odds_snapshots_v10`, ensuring identical `snapshot_id` preservation.
   - Drop old `odds_snapshots`.
   - Rename `odds_snapshots_v10` to `odds_snapshots`.
   - Recreate indexes `idx_odds_game_market`, `idx_odds_book`, plus any new prop-friendly index.

### 3.4 Schema Analysis: `market_consensus`
Current DDL from Migration 003 (`cfb_analytics/db.py:175-195`):
```sql
CREATE TABLE IF NOT EXISTS market_consensus (
    game_id        TEXT NOT NULL REFERENCES games(game_id),
    market         TEXT NOT NULL,
    line           REAL,
    side           TEXT NOT NULL,
    as_of_utc      TEXT NOT NULL,
    n_books        INTEGER NOT NULL,
    consensus_price INTEGER,
    best_price     INTEGER,
    best_book      TEXT,
    hold           REAL,
    anchor         TEXT NOT NULL,          -- sharp | all_books | none
    prob_multiplicative REAL,
    prob_shin      REAL,
    prob_power     REAL,
    prob_spread    REAL,                   -- max-min across methods
    flags          TEXT,
    PRIMARY KEY (game_id, market, line, side, as_of_utc)
);
```

#### Structural Risk & Architectural Choice
The primary key is `(game_id, market, line, side, as_of_utc)`.
- If team props are stored here, two teams in the same game could have identical team totals (e.g. both lines 24.5 OVER) unless disambiguated by team or market naming (e.g., `HOME_POINTS` vs `AWAY_POINTS`, or a dedicated `prop_consensus` table).
- As noted in R3 and the probe report: A dedicated `prop_consensus` table or dedicated prop odds schema avoids polluting the gameline consensus pipeline while preserving the strict contract of the M model.

### 3.5 Deterministic `snapshot_id` Generation
Deterministic ID generation is anchored in `cfb_analytics.utils.stable_id`:
```python
def stable_id(*parts: Any) -> str:
    payload = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```
- In `OddsRow` (`cfb_analytics/sources/outlier.py:71-75`):
  ```python
  @property
  def snapshot_id(self) -> str:
      return stable_id(
          self.source, self.game_id, self.book, self.market, self.side,
          self.line, self.price_american, self.captured_utc,
      )
  ```
- In `cfb_analytics/ingest/store.py:344-363` (`insert_odds`):
  Uses `OddsRow.snapshot_id` as primary key for `INSERT OR IGNORE INTO odds_snapshots`.
- **Invariance Guarantee:** Re-ingesting an unchanged capture produces identical SHA256 hashes, ensuring idempotent writes without duplicate rows.

---

## 4. Outlier Source Client & Ingestion (`cfb_analytics/sources/outlier.py`)

### 4.1 Client Configuration & Endpoints
- **League Token:** `NCAAFB` (Control token: `NFL`). Note: `NCAAF`, `CFB`, `FBS`, `CFP` return HTTP 502.
- **Client Class:** `OutlierClient` initialized with `HttpClient`.
- **Existing Endpoints:**
  - `GET /sportsdata/leagues/{token}/schedule`
  - `GET /sportsdata/events/{eventId}/markets?marketType={market_type}`
  - `GET /sportsdata/leagues/{token}/teams/{teamId}/injuries`
- **Missing / Target Endpoints:**
  - `GET /sportsdata/events/{eventId}/insights` (probed in R1: returns empty list `{"insights": []}`).

### 4.2 Documented Feed Traps
1. **Trap 1: Non-Parallel Book and Odds Arrays**
   - In Outlier API outcomes, `outcome["books"]` is an unaligned coverage list.
   - The real book identity must **strictly** be read from `odds_entry["book"]`.
   - Verified in probe and test suite (`TestBookAttribution` in `tests/test_outlier_parsing.py`).
2. **Trap 2: Multi-Row Proposition Spanning**
   - A single proposition (e.g. `SPREAD` or `TOTAL`) is split across multiple market cards, each containing a subset of sportsbooks.
   - The parser must union all market rows for the proposition and deduplicate on `(book, market, side, line)`.
   - Verified in probe (`SPREAD` spanned 25 cards, `TOTAL` spanned 26 cards) and `TestParseOddsRows`.

### 4.3 Market Whitelist & Target Rules (R2)
Authoritative whitelist frozenset:
```python
WHITELISTED_MARKETS = frozenset({
    ("GAMELINE", "SPREAD", "full_game"),
    ("GAMELINE", "SPREAD", "first_half"),
    ("GAMELINE", "TOTAL", "full_game"),
    ("GAMELINE", "ML", "full_game"),
    ("TEAM_PROP", "POINTS", "full_game"),
    ("TEAM_PROP", "OFFENSIVE_YARDS", "full_game"),
    ("TEAM_PROP", "RECEIVING_YARDS", "full_game"),
    ("TEAM_PROP", "RUSHING_YARDS", "full_game"),
})
```
- **Allowed Sides:**
  - Spreads & ML: `HOME`, `AWAY`
  - Game/Team Totals & Yards: `OVER`, `UNDER`
- **Exclusions:** NCAAF player props (`PLAYER_PROP`), game props (`GAME_PROP`), derivative spreads, quarters, alternate player lines.

---

## 5. Ingestion Pipeline & CLI Wiring

### 5.1 `cfb_analytics/ingest/outlier_ingest.py`
Current structure of `ingest_slate`:
```python
def ingest_slate(
    conn: sqlite3.Connection,
    client: OutlierClient,
    slate_date: str,
    *,
    with_odds: bool = True,
    with_injuries: bool = True,
    limit: int | None = None,
) -> IngestSummary:
```
- Filters events by `football_date(kickoff_utc) == slate_date`.
- Upserts teams and games.
- Calls `_ingest_odds(...)` if `with_odds`.
- Calls `_ingest_injuries(...)` if `with_injuries`.

#### Required Modifications
- Add keyword arguments: `with_props: bool = False`, `with_insights: bool = False`.
- Ingest props via `_ingest_props(...)`:
  - Fetch `client.fetch_event_markets(game_id, "TEAM_PROP")`.
  - Filter against whitelist frozensets.
  - Degrade gracefully: network or parse failure logs to `source_health` and appends to `IngestSummary.market_failures` without aborting other games on the slate.
- Update `IngestSummary` to track `prop_rows`, prop breakdown by family, and distinct books.

### 5.2 CLI Entry Point (`cfb_analytics/cli.py`)
Current parser definition for `ingest`:
```python
ingest = sub.add_parser("ingest", help="ingest one slate from Outlier")
ingest.add_argument("--date", required=True, help="slate date, YYYY-MM-DD")
ingest.add_argument("--no-odds", action="store_true", help="skip gameline odds")
ingest.add_argument("--no-injuries", action="store_true", help="skip the injury feed")
ingest.add_argument("--limit", type=int, default=None, help="cap events (for smoke tests)")
ingest.set_defaults(func=_cmd_ingest)
```

#### Required Modifications
Add arguments:
```python
ingest.add_argument("--with-props", action="store_true", default=False, help="ingest team props from Outlier")
ingest.add_argument("--with-insights", action="store_true", default=False, help="ingest insights from Outlier")
```
Pass `with_props=args.with_props` and `with_insights=args.with_insights` into `ingest_slate`.

### 5.3 Scheduled Daily Job (`cfb_analytics/daily.py`)
- `_run_outlier(...)` executes `ingest_slate(conn, client, slate)`.
- Because `with_props` and `with_insights` default to `False`, the unattended daily cron remains safe from unexpected schema or network issues while props are phased in.

---

## 6. FORBIDDEN ELO FILES

The user has implemented an internal Elo rating engine, ridge team strength models, CFBD backfill routines, and a walk-forward backtest harness. Under the project boundaries, **these files must NEVER be edited, modified, or interfered with**.

### 6.1 Production Elo & Modeling Modules (DO NOT TOUCH)
1. `cfb_analytics/features/elo_ratings.py` — Leakage-safe lookup of weekly CFBD Elo ratings
2. `cfb_analytics/features/team_ratings.py` — Ridge ratings fit and shrinkage prior blending
3. `cfb_analytics/features/asof.py` — Point-in-time leakage prevention reader
4. `cfb_analytics/features/qb.py` — Quarterback starter signals
5. `cfb_analytics/features/build_market.py` — Gameline market consensus builder
6. `cfb_analytics/features/market.py` — M-model vig-free probabilities
7. `cfb_analytics/models/ridge.py` — Ridge regression team-strength ratings
8. `cfb_analytics/models/shrinkage.py` — Preseason shrinkage priors
9. `cfb_analytics/models/linalg.py` — Stdlib linear algebra matrix solver
10. `cfb_analytics/models/devig.py` — Multiplicative, Shin, and Power devigging math
11. `cfb_analytics/backtest/elo_baseline.py` — Elo win probability baseline model
12. `cfb_analytics/backtest/harness.py` — Walk-forward moneyline prediction harness
13. `cfb_analytics/backtest/moneyline.py` — Walk-forward calibration and moneyline backtesting
14. `cfb_analytics/backtest/metrics.py` — Brier, log-loss, and calibration error metrics
15. `cfb_analytics/backtest/calibration.py` — Margin-to-probability sigmoid fitting
16. `cfb_analytics/sources/cfbd.py` — CollegeFootballData API client
17. `cfb_analytics/ingest/cfbd_fundamentals.py` — CFBD SP+, SRS, and Elo backfill
18. `cfb_analytics/ingest/cfbd_ingest.py` — CFBD teams, venues, and games backfill
19. `cfb_analytics/ingest/cfbd_lines.py` — CFBD historical betting lines
20. `cfb_analytics/ingest/cfbd_players.py` — CFBD roster and passing stats backfill

### 6.2 Test Suite Files Protecting Elo & Modeling (DO NOT TOUCH)
1. `tests/test_elo_baseline.py`
2. `tests/test_elo_ratings.py`
3. `tests/test_backtest_harness.py`
4. `tests/test_backtest_moneyline.py`
5. `tests/test_backtest_metrics.py`
6. `tests/test_backtest_calibration.py`
7. `tests/test_ridge.py`
8. `tests/test_shrinkage.py`
9. `tests/test_linalg.py`
10. `tests/test_devig.py`
11. `tests/test_team_ratings.py`
12. `tests/test_cfbd_fundamentals.py`
13. `tests/test_cfbd_ingest.py`
14. `tests/test_cfbd_players.py`

---

## 7. Test Suite Infrastructure & Quality Standards

### 7.1 Test Environment & Configuration
- **Runner:** `pytest -q`
- **Replay Mode Isolation:** `tests/conftest.py` sets:
  - `CFB_DATA_DIR = tmp_path / "data"` (prevents writing to real store)
  - `CFB_HTTP_MODE = replay` (prevents external network calls)
  - `CFBD_API_KEY = ""` (guarantees hermetic offline testing)
- **Status:** All tests pass (0 failures).

### 7.2 Quality Gates & Acceptance Requirements
- **Coverage Requirement:** `pytest --cov=cfb_analytics` requires **>= 80% coverage** on every modified module.
- **Type Checking:** Clean runs on `mypy cfb_analytics` and `ruff check .`.
- **Stdlib Only:** Modeling math must remain stdlib-only (no numpy/pandas/scipy).
- **Cleanliness:** `tests/test_fixtures_cleanliness.py` verifies zero authentication or credential leaks in any new fixture files.

---

## 8. Summary of Scope Boundaries for Downstream Implementation

| Component | In-Scope Modifications | Forbidden Modifications |
|---|---|---|
| **Database (`db.py`)** | Add Migration 010 (table rebuild for `odds_snapshots` or new prop tables). Keep existing snapshot IDs. | Never alter migrations 001–009. Never modify `team_ratings` or `internal_team_ratings`. |
| **Source Client (`sources/outlier.py`)** | Add prop parsing, whitelist filtering, team prop attribution, insights handling. | Do not alter `parse_event` or existing gameline parsing logic in a way that breaks existing tests. |
| **Ingestion (`ingest/outlier_ingest.py`)** | Add `with_props` and `with_insights` flags to `ingest_slate`, update `IngestSummary`, handle degradation. | Do not touch `cfbd_*.py` ingestion scripts. |
| **Store (`ingest/store.py`)** | Add prop odds snapshot / prop consensus insert helpers. | Do not alter `insert_team_ratings` or `upsert_internal_team_ratings`. |
| **CLI (`cli.py`)** | Add `--with-props`, `--with-insights` to `ingest` subcommand. | Do not modify `fit-ratings`, `backtest`, `backfill-elo`, or `backfill-fundamentals` commands. |
| **Tests (`tests/`)** | Add tests for prop parsing, traps, whitelist dropping, migration idempotency. | Never edit or delete tests in the forbidden list. |
