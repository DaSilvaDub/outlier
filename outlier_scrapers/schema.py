"""Schema compatibility validation gates for Outlier scraper pipeline.

Validates that raw API payloads and serialized output data match expected structures,
types, and keys, raising errors or logging warnings to protect downstream logic.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    """Raised when a critical schema compatibility violation occurs."""
    pass


def validate_raw_schedule(payload: Any) -> list[str]:
    """Validate raw schedule payload structure and types."""
    errors = []
    if not isinstance(payload, dict):
        return ["Schedule payload must be a dictionary"]

    events = payload.get("events")
    if events is None:
        return ["Schedule payload is missing 'events' key"]
    if not isinstance(events, list):
        return [f"'events' must be a list, got {type(events).__name__}"]

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"Event at index {idx} must be a dictionary")
            continue

        event_id = event.get("eventId") or event.get("id")
        if not event_id:
            errors.append(f"Event at index {idx} is missing 'eventId' or 'id'")

        # Non-critical warning checks
        for side in ("away", "home"):
            team_payload = event.get(side)
            if team_payload is not None and not isinstance(team_payload, dict):
                errors.append(f"Event {event_id or idx} side '{side}' is not a dictionary")

        # Check at least one schedule time exists
        has_time = any(
            event.get(k) is not None
            for k in ("scheduledTime", "startTime", "startDate", "date", "scheduled")
        )
        if not has_time:
            errors.append(f"Event {event_id or idx} is missing all schedule/start time keys")

    return errors


def validate_raw_player_props(payload: Any) -> list[str]:
    """Validate raw player props payload structure and types."""
    errors = []
    if not isinstance(payload, dict):
        return ["Player props payload must be a dictionary"]

    props = payload.get("props")
    if props is None:
        return ["Player props payload is missing 'props' key"]
    if not isinstance(props, list):
        return [f"'props' must be a list, got {type(props).__name__}"]

    for idx, prop in enumerate(props):
        if not isinstance(prop, dict):
            errors.append(f"Prop at index {idx} must be a dictionary")
            continue

        outcome = prop.get("outcome")
        if outcome is None:
            errors.append(f"Prop at index {idx} is missing 'outcome' sub-object")
            continue
        if not isinstance(outcome, dict):
            errors.append(f"Prop {idx} 'outcome' is not a dictionary")
            continue

        # Critical outcome keys
        event_id = outcome.get("eventId")
        if not event_id:
            errors.append(f"Prop {idx} 'outcome' is missing 'eventId'")

        market_id = outcome.get("marketId")
        if not market_id:
            errors.append(f"Prop {idx} 'outcome' is missing 'marketId'")

        outcome_id = outcome.get("outcomeId")
        if not outcome_id:
            errors.append(f"Prop {idx} 'outcome' is missing 'outcomeId'")

        # Verify basic type integrity
        position = outcome.get("position")
        if position is not None and not isinstance(position, str):
            errors.append(f"Prop {idx} outcome 'position' must be a string, got {type(position).__name__}")

        line = outcome.get("line")
        if line is not None and not isinstance(line, (int, float, str)):
            errors.append(f"Prop {idx} outcome 'line' must be a number or string, got {type(line).__name__}")

    return errors


def validate_raw_games(payload: Any) -> list[str]:
    """Validate raw games/events payload structure and types."""
    errors = []
    if not isinstance(payload, dict):
        return ["Games payload must be a dictionary"]

    events = payload.get("events")
    if events is None:
        return ["Games payload is missing 'events' key"]
    if not isinstance(events, list):
        return [f"'events' must be a list, got {type(events).__name__}"]

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"Game event at index {idx} must be a dictionary")
            continue

        event_id = event.get("eventId") or event.get("id")
        if not event_id:
            errors.append(f"Game event at index {idx} is missing 'eventId' or 'id'")

        markets = event.get("markets") or []
        if not isinstance(markets, list):
            errors.append(f"Game event {event_id or idx} 'markets' key is not a list")
            continue

        for m_idx, market in enumerate(markets):
            if not isinstance(market, dict):
                errors.append(f"Game event {event_id or idx} market {m_idx} must be a dictionary")
                continue
            market_id = market.get("marketId")
            if not market_id:
                errors.append(f"Game event {event_id or idx} market {m_idx} is missing 'marketId'")

            outcomes = market.get("outcomes") or []
            if not isinstance(outcomes, list):
                errors.append(f"Game event {event_id or idx} market {market_id or m_idx} outcomes is not a list")
                continue

            for o_idx, outcome in enumerate(outcomes):
                if not isinstance(outcome, dict):
                    errors.append(f"Game event {event_id or idx} market {market_id or m_idx} outcome {o_idx} must be a dictionary")
                    continue
                outcome_id = outcome.get("outcomeId") or outcome.get("id")
                if not outcome_id:
                    errors.append(f"Game event {event_id or idx} market {market_id or m_idx} outcome {o_idx} is missing ID")

    return errors


def validate_raw_line_movement(payload: Any) -> list[str]:
    """Validate raw line movement payload structure and types."""
    errors = []
    if not isinstance(payload, dict):
        return ["Line movement payload must be a dictionary"]

    ev_records = payload.get("ev_records")
    if ev_records is not None:
        if not isinstance(ev_records, list):
            errors.append(f"'ev_records' must be a list, got {type(ev_records).__name__}")
        else:
            for idx, rec in enumerate(ev_records):
                if not isinstance(rec, dict):
                    errors.append(f"EV record at index {idx} is not a dictionary")
                    continue
                # An EV record needs to reference a market to be joinable
                if not rec.get("market_id") and not rec.get("outcome_id"):
                    errors.append(f"EV record {idx} is missing both 'market_id' and 'outcome_id'")

    return errors


def validate_normalized_props(records: Any) -> list[str]:
    """Validate normalizer output for player props."""
    errors = []
    if not isinstance(records, list):
        return ["Normalized props records must be a list"]

    required_keys = {"league", "event_id", "market_id", "outcome_id", "position", "line", "team", "matchup"}
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"Normalized prop record {idx} is not a dictionary")
            continue
        missing = required_keys - set(rec.keys())
        if missing:
            errors.append(f"Normalized prop record {idx} is missing key(s): {', '.join(missing)}")

    return errors


def validate_normalized_games(records: Any) -> list[str]:
    """Validate normalizer output for games."""
    errors = []
    if not isinstance(records, list):
        return ["Normalized games records must be a list"]

    required_keys = {"league", "event_id", "market_id", "outcome_id", "position", "line", "matchup"}
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"Normalized game record {idx} is not a dictionary")
            continue
        missing = required_keys - set(rec.keys())
        if missing:
            errors.append(f"Normalized game record {idx} is missing key(s): {', '.join(missing)}")

    return errors


def validate_candidate_row(row: dict[str, Any], header_columns: list[str]) -> list[str]:
    """Validate a single candidate row before serialization to candidates.csv."""
    errors = []
    if not isinstance(row, dict):
        return ["Candidate row is not a dictionary"]

    # Check for unexpected columns
    unexpected = set(row.keys()) - set(header_columns)
    # Filter out columns starting with underscore (coordination/internal variables)
    unexpected = {k for k in unexpected if not k.startswith("_")}
    if unexpected:
        errors.append(f"Row contains unexpected column(s) not in header: {', '.join(unexpected)}")

    # Check type constraints for specific numeric/boolean fields
    def check_float_or_int_or_none(field: str):
        val = row.get(field)
        if val not in (None, ""):
            try:
                float(val)
            except (ValueError, TypeError):
                errors.append(f"Field '{field}' must be numeric, got value '{val}' ({type(val).__name__})")

    check_float_or_int_or_none("decimal_price")
    check_float_or_int_or_none("model_prob")
    check_float_or_int_or_none("push_prob")
    check_float_or_int_or_none("implied_prob")
    check_float_or_int_or_none("edge_pct")
    check_float_or_int_or_none("kelly_025_units")
    check_float_or_int_or_none("max_units")
    check_float_or_int_or_none("recommended_units_pre_news")

    # Critical fields check
    for req in ("sport", "selection"):
        if not row.get(req):
            errors.append(f"Critical field '{req}' is empty or missing")

    return errors
