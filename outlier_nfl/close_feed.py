"""Book-close feed helpers for NFL shadow settle.

Honest contract:
- Never label snapshot ``best_odds`` as ``book_close``.
- Live sources require ``ODDS_API_KEY`` (The Odds API) or a kickoff-time
  Outlier scrape persisted as a close-feed JSON.
- Fixture / offline JSON feeds are first-class for CI and CLV proofs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from outlier_nfl.enrich_close import CLOSE_SOURCE_BOOK, load_book_close_feed
from outlier_nfl.utils import safe_read_json, safe_write_json

# Env var names checked (first wins). Documented for Daniel.
ODDS_API_KEY_ENV_NAMES: tuple[str, ...] = (
    "ODDS_API_KEY",
    "THE_ODDS_API_KEY",
    "THEODDSAPI_KEY",
)

CLOSE_FEED_SCHEMA_VERSION = 1


def resolve_odds_api_key(*, env: Mapping[str, str] | None = None) -> str | None:
    """Return the first non-empty Odds API key from known env names."""
    source = env if env is not None else os.environ
    for name in ODDS_API_KEY_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if value:
            return value
    return None


def close_feed_blocker_message() -> str:
    """Exact next-step text when no live close source is configured."""
    names = ", ".join(ODDS_API_KEY_ENV_NAMES)
    return (
        "No live NFL book-close feed on this box. "
        f"Set one of [{names}] then run "
        "`python -m outlier_nfl.fetch_odds_close --out closes.json` "
        "(optional `--predictions pack.json`, `--historical-date ISO` when paid) "
        "or schedule an Outlier T-0/kickoff props scrape and pass it as "
        "--close-feed JSON (schema: records[] with player_name, market, line, "
        "position, close_odds|odds, optional close_implied|implied_probability). "
        "Never label snapshot best_odds as book_close."
    )


def validate_close_feed_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate minimal close-feed schema; returns summary counts."""
    n = 0
    n_with_odds = 0
    n_with_implied = 0
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("player_name") or "").strip()
        market = str(raw.get("market") or "").strip()
        if not name or not market:
            continue
        n += 1
        odds = raw.get("close_odds", raw.get("odds", raw.get("price")))
        implied = raw.get("close_implied", raw.get("implied_probability", raw.get("implied")))
        if odds is not None and odds != "":
            n_with_odds += 1
        if implied is not None and implied != "":
            n_with_implied += 1
    return {
        "schema_version": CLOSE_FEED_SCHEMA_VERSION,
        "n_records": n,
        "n_with_close_odds": n_with_odds,
        "n_with_close_implied": n_with_implied,
    }


def write_close_feed(
    path: Path | str,
    records: Sequence[Mapping[str, Any]],
    *,
    source: str = CLOSE_SOURCE_BOOK,
    note: str | None = None,
) -> dict[str, Any]:
    """Persist a close-feed JSON document."""
    summary = validate_close_feed_records(records)
    payload = {
        "schema_version": CLOSE_FEED_SCHEMA_VERSION,
        "source": source,
        "note": note
        or (
            "book_close feed — closes differ from emit-time snapshot when prices moved; "
            "never a copy of best_odds labeled as book_close without a real capture."
        ),
        "count": summary["n_records"],
        "records": [dict(r) for r in records if isinstance(r, Mapping)],
    }
    safe_write_json(Path(path), payload)
    return payload


