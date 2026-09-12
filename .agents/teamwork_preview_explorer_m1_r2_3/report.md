# Strategic Investigation & Fix Strategy Report: Schema Validation & Atomic I/O Resiliency

**Document**: `report.md`  
**Author**: Explorer 3 (`teamwork_preview_explorer_m1_r2_3`)  
**Parent**: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)  
**Target Modules**: `outlier_nfl/schema.py`, `outlier_nfl/utils.py`, and `outlier_nfl/models.py`  
**Date**: 2026-09-12T11:20:00Z  

---

## 1. Executive Summary

During Milestone 1 adversarial evaluation, Challenger 2 identified four critical defects across runtime schema validation and atomic file persistence in `outlier_nfl`:
1. **Unhandled `AttributeError` Crash in Schema Gates** (`outlier_nfl/schema.py`): Validation functions (`validate_game_line_record`, `validate_player_prop_record`) invoke `.to_dict()` on non-dict items in `books` without type checking. Non-dict items (such as `None`, raw strings, or integers) trigger unhandled `AttributeError` exceptions, crashing the pipeline rather than gracefully collecting errors.
2. **Atomic Write Temp Path Collision Race Condition** (`outlier_nfl/utils.py`): `safe_write_json` generates temporary filenames using millisecond timestamps (`int(time.time() * 1000)`). On Windows, rapid or concurrent writes share identical timestamps, leading to temporary file collision, race conditions during `src.replace(dst)`, and `FileNotFoundError: [WinError 2]`.
3. **Zero-Retry Transient Lock Sensitivity in `safe_read_json`** (`outlier_nfl/utils.py`): Readers hitting a file undergoing atomic replacement or cloud synchronizer locking (e.g. OneDrive, anti-virus) immediately raise `PermissionError: [WinError 32]` or `[WinError 5]`. `safe_read_json` catches `Exception` without retrying and returns `default` (`None`), causing valid files to appear missing.
4. **Permissive Numeric Validation for Booleans and NaN** (`outlier_nfl/schema.py`): Because `bool` is a subclass of `int` in Python, `isinstance(True, (int, float))` is `True`. Values like `line: True` or `implied_probability: True` pass validation unnoticed. Furthermore, `float('nan')` passes `isinstance(x, (int, float))` and produces non-standard JSON `NaN`.

This report provides the exact root causes, mathematical and concurrency rationale, drop-in code diffs, test suite remediation steps, and independent verification commands for Worker 1 to apply in Iteration 2.

---

## 2. Deep-Dive Problem Analysis

### 2.1 Issue 1: `AttributeError` Crash on Non-Dict Book Items (`outlier_nfl/schema.py`)

#### Code Inspection
In `outlier_nfl/schema.py`, lines 150–154 and 197–201:
```python
for b_idx, book_entry in enumerate(books):
    b_dict = book_entry if isinstance(book_entry, dict) else book_entry.to_dict()
    if "book" not in b_dict or "odds" not in b_dict:
        errors.append(f"Book entry at index {b_idx} missing 'book' or 'odds'")
```

#### Failure Mechanism
- If an upstream API scraper, malformed feed, or corrupt record provides a list containing strings (e.g. `books: ["DraftKings -110"]`), nulls (`books: [None]`), or scalar values (`books: [123]`), `isinstance(book_entry, dict)` evaluates to `False`.
- Python then attempts `book_entry.to_dict()`.
- Built-in types (`str`, `NoneType`, `int`, etc.) lack a `.to_dict` attribute, raising:
  `AttributeError: 'str' object has no attribute 'to_dict'`
  `AttributeError: 'NoneType' object has no attribute 'to_dict'`
- A validation gate exists specifically to prevent unhandled crashes and sanitize untrusted input. Raising an uncaught exception breaks pipeline fault tolerance.
- Furthermore, the existing check `"book" not in b_dict or "odds" not in b_dict` only tests dictionary key presence. A dictionary like `{"book": None, "odds": None}` or `{"book": "", "odds": "bad"}` will pass without error.

