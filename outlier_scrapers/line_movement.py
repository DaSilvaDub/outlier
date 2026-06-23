from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .api import AuthRequiredError, OutlierApiClient, OutlierApiError
from .normalizer import (
    game_sides,
    _to_float,
    _to_int,
    detect_scope,
    implied_probability,
    normalize_book_label,
    parse_market_descriptor,
    percent_number,
)
from .paths import league_paths
from .props import write_json
from .registry import SportConfig, get_sport_config, normalize_market, supported_leagues


SIDES = ("OVER", "UNDER")
DEFAULT_WORKERS = 4
STALE_PROPS_MAX_AGE_HOURS = 12.0
DEFAULT_RETRY_403_COOLDOWN_SECONDS = 15.0
DEFAULT_RETRY_403_WORKERS = 1
EV_METHOD_PRIORITY = ("AVERAGE", "MULTIPLICATIVE", "ADDITIVE", "SHIN", "POWER", "PROBIT", "WORSTCASE")


class StalePropsError(ValueError):
    pass


@dataclass(frozen=True)
class PropsFreshness:
    generated_at: str | None
    age_hours: float | None
    is_stale: bool
    stale_reason: str | None


@dataclass(frozen=True)
class PropsMarketSource:
    market_ids: list[str]
    context: dict[str, dict[str, Any]]
    path: str
    freshness: PropsFreshness


def _diff_float(current: float | None, opened: float | None) -> float | None:
    if current is None or opened is None:
        return None
    return round(current - opened, 3)


def _diff_int(current: int | None, opened: int | None) -> int | None:
    if current is None or opened is None:
        return None
    return current - opened


def _side_token(value: Any) -> str:
    token = str(value or "").strip().upper()
    return token if token in SIDES else ""


def _side_for_source(value: Any, proposition: str = "", source: str = "props") -> str:
    """Resolve a side token, gated by feed: props accept OVER/UNDER only; games
    accept the proposition's valid team sides (HOME/AWAY[/DRAW]) or any token
    when the proposition is unrecognized."""
    token = str(value or "").strip().upper()
    if source == "props":
        return token if token in SIDES else ""
    valid = game_sides(proposition)
    return token if (not valid or token in valid) else ""


def _ordered_market_ids(props_latest: dict[str, Any], limit: int | None = None) -> tuple[list[str], dict[str, dict[str, Any]]]:
    records = props_latest.get("records")
    if not isinstance(records, list):
        return [], {}

    seen: set[str] = set()
    ids: list[str] = []
    context: dict[str, dict[str, Any]] = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        market_id = str(row.get("market_id") or "").strip()
        if not market_id or market_id in seen:
            continue
        seen.add(market_id)
        ids.append(market_id)
        context[market_id] = {
            "event_id": row.get("event_id"),
            "market": row.get("market"),
            "market_raw": row.get("market_raw"),
            "player": row.get("player"),
            "player_raw": row.get("player_raw"),
            "team": row.get("team"),
            "team_raw": row.get("team_raw"),
            "opponent": row.get("opponent"),
            "opponent_raw": row.get("opponent_raw"),
            "matchup": row.get("matchup"),
            "matchup_raw": row.get("matchup_raw"),
            "sport_context": row.get("sport_context"),
        }
        if limit is not None and len(ids) >= limit:
            break
    return ids, context


def _parse_props_generated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _props_freshness(
    props_latest: dict[str, Any],
    *,
    now: datetime,
    max_age_hours: float = STALE_PROPS_MAX_AGE_HOURS,
) -> PropsFreshness:
    generated_at = props_latest.get("generated_at")
    generated_text = generated_at if isinstance(generated_at, str) else None
    parsed = _parse_props_generated_at(generated_text)
    if parsed is None:
        reason = "missing props generated_at" if not generated_text else "invalid props generated_at"
        return PropsFreshness(
            generated_at=generated_text,
            age_hours=None,
            is_stale=True,
            stale_reason=reason,
        )

    if parsed.tzinfo is None:
        generated_local = parsed.replace(tzinfo=now.tzinfo)
    elif now.tzinfo is None:
        generated_local = parsed.replace(tzinfo=None)
    else:
        generated_local = parsed.astimezone(now.tzinfo)

    raw_age_hours = (now - generated_local).total_seconds() / 3600.0
    age_hours = round(raw_age_hours, 2)
    reasons: list[str] = []
    if generated_local.date() != now.date():
        reasons.append(
            f"props date {generated_local.date().isoformat()} != today {now.date().isoformat()}"
        )
    if raw_age_hours > max_age_hours:
        reasons.append(f"props age {raw_age_hours:.3f}h > {max_age_hours:.2f}h")
    if raw_age_hours < -1.0:
        reasons.append(f"props generated_at is {abs(raw_age_hours):.3f}h in the future")

    return PropsFreshness(
        generated_at=generated_text,
        age_hours=age_hours,
        is_stale=bool(reasons),
        stale_reason="; ".join(reasons) if reasons else None,
    )


