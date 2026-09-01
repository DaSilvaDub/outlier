"""Shared utilities for outlier scrapers.

Provides common helpers for formatting, file operations, odds conversion,
and event scheduling to reduce code duplication and break circular dependencies.
"""

from __future__ import annotations

import csv
import os
import tempfile
import time
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


def _summary_stat_for_team(rec: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the home/away summary-stat blob matching the record's team.

    Returns (stat_dict, flag). Ambiguous side -> (None, "AMBIGUOUS_STATS_SIDE");
    missing stats -> (None, None).
    """
    stats = rec.get("stats")
    if not isinstance(stats, dict):
        return None, None
    home = stats.get("homeSummaryStat")
    away = stats.get("awaySummaryStat")
    home = home if isinstance(home, dict) else None
    away = away if isinstance(away, dict) else None
    if home is None and away is None:
        return None, None
    if home is not None and away is None:
        return home, None
    if away is not None and home is None:
        return away, None
    # Both present: match team against "away @ home" matchup.
    team = str(rec.get("team") or "").strip().lower()
    matchup = str(rec.get("matchup") or "")
    if team and " @ " in matchup:
        away_name, _, home_name = matchup.partition(" @ ")
        if team == away_name.strip().lower():
            return away, None
        if team == home_name.strip().lower():
            return home, None
    return None, "AMBIGUOUS_STATS_SIDE"


def _replace_with_retry(
    source: Path,
    destination: Path,
    *,
    retries: int,
    delay: float,
) -> None:
    """Atomically replace ``destination``, retrying transient Windows file locks."""
    if retries < 1:
        raise ValueError("retries must be at least 1")
    for attempt in range(retries):
        try:
            source.replace(destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {32, 33}:
                raise
            if attempt == retries - 1:
                raise
            time.sleep(delay)


def _write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[dict[str, Any]],
    retries: int = 5,
    delay: float = 0.2,
) -> None:
    """Atomically write CSV and fail closed if a cloud-sync lock never clears."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}_tmp_", suffix=".csv"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        _replace_with_retry(tmp_path, path, retries=retries, delay=delay)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def safe_write_text(path: Path, content: str, retries: int = 5, delay: float = 0.2) -> None:
    """Atomically write text and fail closed if a cloud-sync lock never clears."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}_tmp_", suffix=".tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        _replace_with_retry(tmp_path, path, retries=retries, delay=delay)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


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
