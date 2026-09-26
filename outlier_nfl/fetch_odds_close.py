"""Fetch The Odds API NFL player-prop odds and map them to Outlier close-feed rows.

Honest contract
---------------
- Auth: ``apiKey`` query param (The Odds API v4). Optional ``x-api-key`` header is
  also sent; query param is what the API requires.
- Never prints the API key. Never stamps snapshot ``best_odds`` as ``book_close``.
- Live / event odds are for **future** kickoff capture. Historical close for a past
  slate needs the paid historical endpoint; when unavailable, capture live at T-0
  and persist ``--out`` JSON for ``enrich_close --mode book_close --close-feed``.
- Join onto Outlier packs: primary keys ``player_name, market, line, position``.
  Optional ``--predictions`` copies ``matchup`` / ``event_id`` from the pack onto
  matched rows so the full enrich_close key also hits.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from outlier_nfl.close_feed import (
    CLOSE_FEED_SCHEMA_VERSION,
    resolve_odds_api_key,
    validate_close_feed_records,
    write_close_feed,
)
from outlier_nfl.enrich_close import CLOSE_SOURCE_BOOK
from outlier_nfl.games import american_to_implied_probability
from outlier_nfl.utils import safe_read_json

ODDS_API_BASE = "https://api.the-odds-api.com"
DEFAULT_SPORT = "americanfootball_nfl"
DEFAULT_REGIONS = "us"
DEFAULT_ODDS_FORMAT = "american"

# Featured + common alternate NFL player-prop markets we map into Outlier codes.
DEFAULT_PROP_MARKETS: tuple[str, ...] = (
    "player_pass_yds",
    "player_pass_yds_alternate",
    "player_rush_yds",
    "player_rush_yds_alternate",
    "player_reception_yds",
    "player_reception_yds_alternate",
    "player_receptions",
    "player_receptions_alternate",
    "player_pass_tds",
    "player_pass_tds_alternate",
    "player_rush_attempts",
    "player_rush_attempts_alternate",
    "player_rush_reception_yds",
    "player_rush_reception_yds_alternate",
    "player_pass_rush_yds",
    "player_pass_rush_yds_alternate",
    "player_anytime_td",
    "player_assists",
    "player_assists_alternate",
    "player_tackles_assists",
    "player_tackles_assists_alternate",
    "player_solo_tackles",
    "player_solo_tackles_alternate",
    "player_sacks",
    "player_sacks_alternate",
    "player_kicking_points",
    "player_kicking_points_alternate",
    "player_field_goals",
    "player_field_goals_alternate",
    "player_pass_completions",
    "player_pass_completions_alternate",
    "player_pass_attempts",
    "player_pass_attempts_alternate",
    "player_pass_interceptions",
    "player_pass_interceptions_alternate",
    "player_reception_longest",
    "player_reception_longest_alternate",
    "player_rush_longest",
    "player_rush_longest_alternate",
    "player_pass_longest_completion",
    "player_pass_longest_completion_alternate",
)

# Odds-API market key (strip _alternate) → Outlier canonical market.
ODDS_MARKET_TO_OUTLIER: dict[str, str] = {
    "player_pass_yds": "PASS_YDS",
    "player_rush_yds": "RUSH_YDS",
    "player_reception_yds": "REC_YDS",
    "player_receptions": "REC",
    "player_pass_tds": "PASS_TD",
    "player_rush_attempts": "RUSH_ATT",
    "player_rush_reception_yds": "RUSH_REC_YDS",
    "player_pass_rush_yds": "PASS_RUSH_YDS",
    "player_anytime_td": "ANYTIME_TD",
    "player_assists": "ASSISTS",
    "player_tackles_assists": "DEFENSIVE_TACKLES_ASSISTS",
    "player_solo_tackles": "DEFENSIVE_TACKLES",
    "player_sacks": "SACKS",
    "player_kicking_points": "KICK_PTS",
    "player_field_goals": "MADE_FIELD_GOALS",
    "player_pass_completions": "PASSING_COMPLETIONS",
    "player_pass_attempts": "PASS_ATT",
    "player_pass_interceptions": "INTERCEPTIONS_THROWN",
    "player_reception_longest": "LONG_REC",
    "player_rush_longest": "LONG_RUSH",
    "player_pass_longest_completion": "LONGEST_PASSING_COMPLETION",
    "player_tds": "ANYTIME_TD",  # over/under TD count; line usually 0.5
}


def odds_market_to_outlier(market_key: str) -> str | None:
    """Map an Odds-API market key (possibly ``*_alternate``) to Outlier code."""
    key = str(market_key or "").strip().lower()
    if key.endswith("_alternate"):
        key = key[: -len("_alternate")]
    return ODDS_MARKET_TO_OUTLIER.get(key)


def _american_to_decimal(odds: int | float) -> float:
    val = float(odds)
    if val == 0:
        return 2.0
    if val > 0:
        return 1.0 + val / 100.0
    return 1.0 + 100.0 / abs(val)


def better_american_odds(a: int | float, b: int | float) -> int:
    """Return the better (higher payout) American price for the bettor."""
    return int(a) if _american_to_decimal(a) >= _american_to_decimal(b) else int(b)


def outcome_to_position(name: str | None, *, market_outlier: str) -> str | None:
    """Normalize Odds-API outcome name → Outlier ``OVER`` / ``UNDER``."""
    raw = str(name or "").strip().upper()
    if raw in {"OVER", "O", "YES"}:
        return "OVER"
    if raw in {"UNDER", "U", "NO"}:
        return "UNDER"
    # Some anytime-TD books list the player as ``name`` with no Over/Under.
    if market_outlier == "ANYTIME_TD" and raw not in {"", "OVER", "UNDER"}:
        return "OVER"
    return None


def _short_join_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        line = float(row["line"]) if row.get("line") is not None else None
    except (TypeError, ValueError):
        line = None
    return (
        str(row.get("player_name") or "").strip().upper(),
        str(row.get("market") or "").strip().upper(),
        line,
        str(row.get("position") or "").strip().upper(),
    )


def redact_odds_api_payload(payload: Any) -> Any:
    """Deep-copy JSON-like payload stripping any accidental apiKey fields."""
    if isinstance(payload, Mapping):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            lk = str(k).lower()
            if lk in {"apikey", "api_key", "x-api-key", "authorization"}:
                out[k] = "REDACTED"
            else:
                out[k] = redact_odds_api_payload(v)
        return out
    if isinstance(payload, list):
        return [redact_odds_api_payload(x) for x in payload]
    return payload


def _request_json(
    path: str,
    *,
    api_key: str,
    params: Mapping[str, str] | None = None,
    timeout: int = 30,
) -> tuple[Any, dict[str, str]]:
    """GET ``path`` against The Odds API. Never logs the key."""
    q = dict(params or {})
    q["apiKey"] = api_key
    url = f"{ODDS_API_BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            # Docs emphasize apiKey query; header alone returns MISSING_KEY.
            "x-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Odds API HTTP {exc.code}: {err_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Odds API network error: {exc}") from exc
    return json.loads(body), headers


def list_sports(*, api_key: str, timeout: int = 30) -> list[dict[str, Any]]:
    data, _ = _request_json("/v4/sports/", api_key=api_key, params={"all": "true"}, timeout=timeout)
    if not isinstance(data, list):
        raise RuntimeError("Odds API /sports returned non-list")
    return data


def list_events(
    *,
    api_key: str,
    sport: str = DEFAULT_SPORT,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    data, _ = _request_json(
        f"/v4/sports/{sport}/events",
        api_key=api_key,
        timeout=timeout,
    )
    if not isinstance(data, list):
        raise RuntimeError("Odds API /events returned non-list")
    return data


def fetch_event_odds(
    *,
    api_key: str,
    event_id: str,
    sport: str = DEFAULT_SPORT,
    regions: str = DEFAULT_REGIONS,
    markets: Sequence[str] | None = None,
    odds_format: str = DEFAULT_ODDS_FORMAT,
    timeout: int = 30,
    historical_date: str | None = None,
) -> dict[str, Any]:
    """Fetch event-level odds (player props). Optional historical snapshot ISO date."""
    mkt = ",".join(markets or DEFAULT_PROP_MARKETS)
    params: dict[str, str] = {
        "regions": regions,
        "markets": mkt,
        "oddsFormat": odds_format,
    }
    if historical_date:
        params["date"] = historical_date
        path = f"/v4/historical/sports/{sport}/events/{event_id}/odds"
    else:
        path = f"/v4/sports/{sport}/events/{event_id}/odds"
    data, headers = _request_json(path, api_key=api_key, params=params, timeout=timeout)
    if not isinstance(data, dict):
        raise RuntimeError("Odds API event odds returned unexpected shape")
    # Unwrap historical envelope when present: {"timestamp", "data": {...}}.
    if "bookmakers" not in data and isinstance(data.get("data"), dict):
        inner = dict(data["data"])
        inner["_historical_meta"] = {
            "timestamp": data.get("timestamp"),
            "previous_timestamp": data.get("previous_timestamp"),
            "next_timestamp": data.get("next_timestamp"),
            "requests_remaining": headers.get("x-requests-remaining"),
            "requests_used": headers.get("x-requests-used"),
        }
        return inner
    out = dict(data)
    out["_quota"] = {
        "requests_remaining": headers.get("x-requests-remaining"),
        "requests_used": headers.get("x-requests-used"),
    }
    return out


def map_event_odds_to_close_records(
    event_payload: Mapping[str, Any],
    *,
    bookmaker_filter: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Flatten an Odds-API event-odds payload into close-feed records.

    For each (player, outlier_market, line, position) keeps the **best** American
    price across bookmakers (Outlier ``best_odds`` convention). ``close_implied``
    is derived via ``american_to_implied_probability`` (percentage points).
    """
    allowed_books = None
    if bookmaker_filter:
        allowed_books = {str(b).strip().lower() for b in bookmaker_filter if str(b).strip()}

    home = str(event_payload.get("home_team") or "").strip()
    away = str(event_payload.get("away_team") or "").strip()
    matchup = f"{away} @ {home}" if away and home else None
    odds_event_id = str(event_payload.get("id") or "").strip() or None
    commence = event_payload.get("commence_time")

    # Accumulate best price per short join key.
    best: dict[tuple[Any, ...], dict[str, Any]] = {}

    for book in event_payload.get("bookmakers") or []:
        if not isinstance(book, Mapping):
            continue
        book_key = str(book.get("key") or "").strip().lower()
        if allowed_books is not None and book_key not in allowed_books:
            continue
        for market in book.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            outlier_mkt = odds_market_to_outlier(str(market.get("key") or ""))
            if not outlier_mkt:
                continue
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, Mapping):
                    continue
                player = str(
                    outcome.get("description") or outcome.get("participant") or ""
                ).strip()
                position = outcome_to_position(
                    str(outcome.get("name") or ""), market_outlier=outlier_mkt
                )
                # Anytime TD sometimes puts player in ``name`` and Yes in description.
                if not player and outlier_mkt == "ANYTIME_TD":
                    player = str(outcome.get("name") or "").strip()
                    if str(outcome.get("description") or "").strip().upper() in {
                        "YES",
                        "NO",
                        "OVER",
                        "UNDER",
                    }:
                        position = outcome_to_position(
                            str(outcome.get("description") or ""),
                            market_outlier=outlier_mkt,
                        )
                if not player or position is None:
                    continue
                try:
                    price = int(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                point_raw = outcome.get("point")
                if point_raw is None and outlier_mkt == "ANYTIME_TD":
                    point_raw = 0.5
                try:
                    line = float(point_raw)
                except (TypeError, ValueError):
                    continue

                row = {
                    "player_name": player,
                    "market": outlier_mkt,
                    "line": line,
                    "position": position,
                    "matchup": matchup,
                    "event_id": odds_event_id,
                    "commence_time": commence,
                    "close_line": line,
                    "close_odds": price,
                    "close_implied": american_to_implied_probability(price),
                    "close_source": CLOSE_SOURCE_BOOK,
                    "bookmaker": book_key or book.get("title"),
                    "odds_api_market": market.get("key"),
                }
                key = _short_join_key(row)
                prev = best.get(key)
                if prev is None:
                    best[key] = row
                else:
                    chosen = better_american_odds(prev["close_odds"], price)
                    if chosen == price and price != prev["close_odds"]:
                        best[key] = row
                    elif chosen == price:
                        # tie — keep existing
                        pass

    return list(best.values())


def align_close_records_to_predictions(
    close_records: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Copy Outlier ``matchup`` / ``event_id`` onto close rows when short keys match.

    Enables full ``_row_match_key`` hits in ``enrich_close`` without inventing
    closes for unmatched pack rows.
    """
    if isinstance(predictions, Mapping):
        pred_rows = predictions.get("records") or []
    else:
        pred_rows = predictions
    by_short: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for raw in pred_rows:
        if not isinstance(raw, Mapping):
            continue
        by_short[_short_join_key(raw)] = raw

    out: list[dict[str, Any]] = []
    for raw in close_records:
        row = dict(raw)
        hit = by_short.get(_short_join_key(row))
        if hit is not None:
            if hit.get("matchup"):
                row["matchup"] = hit.get("matchup")
            if hit.get("event_id"):
                row["event_id"] = hit.get("event_id")
            row["aligned_to_predictions"] = True
        else:
            row["aligned_to_predictions"] = False
        out.append(row)
    return out


def build_close_feed_from_event_odds(
    event_payload: Mapping[str, Any],
    *,
    predictions: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    bookmaker_filter: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    records = map_event_odds_to_close_records(
        event_payload, bookmaker_filter=bookmaker_filter
    )
    if predictions is not None:
        records = align_close_records_to_predictions(records, predictions)
    return records


def probe_plan_coverage(*, api_key: str, timeout: int = 30) -> dict[str, Any]:
    """Probe sports list + one NFL event props fetch; summarize what this key can do.

    Never includes the API key in the returned dict.
    """
    report: dict[str, Any] = {
        "sports_ok": False,
        "nfl_active": None,
        "events_ok": False,
        "n_events": None,
        "props_ok": False,
        "props_error": None,
        "historical_ok": False,
        "historical_error": None,
        "quota": {},
    }
    try:
        sports = list_sports(api_key=api_key, timeout=timeout)
        report["sports_ok"] = True
        nfl = next((s for s in sports if s.get("key") == DEFAULT_SPORT), None)
        report["nfl_active"] = bool(nfl and nfl.get("active"))
        report["n_sports"] = len(sports)
    except Exception as exc:  # noqa: BLE001 — probe must not raise
        report["sports_error"] = str(exc)[:300]
        return report

    try:
        events = list_events(api_key=api_key, timeout=timeout)
        report["events_ok"] = True
        report["n_events"] = len(events)
    except Exception as exc:  # noqa: BLE001
        report["events_error"] = str(exc)[:300]
        return report

    if not events:
        report["props_error"] = "no live/upcoming NFL events to probe props"
        return report

    eid = str(events[0].get("id") or "")
    report["probe_event_id"] = eid
    report["probe_commence"] = events[0].get("commence_time")
    try:
        odds = fetch_event_odds(
            api_key=api_key,
            event_id=eid,
            markets=("player_pass_yds", "player_rush_yds", "player_anytime_td"),
            timeout=timeout,
        )
        books = odds.get("bookmakers") or []
        mkts: set[str] = set()
        for b in books:
            for m in b.get("markets") or []:
                mkts.add(str(m.get("key")))
        report["props_ok"] = True
        report["n_bookmakers"] = len(books)
        report["markets_found"] = sorted(mkts)
        report["quota"] = odds.get("_quota") or {}
        report["n_close_records"] = len(map_event_odds_to_close_records(odds))
    except Exception as exc:  # noqa: BLE001
        report["props_error"] = str(exc)[:400]

    # Historical: try events list at a fixed past kickoff window (often paid).
    try:
        _request_json(
            f"/v4/historical/sports/{DEFAULT_SPORT}/events",
            api_key=api_key,
            params={"date": "2026-09-20T17:00:00Z"},
            timeout=timeout,
        )
        report["historical_ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["historical_error"] = str(exc)[:400]
        report["historical_ok"] = False

    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch The Odds API NFL player props and write an Outlier book_close "
            "feed JSON. Requires ODDS_API_KEY | THE_ODDS_API_KEY. Never labels "
            "snapshot best_odds as book_close."
        )
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination close-feed JSON for enrich_close --mode book_close (required unless --probe-only).",
    )
    parser.add_argument("--sport", default=DEFAULT_SPORT)
    parser.add_argument("--regions", default=DEFAULT_REGIONS)
    parser.add_argument(
        "--event-id",
        default=None,
        help="Odds-API event id. If omitted, fetches all upcoming NFL events.",
    )
    parser.add_argument(
        "--markets",
        default=",".join(DEFAULT_PROP_MARKETS),
        help="Comma-separated Odds-API market keys.",
    )
    parser.add_argument(
        "--historical-date",
        default=None,
        help="ISO timestamp for historical event odds (paid tier). Example: 2026-09-20T17:00:00Z",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=None,
        help="Optional Outlier pack; copies matchup/event_id onto short-key matches.",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Print plan coverage JSON (no close-feed write) and exit.",
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        default=None,
        help="Optional path to write redacted raw Odds-API JSON (secrets stripped).",
    )
    parser.add_argument(
        "--bookmakers",
        default=None,
        help="Optional comma-separated bookmaker keys to keep (e.g. draftkings,fanduel).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.probe_only and args.out is None:
        parser.error("--out is required unless --probe-only")

    key = resolve_odds_api_key()
    if not key:
        from outlier_nfl.close_feed import close_feed_blocker_message

        raise SystemExit("error: missing Odds API key. " + close_feed_blocker_message())

    if args.probe_only:
        report = probe_plan_coverage(api_key=key)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get("sports_ok") else 2

    markets = tuple(m.strip() for m in str(args.markets).split(",") if m.strip())
    book_filter = None
    if args.bookmakers:
        book_filter = [b.strip() for b in args.bookmakers.split(",") if b.strip()]

    predictions = None
    if args.predictions is not None:
        predictions = safe_read_json(args.predictions)
        if not isinstance(predictions, (dict, list)):
            raise SystemExit(f"error: bad predictions JSON: {args.predictions}")

    event_ids: list[str]
    if args.event_id:
        event_ids = [args.event_id]
    else:
        events = list_events(api_key=key, sport=args.sport)
        event_ids = [str(e.get("id")) for e in events if e.get("id")]
        if not event_ids:
            raise SystemExit("error: no upcoming NFL events returned by Odds API")

    all_records: list[dict[str, Any]] = []
    raw_bundle: list[Any] = []
    for eid in event_ids:
        payload = fetch_event_odds(
            api_key=key,
            event_id=eid,
            sport=args.sport,
            regions=args.regions,
            markets=markets,
            historical_date=args.historical_date,
        )
        raw_bundle.append(redact_odds_api_payload(payload))
        all_records.extend(
            build_close_feed_from_event_odds(
                payload,
                predictions=predictions,
                bookmaker_filter=book_filter,
            )
        )

    if args.save_raw is not None:
        args.save_raw.parent.mkdir(parents=True, exist_ok=True)
        args.save_raw.write_text(
            json.dumps(raw_bundle if len(raw_bundle) > 1 else raw_bundle[0], indent=2),
            encoding="utf-8",
        )

    note = (
        "Odds-API NFL player-prop capture → Outlier book_close feed. "
        "Best American price across requested books. "
        "Never a copy of snapshot best_odds."
    )
    if args.historical_date:
        note += f" historical_date={args.historical_date}."
    write_close_feed(args.out, all_records, source=CLOSE_SOURCE_BOOK, note=note)
    summary = validate_close_feed_records(all_records)
    n_aligned = sum(1 for r in all_records if r.get("aligned_to_predictions"))
    print(
        f"Wrote {args.out} schema={CLOSE_FEED_SCHEMA_VERSION} "
        f"n_records={summary['n_records']} n_with_odds={summary['n_with_close_odds']} "
        f"n_aligned={n_aligned} events={len(event_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
