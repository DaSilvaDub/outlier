# Specification & Mining Report: Outlier NCAAFB Props & Insights Integration

**Document Version:** 1.0.0  
**Target Repository:** `cfb-analytics` (`C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`)  
**Target Branch:** `feat/outlier-props-insights`  
**Mining Agent:** `spec_miner_survey_3`  
**Date:** 2026-09-06  
**Status:** COMPLETE & AUTHORITATIVE  

---

## 1. Executive Summary & Specification Scope

This document provides the authoritative technical specification, database migration blueprints, parsing contracts, ingestion designs, and acceptance criteria for integrating Outlier NCAAFB props (team props) and insights into the `cfb-analytics` pipeline.

### Core Architectural Invariants
1. **Isolated Repository:** `cfb-analytics` is completely independent from `outlier` and imports zero code from it.
2. **League Token:** The verified Outlier league token for NCAA Football is `NCAAFB` (all other variants such as `NCAAF`, `CFB`, `FBS` return HTTP 502).
3. **Elo Engine Sanctuary (CRITICAL):** The user's Elo models, CFBD backfill, walk-forward backtest harnesses, and rating math must **never be touched or modified**. Props and insights work is strictly isolated to the ingestion, parsing, schema, and consensus layers.
4. **Benchmark Integrity Mode:** No external AI/paid reasoning layer (OpenAI, Claude, Gemini). All modelling and parsing must be deterministic and stdlib-only (no numpy/scipy/pandas).
5. **Hermetic Replay Testing:** All automated tests must execute against committed fixtures with `CFB_HTTP_MODE=replay`, never hitting live external networks during test execution.

---

## 2. Features Discovered & State Audit

### Features Discovered Table
| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Discovery / Feed | Schedule Ingestion | Fetches NCAA FBS games from Outlier schedule endpoint | League token `NCAAFB` | List of event dicts with kickoff, teams, venue | Raises `UnknownLeagueError` on 502/404; disambiguated via `NFL` control | `cfb_analytics/sources/outlier.py:117` |
| 2 | Discovery / Feed | Gameline Markets | Fetches Moneyline, Spread, Total cards | `eventId`, `marketType=GAMELINE` | Raw JSON envelope `{"markets": [...]}` | HTTP error wrapped in `SourceError` | `cfb_analytics/sources/outlier.py:143` |
| 3 | Discovery / Feed | Team Props | Outlier endpoint for team-level proposition cards | `eventId`, `marketType=TEAM_PROP` | JSON envelope (returns `{"markets": []}` on probed 2026-09-05 slate) | Empty array handled as unoffered / graceful degradation | `docs/probes/2026-09-05-ncaafb-props-discovery.md` |
| 4 | Discovery / Feed | Player Props | Outlier endpoint for individual player proposition cards | `eventId`, `marketType=PLAYER_PROP` | JSON envelope (returns `{"markets": []}` on probed 2026-09-05 slate) | Excluded per R2 §61; dropped during parsing | `docs/probes/2026-09-05-ncaafb-props-discovery.md` |
| 5 | Discovery / Feed | Insights Endpoint | Outlier endpoint for event betting insights and trends | `eventId` (`/sportsdata/events/{id}/insights`) | JSON envelope `{"insights": []}` | HTTP 200 with empty list; disabled by default | `docs/probes/2026-09-05-ncaafb-props-discovery.md:75` |
| 6 | Schema / Migrations | Schema Migrations Store | Forward-only numbered migration engine in SQLite | `conn: sqlite3.Connection` | Applies pending migrations in order | Atomic per migration transaction; rollbacks on error | `cfb_analytics/db.py:23` |
| 7 | Schema / Migrations | Odds Snapshots Table | Append-only odds capture table with deterministic PK | Normalized `OddsRow` | SQLite row with `snapshot_id` PK | Re-ingestion collides idempotently via `INSERT OR IGNORE` | `cfb_analytics/db.py:100` |
| 8 | Parsing | Non-Parallel Book Extraction | Extracts book directly from odds entry | `outcome.odds[].book` | Attributed sportsbook price | Eliminates silent book mis-attribution (Trap 1) | `cfb_analytics/sources/outlier.py:194` |
| 9 | Parsing | Market Card Union & Dedup | Unions multiple market cards for same proposition | List of market cards | Deduplicated `OddsRow` list by `(book, market, side, line)` | Overcomes card partitioning across books (Trap 2) | `cfb_analytics/sources/outlier.py:171` |
| 10 | Consensus | Multi-method Vig-Free Devig | Calculates fair probabilities via multiplicative, Shin, power | Two-sided book quotes (min 3 books) | `MarketConsensus` dataclass | Flags `thin_market` or returns unpriced if books < 3 | `cfb_analytics/features/market.py:137` |
| 11 | Ingestion | Slate Ingest Orchestrator | Ingests schedule, teams, games, odds, injuries | `slate_date` (Eastern date) | `IngestSummary` report | Failure per event recorded in `source_health` without halting slate | `cfb_analytics/ingest/outlier_ingest.py:67` |

