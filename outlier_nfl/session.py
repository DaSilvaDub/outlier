"""Renew the selected saved Cognito session without logging credentials.

JWT claims here select an expiry/refresh route, not authentication authority.
Only Cognito's HTTPS response supplies replacement credentials.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request

from outlier_nfl.utils import atomic_write_text, file_lock, safe_write_json

APP_ORIGIN = "https://app.outlier.bet"
_ISSUER = re.compile(
    r"https://cognito-idp\.([a-z]{2}(?:-gov)?-[a-z]+-\d)\.amazonaws\.com/[\w-]+"
)
_GROUP = re.compile(r"CognitoIdentityServiceProvider\.([a-zA-Z0-9]+)\.[^.]+\.(accessToken|idToken)")


class SessionRefreshError(RuntimeError):
    """An expired saved session could not be renewed safely."""


def token_claims(token: str | None) -> dict[str, Any]:
    try:
        part = (token or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return claims if isinstance(claims, dict) else {}
    except (ValueError, IndexError, TypeError):
        return {}


def token_needs_refresh(token: str | None) -> bool:
    exp = token_claims(token).get("exp")
    return isinstance(exp, (int, float)) and exp <= time.time() + 60


def refresh_saved_session(
    path: Path, selected_token: str, *, opener: Callable[..., Any], timeout: float
) -> tuple[str, dict[str, Any]]:
    """Refresh one matched account, reusing a concurrent writer's fresh token.

The access and ID tokens are committed together in storage_state.json. A
matching legacy bearer sidecar is updated afterwards; discovery prefers the
storage state, so it remains authoritative if that secondary write fails.
    """
    try:
        with file_lock(path, timeout=10):
            state = json.loads(path.read_text(encoding="utf-8"))
            old = token_claims(selected_token)
            issuer = str(old.get("iss") or "")
            match = _ISSUER.fullmatch(issuer)
            if not match or not old.get("sub"):
                raise SessionRefreshError("Unsupported saved authentication session.")
            for origin in state.get("origins", []):
                if origin.get("origin") != APP_ORIGIN:
                    continue
                entries = origin.get("localStorage", [])
                for entry in entries:
                    group = _GROUP.fullmatch(str(entry.get("name") or ""))
                    if not group:
                        continue
                    current = token_claims(entry.get("value"))
                    # Match the selected identity and token kind, even if another
                    # process has already replaced this token while we waited.
                    if any(current.get(k) != old.get(k) for k in ("iss", "sub", "token_use")):
                        continue
                    if current.get("client_id", current.get("aud")) != group[1]:
                        continue
                    if current.get("client_id", current.get("aud")) != old.get("client_id", old.get("aud")):
                        continue
                    if not isinstance(current.get("exp"), (int, float)):
                        continue
                    if not token_needs_refresh(entry.get("value")):
                        return entry["value"], state
                    prefix = entry["name"].rsplit(".", 1)[0]
                    values = {e.get("name"): e.get("value") for e in entries}
                    refresh = values.get(prefix + ".refreshToken")
                    if not isinstance(refresh, str) or not refresh:
                        raise SessionRefreshError("Saved refresh token is missing.")
                    params = {"REFRESH_TOKEN": refresh}
                    if values.get(prefix + ".deviceKey"):
                        params["DEVICE_KEY"] = values[prefix + ".deviceKey"]
                    request = Request(
                        f"https://cognito-idp.{match[1]}.amazonaws.com/",
                        data=json.dumps({"AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": group[1],
                                         "AuthParameters": params}).encode(),
                        headers={"Content-Type": "application/x-amz-json-1.1",
                                 "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"},
                    )
                    with opener(request, timeout=timeout) as response:
                        result = json.loads(response.read()).get("AuthenticationResult", {})
                    updates = {}
                    for kind, field in (("accessToken", "AccessToken"), ("idToken", "IdToken")):
                        token = result.get(field)
                        claims = token_claims(token)
                        if (not isinstance(token, str) or not isinstance(claims.get("exp"), (int, float))
                                or token_needs_refresh(token)
                                or claims.get("iss") != issuer or claims.get("sub") != old["sub"]
                                or claims.get("token_use") != ("access" if kind == "accessToken" else "id")
                                or claims.get("client_id", claims.get("aud")) != group[1]):
                            raise SessionRefreshError("Authentication renewal returned invalid credentials.")
                        updates[prefix + "." + kind] = token
                    if result.get("RefreshToken"):
                        updates[prefix + ".refreshToken"] = result["RefreshToken"]
                    previous = {values.get(prefix + "." + kind): prefix + "." + kind
                                for kind in ("accessToken", "idToken")}
                    sidecar = path.with_name("api_bearer_token.txt")
                    try:
                        sidecar_value = sidecar.read_text(encoding="utf-8").strip().removeprefix("Bearer ")
                    except OSError:
                        sidecar_value = None
                    for item in entries:
                        if item.get("name") in updates:
                            item["value"] = updates.pop(item["name"])
                    entries.extend({"name": name, "value": value} for name, value in updates.items())
                    safe_write_json(path, state)
                    renewed = next(e["value"] for e in entries if e.get("name") == entry["name"])
                    if sidecar_value and sidecar_value in previous:
                        try:
                            sidecar_token = next(e["value"] for e in entries
                                                 if e.get("name") == previous[sidecar_value])
                            atomic_write_text(sidecar, sidecar_token)
                        except OSError:
                            pass  # the atomically updated storage state is authoritative
                    return renewed, state
            raise SessionRefreshError("No matching saved Cognito account can be renewed.")
    except Exception:
        # Never expose HTTP response bodies or chained transport errors containing
        # request data. A failed refresh does not overwrite the original session.
        raise SessionRefreshError("Saved Outlier session could not be renewed; sign in again.") from None
