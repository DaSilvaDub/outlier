"""Empirical Challenger stress-test suite for outlier_nfl (Milestone 1).

Tests:
1. API network resilience: rapid 429 bursts, 500/502/503/504 errors, corrupted gzip bytes,
   malformed JSON with unescaped control characters, non-dict payloads, fast-fail 401/404.
2. Team normalization: all 32 NFL franchises, extreme casing, punctuation, nicknames,
   historical cities/names, invalid/adversarial inputs, get_team_info, get_team_display_name.
3. Pagination robustness: repeated signatures, empty pages, token cycle detection,
   numbered pagination limits, max_pages ceiling, auth propagation, mid-stream errors.
4. Market taxonomy & scope detection: canonical proposition aliases, market categorizers,
   scope detector across quarters and halves.
5. Models & Schema: dataclass immutability (frozen), validation gates for schedules, markets,
   game lines, player props.
6. Utils: safe_write_json streaming, safe_read_json resilience, to_eastern_date timezone handling,
   format_signed_line formatting.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock
import urllib.error
import pytest

from outlier_nfl.api import (
    AuthRequiredError,
    NotFoundError,
    OutlierNflApiClient,
    OutlierNflApiError,
    RateLimitError,
    RetryPolicy,
)
from outlier_nfl.config import (
    NFL_TEAMS,
    detect_scope,
    get_team_display_name,
    get_team_info,
    is_game_total,
    is_moneyline,
    is_spread,
    is_team_total,
    normalize_market,
    normalize_team,
)
from outlier_nfl.models import (
    BookPrice,
    NflGameLine,
    NflPlayerProp,
)
from outlier_nfl.schema import (
    validate_game_line_record,
    validate_schedule_payload,
)
from outlier_nfl.utils import (
    format_signed_line,
    safe_read_json,
    safe_write_json,
    to_eastern_date,
)


# =============================================================================
# 1. API Network Resilience & Error Handling
# =============================================================================


def _make_http_error(code: int, msg: str = "Error", url: str = "https://api.outlier.bet/test") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url=url, code=code, msg=msg, hdrs={}, fp=None)


def _make_success_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    return resp


def test_api_recovers_from_429_burst_before_max_retries():
    """Client handles 2 successive 429s and succeeds on attempt 3."""
    err_429_1 = _make_http_error(429, "Too Many Requests")
    err_429_2 = _make_http_error(429, "Too Many Requests")
    success_resp = _make_success_response({"status": "ok", "events": []})

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [err_429_1, err_429_2, success_resp]

    policy = RetryPolicy(max_retries=4, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    result = client.fetch_json("/sportsdata/leagues/NFL/schedule")
    assert result == {"status": "ok", "events": []}
    assert mock_opener.call_count == 3


def test_api_raises_ratelimiterror_when_429_persists():
    """Client exhausts retries on persistent 429 and raises RateLimitError."""
    err_429 = _make_http_error(429, "Too Many Requests")

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [err_429, err_429, err_429]

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    with pytest.raises(RateLimitError) as exc_info:
        client.fetch_json("/sportsdata/leagues/NFL/schedule")
    assert "429" in str(exc_info.value)
    assert mock_opener.call_count == 3


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_api_retries_and_recovers_on_server_errors(status_code: int):
    """Client retries on transient 50x errors and returns payload on recovery."""
    err_50x = _make_http_error(status_code, f"Server Error {status_code}")
    success_resp = _make_success_response({"events": [{"eventId": "kc-bal"}]})

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [err_50x, success_resp]

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    result = client.fetch_json("/sportsdata/leagues/NFL/schedule")
    assert len(result["events"]) == 1
    assert mock_opener.call_count == 2


def test_api_raises_on_persistent_503():
    """Client raises OutlierNflApiError when 503 persists beyond max_retries."""
    err_503 = _make_http_error(503, "Service Unavailable")

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = [err_503, err_503, err_503]

    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/sportsdata/leagues/NFL/schedule")
    assert "503" in str(exc_info.value)
    assert mock_opener.call_count == 3


def test_api_fails_fast_on_404_not_found():
    """Client raises NotFoundError immediately on 404 without retrying."""
    err_404 = _make_http_error(404, "Not Found")

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = err_404

    policy = RetryPolicy(max_retries=4, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    with pytest.raises(NotFoundError) as exc_info:
        client.fetch_json("/sportsdata/events/non-existent/markets")
    assert "404" in str(exc_info.value)
    assert mock_opener.call_count == 1


def test_api_fails_fast_on_400_bad_request():
    """Client raises OutlierNflApiError immediately on 400 without retrying."""
    err_400 = _make_http_error(400, "Bad Request")

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.side_effect = err_400

    policy = RetryPolicy(max_retries=4, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener, retry_policy=policy)

    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/sportsdata/bad-url")
    assert "400" in str(exc_info.value)
    assert mock_opener.call_count == 1


def test_api_corrupted_gzip_handling():
    """Client handles corrupted gzip header or payload."""
    import zlib
    # When zlib.error or BadGzipFile occurs, verify how the client behaves
    corrupted_bytes = b"\x1f\x8b\x08\x00corrupted_garbage_bytes_here"
    corrupted_resp = MagicMock()
    corrupted_resp.read.return_value = corrupted_bytes

    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = corrupted_resp

    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener)
    # Documents that zlib.error is raised because zlib.error is not an OSError
    with pytest.raises((zlib.error, gzip.BadGzipFile, OutlierNflApiError)):
        client.fetch_json("/sportsdata/leagues/NFL/schedule")


def test_api_control_characters_in_raw_json():
    """Client correctly parses JSON containing unescaped ASCII control characters."""
    # Control chars: tab (\t), newline (\n), carriage return (\r), null (\0), backspace (\b)
    raw_bytes = (
        b'{"matchup": "KC @ BAL", "notes": "Line shifted \t overnight \r\n with \x00 null and \x08 bs", "count": 1}'
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_bytes
    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = mock_resp

    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener)
    result = client.fetch_json("/test")
    assert result["matchup"] == "KC @ BAL"
    assert result["count"] == 1
    assert "\t" in result["notes"]


def test_api_rejects_non_dict_json_root():
    """Client raises OutlierNflApiError if endpoint returns a JSON array instead of an object."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"item": 1}]).encode("utf-8")
    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = mock_resp

    client = OutlierNflApiClient(bearer_token="test_token", opener=mock_opener)
    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/test")
    assert "Unexpected non-object response" in str(exc_info.value)


