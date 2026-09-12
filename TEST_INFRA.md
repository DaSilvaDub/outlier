# NFL Data Pipeline Test Infrastructure (`TEST_INFRA.md`)

## 1. Executive Overview & Philosophy

The test infrastructure for `outlier_nfl` enforces high-reliability standards for sportsbook data ingestion, normalization, and persistence. Sports betting pipelines operate under strict financial and operational constraints: invalid spreads, inverted team lines, missing total over/under sides, or miscalculated implied probabilities directly invalidate analytical models.

### Core Testing Mandates
1. **Zero Runtime Coupling**: Tests and fixtures must never import, reference, or depend on `outlier_scrapers` or MLB/WNBA modules.
2. **Deterministic Offline Execution**: 100% of unit and pipeline integration tests execute completely offline using rich pre-recorded JSON fixtures. No test hits external networks or live endpoints during CI.
3. **Strict Math & Formatting Invariants**:
   - Implied probability returns a percentage float (e.g. `52.381` for `-110`), matching codebase conventions.
   - Point spreads require signed handicap representations (`-3.5`, `+3.5`).
   - Football push adjustments on integer totals and spreads (key numbers 3 and 7).
4. **Resilient File I/O Verification**: Mocking and testing cloud-sync file lock collisions (`[WinError 32]` and `[WinError 33]`) to ensure atomic file writing retries succeed.
5. **House Rule Compliance**: Reasoning models (AI Research Desk / LLMs) are strictly prohibited and never invoked by the test suite.

---

## 2. Four-Tier Test Methodology

The `outlier_nfl` test suite is organized into a four-tier hierarchical validation framework:

```
┌─────────────────────────────────────────────────────────────┐
│ Tier 4: Standalone Verification Runner                     │
│ (`verify_nfl_pipeline.py` CLI: live & fixture verification) │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 3: End-to-End Pipeline Integration Tests               │
│ (`tests/test_nfl_pipeline.py`: full schedule-to-disk flow)  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 2: Schema & Payload Integrity Gates                    │
│ (`schema.py` validation: raw & normalized contracts)        │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: Unit & Contract Tests                               │
│ (`tests/test_nfl_api.py`, `tests/test_nfl_normalizer.py`)    │
└─────────────────────────────────────────────────────────────┘
```

### Tier 1: Unit & Contract Tests
- **API Client Tests (`tests/test_nfl_api.py`)**:
  - URL generation for league schedule, bulk player props, event markets (`GAMELINE`, `TEAM_PROP`), matchups, and team injuries.
  - Header construction: Bearer authentication from session storage or environment variables, User-Agent, and origin headers.
  - Transport mechanics: Gzip payload decompression, unescaped control character tolerance via `json.loads(..., strict=False)`.
  - Resilience: Bounded exponential backoff with jitter on HTTP 403, 429, 500, 502, 503, 504; immediate fail-fast on 401 Unauthorized.
  - Pagination safety: Termination on cursor exhaustion, page count limits, and payload signature fingerprint loops.
- **Normalizer & Taxonomy Tests (`tests/test_nfl_normalizer.py`)**:
  - Team canonicalization: Mapping all 32 NFL franchises, cities, and historical nicknames to canonical 2/3-letter codes (`KC`, `BAL`, `SF`, `LAR`, `NE`, etc.).
  - Market taxonomy: Normalizing football proposition keys across passing (`PASS_YDS`, `PASS_TD`, `PASS_COMP`, `PASS_ATT`, `INT`), rushing (`RUSH_YDS`, `RUSH_ATT`), receiving (`REC_YDS`, `REC`), scoring (`ANYTIME_TD`), game totals, team totals, and spreads.
  - Math invariants: American odds to implied probability percentage conversion; signed spread lines (`format_signed_line`); key-number push probability adjustments.
  - Scope detection: Distinguishing `full_game` from `first_half`, `second_half`, and quarter-level markets.
  - Sportsbook odds extraction: Normalizing both player props dictionary structure (`outcome["bookOdds"]`) and games list structure (`outcome["odds"]`).

### Tier 2: Schema & Payload Integrity Gates
- **Validation Gates (`outlier_nfl/schema.py`)**:
  - `validate_schedule_payload`: Ensures schedule contains an `events` array with valid `eventId`, team metadata, and timestamps.
  - `validate_event_markets_payload`: Ensures market payloads contain valid `markets` with `outcomes`, `books`, and odds objects.
  - `validate_player_props_payload`: Ensures paginated prop feeds contain `props` array with active status, `line`, `position`, and book odds.
  - `validate_normalized_records`: Enforces mandatory fields across normalized outputs (`league`, `event_id`, `market`, `position`, `line`, `books`, `implied_probability`).

### Tier 3: End-to-End Pipeline Integration Tests
- **Pipeline Integration (`tests/test_nfl_pipeline.py`)**:
  - Orchestration of `NflPipeline`: Ingest schedule -> Filter target slate date -> Extract `GAMELINE` and `TEAM_PROP` markets -> Extract `PLAYER_PROP` bulk feed -> Normalize -> Execute schema gates -> Persist to disk.
  - Offline replay mode: Mocking `OutlierNflApiClient` to serve `tests/fixtures/nfl/` payloads.
  - Verifies persistence contracts: Ensures files are written to `data/NFL/normalized/nfl_games_latest.json` and `data/NFL/normalized/nfl_props_latest.json`.
  - Verifies memory efficiency: Confirms atomic writes use file streams (`json.dump`) and retry loops on Windows file locking.

