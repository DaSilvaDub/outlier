"""Offline Cognito renewal tests. All credentials are unsigned placeholders."""
import base64
import json
import time
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

from outlier_nfl.api import AuthRequiredError, OutlierNflApiClient
from outlier_nfl.session import SessionRefreshError, refresh_saved_session

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_placeholder"
PREFIX = "CognitoIdentityServiceProvider.placeholderclient.placeholderuser"


def jwt(kind="access", *, future=False, issuer=ISSUER, sub="placeholderuser", client="placeholderclient"):
    claims = {"iss": issuer, "sub": sub, "token_use": kind, "exp": int(time.time()) + (3600 if future else -3600),
              "client_id" if kind == "access" else "aud": client}
    parts = [base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip("=")
             for x in ({"alg": "none"}, claims)]
    return ".".join(parts + ["placeholder-signature"])


def state():
    return {"cookies": [{"name": "theme", "value": "placeholder", "domain": "app.outlier.bet"}],
            "origins": [{"origin": "https://app.outlier.bet", "localStorage": [
                {"name": PREFIX + ".accessToken", "value": jwt()},
                {"name": PREFIX + ".idToken", "value": jwt("id")},
                {"name": PREFIX + ".refreshToken", "value": "placeholder-refresh-only"},
                {"name": "theme", "value": "dark"}]}]}


def response(payload):
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = json.dumps(payload).encode()
    return resp


def renewal():
    return {"AuthenticationResult": {"AccessToken": jwt(future=True), "IdToken": jwt("id", future=True)}}


@pytest.fixture(autouse=True)
def no_environment_credentials(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1800000000.0)
    for key in ("OUTLIER_BEARER_TOKEN", "OUTLIER_API_TOKEN", "OUTLIER_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def save(tmp_path, payload=None):
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps(payload or state()), encoding="utf-8")
    return path


@pytest.mark.parametrize("sidecar_kind", ["access", "id"])
def test_expired_saved_session_is_renewed_before_get_and_persisted(tmp_path, sidecar_kind):
    path = save(tmp_path)
    before = json.loads(path.read_text())
    sidecar = tmp_path / "api_bearer_token.txt"
    sidecar.write_text(jwt(sidecar_kind))
    opener = MagicMock(side_effect=[response(renewal()), response({"events": []}), response({})])
    client = OutlierNflApiClient(session_path=path, opener=opener)
    assert client.fetch_schedule() == {"events": []}
    refresh_req, get_req = [call.args[0] for call in opener.call_args_list]
    assert refresh_req.full_url == "https://cognito-idp.us-east-1.amazonaws.com/"
    assert json.loads(refresh_req.data) == {"AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": "placeholderclient",
                                          "AuthParameters": {"REFRESH_TOKEN": "placeholder-refresh-only"}}
    assert get_req.get_header("Authorization") == "Bearer " + jwt(future=True)
    saved = json.loads(path.read_text())
    assert saved["cookies"] == before["cookies"]
    values = {x["name"]: x["value"] for x in saved["origins"][0]["localStorage"]}
    assert values[PREFIX + ".refreshToken"] == "placeholder-refresh-only"
    assert values["theme"] == "dark"
    assert values[PREFIX + ".idToken"] == jwt("id", future=True)
    assert sidecar.read_text() == jwt(sidecar_kind, future=True)
    client.fetch_json("/second")  # no second refresh for a valid token
    assert opener.call_count == 3


def test_valid_saved_token_does_not_refresh(tmp_path):
    payload = state()
    payload["origins"][0]["localStorage"][0]["value"] = jwt(future=True)
    path = save(tmp_path, payload)
    before = path.read_bytes()
    opener = MagicMock(return_value=response({"events": []}))
    OutlierNflApiClient(session_path=path, opener=opener).fetch_schedule()
    assert opener.call_count == 1
    assert path.read_bytes() == before


def test_failed_refresh_is_redacted_and_preserves_saved_files(tmp_path, caplog):
    path = save(tmp_path)
    before = path.read_bytes()
    opener = MagicMock(side_effect=HTTPError("https://placeholder/secret", 400, "placeholder-refresh-only", {}, None))
    with pytest.raises(AuthRequiredError) as exc:
        OutlierNflApiClient(session_path=path, opener=opener).fetch_schedule()
    assert exc.value.__suppress_context__
    assert "placeholder-refresh-only" not in str(exc.value) + caplog.text
    assert path.read_bytes() == before
    assert opener.call_count == 1


@pytest.mark.parametrize("bad_result", [{}, {"AccessToken": jwt(future=True)},
    {"AccessToken": jwt(future=True, sub="someoneelse"), "IdToken": jwt("id", future=True)},
    {"AccessToken": jwt(), "IdToken": jwt("id", future=True)}])