# =============================================================================
# 2. 32 NFL Franchises Normalization Stress Tests
# =============================================================================

ALL_32_NFL_CODES = [
    # AFC
    "BUF", "MIA", "NE", "NYJ",
    "BAL", "CIN", "CLE", "PIT",
    "HOU", "IND", "JAX", "TEN",
    "DEN", "KC", "LV", "LAC",
    # NFC
    "DAL", "NYG", "PHI", "WAS",
    "CHI", "DET", "GB", "MIN",
    "ATL", "CAR", "NO", "TB",
    "ARI", "LAR", "SF", "SEA",
]


def test_all_32_canonical_teams_exist_in_registry():
    """Every one of the 32 NFL franchises must exist in the NFL_TEAMS registry."""
    assert len(NFL_TEAMS) == 32
    for code in ALL_32_NFL_CODES:
        assert code in NFL_TEAMS
        info = NFL_TEAMS[code]
        assert info.code == code
        assert info.conference in ("AFC", "NFC")
        assert info.division in ("East", "North", "South", "West")
        assert len(info.name) > 0
        assert len(info.city) > 0
        assert len(info.nickname) > 0


@pytest.mark.parametrize("code", ALL_32_NFL_CODES)
def test_normalize_team_for_all_32_full_names(code: str):
    """normalize_team must correctly resolve the full official name for every NFL franchise."""
    info = NFL_TEAMS[code]
    assert normalize_team(info.name) == code
    # Test uppercase and lowercase
    assert normalize_team(info.name.upper()) == code
    assert normalize_team(info.name.lower()) == code


