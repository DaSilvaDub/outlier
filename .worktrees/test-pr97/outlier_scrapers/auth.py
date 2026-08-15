from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .paths import (
    api_request_headers_file,
    bearer_token_file,
    otp_code_file,
    otp_status_file,
    session_dir,
    session_metadata_file,
    storage_state_candidates,
    storage_state_file,
)

# Provenance: adapted from
# C:\Users\dasil\Dev\GitHub\nba-props-pipeline\scrapers\outlier\outlier_auth.py
# as inspected on 2026-06-19. Keep auth/session fixes synced manually.

APP_ORIGIN = "https://app.outlier.bet"
API_ORIGIN = "https://api.outlier.bet"
TOKEN_MARKERS = (
    "access_token",
    "accesstoken",
    "auth_token",
    "authtoken",
    "id_token",
    "idtoken",
    "bearer",
    "authorization",
)


def ensure_session_dirs() -> Path:
    directory = session_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_storage_state() -> dict[str, Any]:
    for candidate in storage_state_candidates():
        if candidate.exists() and candidate.stat().st_size > 0:
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        "No saved Outlier storage_state found. Expected one of: "
        + ", ".join(str(item) for item in storage_state_candidates())
    )


def persist_storage_state(storage_state: dict[str, Any]) -> list[Path]:
    ensure_session_dirs()
    payload = json.dumps(storage_state, indent=2)
    target = storage_state_file()
    target.write_text(payload, encoding="utf-8")
    return [target]


def _normalize_bearer_token(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower().startswith("bearer "):
        text = text.split(" ", 1)[1].strip()
    if len(text) < 20:
        return None
    if any(ch.isspace() for ch in text):
        return None
    return text


def _token_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_token = (
                str(key or "").strip().lower().replace(".", "").replace("-", "").replace("_", "")
            )
            if any(marker.replace("_", "") in key_token for marker in TOKEN_MARKERS):
                token = _normalize_bearer_token(value)
                if token:
                    return token
            token = _token_from_payload(value)
            if token:
                return token
        return None
    if isinstance(payload, list):
        for item in payload:
            token = _token_from_payload(item)
            if token:
                return token
        return None
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                decoded = json.loads(text)
            except Exception:
                decoded = None
            if decoded is not None:
                token = _token_from_payload(decoded)
                if token:
                    return token
        return _normalize_bearer_token(text)
    return None


def read_saved_bearer_token() -> str | None:
    metadata_path = session_metadata_file()
    if metadata_path.exists() and metadata_path.stat().st_size > 0:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = None
        if isinstance(metadata, dict):
            for key in ("bearer_token", "access_token", "authorization"):
                token = _normalize_bearer_token(metadata.get(key))
                if token:
                    return token

    token_path = bearer_token_file()
    if token_path.exists() and token_path.stat().st_size > 0:
        token = _normalize_bearer_token(token_path.read_text(encoding="utf-8"))
        if token:
            return token
    return None


def write_saved_bearer_token(token: str) -> Path:
    ensure_session_dirs()
    target = bearer_token_file()
    target.write_text(token.strip(), encoding="utf-8")
    return target


def write_session_metadata(payload: dict[str, Any]) -> Path:
    ensure_session_dirs()
    target = session_metadata_file()
    safe_payload = {
        key: ("[redacted]" if str(key).lower() in {"bearer_token", "access_token", "authorization"} else value)
        for key, value in payload.items()
    }
    target.write_text(json.dumps(safe_payload, indent=2), encoding="utf-8")
    return target


def read_saved_api_request_headers() -> dict[str, str]:
    target = api_request_headers_file()
    if not target.exists() or target.stat().st_size <= 0:
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    headers: dict[str, str] = {}
    for key, value in payload.items():
        name = str(key or "").strip()
        text = str(value or "").strip()
        if name and text and not name.startswith(":"):
            headers[name] = text
    return headers


def write_saved_api_request_headers(headers: dict[str, Any]) -> Path:
    ensure_session_dirs()
    blocked = {
        "host",
        "content-length",
        "connection",
        "content-encoding",
        "authorization",
        "cookie",
        "x-api-key",
        "x-amz-security-token",
    }
    filtered: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key or "").strip()
        text = str(value or "").strip()
        if not name or not text or name.startswith(":") or name.lower() in blocked:
            continue
        filtered[name] = text
    target = api_request_headers_file()
    target.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    return target


def list_local_storage_names(storage_state: dict[str, Any], origin: str = APP_ORIGIN) -> list[str]:
    names: list[str] = []
    for entry in storage_state.get("origins", []):
        if entry.get("origin") != origin:
            continue
        for item in entry.get("localStorage", []):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return sorted(set(names))


def extract_access_token(storage_state: dict[str, Any]) -> str:
    for origin in storage_state.get("origins", []):
        if origin.get("origin") != APP_ORIGIN:
            continue
        for item in origin.get("localStorage", []):
            name = str(item.get("name") or "").strip().lower()
            compact = re.sub(r"[^a-z0-9]+", "", name)
            if not any(marker.replace("_", "") in compact for marker in TOKEN_MARKERS):
                continue
            token = _token_from_payload(item.get("value"))
            if token:
                return token

    token = read_saved_bearer_token()
    if token:
        return token

    raise ValueError("Outlier access token not found in saved storage state or token cache")


def build_cookie_header(
    storage_state: dict[str, Any],
    target_hosts: Iterable[str] = ("api.outlier.bet", "app.outlier.bet"),
) -> str:
    hosts = tuple(host.lower() for host in target_hosts)
    now = time.time()
    pairs: list[str] = []
    for cookie in storage_state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        domain = str(cookie.get("domain") or "").strip().lstrip(".").lower()
        expires = cookie.get("expires")
        if not name or not value or not domain:
            continue
        if isinstance(expires, (int, float)) and expires > 0 and expires < now:
            continue
        if not any(
            host == domain or host.endswith(f".{domain}") or domain.endswith(f".{host}")
            for host in hosts
        ):
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def build_api_headers(storage_state: dict[str, Any]) -> dict[str, str]:
    headers = read_saved_api_request_headers()
    if "Accept" not in headers and "accept" not in headers:
        headers["Accept"] = "application/json"
    if "User-Agent" not in headers and "user-agent" not in headers:
        headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    if "Origin" not in headers and "origin" not in headers:
        headers["Origin"] = "https://app.outlier.bet"
    if "Referer" not in headers and "referer" not in headers:
        headers["Referer"] = "https://app.outlier.bet/"

    cookie_header = build_cookie_header(storage_state)
    if cookie_header and "Cookie" not in headers and "cookie" not in headers:
        headers["Cookie"] = cookie_header
    try:
        token = extract_access_token(storage_state)
    except Exception:
        token = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def write_otp_status(stage: str, **extra: Any) -> None:
    ensure_session_dirs()
    payload = {
        "stage": stage,
        "updated_at": datetime.now().astimezone().isoformat(),
        **extra,
    }
    otp_status_file().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_otp_code() -> str:
    target = otp_code_file()
    if not target.exists():
        return ""
    value = target.read_text(encoding="utf-8").strip()
    return value if len(value) == 6 and value.isdigit() else ""


def is_props_url(url: str, league: str) -> bool:
    lower = str(url or "").lower()
    route = f"/{league.strip().lower()}/props"
    return "app.outlier.bet" in lower and route in lower and "login" not in lower