### Edge Cases Observed
| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Gameline Book Alignment | Outcome `books = ["MIDNITE", "DRAFTKINGS"]` but `odds[0].book = "DRAFTKINGS"` | Index-zipping assigns DraftKings price to Midnite. Reading `odds[0].book` accurately attributes price. |
| 2 | Market Card Partitioning | 25 distinct `SPREAD` cards for one game | Unioning extracts 13 distinct sportsbooks; taking first card alone drops >75% of sportsbooks. |
| 3 | Unoffered Team Props | `GET /sportsdata/events/{id}/markets?marketType=TEAM_PROP` returns `{"markets": []}` | Parser emits empty list `[]`; `IngestSummary` reports 0 team prop rows; slate continues normally. |
| 4 | Empty Insights Endpoint | `GET /sportsdata/events/{id}/insights` returns `{"insights": []}` | Ingestion records 0 insight rows; no mock data synthesized. |
| 5 | Post-Kickoff Odds | Odds captured after game kickoff | Filtered out by `AsOfReader` in `features/build_market.py` to prevent lookahead leakage. |
| 6 | Push-Capable / Integer Lines | Push on spread/total | Devig odds are conditional on no-push; probabilities must account for push or be flagged. |
| 7 | Placeholder Quotes | American price `-100000` or `+100000` | Excluded by `MIN_CREDIBLE_PROB` (0.0005) and `MAX_CREDIBLE_PROB` (0.995) to prevent consensus skew. |

---

## 3. Detailed Requirement Analysis

### R2: Target Markets, Whitelist, and Side Restrictions

#### 3.1 Market Whitelist Specification
In accordance with R2 §50, Outlier market types and proposition strings must be strictly whitelisted before ingestion. Any proposition outside this whitelist must be discarded during parsing.

```python
# cfb_analytics/sources/outlier.py

ALLOWED_MARKET_TYPES: frozenset[str] = frozenset({
    "GAMELINE",
    "TEAM_PROP",
})

ALLOWED_MARKETS: frozenset[str] = frozenset({
    "SPREAD",
    "TOTAL",
    "ML",
    "POINTS",
    "OFFENSIVE_YARDS",
    "RECEIVING_YARDS",
    "RUSHING_YARDS",
})

ALLOWED_SCOPES: frozenset[str] = frozenset({
    "full_game",
    "first_half",
})

PROPOSITION_TO_MARKET: dict[str, str] = {
    # GAMELINE
    "MONEYLINE": "ML",
    "SPREAD": "SPREAD",
    "TOTAL": "TOTAL",
    # TEAM_PROP
    "POINTS": "POINTS",
    "OFFENSIVE_YARDS": "OFFENSIVE_YARDS",
    "RECEIVING_YARDS": "RECEIVING_YARDS",
    "RUSHING_YARDS": "RUSHING_YARDS",
}

# Strict side constraints per market
ALLOWED_SIDES_BY_MARKET: dict[str, frozenset[str]] = {
    "ML": frozenset({"HOME", "AWAY"}),
    "SPREAD": frozenset({"HOME", "AWAY"}),
    "TOTAL": frozenset({"OVER", "UNDER"}),
    "POINTS": frozenset({"OVER", "UNDER"}),
    "OFFENSIVE_YARDS": frozenset({"OVER", "UNDER"}),
    "RECEIVING_YARDS": frozenset({"OVER", "UNDER"}),
    "RUSHING_YARDS": frozenset({"OVER", "UNDER"}),
}
```

