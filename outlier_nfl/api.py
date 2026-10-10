"""Standalone, resilient HTTP API client for Outlier NFL data endpoints.

Features:
- Automatic session discovery from storage_state.json or environment variables.
- Bounded exponential backoff with random jitter on transient errors (403, 429, 50x).
- Transparent Gzip decompression and tolerant JSON parsing with strict=False.
- Robust pagination loop with progress fingerprinting to prevent stalls.
- Zero runtime coupling with outlier_scrapers.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import http.client
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import zlib

from outlier_nfl.constants import (
    API_BASE_URL,
    APP_ORIGIN,
    BASE_DELAY_SECONDS,
    EVENT_INSIGHTS_ENDPOINT,
    EVENT_MARKETS_ENDPOINT,
    EVENT_MATCHUP_ENDPOINT,
    EVENT_METADATA_ENDPOINT,
    LEAGUE_TOKEN,
    MARKET_DETAIL_ENDPOINT,
    MARKET_TYPE_GAMELINE,
    MAX_DELAY_SECONDS,
    MAX_RETRIES,
    NUMBER_PARAM_CANDIDATES,
    PAGINATION_MAX_PAGES,
    REQUEST_TIMEOUT_SECONDS,
    RETRYABLE_STATUS_CODES,
    TOKEN_PARAM_CANDIDATES,
)

logger = logging.getLogger("outlier_nfl.api")

TOKEN_MARKERS: tuple[str, ...] = (
    "access_token",
    "accesstoken",
    "auth_token",
    "authtoken",
    "id_token",
    "idtoken",
    "bearer",
    "authorization",
    "sb-access-token",
)

STREAM_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    URLError,
    OSError,
    http.client.HTTPException,
    zlib.error,
    EOFError,
)


class OutlierNflApiError(RuntimeError):
    """Base exception for Outlier NFL API errors.

    ``status_code`` is the final HTTP status when the error came from one.
    """

    def __init__(self, message: str = "", *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code


class IncompletePaginationError(OutlierNflApiError):
    """A paginated list could not be fetched to its end (F04).

    Raised instead of returning the pages fetched so far: an exhausted-retry
    page failure, a repeated cursor, a page that does not advance, or the page
    cap reached while the feed still advertises more pages.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        pages_fetched: int,
        pages_expected: int | None,
        records_fetched: int,
    ) -> None:
        super().__init__(message)
        self.reason: str = reason
        self.pages_fetched: int = pages_fetched
        self.pages_expected: int | None = pages_expected
        self.records_fetched: int = records_fetched


# A page request with an unproven parameter name failing with one of these means
# "this parameter is not supported", not "the page failed".
UNSUPPORTED_PARAM_STATUS = frozenset({400, 404, 422})


class AuthRequiredError(OutlierNflApiError):
    """Raised when authentication credentials are missing, invalid, or expired (HTTP 401)."""


class RateLimitError(OutlierNflApiError):
    """Raised when API rate limits (HTTP 429) persist past maximum retry attempts."""


class NotFoundError(OutlierNflApiError):
    """Raised when an endpoint or resource returns HTTP 404."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter."""

    max_retries: int = MAX_RETRIES
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS
    base_delay_seconds: float = BASE_DELAY_SECONDS
    max_delay_seconds: float = MAX_DELAY_SECONDS

    def delay_for(self, attempt: int, *, rng: Callable[[], float] = random.random) -> float:
        delay = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))
        return delay * (0.5 + rng() * 0.5)