def test_incomplete_or_mismatched_refresh_response_is_never_saved(tmp_path, bad_result):
    path = save(tmp_path)
    before = path.read_bytes()
    opener = MagicMock(return_value=response({"AuthenticationResult": bad_result}))
    with pytest.raises(SessionRefreshError):
        refresh_saved_session(path, jwt(), opener=opener, timeout=1)
    assert path.read_bytes() == before


def test_untrusted_issuer_cannot_receive_refresh_token(tmp_path):
    payload = state()
    token = jwt(issuer="https://attacker.invalid/pool")
    payload["origins"][0]["localStorage"][0]["value"] = token
    path = save(tmp_path, payload)
    opener = MagicMock()
    with pytest.raises(SessionRefreshError):
        refresh_saved_session(path, token, opener=opener, timeout=1)
    opener.assert_not_called()


def test_another_account_is_preserved_and_never_used_for_refresh(tmp_path):
    payload = state()
    other = {"name": "CognitoIdentityServiceProvider.otherclient.otheruser.refreshToken", "value": "other-refresh"}
    payload["origins"][0]["localStorage"].insert(0, other)
    path = save(tmp_path, payload)
    opener = MagicMock(return_value=response(renewal()))
    refresh_saved_session(path, jwt(), opener=opener, timeout=1)
    assert json.loads(opener.call_args.args[0].data)["AuthParameters"]["REFRESH_TOKEN"] == "placeholder-refresh-only"
    assert other in json.loads(path.read_text())["origins"][0]["localStorage"]


def test_reuses_concurrent_writers_fresh_token(tmp_path):
    path = save(tmp_path)
    client = OutlierNflApiClient(session_path=path)
    payload = state()
    payload["origins"][0]["localStorage"][0]["value"] = jwt(future=True)
    path.write_text(json.dumps(payload))
    opener = MagicMock(return_value=response({"events": []}))
    client.opener = opener
    client.fetch_schedule()
    assert opener.call_count == 1
    assert client.bearer_token == jwt(future=True)


@pytest.mark.parametrize("mode", ["bearer", "environment"])
def test_explicit_credentials_never_refresh_another_saved_identity(tmp_path, monkeypatch, mode):
    path = save(tmp_path)
    opener = MagicMock()
    args = {"bearer_token": jwt()} if mode == "bearer" else {}
    if mode == "environment":
        monkeypatch.setenv("OUTLIER_BEARER_TOKEN", jwt())
    with pytest.raises(AuthRequiredError):
        OutlierNflApiClient(session_path=path, opener=opener, **args).fetch_schedule()
    opener.assert_not_called()


def test_atomic_write_failure_preserves_original(tmp_path, monkeypatch):
    path = save(tmp_path)
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("placeholder write failure")
    monkeypatch.setattr("outlier_nfl.session.safe_write_json", fail)
    with pytest.raises(SessionRefreshError):
        refresh_saved_session(path, jwt(), opener=MagicMock(return_value=response(renewal())), timeout=1)
    assert path.read_bytes() == before


def test_missing_refresh_token_fails_without_network(tmp_path):
    payload = state()
    payload["origins"][0]["localStorage"] = [e for e in payload["origins"][0]["localStorage"]
                                               if not e["name"].endswith("refreshToken")]
    path = save(tmp_path, payload)
    opener = MagicMock()
    with pytest.raises(SessionRefreshError):
        refresh_saved_session(path, jwt(), opener=opener, timeout=1)
    opener.assert_not_called()


def test_locked_sidecar_does_not_abort_successful_primary_renewal(tmp_path, monkeypatch):
    path = save(tmp_path)
    read_text = type(path).read_text
    def read_with_locked_sidecar(p, *args, **kwargs):
        if p.name == "api_bearer_token.txt":
            raise PermissionError("placeholder cloud lock")
        return read_text(p, *args, **kwargs)
    monkeypatch.setattr(type(path), "read_text", read_with_locked_sidecar)
    opener = MagicMock(side_effect=[response(renewal()), response({"events": []})])
    client = OutlierNflApiClient(session_path=path, opener=opener)
    assert client.fetch_schedule() == {"events": []}
    assert client.bearer_token == jwt(future=True)


def test_id_token_selection_is_preserved_during_refresh(tmp_path):
    payload = state()
    entries = payload["origins"][0]["localStorage"]
    entries[0], entries[1] = entries[1], entries[0]
    path = save(tmp_path, payload)
    opener = MagicMock(side_effect=[response(renewal()), response({"events": []})])
    client = OutlierNflApiClient(session_path=path, opener=opener)
    client.fetch_schedule()
    assert client.bearer_token == jwt("id", future=True)