#### 3.2 Target Market Mapping Matrix
| Requested Market | `market_type` | Outlier `proposition` | Canonical `market` | `scope` | Allowed Sides | Action |
|---|---|---|---|---|---|---|
| Full-game spread | `GAMELINE` | `SPREAD` | `SPREAD` | `full_game` | `HOME`, `AWAY` | ADMIT |
| First-half spread | `GAMELINE` | `SPREAD` | `SPREAD` | `first_half` | `HOME`, `AWAY` | ADMIT |
| Full-game total points | `GAMELINE` | `TOTAL` | `TOTAL` | `full_game` | `OVER`, `UNDER` | ADMIT |
| Moneyline | `GAMELINE` | `MONEYLINE` | `ML` | `full_game` | `HOME`, `AWAY` | ADMIT |
| Team total points | `TEAM_PROP` | `POINTS` | `POINTS` | `full_game` | `OVER`, `UNDER` | ADMIT |
| Team offensive yards | `TEAM_PROP` | `OFFENSIVE_YARDS` | `OFFENSIVE_YARDS` | `full_game` | `OVER`, `UNDER` | ADMIT |
| Team receiving yards | `TEAM_PROP` | `RECEIVING_YARDS` | `RECEIVING_YARDS` | `full_game` | `OVER`, `UNDER` | ADMIT |
| Team rushing yards | `TEAM_PROP` | `RUSHING_YARDS` | `RUSHING_YARDS` | `full_game` | `OVER`, `UNDER` | ADMIT |

#### 3.3 Explicitly Out-of-Scope Markets (Drop at Parser Level)
The following market families are strictly **OUT OF SCOPE** and must be dropped during parsing:
1. **Unwhitelisted Gamelines:**
   - `DOUBLE_RESULT`
   - `MONEYLINE_THREE_WAY`
   - `WINNING_MARGIN`
2. **Player Props (All):**
   - `PLAYER_PROP` is explicitly excluded per R2 §61. Even if Outlier begins publishing NCAAF player props (e.g. passing yards, passing TDs, anytime TD), they must not be admitted to `odds_snapshots`.
3. **Game Props (All):**
   - `GAME_PROP` (e.g., first team to score, safety occurrence).
4. **Quarter Derivatives:**
   - Quarters (1Q, 2Q, 3Q, 4Q) for spread, moneyline, or totals.
5. **Bet Sizing & Execution:**
   - Kelly criterion, unit allocations, bankroll sizing, and automated execution are strictly out of scope.

---

### R3: Schema & Database Integrity

#### 3.1 Migration Version Analysis
- **Current State in `cfb_analytics/db.py`:**
  - Migrations 1 through 9 are registered and applied:
    - 1: `outlier_ingestion_core`
    - 2: `games_football_date`
    - 3: `market_consensus_and_movement`
    - 4: `cfbd_team_history`
    - 5: `cfbd_fundamentals`
    - 6: `feature_rows`
    - 7: `game_venue_id_and_weather`
    - 8: `players_and_passing`
    - 9: `internal_team_ratings`
  - Latest migration in both `origin/master` and `HEAD` (`feat/outlier-props-insights`) is **Migration 9**.
  - **Next Available Migration Version:** **Migration 10** (`MIGRATION_010`).

