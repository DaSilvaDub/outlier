# Handoff Report — Milestone 1 Adversarial Review

**Agent:** Challenger 1 (`teamwork_preview_challenger_m1_1`)  
**Parent:** `teamwork_preview_orchestrator_2` (`d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)  
**Date:** 2026-09-12T11:08:30Z  
**Handoff Type:** Hard (Review Complete)  
**Verdict:** `REQUEST_CHANGES`

---

## 1. Observation

### 1.1 STEP 0 Sync Verification
Executed canonical `report-sync.ps1`:
```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
```
Output:
```
=== VERDICT ===
REPORT STATUS: OK
  bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)

RUN-NONCE: bee682a99079475f  utc=2026-09-12T10:58:21Z  head=a3ad900  status=OK
```

### 1.2 Empirical Stress-Test Execution
Created adversarial test harness `tests/test_nfl_stress.py` (99 tests) verifying API retries, 429 backoff, 50x errors, gzip decompression, control character parsing, pagination loop termination, 32-team normalization, domain model immutability, schema validation, and date conversions.

Running full NFL test suite:
```powershell
pytest tests\test_nfl_api.py tests\test_nfl_normalizer.py tests\test_nfl_stress.py -v
```
Output:
```
tests\test_nfl_api.py ...........                                        [  9%]
tests\test_nfl_normalizer.py ....ssss                                    [ 16%]
tests\test_nfl_stress.py ............................................... [ 55%]
....................................................                     [100%]

======================= 114 passed, 4 skipped in 3.00s ========================
```

Linting and Type Checking:
```powershell
ruff check outlier_nfl tests/test_nfl_stress.py
python -m mypy outlier_nfl
```
Output:
```
All checks passed!
Success: no issues found in 6 source files
```

### 1.3 Vulnerability Findings (Verbatim Observations)

#### Finding 1: Missing Standard Code + Nickname Team Aliases (`outlier_nfl/config.py`)
In `outlier_nfl/config.py`, `NFL_TEAM_ALIASES` maps full city + nickname (`KANSASCITYCHIEFS`), city (`KANSASCITY`), nickname alone (`CHIEFS`), and canonical code (`KC`), but omits standard prefix abbreviations combined with nicknames:
```python
python -c "from outlier_nfl.config import normalize_team; print('KC Chiefs:', normalize_team('KC Chiefs')); print('SF 49ers:', normalize_team('SF 49ers')); print('TB Bucs:', normalize_team('TB Bucs')); print('NE Patriots:', normalize_team('NE Patriots')); print('PIT Steelers:', normalize_team('PIT Steelers'))"
```
Verbatim execution result:
```
KC Chiefs: None
SF 49ers: None
TB Bucs: None
NE Patriots: None
PIT Steelers: None
```
Similarly:
- `normalize_team("NY Giants")` -> `None` (Expected: `"NYG"`)
- `normalize_team("NY Jets")` -> `None` (Expected: `"NYJ"`)
- `normalize_team("BAL Ravens")` -> `None` (Expected: `"BAL"`)
- `normalize_team("GB Packers")` -> `None` (Expected: `"GB"`)
- `normalize_team("PHI Eagles")` -> `None` (Expected: `"PHI"`)

#### Finding 2: Unhandled Decompression Errors on Truncated/Corrupted Gzip Streams (`outlier_nfl/api.py:349`)
In `outlier_nfl/api.py`, lines 346–380:
```python
            try:
                with self.opener(request, timeout=policy.timeout_seconds) as response:
                    body = response.read()
                    if body[:2] == b"\x1f\x8b":
                        body = gzip.decompress(body)
...
            except HTTPError as exc:
...
            except (URLError, OSError) as exc:
```
When `gzip.decompress` encounters a truncated or corrupted stream:
```python
python -c "import gzip, urllib.request, unittest.mock; from outlier_nfl.api import OutlierNflApiClient; mock_resp = unittest.mock.MagicMock(); mock_resp.read.return_value = b'\x1f\x8b\x08\x00corrupted'; mock_opener = unittest.mock.MagicMock(); mock_opener.return_value.__enter__.return_value = mock_resp; client = OutlierNflApiClient(bearer_token='test', opener=mock_opener); client.fetch_json('/test')"
```
Verbatim execution result:
```
Traceback (most recent call last):
  File "C:\Users\dasil\Dev\GitHub\outlier\outlier_nfl\api.py", line 349, in fetch_json
    body = gzip.decompress(body)
  File "C:\Users\dasil\AppData\Local\Programs\Python\Python313\Lib\gzip.py", line 631, in decompress
    raise EOFError("Compressed file ended before the end-of-stream marker was reached")
EOFError: Compressed file ended before the end-of-stream marker was reached
```
`EOFError` and `zlib.error` inherit directly from `Exception` (not `OSError`). Because `fetch_json` only catches `(URLError, OSError)`, corrupted/truncated compressed streams cause an uncaught crash rather than a retry or conversion to `OutlierNflApiError`.

#### Finding 3: Unhandled `JSONDecodeError` on Non-JSON 200 OK Responses (`outlier_nfl/api.py:351`)
In `outlier_nfl/api.py`, line 351:
```python
payload = json.loads(text, strict=False)
```
When an edge proxy or CDN returns an HTML error page with HTTP 200 (e.g. `<html>Gateway Timeout</html>`), `json.loads` raises `json.decoder.JSONDecodeError`:
```python
python -c "import urllib.request, unittest.mock; from outlier_nfl.api import OutlierNflApiClient; mock_resp = unittest.mock.MagicMock(); mock_resp.read.return_value = b'<html>Gateway Timeout</html>'; mock_opener = unittest.mock.MagicMock(); mock_opener.return_value.__enter__.return_value = mock_resp; client = OutlierNflApiClient(bearer_token='test', opener=mock_opener); client.fetch_json('/test')"
```
Verbatim execution result:
```
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```
`JSONDecodeError` is a `ValueError` (not `OSError`), which escapes the retry loop uncaught and crashes `fetch_json`.

---

## 2. Logic Chain

1. **Step 1 — Baseline Evaluation**: The worker delivered clean standalone package scaffolding, zero coupling with `outlier_scrapers`, strong immutable domain models (`models.py`), schema validation functions (`schema.py`), resilient file streaming (`utils.py`), and a passing initial test suite (`tests/test_nfl_api.py`).
2. **Step 2 — Adversarial Challenge of Team Normalization**: In betting data feeds, odds comparisons, and injury reports, NFL team names commonly appear in `Abbrev + Nickname` format (e.g., "KC Chiefs", "SF 49ers", "TB Bucs", "NY Giants", "NY Jets"). Calling `normalize_team` on these strings strips punctuation and whitespace via `_compact_key`, producing keys like `KCCHIEFS`, `SF49ERS`, `TBBUCS`, and `NYGIANTS`. Because `NFL_TEAM_ALIASES` does not contain these composite keys, `normalize_team` fails and returns `None`. This breaks downstream team matching in `games.py` and `props.py`.
3. **Step 3 — Adversarial Challenge of Gzip Decompression**: Outlier API endpoints send large payloads with `Content-Encoding: gzip`. If a network connection is terminated mid-transfer, the received byte stream begins with `\x1f\x8b` but terminates before the gzip trailer. Calling `gzip.decompress` on such bytes raises `EOFError` or `zlib.error`. Neither inherits from `OSError`. Consequently, `fetch_json` fails to retry the request and crashes with an unhandled exception.
4. **Step 4 — Adversarial Challenge of Reverse Proxy & HTML Payloads**: When reverse proxies or CDNs encounter upstream timeouts or maintenance windows, they occasionally return HTML documents under HTTP 200 or 50x without standard JSON headers. Calling `json.loads` raises `JSONDecodeError`, which escapes unhandled because `fetch_json` does not catch `(json.JSONDecodeError, ValueError)`.
5. **Step 5 — Verdict Determination**: Because these three failure modes represent gaps in Milestone 1 core deliverables (`api.py` and `config.py`), a verdict of `REQUEST_CHANGES` is required before Milestone 2 begins.

---

## 3. Caveats

- Milestone 1 is review-only for this agent; no implementation code in `outlier_nfl/` was modified.
- All adversarial stress tests were authored in `tests/test_nfl_stress.py` in accordance with repository layout standards (zero test files in `.agents/`).
- Downstream Milestone 2 modules (`normalizer.py`, `games.py`, `props.py`, `pipeline.py`) were not yet implemented by Worker 1 and are not part of Milestone 1 review.

---

## 4. Conclusion

**Verdict: `REQUEST_CHANGES`**

Worker 1 must implement the following three targeted fixes:

1. **Add Code + Nickname & Abbreviation + Nickname Aliases in `outlier_nfl/config.py`**:
   Expand `NFL_TEAM_ALIASES` to include composite abbreviations:
   - `KCCHIEFS` -> `KC`, `SF49ERS` -> `SF`, `TBBUCS` -> `TB`, `TAMPABAYBUCS` -> `TB`
   - `NEPATRIOTS` -> `NE`, `PITSTEELERS` -> `PIT`, `BALRAVENS` -> `BAL`
   - `NYGIANTS` -> `NYG`, `NYJETS` -> `NYJ`, `GBPACKERS` -> `GB`, `PHIEAGLES` -> `PHI`
   - And systematically for all 32 franchises (`<CODE><NICKNAME>`).

2. **Catch Decompression Exceptions in `outlier_nfl/api.py:fetch_json`**:
   In `fetch_json`, catch `(zlib.error, EOFError)` during decompression, treat them as retryable network/stream errors within the retry loop, and raise `OutlierNflApiError` upon retry exhaustion.

3. **Catch `json.JSONDecodeError` in `outlier_nfl/api.py:fetch_json`**:
   In `fetch_json`, catch `json.JSONDecodeError` inside the retry loop, log the malformed response body, and raise `OutlierNflApiError` (or retry if attempts remain).

---

## 5. Verification Method

To independently verify the observations and reproduced bugs:

1. **Run Full Test Suite (including the 99 stress tests)**:
   ```powershell
   pytest tests/test_nfl_stress.py tests/test_nfl_api.py -v
   ```
   Observe: 99 stress tests pass; `test_normalize_team_code_plus_nickname_adversarial` asserts that all 10 target composite aliases currently return `None`.

2. **Reproduce Unhandled Gzip Decompression Crash**:
   ```powershell
   python -c "import unittest.mock; from outlier_nfl.api import OutlierNflApiClient; mock_resp = unittest.mock.MagicMock(); mock_resp.read.return_value = b'\x1f\x8b\x08\x00corrupted'; mock_opener = unittest.mock.MagicMock(); mock_opener.return_value.__enter__.return_value = mock_resp; client = OutlierNflApiClient(bearer_token='test', opener=mock_opener); client.fetch_json('/test')"
   ```
   Observe: Uncaught `EOFError` terminates execution.

3. **Reproduce Unhandled JSON Decode Crash**:
   ```powershell
   python -c "import unittest.mock; from outlier_nfl.api import OutlierNflApiClient; mock_resp = unittest.mock.MagicMock(); mock_resp.read.return_value = b'<html>Gateway Timeout</html>'; mock_opener = unittest.mock.MagicMock(); mock_opener.return_value.__enter__.return_value = mock_resp; client = OutlierNflApiClient(bearer_token='test', opener=mock_opener); client.fetch_json('/test')"
   ```
   Observe: Uncaught `json.decoder.JSONDecodeError` terminates execution.

4. **Reproduce Code + Nickname Normalization Failure**:
   ```powershell
   python -c "from outlier_nfl.config import normalize_team; print('KC Chiefs:', normalize_team('KC Chiefs')); print('NY Giants:', normalize_team('NY Giants'))"
   ```
   Observe: Both output `None` instead of `"KC"` and `"NYG"`.
