"""F04 (#224): a paginated feed is complete or it raises; page 1 never passes as the feed."""

from __future__ import annotations

import io
from typing import Any
from urllib.error import HTTPError

import pytest

from outlier_nfl.api import (
    IncompletePaginationError,
    NotFoundError,
    OutlierNflApiClient,
    OutlierNflApiError,
    RateLimitError,
    RetryPolicy,
)


def _rec(i: str) -> dict[str, Any]:
    return {"outcome": {"outcomeId": i}}


def _page(ids: list[str], nxt: str | None, num: int, pages: int) -> dict[str, Any]:
    return {"props": [_rec(i) for i in ids],
            "_page": {"nextPageToken": nxt, "pageNumber": num, "pages": pages}}


def _client(fetch: Any) -> OutlierNflApiClient:
    client = OutlierNflApiClient(bearer_token="t")
    client.fetch_json = fetch  # type: ignore[method-assign]
    return client


def _token_feed(fail_on: str | None = None, exc: Exception | None = None):
    pages = {None: _page(["a", "b"], "t2", 1, 2), "t2": _page(["c"], None, 2, 2)}

    def fetch(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        tok = (params or {}).get("pageToken")
        if tok is not None and tok == fail_on:
            assert exc is not None
            raise exc
        return pages[tok]

    return fetch


def test_complete_token_feed_merges_every_page() -> None:
    out = _client(_token_feed()).fetch_player_props()
    assert [r["outcome"]["outcomeId"] for r in out["props"]] == ["a", "b", "c"]


@pytest.mark.parametrize(
    "exc",
    [OutlierNflApiError("HTTP 500 for x", status_code=500),
     RateLimitError("HTTP 429 Rate limited after 3 attempts", status_code=429),
     OutlierNflApiError("Transport/stream error for x after 3 attempts")],
    ids=["exhausted_500", "exhausted_429", "transport"],
)
def test_page_two_failure_raises_incomplete(exc: Exception) -> None:
    with pytest.raises(IncompletePaginationError) as info:
        _client(_token_feed("t2", exc)).fetch_player_props()
    assert info.value.reason == "page_failed"
    assert (info.value.pages_fetched, info.value.pages_expected, info.value.records_fetched) == (1, 2, 2)


def test_exhausted_http_500_through_real_transport_raises_incomplete() -> None:
    first = b'{"props": [{"outcome": {"outcomeId": "a"}}], "_page": {"nextPageToken": "t2", "pageNumber": 1, "pages": 2}}'
    calls = {"n": 0}

    class _Resp(io.BytesIO):
        headers: dict[str, str] = {}

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def getcode(self) -> int:
            return 200

    def opener(req: Any, timeout: Any = None) -> Any:
        calls["n"] += 1
        if "pageToken" not in req.full_url:
            return _Resp(first)
        raise HTTPError(req.full_url, 500, "boom", {}, None)  # type: ignore[arg-type]

    client = OutlierNflApiClient(
        bearer_token="t", opener=opener,
        retry_policy=RetryPolicy(max_retries=2, base_delay_seconds=0.0, max_delay_seconds=0.0),
    )
    with pytest.raises(IncompletePaginationError) as info:
        client.fetch_player_props()
    assert info.value.reason == "page_failed"
    assert calls["n"] == 3  # page 1 once, page 2 retried to exhaustion, no other parameter tried


def test_repeated_cursor_raises_incomplete() -> None:
    pages = {None: _page(["a"], "t2", 1, 3), "t2": _page(["b"], "t3", 2, 3), "t3": _page(["c"], "t2", 3, 3)}
    client = _client(lambda path, params=None: pages[(params or {}).get("pageToken")])
    with pytest.raises(IncompletePaginationError) as info:
        client.fetch_player_props()
    assert info.value.reason == "repeated_cursor"


def test_page_cap_with_pages_remaining_raises_incomplete() -> None:
    def fetch(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        n = int(((params or {}).get("pageToken") or "t1")[1:])
        return _page([f"r{n}"], f"t{n + 1}", n, 10)

    with pytest.raises(IncompletePaginationError) as info:
        _client(fetch).fetch_player_props(max_pages=3)
    assert (info.value.reason, info.value.pages_fetched, info.value.pages_expected) == ("page_cap", 3, 10)


def test_page_cap_exactly_at_last_page_is_complete() -> None:
    def fetch(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        n = int(((params or {}).get("pageToken") or "t1")[1:])
        return _page([f"r{n}"], f"t{n + 1}" if n < 3 else None, n, 3)

    assert len(_client(fetch).fetch_player_props(max_pages=3)["props"]) == 3


def test_page_with_changed_middle_records_is_a_new_page() -> None:
    # Same length and same first/last three ids, different middle: a distinct page.
    p1 = ["a", "b", "c", "m1", "x", "y", "z"]
    p2 = ["a", "b", "c", "m2", "x", "y", "z"]
    pages = {None: _page(p1, "t2", 1, 2), "t2": _page(p2, None, 2, 2)}
    out = _client(lambda path, params=None: pages[(params or {}).get("pageToken")]).fetch_player_props()
    assert len(out["props"]) == 14


def test_unsupported_parameter_is_skipped_during_discovery() -> None:
    def fetch(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if "pageToken" in params:
            raise OutlierNflApiError("HTTP 400 for x", status_code=400)
        if params.get("nextPageToken") == "t2":
            return _page(["c"], None, 2, 2)
        if params:
            raise NotFoundError("HTTP 404", status_code=404)
        return _page(["a", "b"], "t2", 1, 2)

    out = _client(fetch).fetch_player_props()
    assert len(out["props"]) == 3


def test_empty_page_after_proven_cursor_ends_the_feed() -> None:
    pages = {None: _page(["a"], "t2", 1, 0), "t2": _page(["b"], "t3", 2, 0), "t3": _page([], None, 3, 0)}
    out = _client(lambda path, params=None: pages[(params or {}).get("pageToken")]).fetch_player_props()
    assert len(out["props"]) == 2
