"""Runtime schema validation gates for outlier_nfl.

Validates raw Outlier API payloads and normalized game/prop records to ensure
data integrity before persistence or downstream consumption.
"""

from __future__ import annotations

import math
from typing import Any

from outlier_nfl.models import NflGameLine, NflPlayerProp


def validate_schedule_payload(payload: Any) -> list[str]:
    """Validate raw schedule payload structure from Outlier API."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["Schedule payload must be a JSON object (dict)"]

    events = payload.get("events")
    if events is None:
        return ["Schedule payload missing 'events' field"]
    if not isinstance(events, list):
        return ["Schedule 'events' field must be a list"]

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"Event at index {idx} is not an object")
            continue

        event_id = event.get("eventId") or event.get("id")
        if not event_id:
            errors.append(f"Event at index {idx} missing 'eventId' or 'id'")

        home = event.get("home")
        away = event.get("away")
        if not home or not isinstance(home, dict):
            errors.append(f"Event {event_id or idx} missing valid 'home' team object")
        if not away or not isinstance(away, dict):
            errors.append(f"Event {event_id or idx} missing valid 'away' team object")

    return errors


def validate_event_markets_payload(payload: Any) -> list[str]:
    """Validate raw markets payload from Outlier API for a specific event."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["Event markets payload must be a JSON object (dict)"]

    markets = payload.get("markets")
    if markets is None:
        return ["Event markets payload missing 'markets' field"]
    if not isinstance(markets, list):
        return ["Event markets 'markets' field must be a list"]

    for idx, market in enumerate(markets):
        if not isinstance(market, dict):
            errors.append(f"Market at index {idx} is not an object")
            continue

        market_id = market.get("marketId") or market.get("id")
        if not market_id:
            errors.append(f"Market at index {idx} missing 'marketId'")

        outcomes = market.get("outcomes")
        if outcomes is None or not isinstance(outcomes, list):
            errors.append(f"Market {market_id or idx} missing or invalid 'outcomes' list")

    return errors