@pytest.mark.parametrize("code", ALL_32_NFL_CODES)
def test_normalize_team_for_all_32_nicknames(code: str):
    """normalize_team must correctly resolve the nickname for every NFL franchise."""
    info = NFL_TEAMS[code]
    assert normalize_team(info.nickname) == code
    assert normalize_team(info.nickname.lower()) == code


def test_normalize_team_extreme_casing_and_punctuation():
    """normalize_team handles wild whitespace, punctuation, and mixed casing for known aliases."""
    assert normalize_team("  k.C.!!  ") == "KC"
    assert normalize_team("SAN-FRANCISCO---49ERS") == "SF"
    assert normalize_team("\t\tGreen   Bay \n Packers \r\n") == "GB"
    assert normalize_team("t-a-m-p-a   b-a-y   b-u-c-c-a-n-e-e-r-s") == "TB"
    assert normalize_team("L.A. Chargers") == "LAC"
    assert normalize_team("L.A. Rams") == "LAR"


def test_normalize_team_code_plus_nickname_adversarial():
    """Verify standard sports feed format 'Code + Nickname' or 'Abbrev + Nickname' for all franchises."""
    target_aliases = {
        # Challenger 1's original 10 reproduced cases
        "KC Chiefs": "KC",
        "SF 49ers": "SF",
        "TB Bucs": "TB",
        "NE Patriots": "NE",
        "PIT Steelers": "PIT",
        "BAL Ravens": "BAL",
        "GB Packers": "GB",
        "NY Jets": "NYJ",
        "NY Giants": "NYG",
        "PHI Eagles": "PHI",
        # Additional coverage for multi-word & alternate code franchises
        "BUF Bills": "BUF",
        "MIA Dolphins": "MIA",
        "CIN Bengals": "CIN",
        "CLE Browns": "CLE",
        "HOU Texans": "HOU",
        "IND Colts": "IND",
        "JAX Jaguars": "JAX",
        "TEN Titans": "TEN",
        "DEN Broncos": "DEN",
        "LV Raiders": "LV",
        "LAC Chargers": "LAC",
        "DAL Cowboys": "DAL",
        "WAS Commanders": "WAS",
        "CHI Bears": "CHI",
        "DET Lions": "DET",
        "MIN Vikings": "MIN",
        "ATL Falcons": "ATL",
        "CAR Panthers": "CAR",
        "NO Saints": "NO",
        "ARI Cardinals": "ARI",
        "LAR Rams": "LAR",
        "SEA Seahawks": "SEA",
    }
    for raw_name, expected_code in target_aliases.items():
        assert normalize_team(raw_name) == expected_code, f"Failed to normalize {raw_name!r} to {expected_code}"


def test_api_uncaught_decompression_error_adversarial():
    """Verify that truncated gzip stream raises OutlierNflApiError after retries."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"\x1f\x8b\x08\x00corrupted"
    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = mock_resp

    policy = RetryPolicy(max_retries=2, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test", opener=mock_opener, retry_policy=policy)
    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/test")
    assert "Transport/stream error" in str(exc_info.value) or "attempts" in str(exc_info.value)


def test_api_uncaught_json_decode_error_adversarial():
    """Verify that non-JSON body (e.g. 200 OK with HTML proxy error) raises OutlierNflApiError after retries."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
    mock_opener = MagicMock()
    mock_opener.return_value.__enter__.return_value = mock_resp

    policy = RetryPolicy(max_retries=2, base_delay_seconds=0.001, max_delay_seconds=0.01)
    client = OutlierNflApiClient(bearer_token="test", opener=mock_opener, retry_policy=policy)
    with pytest.raises(OutlierNflApiError) as exc_info:
        client.fetch_json("/test")
    assert "Invalid JSON response" in str(exc_info.value)






