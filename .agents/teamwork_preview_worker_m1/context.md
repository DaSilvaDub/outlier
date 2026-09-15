# Milestone 1 Worker Assignment

## Identity & Role
You are Worker 1 (`teamwork_preview_worker`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read before starting work)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Explorer Reports:
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\report.md`
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_2\report.md`
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_3\report.md`

## Scope of Work (Milestone 1)
Implement the core foundation and API client for `outlier_nfl`:
1. `outlier_nfl/__init__.py`: Package export surface.
2. `outlier_nfl/constants.py`: Base URL (`https://api.outlier.bet`), league token (`NFL`), retry and timeout constants, market types.
3. `outlier_nfl/config.py`: 32 NFL team aliases, abbreviations, display names, and football market taxonomy.
4. `outlier_nfl/models.py`: Strongly-typed immutable dataclasses (`BookPrice`, `NflGameLine`, `NflPlayerProp`).
5. `outlier_nfl/schema.py`: Raw payload & normalized record validation gates.
6. `outlier_nfl/api.py`: `OutlierNflApiClient` with session discovery from `config/.outlier_session/storage_state.json` or `OUTLIER_BEARER_TOKEN`, bounded exponential backoff with jitter on 403/429/50x, gzip support, `strict=False` JSON decoding, and methods: `fetch_schedule()`, `fetch_event_markets()`, `fetch_player_props()`, `fetch_event_matchup()`, `fetch_team_injuries()`.
7. `outlier_nfl/utils.py`: Atomic file writing and replace with retries on `[WinError 32]` cloud-sync locks, and ISO date parsing.

## File Ownership
You exclusively own and may create/edit the following files:
- `outlier_nfl/__init__.py`
- `outlier_nfl/constants.py`
- `outlier_nfl/config.py`
- `outlier_nfl/models.py`
- `outlier_nfl/schema.py`
- `outlier_nfl/api.py`
- `outlier_nfl/utils.py`
Do NOT edit any files in `outlier_scrapers/`, `data/`, or `tests/`.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## House Rules & Constraints
- HOUSE RULE: NEVER run reasoning models unless explicitly asked this turn.
- Zero runtime coupling: DO NOT import from `outlier_scrapers`.
- Python memory efficiency: Always use `json.dump(data, f)` when writing JSON files, never `json.dumps()` + `write_text()`.
- Windows PowerShell: Use `| Out-File -Encoding utf8` if redirecting output; do not chain with `&&` or `||`, use `;`.
- Verify your code compiles and imports cleanly. Document verification commands and results in `handoff.md`.
