# E2E Testing Track Task Assignment

## Mission
You are the E2E Testing Orchestrator (`teamwork_preview_orchestrator_e2e`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_e2e`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Scope
Design and implement the comprehensive opaque-box test suite and standalone verification harness for `outlier_nfl` (Features F12, F13, F14 in `PROJECT.md`):
1. Create `TEST_INFRA.md` at project root defining test philosophy, architecture, and coverage thresholds.
2. Create deterministic offline test fixtures in `tests/fixtures/nfl/`:
   - `schedule.json`: Multi-game NFL schedule with teams, event IDs, kickoff times.
   - `event_markets.json`: Rich event markets payload containing `GAMELINE` (totals, spreads, ML) and `TEAM_PROP` (team total points).
   - `player_props.json`: Paginated player props payload covering passing, rushing, receiving, and touchdown props.
3. Create test suite in `tests/`:
   - `test_nfl_api.py`: Tests API client URL construction, exponential backoff, headers, gzip, pagination.
   - `test_nfl_normalizer.py`: Tests team/market normalization, odds conversion, signed spread lines, football scopes, team totals.
   - `test_nfl_pipeline.py`: Tests end-to-end extraction and file writing using offline fixtures.
4. Create root-level standalone verification script `verify_nfl_pipeline.py`:
   - Runs `outlier_nfl` end-to-end (supporting `--fixture` / `--mode replay` and `--live`).
   - Automatically asserts that output data structures contain expected NFL prop markets:
     - Game Totals (`GAMELINE`, `TOTAL`)
     - Point Spreads (`GAMELINE`, `SPREAD` with signed lines)
     - Team Totals (`TEAM_PROP`, `POINTS`/`TOTAL`)
     - Player Props (`PLAYER_PROP` across passing, rushing, receiving)
   - Exits 0 on success.
5. Publish `TEST_READY.md` at project root when complete.

## Constraints & Mandatory Rules
- Read `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md`.
- Read `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`.
- Zero runtime coupling: tests must test `outlier_nfl` without depending on or altering MLB/WNBA tests.
- HOUSE RULE: NEVER run reasoning models unless explicitly asked this turn.
- Follow Project Pattern Iteration Loop (2B) or test writer delegation.
- Maintain `progress.md` and `BRIEFING.md` in your working directory.
- Deliver `handoff.md` and send completion message to parent upon completion.