def _stale_props_warning(league: str, props_latest: str, freshness: PropsFreshness) -> str:
    age_text = "unknown" if freshness.age_hours is None else f"{freshness.age_hours:.2f}"
    generated_at = freshness.generated_at or "missing"
    reason = freshness.stale_reason or "unknown freshness"
    return (
        f"WARNING: {league} props_latest is stale ({reason}). "
        f"props_generated_at={generated_at}; props_age_hours={age_text}; "
        f"props_latest={props_latest}. Run props first or use "
        f"python -m outlier_scrapers.refresh --league {league} --props --line-movement."
    )


def _props_freshness_status_fields(
    freshness: PropsFreshness,
    *,
    warning: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "props_generated_at": freshness.generated_at,
        "props_age_hours": freshness.age_hours,
        "props_is_stale": freshness.is_stale,
        "props_stale_reason": freshness.stale_reason,
    }
    if warning:
        fields["props_freshness_warning"] = warning
    return fields


def _http_status_from_error_text(text: str) -> int | None:
    match = re.search(r"\bHTTP\s+(\d{3})\b", text)
    return int(match.group(1)) if match else None


def _fetch_error(market_id: str, exc: OutlierApiError) -> dict[str, Any]:
    error = str(exc)[:200]
    record: dict[str, Any] = {
        "market_id": market_id,
        "status": "error",
        "error": error,
    }
    http_status = _http_status_from_error_text(error)
    if http_status is not None:
        record["http_status"] = http_status
    return record


def _is_http_403_error(error: dict[str, Any]) -> bool:
    if error.get("http_status") == 403:
        return True
    return _http_status_from_error_text(str(error.get("error") or "")) == 403


def _fetch_market_payloads(
    client: OutlierApiClient,
    market_ids: list[str],
    *,
    workers: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    payloads_by_id: dict[str, dict[str, Any]] = {}
    fetch_errors: list[dict[str, Any]] = []
    worker_count = max(1, int(workers or 1))
    if worker_count == 1 or len(market_ids) <= 1:
        for market_id in market_ids:
            try:
                payloads_by_id[market_id] = client.fetch_market(market_id)
            except AuthRequiredError:
                raise
            except OutlierApiError as exc:
                fetch_errors.append(_fetch_error(market_id, exc))
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(client.fetch_market, market_id): market_id for market_id in market_ids}
            for future in as_completed(futures):
                market_id = futures[future]
                try:
                    payloads_by_id[market_id] = future.result()
                except AuthRequiredError:
                    raise
                except OutlierApiError as exc:
                    fetch_errors.append(_fetch_error(market_id, exc))
    return payloads_by_id, fetch_errors


def load_props_market_source(
    league: str,
    limit: int | None = None,
    *,
    now: datetime | None = None,
) -> PropsMarketSource:
    config = get_sport_config(league)
    path = league_paths(config.league_id).normalized / f"{config.league_id.lower()}_props_latest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing props file {path}. Run python -m outlier_scrapers.props --league {config.league_id} --all first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    market_ids, context = _ordered_market_ids(payload, limit=limit)
    freshness = _props_freshness(payload, now=now or datetime.now().astimezone())
    return PropsMarketSource(
        market_ids=market_ids,
        context=context,
        path=str(path),
        freshness=freshness,
    )



def load_games_market_source(
    league: str,
    limit: int | None = None,
    *,
    now: datetime | None = None,
) -> PropsMarketSource:
    config = get_sport_config(league)
    path = league_paths(config.league_id).normalized / f"{config.league_id.lower()}_games_latest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing games file {path}. Run python -m outlier_scrapers.refresh --league {config.league_id} --games first."
        )
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))

    market_ids_set = set()
    context = {}
    for record in payload.get("records", []):
        if record.get("scope") == "full_game":
            m_id = record.get("market_id")
            if m_id:
                market_ids_set.add(m_id)
                # context can just map m_id to the record for event_id etc
                if m_id not in context:
                    context[m_id] = {
                        "event_id": record.get("event_id"),
                        "market_raw": record.get("market_raw"),
                    }

    market_ids = sorted(list(market_ids_set))
    if limit is not None:
        market_ids = market_ids[:limit]

    freshness = _props_freshness(payload, now=now or datetime.now().astimezone())
    return PropsMarketSource(
        market_ids=market_ids,
        context=context,
        path=str(path),
        freshness=freshness,
    )

def load_props_market_ids(league: str, limit: int | None = None) -> tuple[list[str], dict[str, dict[str, Any]], str]:
    source = load_props_market_source(league, limit=limit)
    return source.market_ids, source.context, source.path