#### 3.2 Table-Rebuild Migration Strategy (`MIGRATION_010`)
In SQLite, table constraints such as `CHECK (market IN ('ML','SPREAD','TOTAL'))` cannot be widened with `ALTER TABLE`. Attempting to insert a `TEAM_PROP` with `market = 'POINTS'` would raise `sqlite3.IntegrityError: CHECK constraint failed`.

To resolve this cleanly without data loss, Migration 10 must execute a 12-step table rebuild for `odds_snapshots`:

```sql
-- Migration 010 DDL: Widen odds_snapshots and create prop_consensus
CREATE TABLE odds_snapshots_v2 (
    snapshot_id     TEXT PRIMARY KEY,
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    market_id       TEXT,
    book            TEXT NOT NULL,
    captured_utc    TEXT NOT NULL,
    market          TEXT NOT NULL CHECK (market IN (
                        'ML', 'SPREAD', 'TOTAL',
                        'POINTS', 'OFFENSIVE_YARDS', 'RECEIVING_YARDS', 'RUSHING_YARDS'
                    )),
    side            TEXT,
    line            REAL,
    price_american  INTEGER,
    price_decimal   REAL,
    is_primary      INTEGER,
    source          TEXT NOT NULL,
    team_id         TEXT REFERENCES teams(team_id),
    player_id       TEXT REFERENCES players(player_id),
    scope           TEXT NOT NULL DEFAULT 'full_game' CHECK (scope IN ('full_game', 'first_half')),
    market_type     TEXT NOT NULL DEFAULT 'GAMELINE' CHECK (market_type IN ('GAMELINE', 'TEAM_PROP'))
);

-- Copy existing data preserving all fields and identical snapshot_ids
INSERT INTO odds_snapshots_v2 (
    snapshot_id, game_id, market_id, book, captured_utc, market, side,
    line, price_american, price_decimal, is_primary, source,
    team_id, player_id, scope, market_type
)
SELECT
    snapshot_id, game_id, market_id, book, captured_utc, market, side,
    line, price_american, price_decimal, is_primary, source,
    NULL, NULL, 'full_game', 'GAMELINE'
FROM odds_snapshots;

DROP TABLE odds_snapshots;
ALTER TABLE odds_snapshots_v2 RENAME TO odds_snapshots;

CREATE INDEX idx_odds_game_market ON odds_snapshots(game_id, market, captured_utc);
CREATE INDEX idx_odds_book ON odds_snapshots(book);
CREATE INDEX idx_odds_team ON odds_snapshots(team_id, market, captured_utc);

-- Dedicated prop consensus table to prevent overloading market_consensus
CREATE TABLE IF NOT EXISTS prop_consensus (
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    team_id         TEXT REFERENCES teams(team_id),
    player_id       TEXT REFERENCES players(player_id),
    market          TEXT NOT NULL,
    line            REAL,
    side            TEXT NOT NULL,
    as_of_utc       TEXT NOT NULL,
    n_books         INTEGER NOT NULL,
    consensus_price INTEGER,
    best_price      INTEGER,
    best_book       TEXT,
    hold            REAL,
    anchor          TEXT NOT NULL,
    prob_multiplicative REAL,
    prob_shin       REAL,
    prob_power      REAL,
    prob_spread     REAL,
    flags           TEXT,
    PRIMARY KEY (game_id, team_id, market, line, side, as_of_utc)
);
CREATE INDEX IF NOT EXISTS idx_prop_consensus_lookup
    ON prop_consensus(game_id, team_id, market, as_of_utc);
```

#### 3.3 Overload Evaluation: `market_consensus` vs `prop_consensus`
- **Existing `market_consensus` Primary Key:** `PRIMARY KEY (game_id, market, line, side, as_of_utc)`.
- **The Collison Trap:** In a game between Team A and Team B, both teams have their own Team Total Points market (`POINTS`). If both lines are set to 28.5 (OVER/UNDER), the primary key `(game_id, 'POINTS', 28.5, 'OVER', as_of_utc)` would collide and overwrite the opposing team's quote!
- **Architectural Decision:** Do **not** overload `market_consensus`. Maintain `market_consensus` strictly for game-level markets (ML, SPREAD, TOTAL) consumed by `board` and `backtest`. Use the dedicated `prop_consensus` table with `PRIMARY KEY (game_id, team_id, market, line, side, as_of_utc)` for all team props.

