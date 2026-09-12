# Milestone 1 Task Assignment: NFL Core & API Client

## Mission
You are the Sub-Orchestrator for Milestone 1 (`teamwork_preview_orchestrator_m1`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_m1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Scope
Implement the core foundation and API client for `outlier_nfl` (Features F1 through F7 in `PROJECT.md`):
1. `outlier_nfl/__init__.py`: Package export surface.
2. `outlier_nfl/constants.py`: Base URL (`https://api.outlier.bet`), league token (`NFL`), retry and timeout constants.
3. `outlier_nfl/config.py`: 32 NFL team aliases, abbreviations, display names, and market taxonomy.
4. `outlier_nfl/models.py`: Strongly-typed dataclasses (`BookPrice`, `NflGameLine`, `NflPlayerProp`).
5. `outlier_nfl/api.py`: `OutlierNflApiClient` with session discovery from `config/.outlier_session`, bounded exponential backoff with jitter on 403/429/50x, gzip support, `strict=False` JSON decoding, and methods: `fetch_schedule()`, `fetch_event_markets()`, `fetch_player_props()`.
6. `outlier_nfl/schema.py`: Validation gates for raw and normalized payloads.
7. `outlier_nfl/utils.py`: Atomic file writing and replace with retries on `[WinError 32]` cloud-sync locks.

## Constraints & Mandatory Rules
- Read `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md`.
- Read `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`.
- Zero runtime coupling: DO NOT import from `outlier_scrapers`.
- HOUSE RULE: NEVER run reasoning models unless explicitly asked this turn.
- Follow Project Pattern Iteration Loop (2B) with Explorers, Workers, Reviewers, Challengers, and Forensic Auditor.
- Maintain `progress.md` and `BRIEFING.md` in your working directory.
- When milestone passes gate, deliver `handoff.md` and send completion message to parent.