def test_normalize_team_historical_and_alternate_names():
    """normalize_team maps historical franchise locations and standard abbreviations."""
    # Raiders: Oakland / Las Vegas
    assert normalize_team("Oakland Raiders") == "LV"
    assert normalize_team("Oakland") == "LV"
    assert normalize_team("OAK") == "LV"
    assert normalize_team("LVR") == "LV"

    # Chargers: San Diego / LA
    assert normalize_team("San Diego Chargers") == "LAC"
    assert normalize_team("San Diego") == "LAC"
    assert normalize_team("SD") == "LAC"

    # Rams: St. Louis / LA
    assert normalize_team("St. Louis Rams") == "LAR"
    assert normalize_team("St. Louis") == "LAR"
    assert normalize_team("STL") == "LAR"

    # Washington: Commanders / Football Team / Redskins
    assert normalize_team("Washington Football Team") == "WAS"
    assert normalize_team("Football Team") == "WAS"
    assert normalize_team("Washington Redskins") == "WAS"
    assert normalize_team("Redskins") == "WAS"
    assert normalize_team("WSH") == "WAS"

    # Short city tokens
    assert normalize_team("GNB") == "GB"
    assert normalize_team("KAN") == "KC"
    assert normalize_team("NOR") == "NO"
    assert normalize_team("NWE") == "NE"
    assert normalize_team("SFO") == "SF"
    assert normalize_team("TAM") == "TB"
    assert normalize_team("AZ") == "ARI"
    assert normalize_team("JAC") == "JAX"


def test_normalize_team_adversarial_and_invalid_inputs():
    """normalize_team gracefully returns None on non-NFL or invalid inputs."""
    assert normalize_team("Alabama Crimson Tide") is None
    assert normalize_team("Los Angeles Lakers") is None
    assert normalize_team("New York Yankees") is None
    assert normalize_team("Real Madrid") is None
    assert normalize_team("") is None
    assert normalize_team("   ") is None
    assert normalize_team(None) is None
    assert normalize_team(12345) is None
    assert normalize_team({"team": "Chiefs"}) is None


def test_get_team_display_name_and_info():
    """get_team_display_name and get_team_info function correctly with fallbacks."""
    assert get_team_display_name("KC") == "Kansas City Chiefs"
    assert get_team_display_name("chiefs") == "Kansas City Chiefs"
    assert get_team_display_name("non_existent_team") == "non_existent_team"

    info = get_team_info("BAL")
    assert info is not None
    assert info.name == "Baltimore Ravens"
    assert info.city == "Baltimore"
    assert info.division == "North"

    assert get_team_info("NOT_A_TEAM") is None


# =============================================================================
# 3. Pagination Robustness & Loop Safety
# =============================================================================


def test_pagination_halts_on_identical_records_signature():
    """Pagination stops when consecutive pages return identical items (signature match)."""
    page_1 = {
        "props": [
            {"outcome": {"outcomeId": "p1", "marketId": "m1"}},
            {"outcome": {"outcomeId": "p2", "marketId": "m2"}},
        ],
        "_page": {"nextPageToken": "tok_page_2", "pageNumber": 1, "pages": 5},
    }
    # Page 2 returns same items despite new page token
    page_2 = {
        "props": [
            {"outcome": {"outcomeId": "p1", "marketId": "m1"}},
            {"outcome": {"outcomeId": "p2", "marketId": "m2"}},
        ],
        "_page": {"nextPageToken": "tok_page_3", "pageNumber": 2, "pages": 5},
    }

    client = OutlierNflApiClient(bearer_token="test_token")
    # Return page_1 on initial call, and page_2 for any subsequent calls
    client.fetch_json = MagicMock(side_effect=lambda path, params=None: page_1 if not params else page_2)  # type: ignore[method-assign]

    result = client.fetch_player_props(max_pages=10)
    # Merged records should only contain page 1 items because page 2 signature matched last_sig
    assert len(result["props"]) == 2


def test_pagination_halts_on_token_cycle():
    """Pagination stops when next token forms a cycle (tok_A -> tok_B -> tok_A)."""
    p1 = {
        "props": [{"outcome": {"outcomeId": "p1"}}],
        "_page": {"nextPageToken": "tok_B", "pageNumber": 1},
    }
    p2 = {
        "props": [{"outcome": {"outcomeId": "p2"}}],
        "_page": {"nextPageToken": "tok_A", "pageNumber": 2},
    }
    p3 = {
        "props": [{"outcome": {"outcomeId": "p3"}}],
        "_page": {"nextPageToken": "tok_B", "pageNumber": 3},  # Cycle back to tok_B!
    }

    client = OutlierNflApiClient(bearer_token="test_token")
    def mock_fetch(path, params=None):
        if not params:
            return p1
        tok = (params or {}).get("pageToken") or (params or {}).get("nextPageToken")
        if tok == "tok_B":
            return p2
        if tok == "tok_A":
            return p3
        return {"props": []}

    client.fetch_json = MagicMock(side_effect=mock_fetch)  # type: ignore[method-assign]

    result = client.fetch_player_props(max_pages=10)
    # Should stop on third page when tok_B is recognized as already seen
    assert len(result["props"]) == 3


