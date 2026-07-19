"""Shared utilities for outlier scrapers.

Provides common helpers for formatting, file operations, odds conversion,
and event scheduling to reduce code duplication and break circular dependencies.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


def _parse_start(ts: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else None


def _local_date(iso_ts: str | None) -> str | None:
    """Convert an ISO timestamp to a local YYYY-MM-DD string."""
    if not iso_ts:
        return None
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def drop_locked_events(
    rows: list[dict[str, Any]], now: datetime | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """House rule: only pack markets proven to be pregame.

    Once an event locks, its markets go live: alt-line ladders re-center on the
    in-game state, settled lines 404 off the API, and EV disappears — numbers
    that read downstream as corrupt pregame lines. Rows without a parseable start 
    time are also dropped because their pregame state cannot be verified. 
    Returns (kept, dropped) as new lists; rows are not mutated.
    """
    now = now or datetime.now().astimezone()
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        start = _parse_start(row.get("_event_starts_at"))
        if start is None or start <= now:
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    """Write an iterable of dictionaries to a CSV file, creating parents if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _american_to_decimal(american: Any) -> float | None:
    """Convert American odds to decimal."""
    if american in (None, ""):
        return None
    try:
        val = float(american)
    except (ValueError, TypeError):
        return None
    if val > 0:
        return (val / 100.0) + 1.0
    if val < 0:
        return (100.0 / abs(val)) + 1.0
    return 2.0


def _decimal_to_american(decimal_price: float) -> int | None:
    """Convert decimal odds back to integer American odds."""
    if decimal_price <= 1.0:
        return None
    if decimal_price >= 2.0:
        return round((decimal_price - 1.0) * 100.0)
    return -round(100.0 / (decimal_price - 1.0))


def _price_text(price: Any) -> str:
    """American odds display: leading + on plus-money prices."""
    if isinstance(price, (int, float)):
        return f"+{price:g}" if price > 0 else f"{price:g}"
    if isinstance(price, str):
        try:
            val = float(price)
            if val > 0:
                return f"+{val:g}"
            return f"{val:g}"
        except ValueError:
            pass
    return str(price)