### Tier 4: Standalone Verification Runner
- **Independent Verification Script (`verify_nfl_pipeline.py`)**:
  - Root-level CLI script callable without `pytest`.
  - Supports `--mode fixture` (offline replay) and `--mode live` (authenticated API fetch).
  - Evaluates hard assertions on normalized datasets:
    1. Game Totals: At least one `GAMELINE` / `TOTAL` market with `OVER` and `UNDER` positions and valid numeric lines.
    2. Point Spreads: At least one `GAMELINE` / `SPREAD` market with `HOME` and `AWAY` positions and signed lines (e.g. `-3.5`, `+3.5`).
    3. Team Totals: At least one `TEAM_PROP` / `POINTS` (or `TOTAL`) market with home/away team attribution.
    4. Player Props: At least one `PLAYER_PROP` market across core categories (passing, rushing, receiving).
  - Exits with status code `0` on success and `1` on assertion or pipeline failure.

---

## 3. Test Fixture Specifications (`tests/fixtures/nfl/`)

The offline fixture catalog mirrors production Outlier API responses:

### 3.1 `tests/fixtures/nfl/schedule.json`
Multi-game slate containing scheduled pregame events across conferences:
- `KC @ BAL`: Kansas City Chiefs at Baltimore Ravens (`Arrowhead Stadium`, 2026-09-13T17:00:00Z).
- `SF @ LAR`: San Francisco 49ers at Los Angeles Rams (`SoFi Stadium`, 2026-09-13T20:25:00Z).
- `DAL @ PHI`: Dallas Cowboys at Philadelphia Eagles (`Lincoln Financial Field`, 2026-09-14T00:20:00Z).
Fields captured: `eventId`, `scheduledTime`, `status`, `home`, `away`, `venue`, `network`.

### 3.2 `tests/fixtures/nfl/event_markets.json`
Rich multi-market payload for `GAMELINE` and `TEAM_PROP`:
- `GAMELINE` - `SPREAD`: `KC -3.5` (-110) vs `BAL +3.5` (-110); public money percentages and per-book odds (`DRAFTKINGS`, `FANDUEL`, `BETMGM`, `CAESARS`).
- `GAMELINE` - `TOTAL`: Full-game total `47.5` Over (-110) / Under (-110).
- `GAMELINE` - `MONEYLINE`: `KC` -175 / `BAL` +145.
- `TEAM_PROP` - `POINTS`: `KC Chiefs` Total Points Over 25.5 (-115) / Under 25.5 (-105); `BAL Ravens` Total Points Over 22.5 (-110) / Under 22.5 (-110).

### 3.3 `tests/fixtures/nfl/player_props.json`
Paginated bulk player prop payload covering all major offensive proposition families:
- Passing Yards: Patrick Mahomes Over/Under 268.5.
- Passing Touchdowns: Patrick Mahomes Over/Under 1.5.
- Rushing Yards: Lamar Jackson Over/Under 52.5, Derrick Henry Over/Under 71.5.
- Rushing Attempts: Derrick Henry Over/Under 16.5.
- Receiving Yards: Travis Kelce Over/Under 64.5, Zay Flowers Over/Under 58.5.
- Receptions: Travis Kelce Over/Under 5.5.
- Anytime Touchdown: Derrick Henry Over 0.5.
Metadata captured: `active`, `outcomeId`, `marketId`, `playerId`, `teamId`, `line`, `bookOdds`, and `_page` cursor token.

---

## 4. Coverage Thresholds & Quality Metrics

All modules within `outlier_nfl` are subject to the following strict quality thresholds:

| Module | Scope | Target Line Coverage | Target Branch Coverage |
|---|---|---|---|
| `outlier_nfl/constants.py` | Configuration constants | 100% | N/A |
| `outlier_nfl/config.py` | 32-team registry & market taxonomy | 95%+ | 90%+ |
| `outlier_nfl/models.py` | Immutable dataclasses | 95%+ | 90%+ |
| `outlier_nfl/schema.py` | Validation gates | 90%+ | 85%+ |
| `outlier_nfl/utils.py` | Atomic writes, retry loops, dates | 90%+ | 85%+ |
| `outlier_nfl/api.py` | API client, backoff, pagination | 85%+ | 80%+ |
| `outlier_nfl/normalizer.py`| Market & odds normalization | 90%+ | 85%+ |
| `outlier_nfl/games.py` | Game totals, spreads, team totals | 85%+ | 80%+ |
| `outlier_nfl/props.py` | Player props extraction | 85%+ | 80%+ |
| `outlier_nfl/pipeline.py` | End-to-end orchestration | 85%+ | 80%+ |

### Aggregate Coverage Goal
- Overall package coverage: **>= 85%**.
- 100% test pass rate across `tests/test_nfl_*.py`.
- Clean static analysis: zero errors under `ruff check` and `mypy`.

---

## 5. Verification Commands

### Run Complete Unit Test Suite
```powershell
pytest tests/test_nfl_api.py tests/test_nfl_normalizer.py tests/test_nfl_pipeline.py -v
```

### Run with Coverage Verification
```powershell
pytest tests/test_nfl_*.py --cov=outlier_nfl --cov-report=term-missing
```

### Run Standalone Verification Script (Offline Fixture Mode)
```powershell
python verify_nfl_pipeline.py --mode fixture --verbose
```

### Run Standalone Verification Script (Live API Mode - Requires Session)
```powershell
python verify_nfl_pipeline.py --mode live --verbose
```
