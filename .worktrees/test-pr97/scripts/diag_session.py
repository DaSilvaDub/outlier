"""Read-only session diagnostic. Prints whether the saved session yields a
bearer token + cookies, and the localStorage key NAMES present (never values).

Run from the project root:  python scripts/diag_session.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly as `python scripts/diag_session.py` (adds repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outlier_scrapers.auth import (  # noqa: E402
    build_cookie_header,
    extract_access_token,
    list_local_storage_names,
    load_storage_state,
)


def main() -> int:
    ss = load_storage_state()

    try:
        token = extract_access_token(ss)
        print(f"token_found: True  (length={len(token)})")
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"token_found: False  -> {str(exc)[:120]}")

    print(f"cookie_header_present: {bool(build_cookie_header(ss))}")

    cookies = ss.get("cookies", [])
    cookie_names = sorted({str(c.get('name')) for c in cookies if isinstance(c, dict) and c.get('name')})
    print(f"cookie_names: {cookie_names}")

    print(f"localStorage_keys (app.outlier.bet): {list_local_storage_names(ss)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