---

### 2.2 Issue 2: Temp Path Collision Race Condition in `safe_write_json` (`outlier_nfl/utils.py`)

#### Code Inspection
In `outlier_nfl/utils.py`, line 78:
```python
temp_path = target.parent / f".{target.name}.{int(time.time() * 1000)}.tmp"
```

#### Failure Mechanism
- On Windows, the system wall clock (`time.time()`) resolution is often coarse (typically 10 to 15.6 milliseconds). Even with sub-millisecond clocks, rapid sequential writes or multi-threaded calls (such as concurrent thread pools downloading and saving event markets or player prop batches) frequently produce the exact same millisecond integer.
- Empirical evidence from Challenger 2: `len(set([int(time.time()*1000) for _ in range(1000)])) == 1`.
- When Thread A and Thread B write to the same target file:
  1. Both calculate identical `temp_path = target.parent / ".target.json.1789211110523.tmp"`.
  2. Thread A writes into `temp_path` and executes `_replace_with_retry(temp_path, target)`.
  3. The file at `temp_path` is atomically moved/renamed to `target`.
  4. Thread B now attempts `temp_path.replace(target)`, but `temp_path` no longer exists on disk.
  5. Python raises `FileNotFoundError: [WinError 2] The system cannot find the file specified`.
  6. Under slightly different timing, Thread B overwrites Thread A's temporary file during Thread A's write, resulting in truncated or corrupted JSON.

---

### 2.3 Issue 3: Zero-Retry Sensitivity to Transient File Contention in `safe_read_json` (`outlier_nfl/utils.py`)

#### Code Inspection
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

#### Failure Mechanism
- While `_replace_with_retry` was thoughtfully designed to retry file writes against transient Windows locks (`WIN_LOCK_ERRORS: frozenset({32, 33})`), `safe_read_json` was implemented without any retry mechanism.
- When an atomic file replacement (`src.replace(dst)`) is occurring on Windows, or when an external sync daemon (such as OneDrive, Google Drive, Windows Search Indexer, or Defender) opens a brief shared handle, any simultaneous `open(target, "r")` call raises:
  - `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process`
  - `PermissionError: [WinError 5] Access is denied`
  - `OSError: [Errno 13] Permission denied`
- Because `safe_read_json` catches `Exception` and returns `default` on the very first failure, concurrent readers falsely perceive existing valid data as missing.
- In pipelines that check existing cache files before re-downloading slates, this defect triggers unnecessary duplicate API queries or premature pipeline abortion.

---

### 2.4 Issue 4: Permissive Numeric Validation for Booleans and NaN (`outlier_nfl/schema.py`)

#### Code Inspection
In `outlier_nfl/schema.py`, lines 137–144 and lines 184–191:
```python
line = data.get("line")
if line is not None and not isinstance(line, (int, float)):
    errors.append(f"Line '{line}' must be numeric or None")

ip_pct = data.get("implied_probability")
if ip_pct is not None:
    if not isinstance(ip_pct, (int, float)) or not (0.0 <= ip_pct <= 100.0):
        errors.append(f"implied_probability '{ip_pct}' must be a percentage between 0 and 100")
```

#### Failure Mechanism
- In Python, `bool` is a built-in subclass of `int` (`issubclass(bool, int) is True`). Therefore:
  - `isinstance(True, (int, float))` evaluates to `True`.
  - `isinstance(False, (int, float))` evaluates to `True`.
  - `0.0 <= True <= 100.0` evaluates to `True` (since `True == 1`).
- As a result, corrupt records containing `line: True` or `implied_probability: True` bypass validation completely (`errors == []`).
- Furthermore, `float('nan')` is an instance of `float`. `isinstance(float('nan'), (int, float))` evaluates to `True`.
- When `line: float('nan')` is passed, `validate_game_line_record` accepts it. During serialization to disk, `json.dump()` emits `NaN`, which is non-standard JSON and causes parse crashes across strict JSON engines.

---

