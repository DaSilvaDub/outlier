# Standalone NFL Data Pipeline Architecture Blueprint (`outlier_nfl`)

## 1. Executive Summary & Survey Objectives

### 1.1 Mission
This architectural blueprint provides the complete system design for `outlier_nfl`, a standalone NFL betting data pipeline within the `outlier` repository. This survey answers the requirements set forth in the Teamwork Project Prompt (`ORIGINAL_REQUEST.md`, milestone `2026-09-12T10:39:21Z`) and Explorer 1 Survey Assignment (`context.md`):
1. Analyze the existing `outlier_scrapers/` architecture across networking, data modeling, normalization, and pipeline orchestration.
2. Formulate a modular, decoupled architecture for `outlier_nfl` that guarantees **zero runtime coupling** with the existing MLB and WNBA code paths.
3. Establish clean-room adaptations of established architectural patterns (auth discovery, bounded exponential backoff with jitter, pagination progress fingerprinting, resilient file writes against cloud-sync locks).
4. Define comprehensive market taxonomy and normalization rules for NFL player props and team props (game totals, team totals, and spreads) fulfilling Requirement R3.
5. Provide the blueprint for the unit test suite (`pytest`) and the standalone verification script (`verify_nfl_pipeline.py`).

---

## 2. Existing `outlier_scrapers` Architectural Anatomy

### 2.1 Component Breakdown
The existing `outlier_scrapers` package is composed of 90+ modules serving data ingestion, card generation, and the AI research desk for MLB and WNBA:

| Layer | Key Modules | Core Responsibilities |
|---|---|---|
| **Networking & Auth** | `api.py`, `auth.py`, `paths.py` | HTTP communication via `urllib.request`, Bearer token extraction from Playwright `storage_state.json`, exponential backoff with jitter, paginated payload consumption. |
| **Taxonomy & Schemas** | `registry.py`, `schema.py` | `SportConfig` definitions, team alias dictionaries, market proposition mappings, raw API & normalized schema validation gates. |
| **Data Normalization** | `normalizer.py`, `team_totals.py` | Translating raw Outlier nested JSON payloads into canonical flat records; odds normalization (`implied_probability()`); game scope detection (`full_game` vs periods). |
| **Market Extractors** | `games.py`, `props.py`, `game_totals.py`, `alt_spreads.py`, `alt_team_totals.py` | Dedicated scrapers for league-level player props and event-level game lines, spreads, and totals. |
| **Pipeline & Health** | `daily_job.py`, `refresh_plan.py`, `feed_health.py`, `utils.py` | DAG-based task execution, unified feed-health gate checks (coverage %, freshness, missing streams), atomic file replace retries (`[WinError 32]` mitigation). |
| **AI Research Desk** | `run_desk.py`, `models.py`, `reasoning.py`, `claude_*.py`, `gemini_*.py` | Multi-pass LLM betting synthesis desk (governed by strict House Rule: default OFF). |

### 2.2 Data Flow Architecture
```
[Outlier API (api.outlier.bet)]
         │
         ├──> /sportsdata/leagues/{LEAGUE}/schedule
         ├──> /sportsdata/leagues/{LEAGUE}/playerProps (Paginated)
         └──> /sportsdata/events/{eventId}/markets?marketType={TYPE}
                     │
                     ▼
             [Raw Payloads (.json)]
             (data/{LEAGUE}/raw/)
                     │
                     ▼
           [Schema Validation Gate]
             (schema.py validation)
                     │
                     ▼
          [Normalizer & Taxonomy]
             (normalizer.py)
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
 [Normalized Props]       [Normalized Games]
(data/{LEAGUE}/normalized/  (data/{LEAGUE}/normalized/
   props_latest.json)        games_latest.json)
         │                       │
         ├───────────────────────┤
         ▼                       ▼
   [Player Props]         [Game Lines & Totals]
                           - Game Totals (O/U)
                           - Point Spreads (H/A)
                           - Team Totals (O/U)
```

