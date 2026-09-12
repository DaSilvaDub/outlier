# E2E Testing Track Test Writer Assignment

## Identity & Role
You are Test Writer 1 (`teamwork_preview_test_writer`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_test_writer_1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read before starting work)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Explorer Reports:
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\report.md`
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_2\report.md`
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_3\report.md`

## Scope of Work (E2E Testing Track)
Design and implement the comprehensive test harness, offline fixtures, test suite, and standalone verification runner:
1. `TEST_INFRA.md`: Document test architecture, 4-tier test methodology, and coverage thresholds.
2. `tests/fixtures/nfl/`:
   - `schedule.json`: Multi-game NFL schedule (e.g. KC@BAL, SF@LAR, DAL@PHI) with start times, team IDs.
   - `event_markets.json`: Rich event markets payload containing `GAMELINE` (totals, spreads, ML) and `TEAM_PROP` (team total points).
   - `player_props.json`: Paginated player props payload covering passing, rushing, receiving, and touchdown props.
3. Unit Test Suite:
   - `tests/test_nfl_api.py`: Tests API client URL construction, exponential backoff, headers, gzip, pagination loop safety.
   - `tests/test_nfl_normalizer.py`: Tests team/market normalization, odds conversion, signed spread lines, football scopes, team totals.
   - `tests/test_nfl_pipeline.py`: Tests end-to-end extraction and file writing using offline fixtures.
4. Standalone Verification Script:
   - `verify_nfl_pipeline.py` at repository root: Runs extraction end-to-end and asserts that output contains:
     - Game Totals (`GAMELINE`, `TOTAL`)
     - Point Spreads (`GAMELINE`, `SPREAD` with signed lines)
     - Team Totals (`TEAM_PROP`, `POINTS`/`TOTAL`)
     - Player Props (`PLAYER_PROP` across passing, rushing, receiving)
     - Exits 0 on success.
5. Create `TEST_READY.md` when the test suite is ready.

## File Ownership
You exclusively own and may create/edit:
- `TEST_INFRA.md`
- `tests/fixtures/nfl/schedule.json`
- `tests/fixtures/nfl/event_markets.json`
- `tests/fixtures/nfl/player_props.json`
- `tests/test_nfl_api.py`
- `tests/test_nfl_normalizer.py`
- `tests/test_nfl_pipeline.py`
- `verify_nfl_pipeline.py`
- `TEST_READY.md`
Do NOT modify files in `outlier_scrapers/` or `outlier_nfl/`.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## House Rules & Constraints
- HOUSE RULE: NEVER run reasoning models unless explicitly asked this turn.
- Tests must be completely offline (mocked / fixture-based) so they run fast and never hit external network during CI.
- Document test runner invocation and results in `handoff.md`.