def snapshot_rows_to_moved_close_feed(
    predictions_path: Path | str,
    *,
    odds_delta: int = 15,
    implied_delta_pts: float = 2.5,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Build a *synthetic* close feed that moves prices off the snapshot.

    Used only for fixture/CI proofs that CLV ≠ 0 when closes differ.
    Not a live book close — callers must label fixtures accordingly.
    """
    payload = safe_read_json(predictions_path)
    if not isinstance(payload, dict):
        raise ValueError("predictions must be an object JSON")
    records_in = payload.get("records") or []
    out: list[dict[str, Any]] = []
    for raw in records_in:
        if not isinstance(raw, Mapping):
            continue
        try:
            line = float(raw["line"])
            odds = int(raw["best_odds"])
        except (KeyError, TypeError, ValueError):
            continue
        implied = raw.get("implied_probability")
        try:
            implied_f = float(implied) if implied is not None else None
        except (TypeError, ValueError):
            implied_f = None
        close_odds = odds + int(odds_delta) if odds < 0 else odds - int(odds_delta)
        close_implied = (
            None if implied_f is None else round(implied_f + float(implied_delta_pts), 4)
        )
        out.append(
            {
                "player_name": raw.get("player_name"),
                "market": raw.get("market"),
                "line": line,
                "position": raw.get("position"),
                "matchup": raw.get("matchup"),
                "event_id": raw.get("event_id"),
                "close_line": line,
                "close_odds": close_odds,
                "close_implied": close_implied,
                "close_source": CLOSE_SOURCE_BOOK,
            }
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def fetch_odds_api_event_odds(
    *,
    api_key: str,
    sport: str = "americanfootball_nfl",
    regions: str = "us",
    markets: str = (
        "player_pass_yds,player_rush_yds,player_reception_yds,"
        "player_receptions,player_anytime_td"
    ),
    odds_format: str = "american",
    timeout: int = 30,
    event_id: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Fetch Odds-API odds. Prefer event-level props when ``event_id`` is set.

    Featured h2h/spreads/totals use the sport odds endpoint; NFL player props
    require ``/events/{eventId}/odds``. Mapping onto Outlier close-feed rows is
    in ``outlier_nfl.fetch_odds_close``.
    """
    if event_id:
        from outlier_nfl.fetch_odds_close import fetch_event_odds

        return fetch_event_odds(
            api_key=api_key,
            event_id=event_id,
            sport=sport,
            regions=regions,
            markets=tuple(m.strip() for m in markets.split(",") if m.strip()),
            odds_format=odds_format,
            timeout=timeout,
        )
    params = urllib.parse.urlencode(
        {
            "apiKey": api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
        }
    )
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "x-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Odds API HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Odds API network error: {exc}") from exc
    return json.loads(body)


def try_load_live_or_file_close_index(
    close_feed: Path | str | None,
    *,
    allow_odds_api: bool = False,
    predictions: Path | str | Mapping[str, Any] | None = None,
    event_ids: Sequence[str] | None = None,
) -> tuple[dict[tuple[Any, ...], dict[str, Any]] | None, str]:
    """Load a book-close index from file, or live Odds-API→Outlier prop join.

    Live path requires ``allow_odds_api=True`` and a resolvable API key. Fetches
    upcoming (or provided) NFL event props, maps to close-feed records, and
    builds the enrich_close index. Prefer persisting via
    ``python -m outlier_nfl.fetch_odds_close --out …`` at kickoff.
    """
    if close_feed is not None:
        index = load_book_close_feed(Path(close_feed))
        return index, f"loaded_close_feed:{close_feed}"
    key = resolve_odds_api_key()
    if not (allow_odds_api and key):
        return None, close_feed_blocker_message()

    from outlier_nfl.fetch_odds_close import (
        build_close_feed_from_event_odds,
        fetch_event_odds,
        list_events,
    )

    pred_payload: Mapping[str, Any] | None = None
    if isinstance(predictions, Mapping):
        pred_payload = predictions
    elif predictions is not None:
        loaded = safe_read_json(Path(predictions))
        if isinstance(loaded, Mapping):
            pred_payload = loaded

    ids = list(event_ids) if event_ids else []
    if not ids:
        try:
            events = list_events(api_key=key)
        except Exception as exc:  # noqa: BLE001
            return None, f"odds_api_events_failed:{exc}"
        ids = [str(e.get("id")) for e in events if e.get("id")]
    if not ids:
        return None, "odds_api_no_nfl_events"

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for eid in ids:
        try:
            payload = fetch_event_odds(api_key=key, event_id=eid)
            records.extend(
                build_close_feed_from_event_odds(payload, predictions=pred_payload)
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{eid}:{exc}")
    if not records:
        detail = "; ".join(errors[:3]) if errors else "empty"
        return None, f"odds_api_prop_join_empty:{detail}"

    # Build index compatible with load_book_close_feed (full + short keys).
    from outlier_nfl.enrich_close import index_book_close_records

    index = index_book_close_records(records)
    return index, f"odds_api_live_join:n={len(records)}:events={len(ids)}"
