"""Diagnose why /sportsdata/markets/{id} returns 403 while league endpoints work.

Probes one market with different Authorization variants and prints the HTTP
status + the server's message text (not token values).

Run from project root:  python scripts/diag_markets.py [--league MLB]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outlier_scrapers.api import API_BASE_URL  # noqa: E402
from outlier_scrapers.auth import (  # noqa: E402
    build_api_headers,
    build_cookie_header,
    load_storage_state,
)
from outlier_scrapers.line_movement import load_props_market_ids  # noqa: E402

APP_ORIGIN = "https://app.outlier.bet"


def _cognito_tokens(ss: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for origin in ss.get("origins", []):
        if origin.get("origin") != APP_ORIGIN:
            continue
        for item in origin.get("localStorage", []):
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            if name.endswith(".accessToken"):
                out["accessToken"] = value
            elif name.endswith(".idToken"):
                out["idToken"] = value
    return out


def _probe(market_id: str, headers: dict[str, str], label: str) -> None:
    url = f"{API_BASE_URL}/sportsdata/markets/{market_id}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            print(f"  [{label}] HTTP {resp.status} OK")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            msg = json.loads(body)
        except Exception:
            msg = body[:200]
        print(f"  [{label}] HTTP {exc.code} -> {str(msg)[:200]}")
    except URLError as exc:
        print(f"  [{label}] network error: {str(exc)[:120]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="MLB")
    args = parser.parse_args()

    ss = load_storage_state()
    market_ids, _ctx, _path = load_props_market_ids(args.league, limit=1)
    if not market_ids:
        print("No market IDs in props_latest; run props export first.")
        return 1
    market_id = market_ids[0]
    print(f"Probing market {market_id} ({args.league})")

    tokens = _cognito_tokens(ss)
    print(f"have accessToken: {'accessToken' in tokens}, have idToken: {'idToken' in tokens}")
    cookie = build_cookie_header(ss)
    base = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

    # 1) Current behavior (whatever build_api_headers sends today).
    _probe(market_id, build_api_headers(ss), "current build_api_headers")
    # 2) idToken bearer + cookie.
    if "idToken" in tokens:
        h = dict(base, Cookie=cookie, Authorization=f"Bearer {tokens['idToken']}")
        _probe(market_id, h, "idToken bearer")
    # 3) accessToken bearer + cookie.
    if "accessToken" in tokens:
        h = dict(base, Cookie=cookie, Authorization=f"Bearer {tokens['accessToken']}")
        _probe(market_id, h, "accessToken bearer")
    # 4) cookie only, no Authorization.
    _probe(market_id, dict(base, Cookie=cookie), "cookie only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
