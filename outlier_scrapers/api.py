from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .auth import build_api_headers, load_storage_state
from .redaction import shape_summary

# Provenance: fetch/retry behavior adapted from
# C:\Users\dasil\Dev\GitHub\nba-props-pipeline\scrapers\outlier\Props_outlier_improved.py
# lines 262-293 as inspected on 2026-06-19.

API_BASE_URL = "https://api.outlier.bet"
# Outlier can return short-lived 403s during bursty market-detail fetches.
# Treat only 401 as a definitive auth failure.
RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}

# playerProps is paginated. The exact query-param name Outlier expects for the
# next page is not documented, so we try a small candidate list and lock onto
# whichever one actually advances the feed (verified by a changed page
# signature). A wrong guess therefore degrades safely to "first page only"
# rather than looping or duplicating.
MAX_PROP_PAGES = 60
TOKEN_PARAM_CANDIDATES = ("pageToken", "nextPageToken", "page_token")
NUMBER_PARAM_CANDIDATES = ("pageNumber", "page", "pageNo", "page_number")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_id(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    for key in ("insightId", "outcomeId", "marketOutcomeId", "marketId", "id"):
        value = record.get(key)
        if value:
            return str(value)
    outcome = record.get("outcome")
    if isinstance(outcome, dict):
        return str(outcome.get("outcomeId") or outcome.get("marketId") or "")
    return ""


def _records_signature(records: list[Any]) -> tuple[Any, ...]:
    """A cheap fingerprint of a page used to detect lack of progress."""
    ids = [_record_id(record) for record in records]
    return (len(records), tuple(ids[:3]), tuple(ids[-3:]))


def _page_token_and_meta(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (nextPageToken, page-meta), supporting both layouts.

    playerProps nests pagination under ``_page``; insights puts ``nextPageToken``
    at the top level with no ``_page`` wrapper.
    """
    meta = payload.get("_page") if isinstance(payload.get("_page"), dict) else {}
    token = str(meta.get("nextPageToken") or "").strip()
    if not token:
        token = str(payload.get("nextPageToken") or "").strip()
    return token, meta


class OutlierApiError(RuntimeError):
    pass


class AuthRequiredError(OutlierApiError):
    pass


@dataclass(frozen=True)
class ApiResponse:
    path: str
    status: int
    payload: dict[str, Any]


class OutlierApiClient:
    def __init__(
        self,
        *,
        storage_state: dict[str, Any] | None = None,
        opener: Callable[..., Any] = urlopen,
        max_retries: int = 3,
        timeout: int = 45,
        base_url: str = API_BASE_URL,
    ) -> None:
        self.storage_state = storage_state if storage_state is not None else load_storage_state()
        self.opener = opener
        self.max_retries = max_retries
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def url_for(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base_url}{path}"

    def fetch_json(self, path: str) -> dict[str, Any]:
        url = self.url_for(path)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = Request(url, headers=build_api_headers(self.storage_state))
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    body = response.read()
                    if body[:2] == b"\x1f\x8b":
                        body = gzip.decompress(body)
                    payload = json.loads(body.decode("utf-8"), strict=False)
                    if isinstance(payload, dict):
                        return payload
                    raise OutlierApiError(f"Unexpected non-object payload for {url}")
            except HTTPError as exc:
                last_error = exc
                if exc.code == 401:
                    raise AuthRequiredError(_safe_http_error_message(exc, url)) from exc
                if exc.code not in RETRYABLE_STATUS_CODES or attempt >= self.max_retries:
                    raise OutlierApiError(_safe_http_error_message(exc, url)) from exc
                time.sleep(0.5 * attempt)
            except URLError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise OutlierApiError(f"Network error for {url}: {exc}") from exc
                time.sleep(0.5 * attempt)
        if last_error:
            raise OutlierApiError(f"Failed to fetch {url}: {last_error}") from last_error
        raise OutlierApiError(f"Failed to fetch {url}")

    def fetch_schedule(self, league: str) -> dict[str, Any]:
        token = league.strip().upper()
        return self.fetch_json(f"/sportsdata/leagues/{token}/schedule")

    def _fetch_props_page(
        self,
        base_path: str,
        *,
        param: str,
        page_token: str | None = None,
        page_number: int | None = None,
    ) -> dict[str, Any]:
        sep = "&" if "?" in base_path else "?"
        value = page_token if page_token is not None else page_number
        return self.fetch_json(f"{base_path}{sep}{param}={quote(str(value))}")

    def _fetch_paginated(
        self,
        base: str,
        record_key: str,
        *,
        max_pages: int = MAX_PROP_PAGES,
    ) -> dict[str, Any]:
        """Fetch all pages of a paginated list endpoint, merged under record_key.

        Follows ``nextPageToken`` (under ``_page`` or top-level), otherwise walks
        ``_page.pageNumber`` up to ``_page.pages``. The next-page query param is
        unknown, so we try a candidate list and lock onto whichever advances the
        feed (verified by a changed page signature). Stops on a repeated token, a
        non-advancing page, exhaustion, or ``max_pages``. A non-secret
        ``_page_summary`` is attached.
        """
        first = self.fetch_json(base)
        records = list(first.get(record_key) or [])
        token, meta = _page_token_and_meta(first)
        pages_total = _as_int(meta.get("pages"))

        summary: dict[str, Any] = {
            "pages_reported": pages_total,
            "total_reported": _as_int(meta.get("total")),
            "pages_fetched": 1,
            "merged_records": len(records),
            f"merged_{record_key}": len(records),
            "pagination_method": None,
            "param_used": None,
            "stopped_reason": "single_page",
        }

        if not token and (not pages_total or pages_total <= 1):
            first[record_key] = records
            first["_page_summary"] = summary
            return first

        last_sig = _records_signature(records)
        seen_tokens: set[str] = set()
        locked_param: str | None = None
        method: str | None = None
        current = first
        fetched = 1
        stopped = "exhausted"

        while fetched < max_pages:
            token_val, current_meta = _page_token_and_meta(current)
            cur_num = _as_int(current_meta.get("pageNumber")) or fetched
            use_token = bool(token_val)

            if use_token and token_val in seen_tokens:
                stopped = "token_repeat"
                break
            if not use_token and not (pages_total and cur_num < pages_total):
                stopped = "exhausted"
                break

            if use_token:
                seen_tokens.add(token_val)
                candidates = [locked_param] if locked_param else list(TOKEN_PARAM_CANDIDATES)
                key, value = "page_token", token_val
            else:
                candidates = [locked_param] if locked_param else list(NUMBER_PARAM_CANDIDATES)
                key, value = "page_number", cur_num + 1

            page = None
            used_param = None
            for param in candidates:
                try:
                    candidate = self._fetch_props_page(base, param=param, **{key: value})
                except AuthRequiredError:
                    raise
                except OutlierApiError:
                    continue
                cand_records = candidate.get(record_key) if isinstance(candidate.get(record_key), list) else []
                if cand_records and _records_signature(cand_records) != last_sig:
                    page = candidate
                    used_param = param
                    break

            if page is None:
                stopped = "no_progress"
                break

            locked_param = used_param
            method = "nextPageToken" if use_token else "pageNumber"
            page_records = page.get(record_key)
            records.extend(page_records)
            last_sig = _records_signature(page_records)
            fetched += 1
            current = page
        else:
            stopped = "max_pages_cap"

        summary.update(
            pages_fetched=fetched,
            merged_records=len(records),
            **{f"merged_{record_key}": len(records)},
            pagination_method=method,
            param_used=locked_param,
            stopped_reason=stopped,
        )
        first[record_key] = records
        first["_page_summary"] = summary
        return first

    def fetch_player_props(self, league: str, *, max_pages: int = MAX_PROP_PAGES) -> dict[str, Any]:
        token = league.strip().upper()
        return self._fetch_paginated(
            f"/sportsdata/leagues/{token}/playerProps", "props", max_pages=max_pages
        )

    def fetch_insights(self, league: str, *, max_pages: int = MAX_PROP_PAGES) -> dict[str, Any]:
        token = league.strip().upper()
        return self._fetch_paginated(
            f"/sportsdata/leagues/{token}/insights", "insights", max_pages=max_pages
        )

    def fetch_market(self, market_id: str) -> dict[str, Any]:
        return self.fetch_json(f"/sportsdata/markets/{market_id}")

    def fetch_event(self, event_id: str) -> dict[str, Any]:
        return self.fetch_json(f"/sportsdata/events/{event_id}")

    def fetch_event_markets(self, event_id: str, market_type: str) -> dict[str, Any]:
        return self.fetch_json(f"/sportsdata/events/{event_id}/markets?marketType={quote(market_type)}")

    def fetch_event_insights(self, event_id: str, *, max_pages: int = MAX_PROP_PAGES) -> dict[str, Any]:
        return self._fetch_paginated(
            f"/sportsdata/events/{event_id}/insights", "insights", max_pages=max_pages
        )

    def fetch_event_matchup(self, event_id: str) -> dict[str, Any]:
        return self.fetch_json(f"/sportsdata/events/{event_id}/matchup")

    def fetch_team_injuries(self, league: str, team_id: str) -> dict[str, Any]:
        token = league.strip().upper()
        return self.fetch_json(f"/sportsdata/leagues/{token}/teams/{team_id}/injuries")


def _safe_http_error_message(exc: HTTPError, url: str) -> str:
    """Return an error string with body shape only, never raw payload values."""
    body = exc.read().decode("utf-8", errors="replace")
    body_note = f"body_len={len(body)}"
    if body.strip():
        try:
            decoded = json.loads(body)
        except Exception:
            decoded = None
        if isinstance(decoded, dict):
            body_note = f"body_shape={shape_summary(decoded, max_depth=2)}"
    return f"HTTP {exc.code} for {url}: {body_note}"