def validate_player_props_payload(payload: Any) -> list[str]:
    """Validate raw player props bulk feed payload from Outlier API."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["Player props payload must be a JSON object (dict)"]

    props = payload.get("props")
    if props is None:
        return ["Player props payload missing 'props' field"]
    if not isinstance(props, list):
        return ["Player props 'props' field must be a list"]

    for idx, item in enumerate(props):
        if not isinstance(item, dict):
            errors.append(f"Player prop item at index {idx} is not an object")
            continue

        outcome = item.get("outcome")
        if not outcome or not isinstance(outcome, dict):
            errors.append(f"Player prop item at index {idx} missing valid 'outcome' object")
            continue

        event_id = outcome.get("eventId")
        if not event_id:
            errors.append(f"Player prop item at index {idx} missing outcome 'eventId'")

        position = outcome.get("position")
        if not position:
            errors.append(f"Player prop item at index {idx} missing outcome 'position'")

    return errors


def _validate_book_entry(book_entry: Any, b_idx: int) -> list[str]:
    """Safely validate an individual book entry inside a 'books' collection."""
    errors: list[str] = []
    if isinstance(book_entry, dict):
        b_dict = book_entry
    else:
        # hasattr() only swallows AttributeError, so a descriptor that raises anything
        # else (a property blowing up on access) would escape this validator and abort
        # the whole dataset pass. Resolve the attribute defensively instead.
        try:
            to_dict = getattr(book_entry, "to_dict", None)
        except Exception:
            to_dict = None
        if not callable(to_dict):
            return [f"Book entry at index {b_idx} is not a valid dict or BookPrice instance"]
        try:
            b_dict = to_dict()
        except Exception as exc:
            return [f"Book entry at index {b_idx} to_dict() raised exception: {exc}"]

    if not isinstance(b_dict, dict):
        return [f"Book entry at index {b_idx} did not produce a dictionary"]

    book_name = b_dict.get("book")
    odds = b_dict.get("odds")
    if (
        not book_name
        or not isinstance(book_name, str)
        or not book_name.strip()
        or odds is None
        or isinstance(odds, bool)
        or not isinstance(odds, int)
    ):
        errors.append(f"Book entry at index {b_idx} missing 'book' or 'odds'")

    decimal_val = b_dict.get("decimal")
    if decimal_val is not None:
        if (
            isinstance(decimal_val, bool)
            or not isinstance(decimal_val, (int, float))
            or not math.isfinite(decimal_val)
            or decimal_val <= 0
        ):
            errors.append(f"Book entry at index {b_idx} has invalid 'decimal' odds")

    return errors


def validate_game_line_record(record: dict[str, Any] | NflGameLine) -> list[str]:
    """Validate a single normalized NflGameLine record."""
    errors: list[str] = []
    data = record.to_dict() if isinstance(record, NflGameLine) else record

    if not isinstance(data, dict):
        return ["Game line record must be a dict or NflGameLine instance"]

    required_fields = (
        "event_id",
        "matchup",
        "home_team",
        "away_team",
        "market_type",
        "market",
        "position",
        "books",
    )
    for field_name in required_fields:
        if field_name not in data or data[field_name] is None:
            errors.append(f"Game line record missing required field '{field_name}'")

    market_type = data.get("market_type")
    if market_type not in ("GAMELINE", "TEAM_PROP"):
        errors.append(f"Invalid market_type '{market_type}' (expected GAMELINE or TEAM_PROP)")

    position = data.get("position")
    valid_positions = {"HOME", "AWAY", "OVER", "UNDER", "DRAW"}
    if position not in valid_positions:
        errors.append(f"Invalid position '{position}' for game line (expected one of {valid_positions})")

    line = data.get("line")
    if line is not None:
        if isinstance(line, bool) or not isinstance(line, (int, float)) or not math.isfinite(line):
            errors.append(f"Line '{line}' must be numeric or None")

    ip_pct = data.get("implied_probability")
    if ip_pct is not None:
        if (
            isinstance(ip_pct, bool)
            or not isinstance(ip_pct, (int, float))
            or not math.isfinite(ip_pct)
            or not (0.0 <= ip_pct <= 100.0)
        ):
            errors.append(f"implied_probability '{ip_pct}' must be a percentage between 0 and 100")

    books = data.get("books")
    if not isinstance(books, (list, tuple)):
        errors.append("Field 'books' must be a list or tuple")
    else:
        for b_idx, book_entry in enumerate(books):
            errors.extend(_validate_book_entry(book_entry, b_idx))

    return errors


def validate_player_prop_record(record: dict[str, Any] | NflPlayerProp) -> list[str]:
    """Validate a single normalized NflPlayerProp record."""
    errors: list[str] = []
    data = record.to_dict() if isinstance(record, NflPlayerProp) else record

    if not isinstance(data, dict):
        return ["Player prop record must be a dict or NflPlayerProp instance"]

    required_fields = (
        "event_id",
        "matchup",
        "player_name",
        "market",
        "position",
        "line",
        "books",
    )
    for field_name in required_fields:
        if field_name not in data or data[field_name] is None:
            errors.append(f"Player prop record missing required field '{field_name}'")

    position = data.get("position")
    valid_positions = {"OVER", "UNDER", "YES", "NO"}
    if position not in valid_positions:
        errors.append(f"Invalid position '{position}' for player prop (expected one of {valid_positions})")

    line = data.get("line")
    if line is None or isinstance(line, bool) or not isinstance(line, (int, float)) or not math.isfinite(line):
        errors.append(f"Player prop line '{line}' must be numeric")

    ip_pct = data.get("implied_probability")
    if ip_pct is not None:
        if (
            isinstance(ip_pct, bool)
            or not isinstance(ip_pct, (int, float))
            or not math.isfinite(ip_pct)
            or not (0.0 <= ip_pct <= 100.0)
        ):
            errors.append(f"implied_probability '{ip_pct}' must be a percentage between 0 and 100")

    books = data.get("books")
    if not isinstance(books, (list, tuple)):
        errors.append("Field 'books' must be a list or tuple")
    else:
        for b_idx, book_entry in enumerate(books):
            errors.extend(_validate_book_entry(book_entry, b_idx))

    return errors


def validate_normalized_dataset(
    records: list[Any],
    dataset_type: str = "games",
) -> list[str]:
    """Validate a collection of normalized game or player prop records."""
    all_errors: list[str] = []
    if not isinstance(records, list):
        return [f"Dataset must be a list of records, got {type(records).__name__}"]

    validator = validate_game_line_record if dataset_type == "games" else validate_player_prop_record
    for idx, rec in enumerate(records):
        errs = validator(rec)
        for err in errs:
            all_errors.append(f"Record {idx}: {err}")

    return all_errors