### 2.3 Tight Coupling & Domain Invariants in `outlier_scrapers`
Direct inspection of `outlier_scrapers/` demonstrates why an independent `outlier_nfl` package is essential:
1. **Hardcoded MLB Invariants**:
   - `normalizer.py` lines 23-28 enforce strict whitelists: `ALLOWED_MLB_PLAYER_PROPS = frozenset({"SO"})` (pitcher strikeouts only) and `ALLOWED_MLB_TEAM_PROPS = frozenset({"R", "TOTAL"})`. Any non-whitelisted MLB market is dropped at generation.
   - Baseball-specific period parsers (`first_3_innings`, `first_5_innings`, `first_inning`, `NRFI`, `YRFI`).
2. **Missing NFL Registry**:
   - `registry.py` only defines configurations for `MLB`, `WNBA`, and an inactive placeholder `NBA`. NFL is not registered in `SPORTS`.
3. **Complex AI Desk Entanglement**:
   - The broader pipeline in `outlier_scrapers` is heavily wired into the AI Research Desk (`pack.py`, `run_desk.py`, `cards.py`, `portfolio.py`, `feedback.py`).
   - For an NFL ingestion pipeline, introducing dependencies on `outlier_scrapers` introduces fragile coupling with baseball pitching models, WNBA basketball stats, and multi-model LLM desks.

---

## 3. Standalone Architecture for `outlier_nfl`

### 3.1 Guiding Architectural Principles
1. **Zero Runtime Coupling**: `outlier_nfl` MUST NOT import anything from `outlier_scrapers`. It is a self-contained Python package residing at `outlier_nfl/` at the repository root.
2. **Clean-Room Pattern Adaptation**: Replicate the battle-tested resilience mechanisms from `outlier_scrapers` (such as bounded exponential backoff, safe JSON decoding with `strict=False`, atomic cloud-sync file replacement) using standard library modules (`urllib.request`, `json`, `dataclasses`, `pathlib`, `logging`, `time`).
3. **Shared Filesystem State (Data Contract Only)**: `outlier_nfl` reads the existing login session file on disk (`config/.outlier_session/storage_state.json` or `config/outlier_session.json`) without depending on `outlier_scrapers.auth`.
4. **NFL-Native Taxonomy**: Direct support for 32 NFL franchises, football period scopes (`full_game`, `first_half`, `second_half`, `first_quarter`, `second_quarter`, etc.), and football prop propositions (passing, rushing, receiving, touchdowns, game totals, team totals, spreads).

### 3.2 Target Module Structure
```
outlier_nfl/
├── __init__.py          # Public package surface and version
├── constants.py         # League identifier, API endpoints, timeout/retry constants
├── config.py            # NFL 32-team aliases, display names, and market taxonomy
├── models.py            # Strongly-typed immutable dataclasses for records and payloads
├── api.py               # OutlierNflApiClient (HTTP, auth discovery, backoff, pagination)
├── schema.py            # Runtime validation gates for raw and normalized payloads
├── normalizer.py        # NFL data normalization (odds, books, teams, scopes, markets)
├── props.py             # NFL player props extraction and market categorizer
├── games.py             # NFL game lines extraction (totals, team totals, spreads)
├── pipeline.py          # Unified high-level NFL extraction orchestrator
└── utils.py             # File I/O, atomic replaces, timestamp formatting
```

### 3.3 Detailed Module Blueprint & Interface Specifications

#### 3.3.1 `outlier_nfl/constants.py`
- **Purpose**: Centralized constants defining API endpoints, default parameters, and league identifiers.
```python
API_BASE_URL = "https://api.outlier.bet"
LEAGUE_TOKEN = "NFL"

RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 0.5
MAX_DELAY_SECONDS = 8.0
REQUEST_TIMEOUT_SECONDS = 60

PAGINATION_MAX_PAGES = 60
TOKEN_PARAM_CANDIDATES = ("pageToken", "nextPageToken", "page_token")
NUMBER_PARAM_CANDIDATES = ("pageNumber", "page", "pageNo", "page_number")

# Market types to query per NFL event
NFL_GAME_MARKET_TYPES = ("GAMELINE", "TEAM_PROP", "PLAYER_PROP")
```

