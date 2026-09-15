# Technical Analysis & Fix Strategy Report
## Resilient Gzip Decompression and HTML/Non-JSON Handling in `outlier_nfl/api.py`

**Agent:** Explorer 1 (`teamwork_preview_explorer_m1_r2_1`)  
**Parent:** `teamwork_preview_orchestrator_2` (`d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)  
**Milestone:** Milestone 1 Iteration 2  
**Target Module:** `outlier_nfl/api.py`  
**Date:** 2026-09-12  

---

## 1. Executive Summary

During Milestone 1 adversarial stress testing, Challenger 1 identified two related transport-layer vulnerabilities in `outlier_nfl/api.py:fetch_json`:

1. **Unhandled Gzip Decompression Errors (`EOFError`, `zlib.error`)**:
   When an upstream connection drops mid-transfer, the received byte stream begins with the gzip magic header (`b"\x1f\x8b"`) but lacks trailing compressed blocks or CRC32 checksums. Invoking `gzip.decompress()` raises `EOFError` or `zlib.error`. Because neither exception inherits from `OSError`, both escape the retry loop unhandled and crash the entire scraper.
2. **Unhandled `JSONDecodeError` on Non-JSON / HTML 200 OK Responses**:
   When edge proxies, CDNs (e.g. Cloudflare, CloudFront, Nginx), or captive portals return HTML error pages (such as gateway timeouts or maintenance screens) under HTTP 200 OK, `json.loads()` raises `json.decoder.JSONDecodeError`. Because `JSONDecodeError` inherits from `ValueError` (not `OSError`), it bypasses the retry loop and crashes `fetch_json`.

This report provides the full forensic analysis, exception class inheritance proofs, and an exact drop-in implementation strategy for Worker 1, accompanied by concrete test specifications.

---

## 2. Deep-Dive Forensic Analysis

### 2.1 Finding 1: Gzip Decompression Failure Analysis

#### Current Implementation (`outlier_nfl/api.py:345-354`)
```python
with self.opener(request, timeout=policy.timeout_seconds) as response:
    body = response.read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    text = body.decode("utf-8", errors="replace")
    payload = json.loads(text, strict=False)
```

#### Exception Handlers (`outlier_nfl/api.py:355-390`)
```python
except HTTPError as exc:
    ...
except (URLError, OSError) as exc:
    ...
```

#### The Defect
Python's standard library implements decompression via `gzip._GzipReader` and the low-level `zlib` C-extension:
- If bytes are truncated before the footer:
  `gzip.py:631` raises `EOFError("Compressed file ended before the end-of-stream marker was reached")`.
- If bytes within the compressed payload are corrupted or have invalid CRC32:
  `zlib.decompress()` raises `zlib.error`.
- If gzip header flags or magic bytes are invalid:
  `gzip.BadGzipFile` is raised.

#### Python Class Hierarchy Verification
```
EOFError:            (<class 'EOFError'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
zlib.error:          (<class 'zlib.error'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
gzip.BadGzipFile:    (<class 'gzip.BadGzipFile'>, <class 'OSError'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
```
- While `gzip.BadGzipFile` inherits from `OSError`, **`EOFError` and `zlib.error` inherit directly from `Exception`**.
- They are **NOT** caught by `except (URLError, OSError)`.
- When an API response drops mid-stream, `body[:2] == b"\x1f\x8b"` evaluates to `True`, `gzip.decompress()` raises `EOFError`, and `fetch_json` aborts without retrying.

#### Classification
Truncated compressed payloads over HTTP represent transient network stream interruptions. They must be caught, recorded as `last_error`, logged at warning level, and retried according to `RetryPolicy`.

---

### 2.2 Finding 2: HTML / Non-JSON 200 OK Response Analysis

#### The Defect
In modern distributed web architectures:
1. Reverse proxies (Cloudflare, AWS CloudFront, Fastly, Nginx) or corporate proxies often intercept upstream 502/504 errors and return custom HTML error pages (e.g. `<html><head><title>504 Gateway Time-out</title>...`).
2. In certain proxy misconfigurations, captive portals, or maintenance cutovers, these HTML bodies are returned with HTTP status `200 OK`.
3. When `json.loads(text, strict=False)` receives HTML text starting with `<html>` or `<!DOCTYPE html>`, it immediately raises:
   `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`

#### Python Class Hierarchy Verification
```
JSONDecodeError: (<class 'json.decoder.JSONDecodeError'>, <class 'ValueError'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
```
- `JSONDecodeError` inherits from `ValueError`, **NOT `OSError` or `URLError`**.
- It completely escapes the existing `except (URLError, OSError)` block.
- The scraper crashes with an unhandled exception rather than cleanly retrying or raising `OutlierNflApiError`.

#### Classification & Diagnostic Context
- If caused by a transient reverse-proxy glitch, retrying after exponential backoff allows the origin server to respond with valid JSON on the next attempt.
- If persistent, catching the error allows `fetch_json` to extract a sanitized preview snippet (e.g., `text[:150]`) and raise `OutlierNflApiError(f"Invalid JSON response from {url}: {exc} (body preview: {preview!r})")`.
- This provides instant diagnostic clarity in logs instead of an opaque `JSONDecodeError`.

---

### 2.3 Additional Stream Vector: `http.client.IncompleteRead`

During `body = response.read()`, if the socket connection is closed before all bytes specified in `Content-Length` are received, Python's `http.client` raises `http.client.IncompleteRead`.
```
IncompleteRead: (<class 'http.client.IncompleteRead'>, <class 'http.client.HTTPException'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
```
Like `EOFError`, `http.client.HTTPException` does **not** inherit from `OSError`. Adding `http.client.HTTPException` to the stream error tuple ensures complete network resilience.

---

## 3. Exact Fix Strategy for `outlier_nfl/api.py`

### 3.1 Required Imports Update (`outlier_nfl/api.py:10-26`)

Add `http.client` and `zlib` to the standard library imports:

```python
from dataclasses import dataclass
import gzip
import http.client
import json
import logging
import os
from pathlib import Path
import random
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import zlib
```

### 3.2 Definition of Error Classification Tuples

Define module-level constants for robust exception grouping:

```python
STREAM_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    URLError,
    OSError,
    http.client.HTTPException,
    zlib.error,
    EOFError,
)
```

*(Note: `gzip.BadGzipFile` is a subclass of `OSError`, so it is already covered by `OSError`, but `zlib.error` and `EOFError` are now explicitly included).*

### 3.3 Target Implementation for `fetch_json` (`outlier_nfl/api.py:337-394`)

Here is the exact replacement code for `fetch_json`:

```python
    def fetch_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform HTTP GET request with retries, gzip decompression, and strict=False JSON."""
        url = self.url_for(path, params=params)
        policy = self.retry_policy
        last_error: Exception | None = None

        for attempt in range(1, policy.max_retries + 1):
            request = Request(url, headers=self.build_headers())
            try:
                with self.opener(request, timeout=policy.timeout_seconds) as response:
                    body = response.read()
                    if body[:2] == b"\x1f\x8b":
                        body = gzip.decompress(body)
                    text = body.decode("utf-8", errors="replace")
                    try:
                        payload = json.loads(text, strict=False)
                    except (json.JSONDecodeError, ValueError) as json_exc:
                        snippet = " ".join(text[:160].split())
                        if attempt >= policy.max_retries:
                            raise OutlierNflApiError(
                                f"Invalid JSON response from {url} after {policy.max_retries} attempts: "
                                f"{json_exc} (preview: {snippet!r})"
                            ) from json_exc
                        last_error = json_exc
                        sleep_time = policy.delay_for(attempt)
                        logger.warning(
                            "Invalid JSON/HTML response on %s (attempt %d/%d), retrying in %.2fs: %s (preview: %r)",
                            url,
                            attempt,
                            policy.max_retries,
                            sleep_time,
                            json_exc,
                            snippet,
                        )
                        time.sleep(sleep_time)
                        continue

                    if isinstance(payload, dict):
                        return payload
                    raise OutlierNflApiError(f"Unexpected non-object response from {url}: {type(payload)}")
            except HTTPError as exc:
                last_error = exc
                if exc.code == 401:
                    raise AuthRequiredError(f"HTTP 401 Unauthorized for {url}. Valid login session required.") from exc
                if exc.code == 404:
                    raise NotFoundError(f"HTTP 404 Not Found for {url}") from exc
                if exc.code == 429 and attempt >= policy.max_retries:
                    raise RateLimitError(f"HTTP 429 Rate limited after {policy.max_retries} attempts: {url}") from exc
                if exc.code not in RETRYABLE_STATUS_CODES or attempt >= policy.max_retries:
                    raise OutlierNflApiError(f"HTTP {exc.code} for {url}") from exc

                sleep_time = policy.delay_for(attempt)
                logger.warning(
                    "HTTP %d on %s (attempt %d/%d), retrying in %.2fs",
                    exc.code,
                    url,
                    attempt,
                    policy.max_retries,
                    sleep_time,
                )
                time.sleep(sleep_time)
            except STREAM_TRANSPORT_ERRORS as exc:
                last_error = exc
                if attempt >= policy.max_retries:
                    raise OutlierNflApiError(
                        f"Transport/stream error for {url} after {policy.max_retries} attempts: {exc}"
                    ) from exc
                sleep_time = policy.delay_for(attempt)
                logger.warning(
                    "Transport/stream error on %s (attempt %d/%d), retrying in %.2fs: %s",
                    url,
                    attempt,
                    policy.max_retries,
                    sleep_time,
                    exc,
                )
                time.sleep(sleep_time)

        if last_error:
            raise OutlierNflApiError(f"Failed to fetch {url}: {last_error}") from last_error
        raise OutlierNflApiError(f"Failed to fetch {url}")
```

---

## 4. Key Design Decisions & Invariants

1. **Clean Separation of Concerns**:
   - `STREAM_TRANSPORT_ERRORS`: Covers socket disconnects, DNS errors, timeouts, `IncompleteRead`, and compressed stream truncation (`zlib.error`, `EOFError`).
   - `json.JSONDecodeError` / `ValueError`: Handled at the payload parsing stage. Truncated HTML responses are previewed and logged without blowing up memory or flooding console output.
   - Non-dict payload check: If valid JSON is returned but is a list (e.g. `[{"id": 1}]`), it immediately raises `OutlierNflApiError` (preserving existing test contract in `test_api_rejects_non_dict_json_root`).
2. **Deterministic Retry Loop Flow**:
   - `continue` after `time.sleep(sleep_time)` ensures proper retry cycle without fallthrough.
   - `last_error` is tracked across all branches so final retry exhaustion raises an informative `OutlierNflApiError` linked via `from last_error`.
3. **No External Dependencies**:
   - Relies strictly on Python stdlib (`zlib`, `gzip`, `http.client`, `json`, `urllib`).
   - Zero runtime coupling with `outlier_scrapers`.

---

## 5. Verification & Test Plan

Worker 1 should add / update the following tests in `tests/test_nfl_api.py` and `tests/test_nfl_stress.py`:

### Test 1: Transient Corrupted Gzip Stream Recovers
```python
def test_api_recovers_from_transient_corrupted_gzip():
    """Client retries on truncated gzip stream and succeeds on subsequent attempt."""
    corrupted_resp = MagicMock()
    corrupted_resp.read.return_value = b"\x1f\x8b\x08\x00corrupted_truncated_bytes"

    valid_payload = {"events": [{"id": "kc-bal"}]}
    valid_resp = MagicMock()
    valid_resp.read.return_value = gzip.compress(json.dumps(valid_payload).encode("utf-8"))

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [corrupted_resp, valid_resp]

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    result = client.fetch_json("/test")
    assert result == valid_payload
    assert mock_opener.call_count == 2
```

### Test 2: Persistent Corrupted Gzip Stream Raises `OutlierNflApiError`
```python
def test_api_raises_on_persistent_corrupted_gzip():
    """Client raises OutlierNflApiError after retrying persistent gzip corruption."""
    corrupted_resp = MagicMock()
    corrupted_resp.read.return_value = b"\x1f\x8b\x08\x00corrupted_truncated_bytes"

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = corrupted_resp

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/test")
    assert mock_opener.call_count == 3
    assert "after 3 attempts" in str(exc_info.value) or "Transport/stream error" in str(exc_info.value)
```

### Test 3: Transient HTML 200 Response Recovers
```python
def test_api_recovers_from_transient_html_200():
    """Client retries on HTML 200 response and succeeds when valid JSON is returned."""
    html_resp = MagicMock()
    html_resp.read.return_value = b"<html><head><title>504 Gateway Time-out</title></head></html>"

    valid_resp = MagicMock()
    valid_resp.read.return_value = b'{"status": "recovered"}'

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [html_resp, valid_resp]

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    result = client.fetch_json("/test")
    assert result == {"status": "recovered"}
    assert mock_opener.call_count == 2
```

### Test 4: Persistent HTML 200 Response Raises `OutlierNflApiError` with Preview
```python
def test_api_raises_on_persistent_html_200():
    """Client raises OutlierNflApiError with body snippet when endpoint continuously returns HTML."""
    html_resp = MagicMock()
    html_resp.read.return_value = b"<!DOCTYPE html><html><body>Error page</body></html>"

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = html_resp

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/test")
    assert mock_opener.call_count == 3
    assert "Invalid JSON response" in str(exc_info.value)
    assert "<!DOCTYPE html>" in str(exc_info.value)
```

---

## 6. Summary for Implementer (Worker 1)

1. Update imports in `outlier_nfl/api.py` to include `http.client` and `zlib`.
2. Define `STREAM_TRANSPORT_ERRORS = (URLError, OSError, http.client.HTTPException, zlib.error, EOFError)`.
3. Wrap `json.loads(text, strict=False)` in `fetch_json` with a dedicated `try/except (json.JSONDecodeError, ValueError)` block that retries with backoff and logs a sanitized preview snippet.
4. Replace `except (URLError, OSError) as exc:` with `except STREAM_TRANSPORT_ERRORS as exc:`.
5. Add the 4 test cases to `tests/test_nfl_api.py` to permanently lock in this resilience behavior.
