# Handoff Report — Explorer 2: NFL Data Sourcing & API Patterns

## 1. Observation

1. **NFL League Token Resolution**:
   - In `docs/plans/2026-08-31-ncaaf-analytics-pipeline.md` (lines 714-719):
     ```markdown
     Probed api.outlier.bet against ten candidate tokens using the existing authenticated session.
     NCAAF, CFB, FBS, NCAAFOOTBALL, COLLEGEFOOTBALL, NCAA_FB, NCAAF_FBS, CFP all return
     HTTP 502 (unknown league). NCAAFB returns 137 pregame events spanning 2026-09-03 → 2026-12-12,
     including 30 games on Saturday 2026-09-05. NFL also resolves (260 events), which confirms 502
     means "unknown league" rather than a transport failure.
     ```
2. **API Client & Request Structure**:
   - In `outlier_scrapers/api.py`:
     - `API_BASE_URL = "https://api.outlier.bet"` (line 20).
     - `RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}` (line 23).
     - `fetch_schedule`: `GET /sportsdata/leagues/{token}/schedule` (lines 170-172).
     - `fetch_player_props`: `GET /sportsdata/leagues/{token}/playerProps` paginated (lines 292-296).
     - `fetch_event_markets`: `GET /sportsdata/events/{event_id}/markets?marketType={quote(market_type)}` (lines 310-312).
     - `json.loads(body.decode("utf-8"), strict=False)` (line 151).
3. **Mandatory Query Parameter on Event Markets**:
   - In `docs/games_section_api_map.md` (lines 19, 25-26):
     ```markdown
     | 3 | GET /sportsdata/events/{eventId}/markets | marketType= required | markets[] — the core game data
     marketType is a hard filter — calling /markets with no marketType returns 0 markets.
     ```
4. **Market Types & Propositions**:
   - In `outlier_scrapers/registry.py`:
     - `GAME_MARKET_TYPES = ("GAMELINE", "PLAYER_PROP", "TEAM_PROP", "GAME_PROP")` (line 37).
     - Game lines include `TOTAL`, `SPREAD`, `MONEYLINE`.
   - In `outlier_scrapers/team_totals.py` (lines 12-22):
     - Team totals match `POINTS`, `TOTAL`, `TEAM_TOTAL`.
   - In `outlier_scrapers/alt_spreads.py` (lines 48-60):
     - Spread validation requires `market_type == "GAMELINE"`, `proposition == "SPREAD"`, and `position in {"HOME", "AWAY"}`.
5. **Football Date Scoping Trap**:
   - In `docs/plans/2026-08-31-ncaaf-analytics-pipeline.md` (lines 805-817):
     ```markdown
     scheduledTime is UTC (+00:00 on all 137 events) and dayOfWeek is numeric... Grouping the slate by UTC date is wrong in both directions...
     The slate is therefore defined by utils.football_date (US Eastern calendar date), which reproduces the feed's own dayOfWeek exactly;
     ```
6. **Fixture & Mocking Patterns**:
   - `tests/test_games.py` (lines 344-400) implements `FakeGamesClient` providing offline mock fixtures for `fetch_schedule`, `fetch_event_matchup`, `fetch_event_markets`, and `fetch_team_injuries`.
   - `tests/fixtures/` stores minimal JSON payloads (`mlb_schedule.json`, `mlb_player_props.json`, `wnba_schedule.json`, `wnba_player_props.json`).

## 2. Logic Chain

1. **League Identification**: Observation 1 proves that `NFL` is the canonical, verified league token on `api.outlier.bet` that resolves 200 OK and returns live schedules (260 events), with no custom token guessing needed.
2. **Endpoint Mapping**:
   - Sourcing schedule and slate requires `GET /sportsdata/leagues/NFL/schedule` (Observation 2).
   - Sourcing game totals and spreads requires `GET /sportsdata/events/{eventId}/markets?marketType=GAMELINE` (Observations 2, 3, 4).
   - Sourcing team totals requires `GET /sportsdata/events/{eventId}/markets?marketType=TEAM_PROP` (Observations 2, 3, 4).
   - Sourcing player props can be performed either per-event via `marketType=PLAYER_PROP` or in bulk via `GET /sportsdata/leagues/NFL/playerProps` (Observations 2, 4).
3. **Data Quality & Ingestion Invariants**:
   - Because `marketType` is mandatory (Observation 3), any generic call without `marketType` will fail to return markets; the pipeline must iterate over the required types.
   - Because late games kick off across UTC midnight (Observation 5), date scoping for NFL slates must convert `scheduledTime` to US Eastern date before filtering.
   - Because Outlier's upstream API periodically emits unescaped control characters (Observation 2), `json.loads(..., strict=False)` is mandatory.
4. **Architectural Separation**:
   - The user request explicitly demands a standalone NFL pipeline (`outlier_nfl`) leaving MLB and WNBA code paths intact. Creating `outlier_nfl/` as a dedicated top-level package with its own `api.py`, `registry.py`, `normalizer.py`, and `extractor.py` ensures zero regression on `outlier_scrapers/` while reusing proven patterns.

## 3. Caveats

- **Live Endpoint Calls**: Under the read-only exploration mandate and the repo house rule ("never run reasoning models / stay offline"), live network calls to `api.outlier.bet` were not executed during this turn. Findings rely on committed code, verified probe logs (`docs/plans/2026-08-31-ncaaf-analytics-pipeline.md`), and historical integration docs (`docs/games_section_api_map.md`).
- **Active Season Timing**: While `NFL` endpoint availability is proven, market liquidity and available books vary by day of week (widest Thursday through Monday).

## 4. Conclusion

The Outlier API endpoints, token identifiers, request structures, and parsing patterns for NFL are fully mapped and documented in `report.md`. A standalone `outlier_nfl` module can be implemented with high fidelity using existing repo design patterns, backed by offline fixtures and verified via `verify_nfl_pipeline.py`.

## 5. Verification Method

To independently verify the observations and findings:
1. Inspect the Outlier API endpoints and client patterns:
   `view_file` on `outlier_scrapers/api.py` and `docs/games_section_api_map.md`.
2. Inspect the NFL token probe evidence:
   `view_file` on `docs/plans/2026-08-31-ncaaf-analytics-pipeline.md` at line 717.
3. Review the complete survey report:
   `view_file` on `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_2\report.md`.
4. Run project offline test suite to ensure workspace integrity:
   `pytest tests/test_games.py tests/test_api_pagination.py`
