# Milestone 1 Iteration 2 Worker Assignment

## Identity & Role
You are Worker 2 (`teamwork_preview_worker`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1_r2`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read first)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Explorer Reports:
   - Explorer 1 (API Resilience): `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_m1_r2_1\report.md`
   - Explorer 2 (Team Taxonomy): `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_m1_r2_2\report.md`
   - Explorer 3 (Schema & I/O): `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_m1_r2_3\report.md`

## Scope of Work (Remediation Items)
Implement the exact fixes recommended by the 3 Explorers:
1. `outlier_nfl/api.py`:
   - Expand stream error catching to include `STREAM_TRANSPORT_ERRORS = (URLError, OSError, http.client.HTTPException, zlib.error, EOFError)`.
   - In `fetch_json`, handle `(json.JSONDecodeError, ValueError)` during `json.loads(text, strict=False)` with retry on transient errors and clear `OutlierNflApiError` on persistent errors.
2. `outlier_nfl/config.py`:
   - Expand `NFL_TEAM_ALIASES` with all composite code+nickname and nickname+code aliases across all 32 teams (as documented in Explorer 2 `report.md`).
3. `outlier_nfl/schema.py`:
   - Implement `_validate_book_entry` checking `isinstance(b_dict, dict)` safely without bare `.to_dict()` crash.
   - Reject `bool`, `NaN`, and `Inf` in numeric fields (`line`, `implied_probability`, `decimal`, `odds`).
   - Accept both `list` and `tuple` for `books`.
4. `outlier_nfl/utils.py`:
   - Formulate unique temp path in `safe_write_json`: `unique_suffix = f"{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{uuid.uuid4().hex}"`.
   - Add retry loop in `safe_read_json` on transient Windows lock errors (`[WinError 32]`, `[WinError 5]`, `PermissionError`).
5. `outlier_nfl/models.py`:
   - Store `books: tuple[BookPrice, ...] | list[BookPrice]`, coercing `list` to `tuple` in `__post_init__` so models are immutable and hashable.
6. Test suite updates:
   - Update `test_normalize_team_code_plus_nickname_adversarial` in `tests/test_nfl_stress.py` to assert that all 10 composite names resolve successfully.
   - Update the 4 test cases in `tests/test_nfl_stress_m1.py` as detailed in Explorer 3 `report.md § 4.1`.

## File Ownership
- `outlier_nfl/api.py`
- `outlier_nfl/config.py`
- `outlier_nfl/schema.py`
- `outlier_nfl/utils.py`
- `outlier_nfl/models.py`
- `tests/test_nfl_stress.py`
- `tests/test_nfl_stress_m1.py`
Do NOT modify files in `outlier_scrapers/` or `data/`.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## House Rules & Verification
- HOUSE RULE: NEVER run reasoning models unless explicitly asked this turn.
- Run pytest across `tests/test_nfl_*.py` and verify all tests pass.
- Run `ruff check outlier_nfl` and `python -m mypy outlier_nfl`.
- Deliver `handoff.md` and report completion via send_message.
