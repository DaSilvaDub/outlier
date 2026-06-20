from urllib.parse import parse_qs, urlparse

from outlier_scrapers.api import OutlierApiClient


def _client_with(fetch_json):
    client = OutlierApiClient(storage_state={})
    client.fetch_json = fetch_json  # type: ignore[assignment]
    return client


def _qs(path: str) -> dict:
    return parse_qs(urlparse("http://x" + path).query)


def test_token_pagination_merges_pages_and_summarizes():
    def fetch(path):
        params = _qs(path)
        token = params.get("pageToken", [None])[0]
        if token is None:
            return {
                "props": [{"outcome": {"outcomeId": "a"}}, {"outcome": {"outcomeId": "b"}}],
                "_page": {"nextPageToken": "t2", "pageNumber": 1, "pages": 3, "total": 5},
            }
        if token == "t2":
            return {
                "props": [{"outcome": {"outcomeId": "c"}}, {"outcome": {"outcomeId": "d"}}],
                "_page": {"nextPageToken": "t3", "pageNumber": 2, "pages": 3},
            }
        if token == "t3":
            return {"props": [{"outcome": {"outcomeId": "e"}}], "_page": {"pageNumber": 3, "pages": 3}}
        return {"props": [], "_page": {}}

    out = _client_with(fetch).fetch_player_props("MLB")
    assert len(out["props"]) == 5
    summary = out["_page_summary"]
    assert summary["pages_fetched"] == 3
    assert summary["pagination_method"] == "nextPageToken"
    assert summary["param_used"] == "pageToken"
    # The secret token must never appear in the non-secret summary.
    assert "nextPageToken" not in summary


def test_number_pagination_merges_pages():
    def fetch(path):
        params = _qs(path)
        num = None
        for key in ("pageNumber", "page", "pageNo", "page_number"):
            if key in params:
                num = int(params[key][0])
                break
        if num is None:
            return {
                "props": [{"outcome": {"outcomeId": "a"}}, {"outcome": {"outcomeId": "b"}}],
                "_page": {"pages": 2, "total": 3, "pageNumber": 1},
            }
        if num == 2:
            return {"props": [{"outcome": {"outcomeId": "c"}}], "_page": {"pages": 2, "pageNumber": 2}}
        return {"props": [], "_page": {"pages": 2, "pageNumber": num}}

    out = _client_with(fetch).fetch_player_props("WNBA")
    assert len(out["props"]) == 3
    assert out["_page_summary"]["pagination_method"] == "pageNumber"
    assert out["_page_summary"]["param_used"] == "pageNumber"


def test_single_page_reports_no_pagination():
    out = _client_with(
        lambda path: {"props": [{"outcome": {"outcomeId": "a"}}], "_page": {"pages": 1, "total": 1, "pageNumber": 1}}
    ).fetch_player_props("WNBA")
    assert len(out["props"]) == 1
    assert out["_page_summary"]["pages_fetched"] == 1
    assert out["_page_summary"]["stopped_reason"] == "single_page"


def test_pagination_stops_when_a_page_does_not_advance():
    calls = {"n": 0}

    def fetch(path):
        calls["n"] += 1
        # Param is ignored: every request returns the same first page.
        return {"props": [{"outcome": {"outcomeId": "a"}}], "_page": {"nextPageToken": "t", "pages": 5}}

    out = _client_with(fetch).fetch_player_props("MLB", max_pages=10)
    assert len(out["props"]) == 1
    assert out["_page_summary"]["stopped_reason"] in {"no_progress", "token_repeat"}
    # page 1 + at most the token-param candidates, never an unbounded loop.
    assert calls["n"] <= 1 + 3


def test_pagination_respects_max_pages_cap():
    def fetch(path):
        token = _qs(path).get("pageToken", [None])[0]
        n = int(token[1:]) if token else 1
        return {
            "props": [{"outcome": {"outcomeId": f"id{n}"}}],
            "_page": {"nextPageToken": f"t{n + 1}", "pages": 999},
        }

    out = _client_with(fetch).fetch_player_props("MLB", max_pages=4)
    assert out["_page_summary"]["pages_fetched"] == 4
    assert out["_page_summary"]["stopped_reason"] == "max_pages_cap"
    assert len(out["props"]) == 4
