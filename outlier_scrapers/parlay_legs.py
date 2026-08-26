"""Structured parlay leg identity and parlay settlement math.

Every parlay CSV serializes its legs as display text ("HME o2.5 (7/10, -160
DK) + AWY o3.5 ..."), which is fine for a human board and useless for grading:
the identity the settlement path needs is gone. Rather than parse that text
back into identity -- lossy, and a re-derivation of what the generator already
had in hand -- each parlay writer emits a ``legs_json`` column carrying the
event/market/outcome ids of its legs.

Grading a parlay then needs no new grading logic at all. Every parlay leg is
drawn from a singles board that feedback capture already stores, so a leg
resolves to a snapshot that grades through the ordinary single-row path. A
parlay settles when all of its legs have settled, and this module holds the
rule for combining them.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

LEG_IDENTITY_FIELDS = ("event_id", "market_id", "outcome_id")

RESULT_WIN = "W"
RESULT_LOSS = "L"
RESULT_PUSH = "PUSH"


def leg_identity(row: dict[str, Any]) -> dict[str, str]:
    """Extract the identity triple that resolves a leg to its captured single."""

    return {field: str(row.get(field) or "").strip() for field in LEG_IDENTITY_FIELDS}


def encode_legs(rows: Iterable[dict[str, Any]]) -> str:
    """Serialize leg identities for a parlay CSV column.

    Returns "" when any leg lacks a complete identity: a parlay that cannot
    resolve all of its legs can never settle, and recording a partial set would
    leave it pending forever instead of visibly absent.
    """

    legs: list[dict[str, str]] = []
    for row in rows:
        identity = leg_identity(row)
        if not all(identity[field] for field in LEG_IDENTITY_FIELDS):
            return ""
        legs.append(identity)
    if not legs:
        return ""
    return json.dumps(legs, separators=(",", ":"), sort_keys=True)


def decode_legs(value: Any) -> list[dict[str, str]]:
    """Parse a ``legs_json`` column back into leg identities.

    Malformed or partial payloads decode to [] so the caller skips the parlay
    rather than settling it against a subset of its legs.
    """

    text = str(value or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list) or not payload:
        return []
    legs: list[dict[str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            return []
        identity = {field: str(entry.get(field) or "").strip() for field in LEG_IDENTITY_FIELDS}
        if not all(identity[field] for field in LEG_IDENTITY_FIELDS):
            return []
        legs.append(identity)
    return legs


def combine_results(results: Sequence[Any]) -> str | None:
    """Combine settled leg results into the parlay's result.

    Returns None while any leg is unsettled, so the parlay stays pending
    instead of being graded on a subset of its legs.

    A pushed leg drops out of the parlay, which is the standard book rule; a
    parlay whose legs all push is itself a push.
    """

    if not results:
        return None
    normalized: list[str] = []
    for result in results:
        token = str(result or "").strip().upper()
        if token not in {RESULT_WIN, RESULT_LOSS, RESULT_PUSH}:
            return None
        normalized.append(token)
    if RESULT_LOSS in normalized:
        return RESULT_LOSS
    if all(token == RESULT_PUSH for token in normalized):
        return RESULT_PUSH
    return RESULT_WIN


def surviving_decimal(
    results: Sequence[Any], decimals: Sequence[Any]
) -> float | None:
    """Decimal price of the legs that did not push.

    A pushed leg pays at 1.0, so the parlay's payout is the product over the
    legs that survived -- not the price captured before the push was known.
    Returns None when any surviving leg has no price, because a payout cannot
    be invented.
    """

    if len(results) != len(decimals):
        return None
    product = 1.0
    for result, decimal in zip(results, decimals):
        if str(result or "").strip().upper() == RESULT_PUSH:
            continue
        try:
            value = float(decimal)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        product *= value
    return product


def parlay_pnl(result: str, units: float, decimal_price: float | None) -> float | None:
    """Units-denominated PnL for a settled parlay."""

    token = str(result or "").strip().upper()
    if units <= 0:
        return 0.0
    if token == RESULT_LOSS:
        return -units
    if token == RESULT_PUSH:
        return 0.0
    if token != RESULT_WIN:
        return None
    if decimal_price is None:
        return None
    return units * (decimal_price - 1.0)