def test_pagination_halts_on_empty_initial_page():
    """Pagination stops immediately if the first page contains an empty record list."""
    empty_page = {"props": [], "_page": {"nextPageToken": "tok_2", "pages": 3}}

    client = OutlierNflApiClient(bearer_token="test_token")
    client.fetch_json = MagicMock(return_value=empty_page)  # type: ignore[method-assign]

    result = client.fetch_player_props(max_pages=5)
    assert result["props"] == []


def test_pagination_respects_max_pages_limit():
    """Pagination never exceeds the max_pages ceiling even if infinite pages exist."""
    def make_page(num: int):
        return {
            "props": [{"outcome": {"outcomeId": f"item_{num}"}}],
            "_page": {"nextPageToken": f"token_{num + 1}", "pageNumber": num, "pages": 100},
        }

    client = OutlierNflApiClient(bearer_token="test_token")
    client.fetch_json = MagicMock(side_effect=[make_page(i) for i in range(1, 20)])  # type: ignore[method-assign]

    max_pages = 4
    result = client.fetch_player_props(max_pages=max_pages)
    assert len(result["props"]) == max_pages
    assert client.fetch_json.call_count == max_pages


def test_pagination_propagates_auth_required_error():
    """Pagination immediately propagates AuthRequiredError from sub-pages."""
    p1 = {
        "props": [{"outcome": {"outcomeId": "p1"}}],
        "_page": {"nextPageToken": "tok_2", "pageNumber": 1},
    }

    client = OutlierNflApiClient(bearer_token="test_token")
    client.fetch_json = MagicMock(side_effect=[p1, AuthRequiredError("401 Unauthorized")])  # type: ignore[method-assign]

    with pytest.raises(AuthRequiredError):
        client.fetch_player_props(max_pages=5)


def test_pagination_recovers_gracefully_on_transient_error_on_later_page():
    """If a subsequent page throws OutlierNflApiError, pagination returns accumulated records."""
    p1 = {
        "props": [{"outcome": {"outcomeId": "p1"}}],
        "_page": {"nextPageToken": "tok_2", "pageNumber": 1},
    }

    client = OutlierNflApiClient(bearer_token="test_token")
    def mock_fetch(path, params=None):
        if not params:
            return p1
        raise OutlierNflApiError("500 Internal Error")

    client.fetch_json = MagicMock(side_effect=mock_fetch)  # type: ignore[method-assign]

    result = client.fetch_player_props(max_pages=5)
    assert len(result["props"]) == 1
    assert result["props"][0]["outcome"]["outcomeId"] == "p1"


# =============================================================================
# 4. Market Taxonomy, Props & Scope Detection
# =============================================================================


