# Handoff Report — Explorer 3 (Milestone 1 Iteration 2)

**Agent**: Explorer 3 (`teamwork_preview_explorer_m1_r2_3`)  
**Parent**: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)  
**Date**: 2026-09-12T11:25:00Z  
**Handoff Type**: Hard (Investigation Complete)  
**Deliverable**: Detailed Fix Strategy in `report.md`  

---

## 1. Observation

Direct empirical observations verified in the repository:

### 1.1 Step 0 Multi-Agent Sync Attestation
Executed canonical sync verification:
```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```
Verbatim trailer:
```
=== VERDICT ===
REPORT STATUS: OK
  bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)

RUN-NONCE: c308e8bc48ce4a27  utc=2026-09-12T11:08:49Z  head=a3ad900  status=OK
```

### 1.2 Unhandled `AttributeError` in Schema Validation (`outlier_nfl/schema.py`)
In `outlier_nfl/schema.py`, lines 150–154 and 197–201:
```python
for b_idx, book_entry in enumerate(books):
    b_dict = book_entry if isinstance(book_entry, dict) else book_entry.to_dict()
    if "book" not in b_dict or "odds" not in b_dict:
        errors.append(f"Book entry at index {b_idx} missing 'book' or 'odds'")
```
When `book_entry` is not a dict and lacks `.to_dict()` (such as `None`, `"DraftKings -110"`, `123`), execution crashes:
`AttributeError: 'str' object has no attribute 'to_dict'`
`AttributeError: 'NoneType' object has no attribute 'to_dict'`
Empirically confirmed in `tests/test_nfl_stress_m1.py::TestGameLineRecordValidationGate::test_adversarial_non_dict_book_entry_behavior` and `TestPlayerPropRecordValidationGate::test_adversarial_non_dict_book_entry_behavior`.

### 1.3 Temp Path Collision in `safe_write_json` (`outlier_nfl/utils.py`)
In `outlier_nfl/utils.py`, line 78:
```python
temp_path = target.parent / f".{target.name}.{int(time.time() * 1000)}.tmp"
```
Windows wall clock resolution (`time.time()`) produces identical millisecond integers across concurrent or rapid consecutive writes.
Empirically confirmed in `tests/test_nfl_stress_m1.py`:
When Thread A and Thread B compute identical `temp_path` and Thread A replaces `target`, Thread B's attempt to execute `src.replace(dst)` raises:
`FileNotFoundError: [WinError 2] The system cannot find the file specified`

### 1.4 Zero-Retry Sensitivity in `safe_read_json` (`outlier_nfl/utils.py`)
In `outlier_nfl/utils.py`, lines 98–103:
```python
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f, strict=False)
    except Exception as exc:
        logger.error("Failed to read JSON from %s: %s", target, exc)
        return default
```
When a file is being atomically replaced on Windows or held by an external process (OneDrive, antivirus), `open(target, "r")` raises:
`PermissionError: [WinError 32] The process cannot access the file because it is being used by another process`
`safe_read_json` catches `Exception` with 0 retries and immediately returns `default` (`None`), causing readers to falsely perceive valid data as missing.

### 1.5 Permissive Numeric Validation for Booleans and NaN (`outlier_nfl/schema.py`)
In `outlier_nfl/schema.py`, lines 137–144:
`isinstance(True, (int, float))` is `True` because `issubclass(bool, int)` is `True`.
`isinstance(float('nan'), (int, float))` is `True`.
Records with `line: True`, `implied_probability: True`, or `line: float('nan')` produce 0 validation errors (`errors == []`), resulting in corrupt data entering normalized storage.

### 1.6 Current Offline Pytest Execution Baseline
Executed:
```powershell
pytest tests/test_nfl_api.py tests/test_nfl_normalizer.py tests/test_nfl_stress_m1.py tests/test_nfl_stress.py
```
Output:
`210 passed, 4 skipped in 6.75s` (all stress tests and unit tests run; skipped tests are normalizer functions scheduled for M2).

