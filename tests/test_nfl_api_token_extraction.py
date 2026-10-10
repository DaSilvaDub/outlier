"""F24 (#227): bearer-token extraction from Playwright storage_state.

Every credential here is a placeholder, never a real token.
"""

from __future__ import annotations

import json

import pytest

from outlier_nfl.api import extract_token_from_storage_state

APP = "https://app.outlier.bet"
PH_JWT = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJwbGFjZWhvbGRlciJ9.placeholder-signature"  # nosec B105
PH_OPAQUE = "placeholder-opaque-session-token-0000"  # nosec B105
COOKIE = {"name": "session", "value": "placeholder-cookie-value-long-enough", "domain": "app.outlier.bet"}


def _state(local: list[dict[str, str]], cookies: list[dict[str, str]] | None = None,
           **extra: object) -> dict[str, object]:
    return {"cookies": [COOKIE] if cookies is None else cookies,
            "origins": [{"origin": APP, "localStorage": local}], **extra}


@pytest.mark.parametrize("state", [
    {"cookies": [], "origins": []},
    _state([]),
    _state([{"name": "lastMessage", "value": "a long unrelated message that is not a credential"}]),
    _state([{"name": "theme", "value": "dark-mode-preference-with-a-long-name"}]),
    _state([], extra_meta={"description": "some-long-metadata-value-without-spaces"}),
])
def test_cookie_only_or_unrelated_metadata_yields_no_bearer(state: dict[str, object]) -> None:
    assert extract_token_from_storage_state(state) is None


def test_cookie_value_is_never_promoted_to_bearer() -> None:
    jwt_cookie = {"name": "session", "value": PH_JWT, "domain": "app.outlier.bet"}
    assert extract_token_from_storage_state(_state([], cookies=[jwt_cookie])) is None


def test_nested_real_auth_entry_is_found() -> None:
    nested = json.dumps({"auth": json.dumps({"accessToken": PH_OPAQUE})})
    state = _state([{"name": "persist:root", "value": nested}])
    assert extract_token_from_storage_state(state) == PH_OPAQUE


def test_marker_named_entry_and_jwt_are_found() -> None:
    assert extract_token_from_storage_state(
        _state([{"name": "access_token", "value": PH_JWT}])) == PH_JWT
    assert extract_token_from_storage_state(
        _state([{"name": "session", "value": json.dumps({"user": {"jwt": PH_JWT}})}])) == PH_JWT
    sb = json.dumps({"access_token": PH_OPAQUE, "refresh_token": "placeholder-refresh-xxxxxxxx"})
    assert extract_token_from_storage_state(
        _state([{"name": "sb-xyz-auth-token", "value": sb}])) == PH_OPAQUE


def test_marker_named_entry_without_a_credential_inside_yields_none() -> None:
    blob = json.dumps({"origin": APP, "note": "a-long-string-that-is-not-a-token"})
    assert extract_token_from_storage_state(_state([{"name": "auth_token", "value": blob}])) is None