def _normalize_bearer_token(value: Any) -> str | None:
    """Extract raw bearer token string if valid."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower().startswith("bearer "):
        text = text.split(" ", 1)[1].strip()
    if len(text) < 20 or any(ch.isspace() for ch in text):
        return None
    return text


_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+$")


def _is_credential_key(key: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(key or "").lower())
    return any(re.sub(r"[^a-z0-9]+", "", m) in compact for m in TOKEN_MARKERS)


def _decode_json_text(text: str) -> Any:
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text, strict=False)
        except ValueError:
            return None
    return None


def _extract_token_from_object(payload: Any, keyed: bool = False) -> str | None:
    """Find a bearer token in a dict, list or JSON string (F24).

    A scalar string counts only when it sits under a recognized credential key
    (``keyed``) or is JWT-shaped. Arbitrary long strings such as an origin URL
    or a stored message are never treated as credentials. Tokens are never logged.
    """
    if isinstance(payload, dict):
        for key, val in payload.items():
            if _is_credential_key(key):
                found = _extract_token_from_object(val, keyed=True)
                if found:
                    return found
        for key, val in payload.items():
            if not _is_credential_key(key) and isinstance(val, (dict, list, str)):
                found = _extract_token_from_object(val)
                if found:
                    return found
        return None
    if isinstance(payload, list):
        for item in payload:
            found = _extract_token_from_object(item, keyed=keyed)
            if found:
                return found
        return None
    if isinstance(payload, str):
        text = payload.strip()
        decoded = _decode_json_text(text)
        if decoded is not None:
            return _extract_token_from_object(decoded, keyed=keyed)
        tok = _normalize_bearer_token(text)
        if tok and (keyed or _JWT_RE.match(tok)):
            return tok
    return None


def extract_token_from_storage_state(storage_state: dict[str, Any]) -> str | None:
    """Extract authorization bearer token from Playwright storage_state structure."""
    # 1. Search localStorage origins for outlier.bet
    origins = storage_state.get("origins", [])
    if isinstance(origins, list):
        for origin_entry in origins:
            if not isinstance(origin_entry, dict):
                continue
            origin_url = str(origin_entry.get("origin", ""))
            if "outlier.bet" in origin_url:
                local_storage = origin_entry.get("localStorage", [])
                if isinstance(local_storage, list):
                    for item in local_storage:
                        if isinstance(item, dict):
                            name = str(item.get("name") or "").strip().lower()
                            compact = re.sub(r"[^a-z0-9]+", "", name)
                            if any(m.replace("-", "").replace("_", "") in compact for m in TOKEN_MARKERS):
                                val = item.get("value")
                                token = _extract_token_from_object(val, keyed=True)
                                if token:
                                    return token

    # 2. Search cookies for token markers
    cookies = storage_state.get("cookies", [])
    if isinstance(cookies, list):
        for cookie in cookies:
            if isinstance(cookie, dict):
                c_name = str(cookie.get("name", "")).lower()
                if any(m in c_name for m in TOKEN_MARKERS):
                    token = _normalize_bearer_token(cookie.get("value"))
                    if token:
                        return token

    # 3. Search the rest of the state (not cookies: those are sent as a Cookie
    # header, never as Authorization) for a credential-keyed entry or a JWT.
    rest = {k: v for k, v in storage_state.items() if k != "cookies"}
    return _extract_token_from_object(rest)


def build_cookie_header_from_storage_state(
    storage_state: dict[str, Any],
    target_hosts: tuple[str, ...] = ("api.outlier.bet", "app.outlier.bet"),
) -> str | None:
    """Format Cookie header string from storage_state cookies for target hosts."""
    cookies = storage_state.get("cookies", [])
    if not isinstance(cookies, list) or not cookies:
        return None
    hosts = tuple(h.lower() for h in target_hosts)
    now = time.time()
    pairs: list[str] = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        val = str(c.get("value") or "").strip()
        domain = str(c.get("domain") or "").strip().lstrip(".").lower()
        expires = c.get("expires")
        if not name or not val or not domain:
            continue
        if isinstance(expires, (int, float)) and 0 < expires < now:
            continue
        if not any(h == domain or h.endswith(f".{domain}") or domain.endswith(f".{h}") for h in hosts):
            continue
        pairs.append(f"{name}={val}")
    return "; ".join(pairs) if pairs else None


def discover_session_credentials(
    session_path: Path | str | None = None,
) -> tuple[str | None, str | None]:
    """Auto-discover Bearer token and Cookie header from storage or environment.

    Returns (bearer_token, cookie_header).
    """
    # 1. Environment variables
    env_token = (
        os.environ.get("OUTLIER_BEARER_TOKEN")
        or os.environ.get("OUTLIER_API_TOKEN")
        or os.environ.get("OUTLIER_TOKEN")
    )
    if env_token:
        norm = _normalize_bearer_token(env_token)
        if norm:
            return norm, None

    # 2. File discovery paths
    candidate_paths: list[Path] = []
    if session_path:
        candidate_paths.append(Path(session_path))

    repo_root = Path.cwd()
    candidate_paths.extend([
        repo_root / "config" / ".outlier_session" / "storage_state.json",
        repo_root / "config" / ".outlier_session" / "api_bearer_token.txt",
        repo_root / "config" / ".outlier_session" / "session_metadata.json",
        repo_root / "config" / ".outlier_session" / "api_request_headers.json",
        repo_root / "config" / "outlier_session.json",
        Path.home() / ".outlier" / "storage_state.json",
    ])

    for path in candidate_paths:
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            if path.suffix == ".txt":
                text = path.read_text(encoding="utf-8").strip()
                tok = _normalize_bearer_token(text)
                if tok:
                    return tok, None
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f, strict=False)
                if isinstance(data, dict):
                    tok = extract_token_from_storage_state(data)
                    cookie_hdr = build_cookie_header_from_storage_state(data)
                    if tok or cookie_hdr:
                        return tok, cookie_hdr
        except Exception as exc:
            logger.debug("Failed reading session candidate %s: %s", path, exc)
            continue

    return None, None


def _record_identity(r: Any) -> str:
    if isinstance(r, dict):
        rec_id = r.get("outcomeId") or r.get("marketId") or r.get("id") or r.get("eventId")
        if not rec_id and isinstance(r.get("outcome"), dict):
            rec_id = r["outcome"].get("outcomeId") or r["outcome"].get("marketId")
        if rec_id:
            return str(rec_id)
    return json.dumps(r, sort_keys=True, default=str)


def _records_signature(records: list[Any]) -> tuple[int, str]:
    """Identity of a whole page: its length and a hash of every record's identity.

    Every record counts (F04): a first/last-three fingerprint let a changed middle
    of a page pass as the same page, and a repeated page pass as a new one.
    """
    digest = hashlib.sha256()
    for r in records:
        digest.update(_record_identity(r).encode("utf-8"))
        digest.update(b"\0")
    return (len(records), digest.hexdigest())


def _extract_next_token_and_meta(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract pagination cursor token and metadata from payload."""
    raw_meta = payload.get("_page")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    token = str(meta.get("nextPageToken") or "").strip()
    if not token:
        token = str(payload.get("nextPageToken") or payload.get("pageToken") or "").strip()
    return token, meta