def _market_context(
    payload: dict[str, Any],
    config: SportConfig,
    props_context: dict[str, Any] | None,
) -> dict[str, Any]:
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    player = market.get("player") if isinstance(market.get("player"), dict) else {}
    props_context = props_context or {}
    market_label = market.get("label") or props_context.get("market_raw") or ""
    proposition = market.get("proposition")
    market_raw = parse_market_descriptor({"marketLabel": market_label, "proposition": proposition})
    scope = detect_scope(market_label, market_raw)
    canonical_market = None
    if scope == "full_game":
        canonical_market = normalize_market(config, proposition) or normalize_market(config, market_raw)

    return {
        "event_id": market.get("eventId") or props_context.get("event_id"),
        "market_id": market.get("marketId") or props_context.get("market_id"),
        "market": canonical_market,
        "market_raw": market_raw or props_context.get("market_raw"),
        "market_label": market_label or None,
        "market_type": market.get("marketType"),
        "prop_type": market.get("propType"),
        "proposition": proposition,
        "player": player.get("fullName") or props_context.get("player"),
        "player_raw": player.get("fullName") or props_context.get("player_raw"),
        "player_id": player.get("playerId"),
        "team_id": player.get("teamId"),
        "team": props_context.get("team"),
        "team_raw": props_context.get("team_raw"),
        "opponent": props_context.get("opponent"),
        "opponent_raw": props_context.get("opponent_raw"),
        "matchup": props_context.get("matchup"),
        "matchup_raw": props_context.get("matchup_raw"),
        "is_active": market.get("isActive"),
        "scope": scope,
    }


def _current_outcomes_by_side(market: dict[str, Any], proposition: str = "", source: str = "props") -> dict[str, dict[str, Any]]:
    outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
    by_side: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        token = str(outcome.get("position") or outcome.get("label") or "").strip().upper()
        if source == "props":
            side = token if token in SIDES else ""
        else:
            valid = game_sides(proposition)
            side = token if (not valid or token in valid) else ""
        if not side:
            continue
        existing = by_side.get(side)
        if existing is None or (outcome.get("primary") is True and existing.get("primary") is not True):
            by_side[side] = outcome
    return by_side


def _history_by_side(payload: dict[str, Any], proposition: str = "", source: str = "props") -> dict[str, list[dict[str, Any]]]:
    market_history = payload.get("marketHistory")
    movements = []
    if isinstance(market_history, dict) and isinstance(market_history.get("marketMovements"), list):
        movements = market_history["marketMovements"]

    if source == "props":
        valid_sides = SIDES
        by_side: dict[str, list[dict[str, Any]]] = {side: [] for side in valid_sides}
    else:
        valid_sides = game_sides(proposition)
        by_side: dict[str, list[dict[str, Any]]] = {side: [] for side in valid_sides}

    for movement in movements:
        if not isinstance(movement, dict):
            continue
        value = movement.get("value") if isinstance(movement.get("value"), dict) else {}
        movement_types = movement.get("movementTypes")
        types = [str(item) for item in movement_types] if isinstance(movement_types, list) else []
        for side_token, side_payload in value.items():
            side_token = str(side_token).upper()
            if source == "props" and side_token not in SIDES:
                continue
            if source == "games" and valid_sides and side_token not in valid_sides:
                continue
            if not isinstance(side_payload, dict):
                continue
            if side_token not in by_side:
                by_side[side_token] = []
            by_side[side_token].append(
                {
                    "updated": movement.get("updated"),
                    "movement_types": types,
                    "line": _to_float(side_payload.get("line")),
                    "odds": _to_int(side_payload.get("odds")),
                    "decimal_odds": _to_float(side_payload.get("decimalOdds")),
                }
            )

    for side, rows in by_side.items():
        by_side[side] = sorted(rows, key=lambda row: str(row.get("updated") or ""))
    return by_side


def _books_from_outcome(outcome: dict[str, Any]) -> list[str]:
    books = outcome.get("books") if isinstance(outcome.get("books"), list) else []
    labels = [normalize_book_label(book) for book in books if str(book or "").strip()]
    odds = outcome.get("odds")
    if isinstance(odds, list):
        for entry in odds:
            if not isinstance(entry, dict):
                continue
            book = entry.get("book")
            if isinstance(book, str) and book.strip():
                label = normalize_book_label(book)
                if label not in labels:
                    labels.append(label)
            elif isinstance(book, dict):
                raw = book.get("name") or book.get("label") or book.get("bookId") or book.get("id")
                if raw:
                    label = normalize_book_label(raw)
                    if label not in labels:
                        labels.append(label)
    return labels


def _american_prices_from_outcome(outcome: dict[str, Any]) -> list[int]:
    odds = outcome.get("odds")
    if isinstance(odds, list):
        prices: list[int] = []
        for entry in odds:
            if not isinstance(entry, dict):
                continue
            price = _to_int(entry.get("american"))
            if price is not None:
                prices.append(price)
        return prices
    price = _to_int(odds)
    return [price] if price is not None else []


