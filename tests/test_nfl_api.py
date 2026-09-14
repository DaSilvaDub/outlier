from email.message import Message
import gzip
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from outlier_nfl.api import (
    AuthRequiredError,
    OutlierNflApiClient,
    OutlierNflApiError,
    RetryPolicy,
)
from outlier_nfl.constants import (
    API_BASE_URL,
    EVENT_MARKETS_ENDPOINT,
    EVENT_MATCHUP_ENDPOINT,
    LEAGUE_TOKEN,
    PLAYER_PROPS_ENDPOINT,
    RETRYABLE_STATUS_CODES,
    SCHEDULE_ENDPOINT,
    TEAM_INJURIES_ENDPOINT,
    MAX_RETRIES,
)


def test_constants_and_default_configuration():
    assert LEAGUE_TOKEN == "NFL"
    assert "https://api.outlier.bet" in API_BASE_URL
    assert 403 in RETRYABLE_STATUS_CODES
    assert 429 in RETRYABLE_STATUS_CODES
    assert 500 in RETRYABLE_STATUS_CODES
    assert 502 in RETRYABLE_STATUS_CODES
    assert 503 in RETRYABLE_STATUS_CODES
    assert 504 in RETRYABLE_STATUS_CODES
    assert MAX_RETRIES >= 3


def test_api_client_url_construction():
    client = OutlierNflApiClient(bearer_token="mock_token")
    assert client.url_for(SCHEDULE_ENDPOINT) == f"{API_BASE_URL}/sportsdata/leagues/{LEAGUE_TOKEN}/schedule"
    assert client.url_for(PLAYER_PROPS_ENDPOINT) == f"{API_BASE_URL}/sportsdata/leagues/{LEAGUE_TOKEN}/playerProps"

    markets_url = client.url_for(
        EVENT_MARKETS_ENDPOINT.format(event_id="evt-123"),
        params={"marketType": "GAMELINE"},
    )
    assert markets_url == f"{API_BASE_URL}/sportsdata/events/evt-123/markets?marketType=GAMELINE"

    tt_url = client.url_for(
        EVENT_MARKETS_ENDPOINT.format(event_id="evt-123"),
        params={"marketType": "TEAM_PROP"},
    )
    assert tt_url == f"{API_BASE_URL}/sportsdata/events/evt-123/markets?marketType=TEAM_PROP"

    matchup_url = client.url_for(EVENT_MATCHUP_ENDPOINT.format(event_id="evt-123"))
    assert matchup_url == f"{API_BASE_URL}/sportsdata/events/evt-123/matchup"

    injuries_url = client.url_for(TEAM_INJURIES_ENDPOINT.format(team_id="kc-chiefs"))
    assert injuries_url == f"{API_BASE_URL}/sportsdata/leagues/{LEAGUE_TOKEN}/teams/kc-chiefs/injuries"


def test_api_client_headers():
    client = OutlierNflApiClient(bearer_token="test_jwt_secret_token")
    headers = client.build_headers()
    assert headers["Authorization"] == "Bearer test_jwt_secret_token"
    assert headers["Accept"] == "application/json"
    assert "User-Agent" in headers
    assert headers["Origin"] == "https://app.outlier.bet"


def test_session_file_discovery(tmp_path: Path):
    session_file = tmp_path / "storage_state.json"
    session_data = {
        "origins": [
            {
                "origin": "https://app.outlier.bet",
                "localStorage": [
                    {"name": "authToken", "value": "extracted_session_token_1234567890"}
                ]
            }
        ]
    }
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session_data, f)

    client = OutlierNflApiClient(session_path=session_file)
    assert client.bearer_token == "extracted_session_token_1234567890"


def test_gzip_response_decompression():
    raw_payload = {"events": [{"eventId": "kc-bal", "status": "scheduled"}]}
    json_bytes = json.dumps(raw_payload).encode("utf-8")
    compressed = gzip.compress(json_bytes)

    # Verify gzip magic bytes
    assert compressed[:2] == b"\x1f\x8b"

    mock_resp = MagicMock()
    mock_resp.read.return_value = compressed
    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = mock_resp

    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener)
    decompressed = client.fetch_json("/test/endpoint")
    assert decompressed == raw_payload


def test_json_strict_false_tolerance():
    # Raw JSON with an unescaped control character (e.g. tab inside string value)
    raw_json_with_control_char = b'{"label": "Patrick Mahomes \t passing yards", "val": 275}'

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_json_with_control_char
    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = mock_resp

    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener)
    parsed = client.fetch_json("/test/endpoint")
    assert parsed["label"] == "Patrick Mahomes \t passing yards"
    assert parsed["val"] == 275


def test_retry_policy_exponential_backoff():
    policy = RetryPolicy(max_retries=4, base_delay_seconds=0.1, max_delay_seconds=1.0)
    delays = [policy.delay_for(attempt=i) for i in range(1, 5)]

    # Delays must be positive and bounded by max_delay_seconds
    for d in delays:
        assert 0.0 < d <= 1.0
    # Expected exponential growth trend
    assert delays[-1] > delays[0]


def test_client_retries_transient_http_errors():
    err_429 = urllib.error.HTTPError(
        url="https://api.outlier.bet/test",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=None,
    )

    mock_success_resp = MagicMock()
    mock_success_resp.read.return_value = json.dumps({"events": []}).encode("utf-8")

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [err_429, mock_success_resp]

    retry_policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=retry_policy)

    result = client.fetch_json("https://api.outlier.bet/test")
    assert result == {"events": []}
    assert mock_opener.call_count == 2


def test_client_fails_fast_on_401():
    err_401 = urllib.error.HTTPError(
        url="https://api.outlier.bet/test",
        code=401,
        msg="Unauthorized",
        hdrs=Message(),
        fp=None,
    )

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = err_401

    retry_policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="expired_token", opener=mock_opener, retry_policy=retry_policy)

    with pytest.raises(AuthRequiredError) as exc_info:
        client.fetch_json("https://api.outlier.bet/test")
    assert "401" in str(exc_info.value)
    assert mock_opener.call_count == 1  # No retries on 401


def test_pagination_merging_and_cursor():
    page_1 = {
        "props": [{"outcome": {"outcomeId": "p1"}}, {"outcome": {"outcomeId": "p2"}}],
        "_page": {"nextPageToken": "tok_page_2", "pageNumber": 1, "pages": 2, "total": 3}
    }
    page_2 = {
        "props": [{"outcome": {"outcomeId": "p3"}}],
        "_page": {"nextPageToken": None, "pageNumber": 2, "pages": 2, "total": 3}
    }

    client = OutlierNflApiClient(bearer_token="test_token")
    client.fetch_json = MagicMock(side_effect=[page_1, page_2])  # type: ignore[method-assign]

    merged = client.fetch_player_props(max_pages=5)
    assert len(merged["props"]) == 3
    assert [p["outcome"]["outcomeId"] for p in merged["props"]] == ["p1", "p2", "p3"]


def test_pagination_loop_guard_on_duplicate_token():
    page_stuck = {
        "props": [{"outcome": {"outcomeId": "p1"}}],
        "_page": {"nextPageToken": "stuck_token", "pageNumber": 1}
    }

    client = OutlierNflApiClient(bearer_token="test_token")
    # API returns identical token endlessly
    client.fetch_json = MagicMock(return_value=page_stuck)  # type: ignore[method-assign]

    merged = client.fetch_player_props(max_pages=10)
    # Should break immediately on second cycle detecting duplicate cursor
    assert len(merged["props"]) == 1


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