---

## 2. Logic Chain

1. **Gate Invariant**: Runtime validation functions (`validate_game_line_record`, `validate_player_prop_record`) are designed as defensive boundaries against untrusted input. Raising unhandled `AttributeError` on corrupted list elements inverts this contract and terminates pipeline runs. (Linked to Observation 1.2)
2. **Concurrency Invariant**: Under multi-threaded scraping or cloud-synced directory structures, atomic write temporary paths must be unique across all processes and threads. Relying on coarse millisecond timestamps guarantees collisions and `FileNotFoundError`. (Linked to Observation 1.3)
3. **Resilient Reading**: Windows file locking during atomic rename operations creates transient `PermissionError: [WinError 32]`. Just as `_replace_with_retry` handles transient locks on write, `safe_read_json` must employ an exponential backoff retry loop before giving up. (Linked to Observation 1.4)
4. **Data Integrity Invariant**: Booleans and non-finite floats (`NaN`, `Inf`) corrupt financial calculation engines and produce invalid JSON tokens. Strict checks using `not isinstance(x, bool)` and `math.isfinite(x)` are necessary. (Linked to Observation 1.5)
5. **Synthesis**: Challenger 2's critique is valid in all aspects. The required changes have been formulated into drop-in diffs in `report.md`.

---

## 3. Caveats

- **Read-Only Scope**: In strict accordance with Explorer archetype rules, no source files in `outlier_nfl/` or tests in `tests/` were modified directly during this investigation.
- **Test Suite Updates Required**: In `tests/test_nfl_stress_m1.py`, Challenger 2 authored tests asserting that the bugs exist (e.g. `with pytest.raises(AttributeError): validate_game_line_record(record)`). When Worker 1 applies the fixes, those assertions in `test_nfl_stress_m1.py` must be updated to assert valid error handling, otherwise pytest will flag test failures on the healed code. Full guidance is provided in `report.md` Section 4.

---

## 4. Conclusion

All Challenger 2 findings are verified and actionable. Worker 1 should apply the following targeted modifications:

1. **`outlier_nfl/schema.py`**:
   - Add `_validate_book_entry` with type guards checking `isinstance(book_entry, dict)`, callable `.to_dict()`, non-empty `book` string, and integer `odds` (excluding `bool`).
   - Guard `line` and `implied_probability` against `bool`, `NaN`, and `Inf` using `math.isfinite()`.
   - Accept both `list` and `tuple` for `books`.
2. **`outlier_nfl/utils.py`**:
   - Update `safe_write_json` temp path to use `f"{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{uuid.uuid4().hex}"`.
   - Update `safe_read_json` to include 5 retries on `PermissionError`, `[WinError 32]`, `[WinError 5]`, and `[Errno 13]`.
3. **`outlier_nfl/models.py`**:
   - Coerce `books` to `tuple[BookPrice, ...]` in `__post_init__` to guarantee true immutability and allow hashability (`hash(gl)`).
4. **`tests/test_nfl_stress_m1.py`**:
   - Update the 4 test assertions currently asserting the bug conditions to assert the healed behavior.

All code diffs and implementation details are fully documented in:
`C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_m1_r2_3\report.md`

---

## 5. Verification Method

To verify after Worker 1 completes implementation:

1. Run sync attestation:
   ```powershell
   & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
   ```
2. Run full test suite:
   ```powershell
   pytest tests/test_nfl_api.py tests/test_nfl_normalizer.py tests/test_nfl_stress.py tests/test_nfl_stress_m1.py -v
   ```
3. Run lint and type checking:
   ```powershell
   ruff check outlier_nfl tests/test_nfl_*.py
   python -m mypy outlier_nfl
   ```
4. Invalidation conditions:
   - If `validate_game_line_record({"books": ["bad"]})` raises `AttributeError`.
   - If `safe_write_json` crashes under concurrent thread writes to the same destination.
   - If `safe_read_json` fails to retry when encountering a transient Windows lock.
