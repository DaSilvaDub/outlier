# Handoff Report — Milestone 1 Adversarial Review (Challenger 2)

**Agent:** Challenger 2 (`teamwork_preview_challenger_m1_2`)  
**Parent:** `teamwork_preview_orchestrator_2` (`d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)  
**Date:** 2026-09-12T11:08:30Z  
**Verdict:** `REQUEST_CHANGES`  
**Handoff Type:** Hard (Review Complete)

---

## 1. Observation

Direct empirical observations obtained by writing and executing 96 adversarial test cases in `tests/test_nfl_stress_m1.py`:

### Observation 1: Unhandled `AttributeError` Crash in Schema Validation
In `outlier_nfl/schema.py`, lines 150–154 and lines 197–201:
```python
        for b_idx, book_entry in enumerate(books):
            b_dict = book_entry if isinstance(book_entry, dict) else book_entry.to_dict()
            if "book" not in b_dict or "odds" not in b_dict:
                errors.append(f"Book entry at index {b_idx} missing 'book' or 'odds'")
```
When `books` contains non-dict items lacking `.to_dict()` (e.g. `["DraftKings -110"]`, `[None]`, `[123]`), both `validate_game_line_record` and `validate_player_prop_record` crash with an unhandled exception rather than appending a validation error:
```
AttributeError: 'str' object has no attribute 'to_dict'
AttributeError: 'NoneType' object has no attribute 'to_dict'
```
*Empirical reproduction*: `tests/test_nfl_stress_m1.py::TestGameLineRecordValidationGate::test_adversarial_non_dict_book_entry_behavior` and `TestPlayerPropRecordValidationGate::test_adversarial_non_dict_book_entry_behavior`.

### Observation 2: Race Condition and `FileNotFoundError` in `safe_write_json`
In `outlier_nfl/utils.py`, line 78:
```python
temp_path = target.parent / f".{target.name}.{int(time.time() * 1000)}.tmp"
```
Rapid calls to `int(time.time() * 1000)` on Windows produce identical millisecond timestamps (`len(set([int(time.time()*1000) for _ in range(1000)])) == 1`). Under concurrent writes to the same destination:
1. Thread A and Thread B compute the identical `temp_path`.
2. Thread A moves the temporary file to target via `_replace_with_retry`.
3. Thread B attempts `src.replace(dst)` on the already-moved temporary file and crashes:
```
FileNotFoundError: [WinError 2] The system cannot find the file specified: 'C:\...\.test_collision.json.1789211110523.tmp' -> 'C:\...\test_collision.json'
```
*Empirical reproduction*: Executing 20 concurrent thread writes to `test_collision.json` reliably caught `FileNotFoundError: [WinError 2]`.

### Observation 3: `safe_read_json` Lacks Transient Lock Retry
In `outlier_nfl/utils.py`, lines 93–104:
```python
def safe_read_json(path: Path | str, default: Any = None) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f, strict=False)
    except Exception as exc:
        logger.error("Failed to read JSON from %s: %s", target, exc)
        return default