#### 3.3.2 `outlier_nfl/config.py`
- **Purpose**: Comprehensive team and market mapping for the NFL.
- **Teams**: All 32 NFL clubs mapped to canonical 2/3 letter abbreviations and display names:
  - AFC East: `BUF` (Bills), `MIA` (Dolphins), `NE` (Patriots), `NYJ` (Jets)
  - AFC North: `BAL` (Ravens), `CIN` (Bengals), `CLE` (Browns), `PIT` (Steelers)
  - AFC South: `HOU` (Texans), `IND` (Colts), `JAX` (Jaguars), `TEN` (Titans)
  - AFC West: `DEN` (Broncos), `KC` (Chiefs), `LV` (Raiders), `LAC` (Chargers)
  - NFC East: `DAL` (Cowboys), `NYG` (Giants), `PHI` (Eagles), `WAS` (Commanders)
  - NFC North: `CHI` (Bears), `DET` (Lions), `GB` (Packers), `MIN` (Vikings)
  - NFC South: `ATL` (Falcons), `CAR` (Panthers), `NO` (Saints), `TB` (Buccaneers)
  - NFC West: `ARI` (Cardinals), `LAR` (Rams), `SF` (49ers), `SEA` (Seahawks)
- **Market Taxonomy**:
  - Gamelines: `SPREAD`, `TOTAL`, `MONEYLINE`
  - Team Props: `POINTS`, `TOTAL`, `TEAM_TOTAL`, `TOTAL_POINTS`
  - Player Props:
    - Passing: `PASS_YDS`, `PASS_TDS`, `PASS_COMP`, `PASS_ATT`, `INT`
    - Rushing: `RUSH_YDS`, `RUSH_ATT`, `RUSH_LONG`
    - Receiving: `REC_YDS`, `REC`, `REC_LONG`
    - Scoring / Defense: `ANYTIME_TD`, `FIRST_TD`, `FGM`, `KICK_PTS`, `TACKLES`, `SACKS`

#### 3.3.3 `outlier_nfl/models.py`
- **Purpose**: Clean domain dataclasses providing strict type safety.
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class BookPrice:
    book: str
    odds: int
    odds_raw: str
    decimal: float | None = None

@dataclass(frozen=True)
class NflGameLine:
    event_id: str
    event_starts_at: str | None
    matchup: str
    home_team: str
    away_team: str
    market_type: str        # GAMELINE | TEAM_PROP
    market: str             # SPREAD | TOTAL | TEAM_TOTAL | MONEYLINE
    proposition: str
    position: str           # HOME | AWAY | OVER | UNDER
    line: float | None
    signed_line: str | None # e.g. -3.5, +3.5
    selection: str          # e.g. KC -3.5, Over 44.5
    team: str | None
    books: list[BookPrice]
    best_odds: int | None
    implied_probability: float | None

@dataclass(frozen=True)
class NflPlayerProp:
    event_id: str
    event_starts_at: str | None
    matchup: str
    team: str | None
    opponent: str | None
    player_name: str
    player_id: str | None
    market: str             # PASS_YDS | RUSH_YDS | REC_YDS | etc.
    market_raw: str
    position: str           # OVER | UNDER
    line: float
    books: list[BookPrice]
    best_odds: int | None
    implied_probability: float | None
    l5_hit_rate: float | None
    l10_hit_rate: float | None
    season_hit_rate: float | None
