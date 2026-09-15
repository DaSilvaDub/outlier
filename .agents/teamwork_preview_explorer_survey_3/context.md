# Survey Explorer 3 Assignment

## Identity & Role
You are Explorer 3 (`teamwork_preview_explorer`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_3`
Parent: `teamwork_preview_orchestrator_2`

## Mission
Investigate NFL market coverage, prop normalization, and testing requirements (R3 and Acceptance Criteria).

## Required Reading
- Read `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (specifically `## 2026-09-12T10:39:21Z`).

## Scope of Investigation
1. Examine how player props and team props (game totals, team totals, spreads) are defined, normalized, and parsed in `outlier_scrapers/normalizer.py`, `models.py`, `game_totals.py`, etc.
2. Enumerate NFL player prop markets (e.g. PASSING_YARDS, RUSHING_YARDS, RECEIVING_YARDS, PASSING_TDS, RECEPTIONS, RUSHING_ATTEMPTS, ANYTIME_TD, etc.) and team prop / game markets (SPREAD, TOTAL/game total, TEAM_TOTAL/points).
3. Investigate how testing is structured across the repository (`tests/test_*.py`, pytest configuration, fixtures).
4. Outline the exact test coverage strategy and design for `tests/test_nfl_*.py` and the standalone verification script `verify_nfl_pipeline.py`.
5. Deliver report to `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_3\report.md`.