### 2.5 Issue 5: Domain Models Immutability and Hashability (`outlier_nfl/models.py`)

#### Code Inspection
In `outlier_nfl/models.py`, lines 43 and 71:
```python
@dataclass(frozen=True)
class NflGameLine:
    ...
    books: list[BookPrice]
```

#### Failure Mechanism
- While `@dataclass(frozen=True)` prevents rebinding fields (e.g. `gl.line = -4.0` raises `FrozenInstanceError`), it does **not** protect mutable field contents.
- `gl.books.append(BookPrice(...))` succeeds at runtime, violating the immutability invariant.
- When `hash(gl)` is executed, standard Python dataclass hashing iterates through all fields. Because `books` is a `list`, Python raises `TypeError: unhashable type: 'list'`.
- This prevents `NflGameLine` and `NflPlayerProp` from being stored in `set()` or used as dictionary keys, which is required in Milestone 2 for slate deduplication, consensus aggregation, and round-robin quota ranking.

---

## 3. Detailed Fix Strategy & Proposed Code Diffs

### 3.1 Fix Strategy for `outlier_nfl/schema.py`

#### Changes Required:
1. Import `math` at module top.
2. Implement a dedicated private helper `_validate_book_entry(book_entry: Any, b_idx: int) -> list[str]` that safely inspects `book_entry` whether it is a dict, an object with `.to_dict()`, or an invalid type.
3. Validate that `book` is a non-empty string and `odds` is a valid integer (excluding `bool`).
4. Validate that `line` and `implied_probability` are strictly not `bool`, are numeric `(int, float)`, and satisfy `math.isfinite()`.
5. Support both `list` and `tuple` for `books` to ensure seamless compatibility with immutable tuple models.

#### Proposed Drop-In Replacement for `outlier_nfl/schema.py`:

```python
--- a/outlier_nfl/schema.py
+++ b/outlier_nfl/schema.py
@@ -7,6 +7,7 @@
 from __future__ import annotations
 
+import math
 from typing import Any
 
 from outlier_nfl.models import NflGameLine, NflPlayerProp
@@ -105,6 +106,47 @@ def validate_player_props_payload(payload: Any) -> list[str]:
     return errors
 
 
+def _validate_book_entry(book_entry: Any, b_idx: int) -> list[str]:
+    """Safely validate an individual book entry inside a 'books' collection."""
+    errors: list[str] = []
+    if isinstance(book_entry, dict):
+        b_dict = book_entry
+    elif hasattr(book_entry, "to_dict") and callable(book_entry.to_dict):
+        try:
+            b_dict = book_entry.to_dict()
+        except Exception as exc:
+            return [f"Book entry at index {b_idx} to_dict() raised exception: {exc}"]
+    else:
+        return [f"Book entry at index {b_idx} is not a valid dict or BookPrice instance"]
+
+    if not isinstance(b_dict, dict):
+        return [f"Book entry at index {b_idx} did not produce a dictionary"]
+
+    book_name = b_dict.get("book")
+    if not book_name or not isinstance(book_name, str) or not book_name.strip():
+        errors.append(f"Book entry at index {b_idx} missing valid non-empty 'book' name")
+
+    odds = b_dict.get("odds")
+    if odds is None or isinstance(odds, bool) or not isinstance(odds, int):
+        errors.append(f"Book entry at index {b_idx} missing valid integer 'odds'")
+
+    decimal_val = b_dict.get("decimal")
+    if decimal_val is not None:
+        if (
+            isinstance(decimal_val, bool)
+            or not isinstance(decimal_val, (int, float))
+            or not math.isfinite(decimal_val)
+            or decimal_val <= 0
+        ):
+            errors.append(f"Book entry at index {b_idx} has invalid 'decimal' odds")
+
+    return errors
+
+
 def validate_game_line_record(record: dict[str, Any] | NflGameLine) -> list[str]:
     """Validate a single normalized NflGameLine record."""
     errors: list[str] = []
@@ -136,19 +178,25 @@ def validate_game_line_record(record: dict[str, Any] | NflGameLine) -> list[str
         errors.append(f"Invalid position '{position}' for game line (expected one of {valid_positions})")
 
     line = data.get("line")
-    if line is not None and not isinstance(line, (int, float)):
-        errors.append(f"Line '{line}' must be numeric or None")
+    if line is not None:
+        if isinstance(line, bool) or not isinstance(line, (int, float)) or not math.isfinite(line):
+            errors.append(f"Line '{line}' must be a finite number or None")
 
     ip_pct = data.get("implied_probability")
     if ip_pct is not None:
-        if not isinstance(ip_pct, (int, float)) or not (0.0 <= ip_pct <= 100.0):
+        if (
+            isinstance(ip_pct, bool)
+            or not isinstance(ip_pct, (int, float))
+            or not math.isfinite(ip_pct)
+            or not (0.0 <= ip_pct <= 100.0)
+        ):
             errors.append(f"implied_probability '{ip_pct}' must be a percentage between 0 and 100")
 
     books = data.get("books")
-    if not isinstance(books, list):
-        errors.append("Field 'books' must be a list")
+    if not isinstance(books, (list, tuple)):
+        errors.append("Field 'books' must be a list or tuple")
     else:
         for b_idx, book_entry in enumerate(books):
-            b_dict = book_entry if isinstance(book_entry, dict) else book_entry.to_dict()
-            if "book" not in b_dict or "odds" not in b_dict:
-                errors.append(f"Book entry at index {b_idx} missing 'book' or 'odds'")
+            errors.extend(_validate_book_entry(book_entry, b_idx))
 
     return errors
@@ -184,18 +232,24 @@ def validate_player_prop_record(record: dict[str, Any] | NflPlayerProp) -> list
         errors.append(f"Invalid position '{position}' for player prop (expected one of {valid_positions})")
 
     line = data.get("line")
-    if not isinstance(line, (int, float)):
-        errors.append(f"Player prop line '{line}' must be numeric")
+    if line is None or isinstance(line, bool) or not isinstance(line, (int, float)) or not math.isfinite(line):
+        errors.append(f"Player prop line '{line}' must be a finite number")
 
     ip_pct = data.get("implied_probability")
     if ip_pct is not None:
-        if not isinstance(ip_pct, (int, float)) or not (0.0 <= ip_pct <= 100.0):
+        if (
+            isinstance(ip_pct, bool)
+            or not isinstance(ip_pct, (int, float))
+            or not math.isfinite(ip_pct)
+            or not (0.0 <= ip_pct <= 100.0)
+        ):
             errors.append(f"implied_probability '{ip_pct}' must be a percentage between 0 and 100")
 
     books = data.get("books")
-    if not isinstance(books, list):
-        errors.append("Field 'books' must be a list")
+    if not isinstance(books, (list, tuple)):
+        errors.append("Field 'books' must be a list or tuple")
     else:
         for b_idx, book_entry in enumerate(books):
-            b_dict = book_entry if isinstance(book_entry, dict) else book_entry.to_dict()
-            if "book" not in b_dict or "odds" not in b_dict:
-                errors.append(f"Book entry at index {b_idx} missing 'book' or 'odds'")
+            errors.extend(_validate_book_entry(book_entry, b_idx))
 
     return errors
```

---

### 3.2 Fix Strategy for `outlier_nfl/utils.py`

#### Changes Required:
1. Import `os`, `threading`, and `uuid`.
2. In `safe_write_json`, formulate the temporary filename with high-entropy components:
   `f".{target.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{uuid.uuid4().hex}.tmp"`
   This completely removes any possibility of filename collision even across concurrent processes or threads.
3. In `safe_read_json`, implement an exponential backoff retry loop (default 5 retries, initial delay 0.05s) catching `PermissionError`, `[WinError 32]`, `[WinError 5]`, and `[Errno 13]`.

#### Proposed Drop-In Replacement for `outlier_nfl/utils.py`:

```python
--- a/outlier_nfl/utils.py
+++ b/outlier_nfl/utils.py
@@ -10,9 +10,12 @@
 from datetime import datetime, timezone
 import json
 import logging
+import os
 from pathlib import Path
+import threading
 import time
 from typing import Any
+import uuid
 import zoneinfo
 
 logger = logging.getLogger("outlier_nfl.utils")
@@ -77,7 +80,8 @@ def safe_write_json(
     target.parent.mkdir(parents=True, exist_ok=True)
 
-    # Use a hidden temporary sibling file in the same directory for atomic replace
-    temp_path = target.parent / f".{target.name}.{int(time.time() * 1000)}.tmp"
+    # Use a unique hidden temporary sibling file in the same directory for atomic replace
+    unique_suffix = f"{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{uuid.uuid4().hex}"
+    temp_path = target.parent / f".{target.name}.{unique_suffix}.tmp"
 
     try:
         with open(temp_path, "w", encoding="utf-8") as f:
@@ -93,12 +97,35 @@ def safe_write_json(
-def safe_read_json(path: Path | str, default: Any = None) -> Any:
-    """Read a JSON file safely with strict=False to tolerate unescaped control chars."""
+def safe_read_json(
+    path: Path | str,
+    default: Any = None,
+    retries: int = 5,
+    delay: float = 0.05,
+) -> Any:
+    """Read a JSON file safely with strict=False and retry on transient Windows file contention."""
     target = Path(path)
     if not target.exists():
         return default
-    try:
-        with open(target, "r", encoding="utf-8") as f:
-            return json.load(f, strict=False)
-    except Exception as exc:
-        logger.error("Failed to read JSON from %s: %s", target, exc)
-        return default
+
+    for attempt in range(1, retries + 1):
+        try:
+            with open(target, "r", encoding="utf-8") as f:
+                return json.load(f, strict=False)
+        except OSError as exc:
+            winerror = getattr(exc, "winerror", None)
+            errno_code = getattr(exc, "errno", None)
+            is_transient = (
+                winerror in WIN_LOCK_ERRORS
+                or winerror == 5
+                or isinstance(exc, PermissionError)
+                or errno_code == 13
+            )
+            if is_transient and attempt < retries:
+                sleep_time = delay * (1.5 ** (attempt - 1))
+                logger.warning(
+                    "Retrying read after transient lock on %s (attempt %d/%d, sleep %.2fs): %s",
+                    target,
+                    attempt,
+                    retries,
+                    sleep_time,
+                    exc,
+                )
+                time.sleep(sleep_time)
+                continue
+            logger.error("Failed to read JSON from %s after %d attempt(s): %s", target, attempt, exc)
+            return default
+        except Exception as exc:
+            logger.error("Failed to parse JSON from %s: %s", target, exc)
+            return default
+
+    return default
```

---

### 3.3 Fix Strategy for Domain Models (`outlier_nfl/models.py`)

#### Changes Required:
1. In `NflGameLine` and `NflPlayerProp`, define `books: tuple[BookPrice, ...] | list[BookPrice] = ()`.
2. In `__post_init__`, coerce any incoming `list` into an immutable `tuple`:
   ```python
   def __post_init__(self) -> None:
       if isinstance(self.books, list):
           object.__setattr__(self, "books", tuple(self.books))
   ```
3. Because `BookPrice` is a frozen dataclass with immutable fields, `tuple[BookPrice, ...]` is fully hashable and immutable.
4. `hash(gl)` and `hash(pp)` will work out-of-the-box without raising `TypeError: unhashable type: 'list'`.
5. In-place mutations (`gl.books.append(...)`) will be prevented with `AttributeError: 'tuple' object has no attribute 'append'`.
6. `to_dict()` converts `books` back to a clean list of dictionaries for JSON serialization:
   ```python
   result["books"] = [b.to_dict() if isinstance(b, BookPrice) else b for b in self.books]
   ```

---

## 4. Test Suite Alignment & Invalidation Defense