#### 3.4 Deterministic `snapshot_id` Integrity
In `cfb_analytics/sources/outlier.py:71`:
```python
def snapshot_id(self) -> str:
    return stable_id(
        self.source, self.game_id, self.book, self.market, self.side,
        self.line, self.price_american, self.captured_utc,
    )
```
Where `cfb_analytics/utils.py:92` computes:
```python
payload = "\x1f".join("" if p is None else str(p) for p in parts)
return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

**CRITICAL INVARIANT:** For existing gameline rows, `team_id` and `player_id` are `None`. To ensure existing snapshot IDs in the database never mutate upon re-ingestion, the arguments passed to `stable_id` for gamelines must remain **exactly 8 parts** in their original order.
For team props where `team_id` is present, `team_id` is appended to the hash payload:
```python
@property
def snapshot_id(self) -> str:
    parts = [
        self.source, self.game_id, self.book, self.market, self.side,
        self.line, self.price_american, self.captured_utc,
    ]
    if self.team_id:
        parts.append(self.team_id)
    if self.scope and self.scope != "full_game":
        parts.append(self.scope)
    return stable_id(*parts)
```
This guarantees:
1. Every pre-existing gameline row computes the exact same hash as before Migration 10.
2. Team props for Home vs Away teams with identical lines and prices produce distinct hashes and do not collide.

---

### R4: Parsing Traps & Normalization Rules

#### 4.1 Trap 1: Non-Parallel Book Attribution
- **Observed Behavior:** The Outlier API emits an outcome object with a top-level `books` array and an `odds` array. These two lists are **not parallel**.
- **Real Evidence (from `tests/fixtures/outlier/event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_GAMELINE.json`):**
  ```json
  {
    "position": "AWAY",
    "books": ["MIDNITE", "DRAFTKINGS", "BETRIVERS"],
    "odds": [
      {"book": "DRAFTKINGS", "american": "-260", "decimal": 1.385},
      {"book": "BETRIVERS",  "american": "-265", "decimal": 1.377},
      {"book": "MIDNITE",    "american": "-250", "decimal": 1.400}
    ]
  }
  ```
  If code executed `zip(outcome["books"], outcome["odds"])`, `MIDNITE` would be attributed with DraftKings's `-260` price, and `DRAFTKINGS` with BetRivers's `-265`.
- **Parsing Rule:** The parser must strictly extract the sportsbook name from `entry["book"]` inside each element of `outcome["odds"]`. The top-level `outcome["books"]` array must be completely ignored during price attribution.

#### 4.2 Trap 2: Proposition Multi-Card Spanning
- **Observed Behavior:** A single proposition (such as `SPREAD` or `TOTAL`) is not returned as a single JSON object. In the probed slate, `SPREAD` spanned up to 9 distinct market cards per game (25 across 3 games), each quoting a distinct subset of sportsbooks (e.g. Card A: DraftKings, FanDuel; Card B: BetMGM, Caesars; Card C: Fanatics, BetRivers).
- **Parsing Rule:**
  1. The parser must iterate over **all** market cards in the `markets` array.
  2. For cards matching a whitelisted proposition, outcomes must be collected into a shared pool.
  3. De-duplicate quotes using the tuple key: `(book, market, side, line)`.
  4. Retain the first observation for each unique key.

#### 4.3 Team Prop Attribution (Home vs Away Mapping)
For `market_type == "TEAM_PROP"`, the market must be attributed to either the home team or the away team.
1. **Direct `teamId` Matching:**
   Check `outcome.get("teamId")` or `market.get("teamId")`.
   - If `teamId == event["home"]["team_id"]`: attribute to `home`.
   - If `teamId == event["away"]["team_id"]`: attribute to `away`.
2. **Text Label Fallback:**
   If `teamId` is missing from the payload, match the lowercase text of `market.get("label")` or `outcome.get("label")` against `event["home"]["school"].lower()` / `event["home"]["alias"].lower()` and `event["away"]["school"].lower()` / `event["away"]["alias"].lower()`.
3. If neither matches, drop the row and record a schema warning.

---

### R5: Ingestion, Consensus, & Reporting

#### 5.1 CLI and Function Signatures
In `cfb_analytics/ingest/outlier_ingest.py`:
```python
def ingest_slate(
    conn: sqlite3.Connection,
    client: OutlierClient,
    slate_date: str,
    *,
    with_odds: bool = True,
    with_injuries: bool = True,
    with_props: bool = False,         # Default OFF per R5 §77
    with_insights: bool = False,     # Default OFF per R5 §77
    limit: int | None = None,
) -> IngestSummary:
```

In `cfb_analytics/cli.py`:
```python
ingest = sub.add_parser("ingest", help="ingest one slate from Outlier")
ingest.add_argument("--date", required=True, help="slate date, YYYY-MM-DD")
ingest.add_argument("--no-odds", action="store_true", help="skip gameline odds")
ingest.add_argument("--no-injuries", action="store_true", help="skip the injury feed")
ingest.add_argument("--with-props", action="store_true", default=False, help="ingest team props")
ingest.add_argument("--with-insights", action="store_true", default=False, help="ingest insights")
ingest.add_argument("--limit", type=int, default=None, help="cap events (for smoke tests)")
```

#### 5.2 Graceful Degradation & Failure Isolation
1. **Per-Event Isolation:** Prop and insight fetches for an individual game must execute inside a `try...except SourceError` block. A network timeout or HTTP 5xx error on one game's props must not abort the remaining games on the slate.
2. **`source_health` Persistence:** Every event fetch attempt must be logged in `source_health`:
   - Success: `run.record_health("outlier", f"props:{game_id}", ok=True, rows=written)`
   - Failure: `run.record_health("outlier", f"props:{game_id}", ok=False, detail=str(exc))`
3. **Empty Feed Tolerance:** When Outlier returns an empty array (`{"markets": []}` or `{"insights": []}`), the parser must return an empty list without raising. The run recorder records `ok=True, rows=0`.

#### 5.3 `IngestSummary` Reporting Requirements
`IngestSummary` must track and format prop metrics:
```python
@dataclass
class IngestSummary:
    slate_date: str
    events_seen: int = 0
    games_written: int = 0
    teams_written: int = 0
    odds_rows: int = 0
    injury_rows: int = 0
    prop_rows: int = 0
    prop_rows_by_family: dict[str, int] = field(default_factory=dict)
    insights_rows: int = 0
    books: set[str] = field(default_factory=set)
    prop_books: set[str] = field(default_factory=set)
    market_failures: list[str] = field(default_factory=list)
    injury_failures: list[str] = field(default_factory=list)
    prop_failures: list[str] = field(default_factory=list)
    insight_failures: list[str] = field(default_factory=list)
    schema_failures: list[str] = field(default_factory=list)
    weekday_mismatches: list[str] = field(default_factory=list)
