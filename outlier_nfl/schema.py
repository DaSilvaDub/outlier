"""Runtime schema validation gates for outlier_nfl.

Validates raw Outlier API payloads and normalized game/prop records to ensure
data integrity before persistence or downstream consumption.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
import json
import logging
import math
from typing import Any, TypeVar

from outlier_nfl.models import NflGameLine, NflPlayerProp

logger = logging.getLogger("outlier_nfl.schema")
T = TypeVar("T")


def _as_mapping(rec: Any) -> Mapping[str, Any]:
    if isinstance(rec, Mapping):
        return rec
    to_dict = getattr(rec, "to_dict", None)
    out = to_dict() if callable(to_dict) else None
    return out if isinstance(out, Mapping) else {}


def _norm_name(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def player_prop_key(rec: Any) -> tuple[Any, ...]:
    """Primary key of a normalized player prop quote (F26)."""
    d = _as_mapping(rec)
    who = str(d.get("player_id") or "").strip() or _norm_name(d.get("player_name"))
    return (str(d.get("event_id") or ""), who, d.get("market"), d.get("line"),
            d.get("position"), d.get("scope") or "full_game")


def game_line_key(rec: Any) -> tuple[Any, ...]:
    """Primary key of a normalized game line quote (F26)."""
    d = _as_mapping(rec)
    return (str(d.get("event_id") or ""), d.get("market"), d.get("scope") or "full_game",
            d.get("team"), d.get("position"), d.get("line"))


def _canonical(rec: Any) -> str:
    return json.dumps(_as_mapping(rec), sort_keys=True, default=str)


def dedupe_quotes(
    records: Iterable[T], key: Callable[[Any], tuple[Any, ...]], label: str
) -> list[T]:
    """Drop identical duplicate quotes; drop every version of a conflicting one (F26).

    Identical copies (same key, same payload) keep the first. Copies that share
    a key but disagree (different odds, books, ...) cannot be resolved here, so
    all of them are dropped. Both counts are logged.
    """
    groups: dict[tuple[Any, ...], list[T]] = defaultdict(list)
    order: list[tuple[Any, ...]] = []
    for rec in records:
        k = key(rec)
        if k not in groups:
            order.append(k)
        groups[k].append(rec)
    out: list[T] = []
    identical = conflicting = 0
    for k in order:
        versions = groups[k]
        if len({_canonical(v) for v in versions}) > 1:
            conflicting += 1
            logger.warning("Dropping %d conflicting %s quotes for %s", len(versions), label, k)
            continue
        identical += len(versions) - 1
        out.append(versions[0])
    if identical or conflicting:
        logger.warning("%s dedupe: %d identical duplicate(s) dropped, %d conflicting key(s) "
                       "dropped (%d in, %d out)", label, identical, conflicting,
                       sum(len(v) for v in groups.values()), len(out))
    return out


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
        # hasattr()/getattr() only swallow AttributeError, so a 'to_dict' property
        # (or __getattr__) that raises anything else would escape this validator.
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

    games = dataset_type == "games"
    validator = validate_game_line_record if games else validate_player_prop_record
    key = game_line_key if games else player_prop_key
    first_at: dict[tuple[Any, ...], int] = {}
    for idx, rec in enumerate(records):
        errs = validator(rec)
        for err in errs:
            all_errors.append(f"Record {idx}: {err}")
        if not isinstance(rec, Mapping):
            continue
        # Primary-key uniqueness (F26): dedupe runs before validation, so a
        # repeat here means a stage produced the same quote twice.
        k = key(rec)
        if k in first_at:
            all_errors.append(f"Record {idx}: duplicate key {k} (first at record {first_at[k]})")
        else:
            first_at[k] = idx
        # Quote ownership (F26): a quote's team must be one of its event's teams.
        team = rec.get("team")
        if team:
            members = _event_teams(rec)
            if members and team not in members:
                all_errors.append(f"Record {idx}: team '{team}' is not in event "
                                  f"{rec.get('event_id')} ({rec.get('matchup')})")

    return all_errors


def _event_teams(rec: Mapping[str, Any]) -> set[str]:
    teams = {str(t) for t in (rec.get("home_team"), rec.get("away_team")) if t}
    if not teams and rec.get("matchup") and "@" in str(rec.get("matchup")):
        teams = {part.strip() for part in str(rec["matchup"]).split("@") if part.strip()}
    return teams
