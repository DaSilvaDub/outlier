from __future__ import annotations

from typing import Any


REDACTED = "[redacted]"
SECRET_KEY_FRAGMENTS = (
    "token",
    "authorization",
    "cookie",
    "secret",
    "password",
    "bearer",
    "session",
    "bookodds",
    "odds",
)


def is_sensitive_key(key: object) -> bool:
    compact = "".join(ch for ch in str(key or "").lower() if ch.isalnum())
    return any(fragment.replace("_", "") in compact for fragment in SECRET_KEY_FRAGMENTS)


def redact_value(value: Any, key: object = "") -> Any:
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item, key) for item in value[:5]]
    if isinstance(value, str) and len(value) > 32:
        return f"{value[:8]}...{REDACTED}"
    return value


def shape_summary(value: Any, *, max_depth: int = 3) -> Any:
    if max_depth <= 0:
        return type(value).__name__
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str):
            if is_sensitive_key(key):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = shape_summary(value[key], max_depth=max_depth - 1)
        return out
    if isinstance(value, list):
        if not value:
            return {"type": "list", "count": 0, "sample": None}
        return {
            "type": "list",
            "count": len(value),
            "sample": shape_summary(value[0], max_depth=max_depth - 1),
        }
    return type(value).__name__