def test_market_taxonomy_canonical_mappings():
    """Verify all key NFL betting markets map to standard canonical codes."""
    assert normalize_market("SPREAD") == "SPREAD"
    assert normalize_market("Point Spread") == "SPREAD"
    assert normalize_market("Handicap") == "SPREAD"

    assert normalize_market("TOTAL") == "TOTAL"
    assert normalize_market("Game Points") == "TOTAL"
    assert normalize_market("Over/Under") == "TOTAL"

    assert normalize_market("MONEYLINE") == "ML"
    assert normalize_market("ML") == "ML"
    assert normalize_market("3-Way Moneyline") == "ML_3WAY"

    assert normalize_market("POINTS") == "POINTS"
    assert normalize_market("Team Total") == "POINTS"
    assert normalize_market("Team Total Points") == "POINTS"
    assert normalize_market("Offensive Yards") == "OFF_YDS"

    # Player props
    assert normalize_market("Passing Yards") == "PASS_YDS"
    assert normalize_market("Passing Touchdowns") == "PASS_TD"
    assert normalize_market("Pass Completions") == "PASS_COMP"
    assert normalize_market("Pass Attempts") == "PASS_ATT"
    assert normalize_market("Interceptions") == "INT"
    assert normalize_market("Longest Completion") == "LONG_PASS"

    assert normalize_market("Rushing Yards") == "RUSH_YDS"
    assert normalize_market("Carries") == "RUSH_ATT"
    assert normalize_market("Longest Rush") == "LONG_RUSH"

    assert normalize_market("Receiving Yards") == "REC_YDS"
    assert normalize_market("Receptions") == "REC"
    assert normalize_market("Longest Reception") == "LONG_REC"

    assert normalize_market("Anytime Touchdown") == "ANYTIME_TD"
    assert normalize_market("First Touchdown") == "FIRST_TD"
    assert normalize_market("Field Goals Made") == "FGM"
    assert normalize_market("Kicking Points") == "KICK_PTS"
    assert normalize_market("Total Tackles") == "TKL_AST"
    assert normalize_market("Sacks") == "SACKS"


def test_market_classification_helpers():
    """Test is_spread, is_game_total, is_team_total, is_moneyline helpers."""
    assert is_spread("SPREAD")
    assert is_spread("PointSpread")
    assert not is_spread("TOTAL")

    assert is_game_total("TOTAL")
    assert is_game_total("OverUnder")
    assert not is_game_total("SPREAD")

    assert is_team_total("POINTS")
    assert is_team_total("TeamTotal")
    assert not is_team_total("SPREAD")

    assert is_moneyline("MONEYLINE")
    assert is_moneyline("ML")
    assert not is_moneyline("TOTAL")


def test_detect_scope_quarters_and_halves():
    """detect_scope correctly differentiates full_game from halves and quarters."""
    assert detect_scope(None) == "full_game"
    assert detect_scope("") == "full_game"
    assert detect_scope("Full Game") == "full_game"

    assert detect_scope("1st Half") == "first_half"
    assert detect_scope("First Half") == "first_half"
    assert detect_scope("1H") == "first_half"

    assert detect_scope("2nd Half") == "second_half"
    assert detect_scope("Second Half") == "second_half"
    assert detect_scope("2H") == "second_half"

    assert detect_scope("1st Quarter") == "first_quarter"
    assert detect_scope("1Q") == "first_quarter"

    assert detect_scope("2nd Quarter") == "second_quarter"
    assert detect_scope("2Q") == "second_quarter"

    assert detect_scope("3rd Quarter") == "third_quarter"
    assert detect_scope("3Q") == "third_quarter"

    assert detect_scope("4th Quarter") == "fourth_quarter"
    assert detect_scope("4Q") == "fourth_quarter"


# =============================================================================
# 5. Schema Validation & Domain Models
# =============================================================================


def test_domain_models_immutability():
    """Domain dataclasses must be frozen to prevent accidental mutation."""
    book = BookPrice(book="draftkings", odds=-110, odds_raw="-110", decimal=1.909)
    with pytest.raises(FrozenInstanceError):
        book.odds = 100  # type: ignore[misc]

    line = NflGameLine(
        event_id="evt-1",
        event_starts_at="2026-09-10T20:20:00Z",
        matchup="KC @ BAL",
        home_team="KC",
        away_team="BAL",
        market_type="GAMELINE",
        market="SPREAD",
        proposition="SPREAD",
        position="HOME",
        line=-3.5,
        signed_line="-3.5",
        selection="KC -3.5",
        team="KC",
        books=[book],
        best_odds=-110,
        implied_probability=52.381,
    )
    with pytest.raises(FrozenInstanceError):
        line.best_odds = -105  # type: ignore[misc]

    prop = NflPlayerProp(
        event_id="evt-1",
        event_starts_at="2026-09-10T20:20:00Z",
        matchup="KC @ BAL",
        team="KC",
        opponent="BAL",
        player_name="Patrick Mahomes",
        player_id="p-15",
        market="PASS_YDS",
        market_raw="Passing Yards",
        position="OVER",
        line=275.5,
        books=[book],
        best_odds=-110,
        implied_probability=52.381,
    )
    with pytest.raises(FrozenInstanceError):
        prop.line = 280.5  # type: ignore[misc]