```

Formatted output from `summary.as_text()`:
```text
slate 2026-09-05
  events on slate : 31
  games written   : 31
  teams written   : 62
  odds rows       : 642  across 13 books
  prop rows       : 0 (POINTS: 0, OFFENSIVE_YARDS: 0, RUSHING_YARDS: 0)
  injury rows     : 14
  books           : BETMGM, BETRIVERS, CAESARS, DRAFTKINGS, FANDUEL, ...
```

#### 5.4 Minimum Books Floor for Consensus
Per R5 §79, any prop consensus must enforce:
```python
MIN_BOOKS_FOR_PROP_CONSENSUS = 3
```
If a team prop line has quotes from only 1 or 2 distinct sportsbooks:
- Flag with `thin_market`.
- Do not compute or publish active consensus probabilities (set `sides = ()`, `anchor = "none"`).
- Prevents spurious edges derived from single-book margin artifacts.

---

## 4. Acceptance Criteria & Quality Gates

### 4.1 Discovery Gates
- [x] R1 probe script committed (`scripts/probe_ncaafb_outlier.py`).
- [x] Reference slate payloads committed under `tests/fixtures/outlier/`.
- [x] Discovery report committed under `docs/probes/2026-09-05-ncaafb-props-discovery.md`.
- [x] Negative findings documented (Team Props: unoffered, Player Props: unoffered, Insights: empty).

### 4.2 Correctness & Logic Gates
- [ ] Test proves parser attributes prices to the correct book despite `outcomes[].books` ordering (Trap 1).
- [ ] Test proves proposition prices are unioned across multiple market cards and deduplicated (Trap 2).
- [ ] Test proves markets outside explicit whitelist (R2) are dropped.
- [ ] Test proves Home and Away team props map to correct teams.
- [ ] Test proves missing/empty prop endpoints degrade cleanly without crashing gameline ingestion.

### 4.3 Database & Migration Gates
- [ ] Migration 10 applies cleanly on top of Migration 9.
- [ ] Pre-existing `odds_snapshots` rows retain 100% identical `snapshot_id` values.
- [ ] Re-running ingestion is idempotent (`INSERT OR IGNORE` creates no duplicates).
- [ ] `prop_consensus` table created and keyed on `(game_id, team_id, market, line, side, as_of_utc)`.

### 4.4 Regression & Quality Gates
- [x] Existing test suite passes with 0 regressions (verified: 491 passed in 34s).
- [x] `ruff check .` runs cleanly with 0 errors.
- [x] `mypy cfb_analytics` runs cleanly with 0 errors (40 source files).
- [ ] `pyright cfb_analytics` runs cleanly with 0 errors (1 trivial `exc_type.__name__` fix in `store.py:45`).
- [ ] `pytest --cov=cfb_analytics` achieves `>=80%` coverage on all touched modules (`outlier.py`, `outlier_ingest.py`, `store.py`).
- [ ] CLI command `cfb-analytics ingest --date <slate> --with-props` emits the standard summary format.

---

## 5. Implementation Roadmap for Implementation Agents

1. **Step 1: Test Fixtures & Unit Tests (TDD)**
   - Add unit tests in `tests/test_outlier_parsing.py` for team prop parsing, home/away mapping, and Trap 1/2.
   - Add unit tests in `tests/test_ingest.py` for `--with-props`, `--with-insights`, and graceful error handling.
2. **Step 2: Database Migration 10**
   - Add `MIGRATION_010` to `cfb_analytics/db.py` with the 12-step table rebuild and `prop_consensus` table.
   - Add Migration 10 verification test in `tests/test_db_and_store.py` verifying row survival and snapshot ID invariance.
3. **Step 3: Parser Extensions (`cfb_analytics/sources/outlier.py`)**
   - Implement `ALLOWED_MARKET_TYPES`, `ALLOWED_MARKETS`, `ALLOWED_SCOPES`, `ALLOWED_SIDES_BY_MARKET`.
   - Update `OddsRow` to support `team_id`, `player_id`, `scope`, `market_type`.
   - Extend `parse_odds_rows()` to parse `TEAM_PROP` with team attribution.
4. **Step 4: Ingestion & CLI Wiring**
   - Update `ingest_slate()` in `cfb_analytics/ingest/outlier_ingest.py` with `with_props` and `with_insights`.
   - Update `_cmd_ingest()` and argument parser in `cfb_analytics/cli.py`.
   - Update `store.insert_odds()` in `cfb_analytics/ingest/store.py` to insert widened columns.
5. **Step 5: Quality Gate Verification**
   - Execute `ruff check .`, `mypy cfb_analytics`, `pyright cfb_analytics`, and `pytest --cov=cfb_analytics`.