def _best_american_price(outcome: dict[str, Any]) -> int | None:
    prices = _american_prices_from_outcome(outcome)
    return max(prices) if prices else None


def _outcome_id(outcome: dict[str, Any]) -> str:
    return str(outcome.get("outcomeId") or outcome.get("id") or "").strip()


def _current_outcomes_by_id(market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
    by_id: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        outcome_id = _outcome_id(outcome)
        if not outcome_id:
            continue
        existing = by_id.get(outcome_id)
        if existing is None or (outcome.get("primary") is True and existing.get("primary") is not True):
            by_id[outcome_id] = outcome
    return by_id


def _ev_outcomes(market: dict[str, Any]) -> list[dict[str, Any]]:
    ev_outcomes = market.get("evOutcomes")
    if not isinstance(ev_outcomes, list):
        return []
    return [outcome for outcome in ev_outcomes if isinstance(outcome, dict)]


def _ev_outcomes_by_id(market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for outcome in _ev_outcomes(market):
        outcome_id = str(outcome.get("outcomeId") or "").strip()
        if outcome_id:
            by_id[outcome_id] = outcome
    return by_id


def _ev_outcomes_by_side(
    market: dict[str, Any],
    current_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_side: dict[str, list[dict[str, Any]]] = {}
    for ev_outcome in _ev_outcomes(market):
        outcome_id = str(ev_outcome.get("outcomeId") or "").strip()
        current = current_by_id.get(outcome_id, {})
        side = _side_token(
            current.get("position")
            or current.get("label")
            or ev_outcome.get("position")
            or ev_outcome.get("side")
            or ev_outcome.get("label")
        )
        if side:
            by_side.setdefault(side, []).append(ev_outcome)
    return {side: rows[0] for side, rows in by_side.items() if len(rows) == 1}


def _selected_ev_method(ev_outcome: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
    if not isinstance(ev_outcome, dict):
        return None, {}
    calculated_ev = ev_outcome.get("calculatedEV")
    if isinstance(calculated_ev, dict):
        for method in EV_METHOD_PRIORITY:
            method_payload = calculated_ev.get(method)
            if isinstance(method_payload, dict):
                return method, method_payload
    return None, {}


def _ev_metric_fields(ev_outcome: dict[str, Any] | None, *, prefix: str = "ev_") -> dict[str, Any]:
    if not isinstance(ev_outcome, dict):
        return {
            f"{prefix}calculated_ev_pct": None,
            f"{prefix}calculated_ev_method": None,
            f"{prefix}kelly_pct": None,
            f"{prefix}devig_odds": None,
            f"{prefix}devig_decimal": None,
            f"{prefix}vig_pct": None,
            f"{prefix}width_pct": None,
        }

    method_used, method_payload = _selected_ev_method(ev_outcome)
    calculated_ev = ev_outcome.get("calculatedEV")
    ev_val = method_payload.get("ev")
    kelly_val = method_payload.get("kelly")
    if not isinstance(calculated_ev, dict):
        ev_val = calculated_ev

    devig = ev_outcome.get("deVigOdds")
    if not isinstance(devig, dict):
        devig = method_payload.get("noVigOdds")
    devig_american = None
    devig_decimal = None
    if isinstance(devig, dict):
        devig_american = _to_int(devig.get("american"))
        devig_decimal = _to_float(devig.get("decimal"))

    return {
        f"{prefix}calculated_ev_pct": percent_number(ev_val),
        f"{prefix}calculated_ev_method": method_used,
        f"{prefix}kelly_pct": percent_number(kelly_val),
        f"{prefix}devig_odds": devig_american,
        f"{prefix}devig_decimal": devig_decimal,
        f"{prefix}vig_pct": percent_number(ev_outcome.get("vig")),
        f"{prefix}width_pct": percent_number(ev_outcome.get("width")),
    }


def _ev_sport_context(ev_outcome: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ev_outcome, dict):
        return {
            "calculated_ev_methods": None,
            "selected_ev_method": None,
            "ev_method_priority": list(EV_METHOD_PRIORITY),
        }
    method_used, _ = _selected_ev_method(ev_outcome)
    calculated_ev = ev_outcome.get("calculatedEV")
    return {
        "calculated_ev_methods": calculated_ev if isinstance(calculated_ev, dict) else None,
        "selected_ev_method": method_used,
        "ev_method_priority": list(EV_METHOD_PRIORITY),
    }


def _ev_book_rows(ev_outcome: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(ev_outcome, dict) or not isinstance(ev_outcome.get("books"), dict):
        return []

    rows: list[dict[str, Any]] = []
    for book_key, payload in ev_outcome["books"].items():
        book_payload = payload if isinstance(payload, dict) else {}
        book_meta = book_payload.get("book") if isinstance(book_payload.get("book"), dict) else {}
        raw_book = str(
            book_meta.get("name")
            or book_meta.get("label")
            or book_meta.get("bookId")
            or book_meta.get("id")
            or book_key
            or ""
        ).strip()
        book_odds = _to_int(book_payload.get("american"))
        rows.append(
            {
                "book": normalize_book_label(raw_book),
                "book_raw": str(book_key or raw_book or "").strip() or None,
                "book_odds": book_odds,
                "book_decimal_odds": _to_float(book_payload.get("decimal")),
                "book_ip_pct": implied_probability(book_odds),
                "book_state": str(book_payload.get("state") or "").strip() or None,
                "max_bet": _to_float(book_payload.get("maxBet")),
            }
        )
    return sorted(rows, key=lambda row: str(row.get("book") or row.get("book_raw") or ""))


def normalize_ev_records(
    *,
    league: str,
    payload: dict[str, Any],
    props_context: dict[str, Any] | None = None,
    source: str = "props",
) -> list[dict[str, Any]]:
    config = get_sport_config(league)
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    context = _market_context(payload, config, props_context)
    proposition = context.get("market_raw") or market.get("proposition") or payload.get("proposition") or ""
    current_by_id = _current_outcomes_by_id(market)
    current_by_side = _current_outcomes_by_side(market, proposition, source)

    rows: list[dict[str, Any]] = []
    for ev_outcome in _ev_outcomes(market):
        outcome_id = str(ev_outcome.get("outcomeId") or "").strip()
        current = current_by_id.get(outcome_id, {})
        side = _side_for_source(
            current.get("position")
            or current.get("label")
            or ev_outcome.get("position")
            or ev_outcome.get("side")
            or ev_outcome.get("label"),
            proposition,
            source,
        )
        if not current and side:
            current = current_by_side.get(side, {})
        book_rows = _ev_book_rows(ev_outcome)
        base = {
            "league": config.league_id,
            "event_id": context.get("event_id"),
            "market_id": context.get("market_id"),
            "outcome_id": outcome_id or None,
            "side": side or None,
            "player": context.get("player"),
            "player_raw": context.get("player_raw"),
            "player_id": context.get("player_id"),
            "team": context.get("team"),
            "team_raw": context.get("team_raw"),
            "opponent": context.get("opponent"),
            "opponent_raw": context.get("opponent_raw"),
            "matchup": context.get("matchup"),
            "matchup_raw": context.get("matchup_raw"),
            "market": context.get("market"),
            "market_raw": context.get("market_raw"),
            "market_label": context.get("market_label"),
            "market_type": context.get("market_type"),
            "prop_type": context.get("prop_type"),
            "proposition": context.get("proposition"),
            "scope": context.get("scope"),
            "is_active": context.get("is_active"),
            "current_line": _to_float(current.get("line")),
            "current_odds": _best_american_price(current),
            "current_ip_pct": implied_probability(_best_american_price(current)),
            **_ev_metric_fields(ev_outcome, prefix=""),
            "sport_context": _ev_sport_context(ev_outcome),
        }
        if not book_rows:
            rows.append(
                {
                    **base,
                    "book": None,
                    "book_raw": None,
                    "book_odds": None,
                    "book_decimal_odds": None,
                    "book_ip_pct": None,
                    "book_state": None,
                    "max_bet": None,
                }
            )
            continue
        for book_row in book_rows:
            rows.append({**base, **book_row})

    return sorted(
        rows,
        key=lambda row: (
            str(row.get("matchup_raw") or ""),
            str(row.get("player_raw") or ""),
            str(row.get("market_raw") or ""),
            str(row.get("side") or ""),
            str(row.get("book") or row.get("book_raw") or ""),
        ),
    )


def normalize_market_detail(
    *,
    league: str,
    payload: dict[str, Any],
    props_context: dict[str, Any] | None = None,
    source: str = "props",
) -> list[dict[str, Any]]:
    config = get_sport_config(league)
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    context = _market_context(payload, config, props_context)
    proposition = context.get("market_raw") or market.get("proposition") or payload.get("proposition") or ""
    current_by_side = _current_outcomes_by_side(market, proposition, source)
    current_by_id = _current_outcomes_by_id(market)
    ev_by_outcome_id = _ev_outcomes_by_id(market)
    ev_by_side = _ev_outcomes_by_side(market, current_by_id)
    history_by_side = _history_by_side(payload, proposition, source)

    all_sides = set(current_by_side.keys()) | {s for s, h in history_by_side.items() if h}
    sides = sorted([side for side in SIDES if side in all_sides]) if source == "props" else sorted(all_sides)

    rows: list[dict[str, Any]] = []
    for side in sides:
        current = current_by_side.get(side, {})
        history = history_by_side.get(side, [])
        opened = history[0] if history else {}
        latest = history[-1] if history else {}
        current_line = _to_float(current.get("line"))
        current_odds = _best_american_price(current)
        current_outcome_id = _outcome_id(current)
        ev_outcome = ev_by_outcome_id.get(current_outcome_id) or ev_by_side.get(side)
        ev_book_rows = _ev_book_rows(ev_outcome)
        open_line = opened.get("line")
        open_odds = opened.get("odds")
        movement_types = sorted(
            {
                movement_type
                for movement in history
                for movement_type in movement.get("movement_types", [])
                if movement_type
            }
        )

        rows.append(
            {
                "league": config.league_id,
                "event_id": context.get("event_id"),
                "market_id": context.get("market_id"),
                "outcome_id": current_outcome_id or None,
                "side": side,
                "player": context.get("player"),
                "player_raw": context.get("player_raw"),
                "player_id": context.get("player_id"),
                "team": context.get("team"),
                "team_raw": context.get("team_raw"),
                "opponent": context.get("opponent"),
                "opponent_raw": context.get("opponent_raw"),
                "matchup": context.get("matchup"),
                "matchup_raw": context.get("matchup_raw"),
                "market": context.get("market"),
                "market_raw": context.get("market_raw"),
                "market_label": context.get("market_label"),
                "market_type": context.get("market_type"),
                "prop_type": context.get("prop_type"),
                "proposition": context.get("proposition"),
                "scope": context.get("scope"),
                "is_active": context.get("is_active"),
                "current_line": current_line,
                "current_odds": current_odds,
                "current_ip_pct": implied_probability(current_odds),
                "current_odds_count": len(_american_prices_from_outcome(current)),
                "open_line": open_line,
                "open_odds": open_odds,
                "open_ip_pct": implied_probability(open_odds),
                "latest_history_line": latest.get("line"),
                "latest_history_odds": latest.get("odds"),
                "latest_movement_at": latest.get("updated"),
                # Deltas describe the recorded consensus movement path
                # (open -> latest history) so line and odds are measured on the
                # same basis. current_line/current_odds remain the separate live
                # best-book snapshot.
                "line_delta_from_open": _diff_float(latest.get("line"), open_line),
                "odds_delta_from_open": _diff_int(latest.get("odds"), open_odds),
                "movement_count": len(history),
                "movement_types": movement_types,
                "history_available": bool(history),
                "books": _books_from_outcome(current),
                "book_count": len(_books_from_outcome(current)),
                "ev_available": ev_outcome is not None,
                "ev_outcome_id": str(ev_outcome.get("outcomeId") or "").strip() if ev_outcome else None,
                "ev_book_count": len(ev_book_rows),
                "ev_books": [str(row.get("book") or row.get("book_raw") or "") for row in ev_book_rows],
                **_ev_metric_fields(ev_outcome),
                "movements": history,
                "sport_context": {
                    "market_group_id": market.get("marketGroupId"),
                    "market_group_sort_order": market.get("marketGroupSortOrder"),
                    "include_overtime": market.get("includeOvertime"),
                    "source": "sportsdata/markets/{marketId}",
                    **_ev_sport_context(ev_outcome),
                },
            }
        )
    return rows


def build_line_movement_payload(
    *,
    league: str,
    market_payloads: list[dict[str, Any]],
    props_context: dict[str, dict[str, Any]],
    source_url_template: str,
    props_latest: str,
    fetch_errors: list[dict[str, Any]] | None = None,
    source: str = "props",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    ev_rows: list[dict[str, Any]] = []
    ev_outcome_count = 0
    markets_with_ev: set[str] = set()
    markets_without_records: list[str | None] = []
    for payload in market_payloads:
        market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
        market_id = str(market.get("marketId") or "").strip() or None
        ev_outcomes = _ev_outcomes(market)
        if ev_outcomes:
            ev_outcome_count += len(ev_outcomes)
            if market_id:
                markets_with_ev.add(market_id)
            ev_rows.extend(
                normalize_ev_records(
                    league=league,
                    payload=payload,
                    props_context=props_context.get(market_id or "", {}),
                    source=source,
                )
            )
        market_rows = normalize_market_detail(
            league=league,
            payload=payload,
            props_context=props_context.get(market_id or "", {}),
            source=source,
        )
        if market_rows:
            rows.extend(market_rows)
        else:
            markets_without_records.append(market_id)

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "league": get_sport_config(league).league_id,
        "source_url_template": source_url_template,
        "source_method": "api",
        "props_latest": props_latest,
        "market_count": len(market_payloads),
        "markets_with_records": len({row.get("market_id") for row in rows if row.get("market_id")}),
        "markets_without_records": markets_without_records,
        "record_count": len(rows),
        "markets_with_ev_count": len(markets_with_ev),
        "ev_outcome_count": ev_outcome_count,
        "ev_record_count": len(ev_rows),
        "fetch_errors": fetch_errors or [],
        "primary_record_array": "records",
        "records": sorted(
            rows,
            key=lambda row: (
                str(row.get("matchup_raw") or ""),
                str(row.get("player_raw") or ""),
                str(row.get("market_raw") or ""),
                str(row.get("side") or ""),
            ),
        ),
        "ev_records": ev_rows,
        "data_contract": {
            "dataset": "outlier_line_movement",
            "version": "1.1",
            "intended_use": "Standalone Outlier market-detail line movement for betting triage.",
            "join_key": "league+market_id",
            "row_grain": "one row per market_id+side",
            "secondary_record_arrays": {
                "ev_records": "one row per market_id+outcome_id+book; no-book EV outcomes keep a null-book row",
            },
        },
    }


def _status_report_name(source: str) -> str:
    """Status report filename for a feed. Games get a ``games_`` prefix; props
    keep the unprefixed name used by the success path."""
    return "games_line_movement_status_latest.json" if source == "games" else "line_movement_status_latest.json"


def export_line_movement_for_league(
    client: OutlierApiClient,
    league: str,
    *,
    source: str = "props",
    limit: int | None = None,
    workers: int = DEFAULT_WORKERS,
    require_fresh_props: bool = False,
    now: datetime | None = None,
    retry_failed_403: bool = True,
    retry_403_cooldown_seconds: float = DEFAULT_RETRY_403_COOLDOWN_SECONDS,
    retry_403_workers: int = DEFAULT_RETRY_403_WORKERS,
) -> dict[str, Any]:
    config = get_sport_config(league)
    paths = league_paths(config.league_id).ensure()
    export_now = now or datetime.now().astimezone()
    if source == "games":
        props_source = load_games_market_source(config.league_id, limit=limit, now=export_now)
    else:
        props_source = load_props_market_source(config.league_id, limit=limit, now=export_now)
    market_ids = props_source.market_ids
    props_context = props_source.context
    props_latest = props_source.path
    freshness = props_source.freshness
    freshness_warning = (
        _stale_props_warning(config.league_id, props_latest, freshness)
        if freshness.is_stale
        else None
    )
    freshness_status = _props_freshness_status_fields(
        freshness,
        warning=freshness_warning,
    )
    if freshness_warning:
        print(freshness_warning, file=sys.stderr)
        if require_fresh_props:
            report = {
                "league": config.league_id,
                "status": "stale_props",
                "generated_at": export_now.isoformat(),
                "props_latest": props_latest,
                "error": freshness_warning[:300],
                **freshness_status,
            }
            write_json(paths.reports / _status_report_name(source), report)
            raise StalePropsError(freshness_warning)

    worker_count = max(1, int(workers or 1))
    payloads_by_id, first_pass_errors = _fetch_market_payloads(
        client,
        market_ids,
        workers=worker_count,
    )
    retry_403_market_ids = [
        str(error.get("market_id"))
        for error in first_pass_errors
        if error.get("market_id") and _is_http_403_error(error)
    ]
    retry_403_count = len(retry_403_market_ids)
    retry_403_recovered = 0
    retry_403_residual_errors = 0
    retry_403_worker_count = max(1, int(retry_403_workers or 1))
    retry_errors: list[dict[str, Any]] = []
    if retry_failed_403 and retry_403_market_ids:
        if retry_403_cooldown_seconds > 0:
            time.sleep(retry_403_cooldown_seconds)
        retry_payloads, retry_errors = _fetch_market_payloads(
            client,
            retry_403_market_ids,
            workers=retry_403_worker_count,
        )
        payloads_by_id.update(retry_payloads)
        retry_403_recovered = len(retry_payloads)
        retry_403_residual_errors = len(retry_errors)

    fetch_errors = [
        error
        for error in first_pass_errors
        if not (retry_failed_403 and _is_http_403_error(error))
    ]
    fetch_errors.extend(retry_errors)
    retry_403_summary = {
        "retry_403_enabled": retry_failed_403,
        "retry_403_markets": retry_403_count if retry_failed_403 else 0,
        "retry_403_recovered": retry_403_recovered,
        "retry_403_residual_errors": retry_403_residual_errors,
        "retry_403_cooldown_seconds": retry_403_cooldown_seconds if retry_failed_403 else 0,
        "retry_403_workers": retry_403_worker_count if retry_failed_403 else 0,
    }

    market_payloads = [payloads_by_id[market_id] for market_id in market_ids if market_id in payloads_by_id]

    exported_at = datetime.now().astimezone().isoformat()
    raw_payload = {
        "exported_at": exported_at,
        "league": config.league_id,
        "source": "Outlier authenticated API",
        "props_latest": props_latest,
        "market_ids_requested": market_ids,
        "workers": worker_count,
        **retry_403_summary,
        "markets": market_payloads,
        "fetch_errors": fetch_errors,
    }
    prefix = f"{config.league_id.lower()}_{source}_" if source == "games" else f"{config.league_id.lower()}_"
    raw_latest = paths.raw / f"{prefix}line_movement_raw_latest.json"
    raw_archive = paths.timestamped(paths.raw, f"{source}_line_movement_raw" if source == "games" else "line_movement_raw")
    write_json(raw_latest, raw_payload)
    write_json(raw_archive, raw_payload)

    source_template = client.url_for("/sportsdata/markets/{marketId}")
    normalized = build_line_movement_payload(
        league=config.league_id,
        market_payloads=market_payloads,
        props_context=props_context,
        source_url_template=source_template,
        props_latest=props_latest,
        fetch_errors=fetch_errors,
        source=source,
    )
    normalized_latest = paths.normalized / f"{prefix}line_movement_latest.json"
    normalized_archive = paths.timestamped(paths.normalized, f"{source}_line_movement" if source == "games" else "line_movement")
    write_json(normalized_latest, normalized)
    write_json(normalized_archive, normalized)

    status_value = "partial" if fetch_errors else "ok"
    status = {
        "league": config.league_id,
        "status": status_value,
        "raw_latest": str(raw_latest),
        "normalized_latest": str(normalized_latest),
        "props_latest": props_latest,
        "markets_requested": len(market_ids),
        "markets_fetched": len(market_payloads),
        "markets_with_records": normalized["markets_with_records"],
        "markets_without_record_count": len(normalized["markets_without_records"]),
        "fetch_error_count": len(fetch_errors),
        "record_count": normalized["record_count"],
        "markets_with_ev_count": normalized["markets_with_ev_count"],
        "ev_outcome_count": normalized["ev_outcome_count"],
        "ev_record_count": normalized["ev_record_count"],
        "workers": worker_count,
        "first_pass_errors": first_pass_errors,
        "retry_403_recovered": retry_403_recovered,
        "retry_403_still_failed": retry_403_residual_errors,
        **retry_403_summary,
        **freshness_status,
    }
    write_json(paths.reports / _status_report_name(source), status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Outlier market-detail line movement")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument("--source", choices=["props", "games"], default="props", help="Source payload to read markets from")
    parser.add_argument("--all", action="store_true", help="Accepted for clarity; exports all market IDs")
    parser.add_argument("--limit", type=int, help="Limit unique market IDs for a smoke run")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent market-detail fetches")
    parser.add_argument(
        "--no-retry-failed-403",
        action="store_true",
        help="Disable the post-run single-market retry pass for HTTP 403 fetch errors",
    )
    parser.add_argument(
        "--retry-403-cooldown-seconds",
        type=float,
        default=DEFAULT_RETRY_403_COOLDOWN_SECONDS,
        help="Cooldown before retrying first-pass HTTP 403 market failures",
    )
    parser.add_argument(
        "--retry-403-workers",
        type=int,
        default=DEFAULT_RETRY_403_WORKERS,
        help="Workers for the HTTP 403 mop-up retry pass",
    )
    parser.add_argument(
        "--require-fresh-props",
        action="store_true",
        help="Exit before fetching markets if props_latest is stale or missing generated_at",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        print("--limit must be a positive integer")
        return 2
    if args.workers <= 0:
        print("--workers must be a positive integer")
        return 2
    if args.retry_403_workers <= 0:
        print("--retry-403-workers must be a positive integer")
        return 2
    if args.retry_403_cooldown_seconds < 0:
        print("--retry-403-cooldown-seconds must be non-negative")
        return 2

    try:
        client = OutlierApiClient()
        status = export_line_movement_for_league(
            client,
            args.league,
            source=args.source,
            limit=args.limit,
            workers=args.workers,
            require_fresh_props=args.require_fresh_props,
            retry_failed_403=not args.no_retry_failed_403,
            retry_403_cooldown_seconds=args.retry_403_cooldown_seconds,
            retry_403_workers=args.retry_403_workers,
        )
    except StalePropsError:
        print(f"{args.league.upper()}: stale_props")
        return 1
    except AuthRequiredError as exc:
        paths = league_paths(args.league).ensure()
        report = {
            "league": args.league.upper(),
            "status": "auth_required",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:200],
        }
        write_json(paths.reports / _status_report_name(args.source), report)
        print(f"{args.league.upper()}: auth_required")
        return 1
    except (OutlierApiError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        paths = league_paths(args.league).ensure()
        report = {
            "league": args.league.upper(),
            "status": "error",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:300],
        }
        write_json(paths.reports / _status_report_name(args.source), report)
        print(f"{args.league.upper()}: error")
        return 1

    print(
        f"{status['league']}: exported {status['record_count']} line-movement records "
        f"from {status['markets_fetched']}/{status['markets_requested']} markets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