```
When `safe_read_json` is invoked during an ongoing atomic replace on Windows, Windows raises `PermissionError` (`[Errno 13] Permission denied` / `[WinError 32] Sharing violation` / `[WinError 5] Access denied`). Because `safe_read_json` has zero retries, it immediately logs an error and returns `default` (`None`), causing readers to perceive valid data as missing.
*Empirical reproduction*: Observed in `TestAtomicFileOperations::test_safe_write_json_concurrent_stress` log: `ERROR outlier_nfl.utils:utils.py:102 Failed to read JSON from ...\worker_2.json: [Errno 13] Permission denied`.

### Observation 4: `NflGameLine` and `NflPlayerProp` Dataclasses Leak Mutability and Break Hashability
In `outlier_nfl/models.py`, lines 43 and 71:
```python
books: list[BookPrice]
```
Even though the dataclasses are decorated with `@dataclass(frozen=True)`:
1. `gl.books.append(BookPrice("FD", -105, "-105"))` succeeds at runtime, increasing the list length from 1 to 2 and breaking the immutability guarantee.
2. `hash(gl)` raises `TypeError: unhashable type: 'list'`, preventing records from being stored in `set()` or used as dict keys for deduplication.
*Empirical reproduction*: `tests/test_nfl_stress_m1.py::TestDomainModelsSafety::test_adversarial_models_list_mutation` and `test_adversarial_dataclass_hashability_with_list`.

### Observation 5: Schema Validation Accepts `bool` and `NaN` Values
In `outlier_nfl/schema.py`, lines 138–144 and 186–190:
- `isinstance(True, (int, float))` is `True` in Python. A record with `line: True` and `implied_probability: True` produces 0 validation errors (`errors == []`).
- `isinstance(float('nan'), (int, float))` is `True`. A record with `line: float('nan')` produces 0 validation errors (`errors == []`), resulting in non-standard JSON `NaN` emitted during serialization.
*Empirical reproduction*: `tests/test_nfl_stress_m1.py::TestAdversarialEdgeCases::test_adversarial_schema_accepts_boolean_values` and `test_adversarial_schema_accepts_nan_line`.

### Observation 6: Verified Robust Functionality
- `_replace_with_retry` successfully recovers from simulated transient Windows `[WinError 32]` lock contention (verified via `TestAtomicFileOperations::test_replace_with_retry_succeeds_under_transient_lock`).
- `to_eastern_date` correctly resolves night games across UTC midnight (00:15–03:30 UTC) to the previous-day US Eastern slate date.
- `safe_read_json` correctly parses unescaped control characters via `strict=False`.
- Top-level raw API payload gates (`validate_schedule_payload`, `validate_event_markets_payload`, `validate_player_props_payload`) correctly reject non-dict payloads, missing collection keys, and malformed entries.

---

## 2. Logic Chain

1. **Gate Reliability (Observation 1)**: Schema validation gates exist to protect data pipelines from unexpected input. When a corrupted record contains non-dict elements in `books`, `validate_game_line_record` and `validate_player_prop_record` crash with an unhandled `AttributeError` instead of recording an error string. This violates the contract of a validation gate.
2. **Concurrency & Atomicity (Observations 2 & 3)**: On multi-threaded runs or during cloud-sync contention (e.g. OneDrive), `safe_write_json` will experience temp file collisions because `int(time.time() * 1000)` does not guarantee uniqueness. Furthermore, concurrent readers calling `safe_read_json` will falsely return `None` when colliding with atomic replaces because `safe_read_json` lacks retry logic on transient locks.
3. **Data Model Safety (Observation 4)**: The contract in `PROJECT.md` specifies immutable dataclasses (`BookPrice`, `NflGameLine`, `NflPlayerProp`). Storing `books` as a standard mutable `list` violates immutability and makes instances unhashable (`TypeError: unhashable type: 'list'`), which will cause failures in Milestone 2 during market deduplication and ranking.
4. **Data Integrity (Observation 5)**: Permitting booleans and `NaN` values to pass through schema validation risks propagating corrupt lines into normalized JSON and sizing models.
5. **Deductive Conclusion**: While the foundation is well-structured and passes basic unit tests, the presence of crashing gates, temp path race conditions, and unhashable/mutable domain models requires remediation before Milestone 2 begins.

---

## 3. Caveats

- Milestone 2 modules (`normalizer.py`, `games.py`, `props.py`, `pipeline.py`) have not yet been implemented; their downstream consumption of these models was evaluated against the interface contract in `PROJECT.md`.
- Network and live API responses were not probed, in strict adherence to offline testing rules.

---

## 4. Conclusion

Verdict: **`REQUEST_CHANGES`**

### Concrete Actionable Changes Required from Worker 1:

1. **Fix Schema Gate Book Entry Check (`outlier_nfl/schema.py`)**:
   In `validate_game_line_record` and `validate_player_prop_record`, replace:
   ```python
   b_dict = book_entry if isinstance(book_entry, dict) else book_entry.to_dict()
   ```
   with safe type verification:
   ```python
   if isinstance(book_entry, dict):
       b_dict = book_entry
   elif hasattr(book_entry, "to_dict") and callable(book_entry.to_dict):
       b_dict = book_entry.to_dict()
   else:
       errors.append(f"Book entry at index {b_idx} is not a valid dict or BookPrice")
       continue
   ```
   And verify `b_dict.get("book")` is a non-empty string and `b_dict.get("odds")` is an integer / numeric value:
   ```python
   if not b_dict.get("book") or b_dict.get("odds") is None:
       errors.append(f"Book entry at index {b_idx} missing valid 'book' or 'odds'")
   ```

2. **Eliminate Temp Path Collision in `safe_write_json` (`outlier_nfl/utils.py`)**:
   Incorporate unique process/UUID entropy:
   ```python
   import os, uuid
   temp_path = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
   ```

3. **Add Transient Lock Retry to `safe_read_json` (`outlier_nfl/utils.py`)**:
   Allow `safe_read_json` to retry on `[WinError 32]`, `[WinError 5]`, or `[Errno 13]` before returning `default`:
   ```python
   def safe_read_json(path: Path | str, default: Any = None, retries: int = 3, delay: float = 0.05) -> Any:
       target = Path(path)
       if not target.exists():
           return default
       for attempt in range(1, retries + 1):
           try:
               with open(target, "r", encoding="utf-8") as f:
                   return json.load(f, strict=False)
           except (PermissionError, OSError) as exc:
               if attempt < retries:
                   time.sleep(delay * attempt)
                   continue
               logger.error("Failed to read JSON from %s after %d retries: %s", target, retries, exc)
               return default
           except Exception as exc:
               logger.error("Failed to parse JSON from %s: %s", target, exc)
               return default
       return default
   ```

4. **Address Model Immutability & Hashability (`outlier_nfl/models.py`)**:
   In `NflGameLine` and `NflPlayerProp`, store `books` as `tuple[BookPrice, ...]` or convert to tuple in `__post_init__` to ensure records can be hashed for deduplication and cannot have their books list mutated in-place. Alternatively, provide explicit `__hash__` and `__eq__` implementations.

5. **Disallow Booleans and NaN in Schema Validation (`outlier_nfl/schema.py`)**:
   - Check `not isinstance(line, bool)` and `math.isfinite(line)` when `line` is numeric.
   - Check `not isinstance(ip_pct, bool)` and `math.isfinite(ip_pct)` when `implied_probability` is checked.

---

## 5. Verification Method

To independently verify all findings and test suites:

1. **Run Full Adversarial Stress-Test Suite**:
   ```powershell
   pytest tests/test_nfl_stress_m1.py -v
   ```
   *Expected*: 96 tests run and pass, documenting all edge cases and validating robust paths.

2. **Run Combined Unit & Stress Test Suite**:
   ```powershell
   pytest tests/test_nfl_api.py tests/test_nfl_normalizer.py tests/test_nfl_stress_m1.py
   ```
   *Expected*: 111 passed, 4 skipped (normalizer pending M2).

3. **Run Lint and Typecheck**:
   ```powershell
   ruff check outlier_nfl tests/test_nfl_stress_m1.py
   python -m mypy outlier_nfl
   ```
   *Expected*: Zero lint or typing errors.

4. **Invalidation Conditions**:
   This critique is invalidated if:
   - Worker 1 proves `validate_game_line_record` handles non-dict book entries without crashing.
   - Worker 1 proves `safe_write_json` cannot collide on millisecond timestamps across concurrent tasks.
   - Worker 1 proves `NflGameLine` can be added to a `set()` without `TypeError: unhashable type: 'list'`.
