import gzip
import io
import json
from urllib.error import HTTPError

from outlier_scrapers.api import AuthRequiredError, OutlierApiClient, OutlierApiError
from outlier_scrapers.auth import (
    build_api_headers,
    is_props_url,
    load_storage_state,
    write_saved_api_request_headers,
)
from outlier_scrapers.redaction import REDACTED, shape_summary


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def storage_state():
    return {
        "origins": [
            {
                "origin": "https://app.outlier.bet",
                "localStorage": [
                    {
                        "name": "CognitoIdentityServiceProvider.fake.accessToken",
                        "value": "x" * 40,
                    }
                ],
            }
        ],
        "cookies": [
            {
                "name": "sid",
                "value": "cookie-value",
                "domain": ".outlier.bet",
                "expires": 9999999999,
            }
        ],
    }


def test_build_api_headers_adds_authorization_and_cookie_without_printing():
    headers = build_api_headers(storage_state())
    assert headers["Authorization"] == "Bearer " + ("x" * 40)
    assert "sid=cookie-value" in headers["Cookie"]
    assert headers["Accept"] == "application/json"


def test_fetch_json_handles_gzip_magic_bytes():
    body = gzip.compress(json.dumps({"ok": True}).encode("utf-8"))

    def opener(request, timeout):
        return FakeResponse(body)

    client = OutlierApiClient(storage_state=storage_state(), opener=opener)
    assert client.fetch_json("/test") == {"ok": True}


def test_fetch_json_retries_only_transient_statuses():
    calls = {"count": 0}

    def opener(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError(
                request.full_url,
                500,
                "server error",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"temporary"}'),
            )
        return FakeResponse(b'{"ok": true}')

    client = OutlierApiClient(storage_state=storage_state(), opener=opener, max_retries=2)
    assert client.fetch_json("/test") == {"ok": True}
    assert calls["count"] == 2


def test_fetch_json_401_is_auth_required_and_not_retried():
    calls = {"count": 0}

    def opener(request, timeout):
        calls["count"] += 1
        raise HTTPError(
            request.full_url,
            401,
            "unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"denied"}'),
        )

    client = OutlierApiClient(storage_state=storage_state(), opener=opener, max_retries=3)
    try:
        client.fetch_json("/test")
    except AuthRequiredError:
        pass
    else:
        raise AssertionError("Expected AuthRequiredError")
    assert calls["count"] == 1


def test_fetch_json_403_then_success_is_retried(monkeypatch):
    monkeypatch.setattr("outlier_scrapers.api.time.sleep", lambda _: None)
    calls = {"count": 0}

    def opener(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError(
                request.full_url,
                403,
                "forbidden",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"temporary block"}'),
            )
        return FakeResponse(b'{"ok": true}')

    client = OutlierApiClient(storage_state=storage_state(), opener=opener, max_retries=2)
    assert client.fetch_json("/test") == {"ok": True}
    assert calls["count"] == 2


def test_fetch_json_persistent_403_raises_outlier_api_error_not_auth_required(monkeypatch):
    monkeypatch.setattr("outlier_scrapers.api.time.sleep", lambda _: None)
    calls = {"count": 0}

    def opener(request, timeout):
        calls["count"] += 1
        raise HTTPError(
            request.full_url,
            403,
            "forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"temporary block"}'),
        )

    client = OutlierApiClient(storage_state=storage_state(), opener=opener, max_retries=2)
    try:
        client.fetch_json("/test")
    except AuthRequiredError as exc:
        raise AssertionError("Expected OutlierApiError") from exc
    except OutlierApiError as exc:
        text = str(exc)
    else:
        raise AssertionError("Expected OutlierApiError")
    assert calls["count"] == 2
    assert "HTTP 403" in text


def test_fetch_json_auth_error_redacts_response_body_values():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "unauthorized",
            hdrs=None,
            fp=io.BytesIO(
                b'{"message":"denied","token":"secret-token","bookOdds":{"HR":{"odds":-110}}}'
            ),
        )

    client = OutlierApiClient(storage_state=storage_state(), opener=opener, max_retries=1)
    try:
        client.fetch_json("/test")
    except AuthRequiredError as exc:
        text = str(exc)
    else:
        raise AssertionError("Expected AuthRequiredError")
    assert "secret-token" not in text
    assert "-110" not in text
    assert "body_shape" in text


def test_fetch_json_non_retryable_status_not_retried():
    calls = {"count": 0}

    def opener(request, timeout):
        calls["count"] += 1
        raise HTTPError(
            request.full_url,
            404,
            "not found",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"missing"}'),
        )

    client = OutlierApiClient(storage_state=storage_state(), opener=opener, max_retries=3)
    try:
        client.fetch_json("/test")
    except OutlierApiError:
        pass
    else:
        raise AssertionError("Expected OutlierApiError")
    assert calls["count"] == 1


def test_is_props_url_is_league_parameterized():
    assert is_props_url("https://app.outlier.bet/MLB/props", "MLB")
    assert is_props_url("https://app.outlier.bet/WNBA/props", "WNBA")
    assert not is_props_url("https://app.outlier.bet/NBA/props", "MLB")


def test_shape_summary_redacts_nested_book_odds_and_tokens():
    payload = {
        "token": "secret-token",
        "props": [
            {
                "outcome": {
                    "bookOdds": {"HARD_ROCK": {"odds": -110}},
                    "marketId": "market-1",
                }
            }
        ],
    }
    summary = shape_summary(payload, max_depth=5)
    assert summary["token"] == REDACTED
    assert summary["props"]["sample"]["outcome"]["bookOdds"] == REDACTED


def test_storage_state_prefers_fresh_session_dir_over_legacy(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "CONFIG_DIR", tmp_path / "config")
    paths_mod.session_dir().mkdir(parents=True)
    paths_mod.legacy_session_file().write_text('{"source":"legacy"}', encoding="utf-8")
    paths_mod.storage_state_file().write_text('{"source":"fresh"}', encoding="utf-8")

    assert load_storage_state()["source"] == "fresh"


def test_saved_api_request_headers_strip_authorization_and_cookie(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "CONFIG_DIR", tmp_path / "config")
    write_saved_api_request_headers(
        {
            "Authorization": "Bearer secret",
            "Cookie": "sid=secret",
            "X-Test": "kept",
        }
    )
    saved = paths_mod.api_request_headers_file().read_text(encoding="utf-8")
    assert "secret" not in saved
    assert "Authorization" not in saved
    assert "Cookie" not in saved
    assert "X-Test" in saved