### 4.1 Necessary Updates to `tests/test_nfl_stress_m1.py`
Challenger 2 authored tests in `tests/test_nfl_stress_m1.py` specifically asserting the presence of the bugs (e.g. `pytest.raises(AttributeError)` and `assert errs == []` for booleans).
When Worker 1 fixes `schema.py` and `utils.py`, the following four test methods in `tests/test_nfl_stress_m1.py` must be updated to assert the **healed** behavior:

1. **`test_adversarial_non_dict_book_entry_behavior` (GameLine & PlayerProp)**:
   - *Old Assertion*: `with pytest.raises(AttributeError): validate_game_line_record(record)`
   - *Updated Assertion*:
     ```python
     errs = validate_game_line_record(record)
     assert len(errs) > 0
     assert any("not a valid dict or BookPrice" in e for e in errs)
     ```
2. **`test_adversarial_schema_accepts_boolean_values`**:
   - *Old Assertion*: `assert errs == []` (demonstrating defect)
   - *Updated Assertion*:
     ```python
     errs = validate_game_line_record(rec)
     assert any("Line 'True' must be a finite number or None" in e for e in errs)
     assert any("implied_probability 'True' must be a percentage" in e for e in errs)
     ```
3. **`test_adversarial_schema_accepts_nan_line`**:
   - *Old Assertion*: `assert errs == []` (demonstrating defect)
   - *Updated Assertion*:
     ```python
     errs = validate_game_line_record(rec)
     assert any("must be a finite number" in e for e in errs)
     ```
4. **`test_adversarial_dataclass_hashability_with_list`**:
   - *Old Assertion*: `with pytest.raises(TypeError): hash(gl)`
   - *Updated Assertion*:
     ```python
     # Asserts that NflGameLine is cleanly hashable
     h = hash(gl)
     assert isinstance(h, int)
     assert gl in {gl}
     ```

### 4.2 Additional Regression Test Cases for Worker 1
Add the following tests to verify all edge cases:
- `test_safe_write_json_concurrent_same_file`: Run 30 threads simultaneously writing to the exact same file path (`target.json`). Assert that no `FileNotFoundError` or corruption occurs, and final target contains valid JSON.
- `test_safe_read_json_retry_under_lock`: Hold an exclusive file lock for 0.15s while invoking `safe_read_json` on a separate thread. Assert that `safe_read_json` recovers and returns the expected dictionary.
- `test_schema_rejects_inf_and_nan`: Pass `float('inf')` and `float('-inf')` to `line` and `implied_probability`. Assert that all non-finite floats are rejected.

---

## 5. Verification Commands for Orchestrator & Worker

To independently verify the implementation after Worker 1 applies the fixes:

1. **Step 0 Mandatory Sync Status**:
   ```powershell
   & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
   ```
   *Expected*: `REPORT STATUS: OK`

2. **Run Full NFL Test Suite**:
   ```powershell
   pytest tests/test_nfl_api.py tests/test_nfl_normalizer.py tests/test_nfl_stress.py tests/test_nfl_stress_m1.py -v
   ```
   *Expected*: All 214+ tests pass cleanly (with only unbuilt M2 normalizer tests skipped).

3. **Verify Zero Linting & Typing Errors**:
   ```powershell
   ruff check outlier_nfl tests/test_nfl_*.py
   python -m mypy outlier_nfl
   ```
   *Expected*: `All checks passed!` with 0 errors.

---

## 6. Conclusion

The adversarial findings by Challenger 2 are fully validated:
- The `AttributeError` in `schema.py` is a genuine unhandled crash caused by unsafe invocation of `.to_dict()`.
- The temp path collision in `utils.py` is a real Windows race condition caused by coarse millisecond clock resolution.
- The lack of retries in `safe_read_json` introduces fragile read behavior under Windows sharing violations.
- Numeric validation in `schema.py` requires explicit rejection of `bool` and `NaN`/`Inf`.

The fix strategy provided in this report resolves all issues cleanly, adheres strictly to stdlib without introducing new dependencies, respects repository memory efficiency rules, and positions `outlier_nfl` for robust Milestone 2 development.