def test_schema_validates_schedule_payload():
    """validate_schedule_payload detects missing or corrupt structure."""
    assert validate_schedule_payload("not a dict") != []
    assert validate_schedule_payload({}) != []
    assert validate_schedule_payload({"events": "not a list"}) != []

    invalid_event = {"events": [{"id": "e1"}]}  # missing home and away
    errors = validate_schedule_payload(invalid_event)
    assert any("missing valid 'home'" in err for err in errors)

    valid_payload = {
        "events": [
            {
                "eventId": "evt-1",
                "home": {"name": "Chiefs"},
                "away": {"name": "Ravens"},
            }
        ]
    }
    assert validate_schedule_payload(valid_payload) == []


def test_schema_validates_game_line_record():
    """validate_game_line_record enforces valid market_type, position, and probability."""
    bad_record = {
        "event_id": "evt-1",
        "matchup": "KC @ BAL",
        "home_team": "KC",
        "away_team": "BAL",
        "market_type": "INVALID_TYPE",
        "market": "SPREAD",
        "position": "INVALID_POS",
        "books": [],
        "implied_probability": 150.0,  # Invalid: >100%
    }
    errs = validate_game_line_record(bad_record)
    assert any("market_type" in e for e in errs)
    assert any("position" in e for e in errs)
    assert any("implied_probability" in e for e in errs)


# =============================================================================
# 6. Resilient File I/O & Date Helpers
# =============================================================================


def test_safe_write_and_read_json(tmp_path: Path):
    """safe_write_json and safe_read_json roundtrip data and handle directories."""
    data_dir = tmp_path / "deep" / "nested" / "dir"
    target_file = data_dir / "test_data.json"

    payload = {"slate": "2026-09-13", "active": True, "games_count": 14}
    safe_write_json(target_file, payload)

    assert target_file.exists()
    loaded = safe_read_json(target_file)
    assert loaded == payload

    # safe_read_json returns default on missing file
    assert safe_read_json(tmp_path / "non_existent.json", default={"fallback": 1}) == {"fallback": 1}


def test_to_eastern_date_cross_midnight_kickoffs():
    """to_eastern_date correctly resolves NFL night game kickoffs across midnight."""
    # Thursday Night Football: 8:20 PM Eastern on Thursday Sept 10 is 00:20 UTC on Friday Sept 11
    tnf_utc = "2026-09-11T00:20:00Z"
    assert to_eastern_date(tnf_utc) == "2026-09-10"

    # Sunday 1:00 PM Eastern: 17:00 UTC on Sunday Sept 13
    sunday_early_utc = "2026-09-13T17:00:00Z"
    assert to_eastern_date(sunday_early_utc) == "2026-09-13"

    # Sunday Night Football: 8:20 PM Eastern on Sunday Sept 13 is 00:20 UTC on Monday Sept 14
    snf_utc = "2026-09-14T00:20:00Z"
    assert to_eastern_date(snf_utc) == "2026-09-13"

    # Monday Night Football: 8:15 PM Eastern on Monday Sept 14 is 00:15 UTC on Tuesday Sept 15
    mnf_utc = "2026-09-15T00:15:00Z"
    assert to_eastern_date(mnf_utc) == "2026-09-14"

    # Edge cases
    assert to_eastern_date(None) is None
    assert to_eastern_date("") is None
    assert to_eastern_date("not-a-date") is None


def test_format_signed_line_values():
    """format_signed_line formats spreads and totals with explicit signs."""
    assert format_signed_line(-3.5) == "-3.5"
    assert format_signed_line(3.5) == "+3.5"
    assert format_signed_line(7) == "+7"
    assert format_signed_line(-7) == "-7"
    assert format_signed_line(0) == "PK"
    assert format_signed_line(0.0) == "PK"
    assert format_signed_line(None) is None