class OutlierNflApiClient:
    """Client for Outlier NFL REST APIs."""

    def __init__(
        self,
        base_url: str = API_BASE_URL,
        session_path: Path | str | None = None,
        bearer_token: str | None = None,
        opener: Callable[..., Any] = urlopen,
        retry_policy: RetryPolicy | None = None,
        max_retries: int | None = None,
        timeout: int | None = None,
        storage_state: dict[str, Any] | None = None,
    ) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.opener: Callable[..., Any] = opener

        default_policy = RetryPolicy()
        self.retry_policy: RetryPolicy = retry_policy or RetryPolicy(
            max_retries=max_retries if max_retries is not None else default_policy.max_retries,
            timeout_seconds=timeout if timeout is not None else default_policy.timeout_seconds,
        )

        self.storage_state: dict[str, Any] | None = storage_state
        self.bearer_token: str | None = None
        self.cookie_header: str | None = None

        if bearer_token:
            self.bearer_token = _normalize_bearer_token(bearer_token)
        elif storage_state:
            self.bearer_token = extract_token_from_storage_state(storage_state)
            self.cookie_header = build_cookie_header_from_storage_state(storage_state)
        else:
            tok, cookie = discover_session_credentials(session_path)
            self.bearer_token = tok
            self.cookie_header = cookie

    def build_headers(self) -> dict[str, str]:
        """Build request headers with auth tokens and browser emulation."""
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Origin": APP_ORIGIN,
            "Referer": f"{APP_ORIGIN}/",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    def url_for(self, path: str, params: dict[str, Any] | None = None) -> str:
        """Construct full URL with optional query parameters."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}
            if clean_params:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}{urlencode(clean_params)}"
        return url

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
                    raise NotFoundError(f"HTTP 404 Not Found for {url}", status_code=404) from exc
                if exc.code == 429 and attempt >= policy.max_retries:
                    raise RateLimitError(
                        f"HTTP 429 Rate limited after {policy.max_retries} attempts: {url}", status_code=429
                    ) from exc
                if exc.code not in RETRYABLE_STATUS_CODES or attempt >= policy.max_retries:
                    raise OutlierNflApiError(f"HTTP {exc.code} for {url}", status_code=exc.code) from exc

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

    def _fetch_paginated(
        self,
        base_path: str,
        record_key: str,
        *,
        max_pages: int = PAGINATION_MAX_PAGES,
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch all pages of a paginated list endpoint, merging records."""
        initial_params = dict(query_params or {})
        first = self.fetch_json(base_path, params=initial_params)
        records = list(first.get(record_key) or [])
        token, meta = _extract_next_token_and_meta(first)
        total_pages = meta.get("pages")
        try:
            total_pages_num = int(total_pages) if total_pages is not None else None
        except (ValueError, TypeError):
            total_pages_num = None

        if not token and (not total_pages_num or total_pages_num <= 1):
            first[record_key] = records
            return first

        last_sig = _records_signature(records)
        seen_tokens: set[str] = set()
        locked_param: str | None = None
        current = first
        fetched = 1

        def incomplete(reason: str, detail: str) -> IncompletePaginationError:
            return IncompletePaginationError(
                f"Incomplete pagination for {base_path} ({reason}) after {fetched} page(s)"
                f"{f' of {total_pages_num}' if total_pages_num else ''}: {detail}",
                reason=reason,
                pages_fetched=fetched,
                pages_expected=total_pages_num,
                records_fetched=len(records),
            )

        # A list is complete only when the feed itself says so (F04): no next
        # cursor and no advertised page beyond the last one fetched.
        while True:
            token_val, cur_meta = _extract_next_token_and_meta(current)
            try:
                cur_num = int(cur_meta.get("pageNumber") or fetched)
            except (ValueError, TypeError):
                cur_num = fetched

            use_token = bool(token_val)
            if not use_token and (not total_pages_num or cur_num >= total_pages_num):
                break
            if use_token and token_val in seen_tokens:
                raise incomplete("repeated_cursor", "the feed returned a cursor it already gave")
            if fetched >= max_pages:
                raise incomplete("page_cap", f"max_pages={max_pages} reached with pages remaining")

            if use_token:
                seen_tokens.add(token_val)
                candidates = [locked_param] if locked_param else list(TOKEN_PARAM_CANDIDATES)
                param_val = token_val
            else:
                candidates = [locked_param] if locked_param else list(NUMBER_PARAM_CANDIDATES)
                param_val = str(cur_num + 1)

            page = None
            used_param = None
            for param_name in candidates:
                p_dict = dict(initial_params)
                p_dict[param_name] = param_val
                try:
                    candidate = self.fetch_json(base_path, params=p_dict)
                except AuthRequiredError:
                    raise
                except OutlierNflApiError as exc:
                    # Only an unproven parameter name may fail as "unsupported";
                    # a page failure (exhausted 429/5xx, transport) is incomplete.
                    unsupported = isinstance(exc, NotFoundError) or (
                        getattr(exc, "status_code", None) in UNSUPPORTED_PARAM_STATUS
                    )
                    if locked_param is None and unsupported:
                        continue
                    raise incomplete("page_failed", str(exc)) from exc

                cand_records = candidate.get(record_key)
                if not isinstance(cand_records, list):
                    if locked_param is None:
                        continue
                    raise incomplete("invalid_page", f"{record_key!r} is not a list")
                if not cand_records:
                    if locked_param is not None:
                        page = candidate
                        used_param = param_name
                        break
                    continue
                if _records_signature(cand_records) != last_sig:
                    page = candidate
                    used_param = param_name
                    break

            if page is None:
                raise incomplete("no_advance", "no page parameter returned the next page")

            locked_param = used_param
            page_records = page.get(record_key, [])
            records.extend(page_records)
            last_sig = _records_signature(page_records)
            fetched += 1
            current = page
            if not page_records:
                # A proven cursor led to an empty page: the feed has ended.
                break

        first[record_key] = records
        return first

    def fetch_schedule(self, league: str = LEAGUE_TOKEN) -> dict[str, Any]:
        """Fetch scheduled events for the NFL slate."""
        token = league.strip().upper()
        return self.fetch_json(f"/sportsdata/leagues/{token}/schedule")

    def fetch_event_markets(
        self,
        event_id: str,
        market_type: str = MARKET_TYPE_GAMELINE,
    ) -> dict[str, Any]:
        """Fetch betting markets for a specific NFL event and market type."""
        endpoint = EVENT_MARKETS_ENDPOINT.format(event_id=quote(str(event_id)))
        return self.fetch_json(endpoint, params={"marketType": market_type})

    def fetch_player_props(
        self,
        max_pages: int = PAGINATION_MAX_PAGES,
        league: str = LEAGUE_TOKEN,
    ) -> dict[str, Any]:
        """Fetch bulk paginated player props for NFL."""
        token = league.strip().upper()
        return self._fetch_paginated(
            f"/sportsdata/leagues/{token}/playerProps",
            record_key="props",
            max_pages=max_pages,
        )

    def fetch_event_matchup(self, event_id: str) -> dict[str, Any]:
        """Fetch matchup and lineup details for an event."""
        endpoint = EVENT_MATCHUP_ENDPOINT.format(event_id=quote(str(event_id)))
        return self.fetch_json(endpoint)

    def fetch_event_insights(
        self,
        event_id: str,
        max_pages: int = PAGINATION_MAX_PAGES,
    ) -> dict[str, Any]:
        """Fetch betting insights and trends for an event."""
        endpoint = EVENT_INSIGHTS_ENDPOINT.format(event_id=quote(str(event_id)))
        return self._fetch_paginated(endpoint, record_key="insights", max_pages=max_pages)

    def fetch_team_injuries(
        self,
        team_id: str,
        league: str = LEAGUE_TOKEN,
    ) -> dict[str, Any]:
        """Fetch injury report for an NFL team."""
        token = league.strip().upper()
        endpoint = f"/sportsdata/leagues/{token}/teams/{quote(str(team_id))}/injuries"
        return self.fetch_json(endpoint)

    def fetch_event_metadata(self, event_id: str) -> dict[str, Any]:
        """Fetch single event metadata."""
        endpoint = EVENT_METADATA_ENDPOINT.format(event_id=quote(str(event_id)))
        return self.fetch_json(endpoint)

    def fetch_market_detail(self, market_id: str) -> dict[str, Any]:
        """Fetch detailed market line movement and positive EV data."""
        endpoint = MARKET_DETAIL_ENDPOINT.format(market_id=quote(str(market_id)))
        return self.fetch_json(endpoint)