```

#### 3.3.4 `outlier_nfl/api.py`
- **Purpose**: Standalone HTTP client implementing Outlier API communications.
- **Key Features**:
  - `load_session_headers()`: Directly reads `PROJECT_ROOT / "config" / ".outlier_session" / "storage_state.json"` or environment variable `OUTLIER_BEARER_TOKEN` without importing `outlier_scrapers`.
  - `RetryPolicy`: Doubling delay with +/- 50% random jitter, capped at `max_delay_seconds`.
  - Gzip decompression on `\x1f\x8b` header.
  - JSON decoding with `strict=False` (handles unescaped control characters safely).
  - Methods:
    - `fetch_schedule()` -> `/sportsdata/leagues/NFL/schedule`
    - `fetch_player_props(max_pages)` -> `/sportsdata/leagues/NFL/playerProps`
    - `fetch_event_markets(event_id, market_type)` -> `/sportsdata/events/{event_id}/markets?marketType={market_type}`
    - `fetch_event_matchup(event_id)` -> `/sportsdata/events/{event_id}/matchup`
    - `fetch_event_insights(event_id)` -> `/sportsdata/events/{event_id}/insights`
    - `fetch_team_injuries(team_id)` -> `/sportsdata/leagues/NFL/teams/{team_id}/injuries`

#### 3.3.5 `outlier_nfl/schema.py`
- **Purpose**: Validation gates protecting downstream extractors from malformed API responses.
- Functions:
  - `validate_schedule_payload(payload: dict) -> list[str]`
  - `validate_player_props_payload(payload: dict) -> list[str]`
  - `validate_event_markets_payload(payload: dict) -> list[str]`
  - `validate_normalized_records(records: list[dict]) -> list[str]`

#### 3.3.6 `outlier_nfl/normalizer.py`
- **Purpose**: Converting raw Outlier payloads into normalized dictionaries and dataclass models.
- Responsibilities:
  - Odds conversion: `american_to_implied_probability(odds: int | str) -> float` (converts -110 to 52.381%, +120 to 45.455%).
  - Book label standardization: (`DRAFTKINGS` -> DraftKings, `FANDUEL` -> FanDuel, `BETMGM` -> BetMGM, etc.).
  - Period/Scope detection: football scopes (`full_game`, `first_half`, `second_half`, `first_quarter`, `second_quarter`, `third_quarter`, `fourth_quarter`).
  - Home/Away team matching against matchup strings.
  - Point spread signed line formatting (`_signed_line(-3.5) -> -3.5`, `_signed_line(3.5) -> +3.5`).

#### 3.3.7 `outlier_nfl/games.py` & `outlier_nfl/props.py`
- **`games.py`**:
  - Iterates target slate events.
  - Fetches `GAMELINE` and `TEAM_PROP` markets.
  - Normalizes game totals (`TOTAL` with `OVER` / `UNDER`), spreads (`SPREAD` with `HOME` / `AWAY`), and team totals (`POINTS`/`TOTAL` with `OVER` / `UNDER`).
- **`props.py`**:
  - Fetches paginated league-wide player props (`fetch_player_props()`) or event-level `PLAYER_PROP` markets.
  - Filters to valid football prop markets (passing, rushing, receiving, scoring).
  - Associates player IDs, team contexts, lines, and hit rate statistics.

#### 3.3.8 `outlier_nfl/pipeline.py`
- **Purpose**: Top-level orchestrator executing the end-to-end extraction.
- Workflow:
  1. Initialize `OutlierNflApiClient`.
  2. Ingest NFL schedule and identify target slate date.
  3. Fan out event queries for game lines (`GAMELINE`) and team props (`TEAM_PROP`).
  4. Ingest and paginate player props (`PLAYER_PROP`).
  5. Normalize game lines into `NflGameLine` records and player props into `NflPlayerProp` records.
  6. Execute schema validation gates.
  7. Persist raw payloads to `data/NFL/raw/` and normalized datasets to `data/NFL/normalized/`.
  8. Return a comprehensive summary dictionary containing record counts by market family.

#### 3.3.9 `outlier_nfl/utils.py`
- **Purpose**: Resilient file writing and date parsing.
- Helpers:
  - `safe_write_json(path: Path, payload: dict, retries: int = 5, delay: float = 0.2)`
  - `_replace_with_retry(src: Path, dst: Path, retries: int = 5, delay: float = 0.2)`: Mitigates Windows `[WinError 32]` file locking issues on OneDrive / cloud mirrors.
  - `parse_iso_datetime(ts: str) -> datetime | None`

---

## 4. Market Coverage Specification (Requirement R3)

Requirement R3 mandates: *The extraction logic must process NFL player props and team props, specifically extracting: game totals, team totals, and spreads.*

### 4.1 Game Totals
- **Source Market Type**: `GAMELINE`
- **Proposition**: `TOTAL`
- **Scope**: `full_game` (periodLabel is null or indicates full event)
- **Positions / Sides**: `OVER`, `UNDER`
- **Fields Produced**: `event_id`, `matchup`, `line` (e.g. 47.5), `position` (`OVER`/`UNDER`), `selection` (`Over 47.5`), `books`, `best_odds`, `implied_probability`.

### 4.2 Point Spreads
- **Source Market Type**: `GAMELINE`
- **Proposition**: `SPREAD`
- **Scope**: `full_game`
- **Positions / Sides**: `HOME`, `AWAY`
- **Signed Line Calculation**:
  - If HOME line is -3.5 -> `selection` is `{home_team} -3.5`
  - If AWAY line is +3.5 -> `selection` is `{away_team} +3.5`
- **Integrity Check**: Enforces that spread side matches team name and opposite sides sum to 0 (e.g. -3.5 and +3.5).

### 4.3 Team Totals
- **Source Market Type**: `TEAM_PROP`
- **Propositions**: `POINTS`, `TOTAL`, `TEAM_TOTAL`, `TOTAL_POINTS`
- **Scope**: `full_game`
- **Positions / Sides**: `OVER`, `UNDER`
- **Team Attribution**: Must resolve `teamId` or team label to canonical team abbreviation (e.g. `KC Over 24.5`).

### 4.4 NFL Player Props
- **Source Market Type**: `PLAYER_PROP`
- **Target Prop Families**:
  | Family | Proposition Keys | Target Metric | Typical Line Range |
  |---|---|---|---|
  | **Passing Yards** | `PASSING_YARDS`, `PASS_YDS` | Passing yards gained | 180.5 - 295.5 |
  | **Passing Touchdowns** | `PASSING_TOUCHDOWNS`, `PASSING_TDS`, `PASS_TDS` | Passing TDs | 0.5 - 2.5 |
  | **Passing Completions**| `PASS_COMPLETIONS`, `COMPLETIONS` | Total completed passes | 16.5 - 26.5 |
  | **Passing Attempts**   | `PASS_ATTEMPTS`, `ATTEMPTS` | Total pass attempts | 28.5 - 39.5 |
  | **Interceptions**      | `INTERCEPTIONS`, `PASSING_INTERCEPTIONS` | Interceptions thrown | 0.5 |
  | **Rushing Yards**      | `RUSHING_YARDS`, `RUSH_YDS` | Rushing yards gained | 35.5 - 85.5 |
  | **Rushing Attempts**   | `RUSHING_ATTEMPTS`, `CARRIES` | Rushing attempts | 9.5 - 18.5 |
  | **Receiving Yards**    | `RECEIVING_YARDS`, `REC_YDS` | Receiving yards gained | 25.5 - 75.5 |
  | **Receptions**         | `RECEPTIONS`, `REC` | Receptions made | 3.5 - 7.5 |
  | **Anytime Touchdown**  | `ANYTIME_TOUCHDOWN`, `TOUCHDOWNS` | Scored at least 1 TD | 0.5 (OVER only) |

---

## 5. Pattern Adaptation & Shared Mechanics

| Pattern | Source in `outlier_scrapers` | Clean Adaptation in `outlier_nfl` |
|---|---|---|
| **Session Discovery** | `auth.load_storage_state()` | `outlier_nfl.api` reads `PROJECT_ROOT / "config" / ".outlier_session" / "storage_state.json"` or environment variable `OUTLIER_BEARER_TOKEN` directly. |
| **Exponential Backoff** | `api.RetryPolicy` | `outlier_nfl.api.RetryPolicy` with formula: `min(max_delay, base_delay * 2**(attempt - 1)) * (0.5 + rng() / 2)`. Retries HTTP 403, 429, 500, 502, 503, 504. |
| **Progress Fingerprinting** | `api._records_signature()` | Fingerprint `(len(records), tuple(first_3_ids), tuple(last_3_ids))` to detect stalled pagination loops without crashing. |
| **JSON Control Characters**| `api.py` / `normalizer.py` | `json.loads(text, strict=False)` in all payload parsers. |
| **Cloud Sync Resiliency** | `utils._replace_with_retry` | Retry atomic file replaces up to 5 times on `OSError` with `winerror in (32, 33)` to defeat OneDrive lock contention. |
| **Implied Probability** | `normalizer.implied_probability()` | Compute implied win percentage from American odds (`-110` -> `52.381%`). Divide by 100 where decimal `[0, 1]` is required. |

---

## 6. Testing & End-to-End Verification Blueprint

### 6.1 Test Suite Organization (`tests/test_nfl_*.py`)
- **`tests/test_nfl_api.py`**:
  - Tests `OutlierNflApiClient` initialization with mocked session states.
  - Tests exponential backoff retry on 429/503 errors and immediate failure on 401.
  - Tests pagination loop termination on token exhaustion, page count limit, and repeated signatures.
- **`tests/test_nfl_normalizer.py`**:
  - Tests American odds conversion and implied probability calculation.
  - Tests full-game total parsing (`OVER` and `UNDER`).
  - Tests point spread line signing and team attribution (`KC -3.5`).
  - Tests team totals extraction and team ID resolution (`BAL Over 21.5`).
  - Tests player prop normalization across passing, rushing, receiving, and touchdowns.
- **`tests/test_nfl_pipeline.py`**:
  - End-to-end pipeline run against committed replay fixtures.
  - Validates that outputs adhere to expected record counts and schemas.

### 6.2 Fixture Strategy (`tests/fixtures/nfl/`)
To guarantee deterministic, offline unit testing without hitting the network (in accordance with repository testing standards), provide three realistic JSON fixtures:
1. `tests/fixtures/nfl/schedule.json`: Contains NFL slate with multiple scheduled matchups.
2. `tests/fixtures/nfl/event_markets.json`: Contains raw `GAMELINE` and `TEAM_PROP` markets (spreads, game totals, team totals).
3. `tests/fixtures/nfl/player_props.json`: Contains raw `PLAYER_PROP` records covering passing, rushing, and receiving lines.

### 6.3 Standalone Verification Script (`verify_nfl_pipeline.py`)
- **CLI Interface**:
  ```powershell
  python verify_nfl_pipeline.py [--mode {live,replay}] [--date YYYY-MM-DD] [--out-dir PATH]
  ```
- **Automated Assertions**:
  1. Asserts pipeline completes without unhandled exceptions.
  2. Asserts output dataset contains `GAMELINE` game totals (both `OVER` and `UNDER`).
  3. Asserts output dataset contains `GAMELINE` spreads (both `HOME` and `AWAY` with signed lines).
  4. Asserts output dataset contains `TEAM_PROP` team totals.
  5. Asserts output dataset contains `PLAYER_PROP` player props across multiple categories.
  6. Prints a formatted summary table of all extracted markets and exits 0 on success.

---

## 7. Implementation Roadmap for Implementer Agents

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Core Foundation & Domain Setup                     │
│ - Create outlier_nfl/ package directory                     │
│ - Implement constants.py, config.py (32 teams), models.py   │
│ - Implement schema.py validation gates                      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: Resilient API Client & Offline Fixtures            │
│ - Implement outlier_nfl/api.py with RetryPolicy             │
│ - Implement session loader (reads config/.outlier_session)  │
│ - Generate tests/fixtures/nfl/ fixture datasets             │
│ - Implement tests/test_nfl_api.py                           │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3: Normalization & Market Extractors                  │
│ - Implement outlier_nfl/normalizer.py                       │
│ - Implement outlier_nfl/games.py (Totals, Spreads, Team Tot)│
│ - Implement outlier_nfl/props.py (Player Props)             │
│ - Implement tests/test_nfl_normalizer.py                    │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 4: Pipeline Orchestration & End-to-End Verification   │
│ - Implement outlier_nfl/pipeline.py (NflPipeline)           │
│ - Implement root-level verify_nfl_pipeline.py               │
│ - Implement tests/test_nfl_pipeline.py                      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 5: Verification & Delivery                            │
│ - Run pytest across entire test suite                       │
│ - Run verify_nfl_pipeline.py in replay and live modes       │
│ - Deliver completion handoff to Sentinel & Orchestrator     │
└─────────────────────────────────────────────────────────────┘
```