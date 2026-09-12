"""Resilient file I/O, cloud-sync lock mitigation, and date utilities for outlier_nfl.

Provides atomic JSON writing that streams directly to disk (never keeping massive
serialized strings in memory) and handles Windows [WinError 32] transient locks
from OneDrive or file synchronizers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any
import uuid
import zoneinfo

logger = logging.getLogger("outlier_nfl.utils")

# Transient Windows file locking error codes (WinError 32: sharing violation, WinError 33: lock violation)
WIN_LOCK_ERRORS: frozenset[int] = frozenset({32, 33})


def _replace_with_retry(
    src: Path,
    dst: Path,
    retries: int = 5,
    delay: float = 0.2,
) -> None:
    """Atomically replace dst with src, retrying on transient Windows file locks."""
    for attempt in range(1, retries + 1):
        try:
            src.replace(dst)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            is_transient = winerror in WIN_LOCK_ERRORS or isinstance(exc, (PermissionError, FileExistsError))
            if is_transient and attempt < retries:
                sleep_time = delay * (1.5 ** (attempt - 1))
                logger.warning(
                    "Retrying file replace after transient lock on %s (attempt %d/%d, sleep %.2fs): %s",
                    dst,
                    attempt,
                    retries,
                    sleep_time,
                    exc,
                )
                time.sleep(sleep_time)
                continue
            # If src still exists and replacement failed, try removing dst first then moving
            if attempt == retries and dst.exists():
                try:
                    dst.unlink()
                    src.replace(dst)
                    return
                except Exception:
                    pass
            raise


def safe_write_json(
    path: Path | str,
    payload: Any,
    indent: int = 2,
    retries: int = 5,
    delay: float = 0.2,
) -> None:
    """Safely and atomically write JSON data to disk using file handlers.

    Enforces python memory efficiency by never serializing large payloads into
    memory via json.dumps(). Instead streams directly to a temporary file via
    json.dump() and replaces the target file atomically.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Use a unique hidden temporary sibling file in the same directory for atomic replace
    unique_suffix = f"{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{uuid.uuid4().hex}"
    temp_path = target.parent / f".{target.name}.{unique_suffix}.tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, default=str, ensure_ascii=False)
            f.flush()
        _replace_with_retry(temp_path, target, retries=retries, delay=delay)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def safe_read_json(
    path: Path | str,
    default: Any = None,
    retries: int = 5,
    delay: float = 0.05,
) -> Any:
    """Read a JSON file safely with strict=False and retry on transient Windows file contention."""
    target = Path(path)
    if not target.exists():
        return default

    for attempt in range(1, retries + 1):
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f, strict=False)
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            errno_code = getattr(exc, "errno", None)
            is_transient = (
                winerror in WIN_LOCK_ERRORS
                or winerror == 5
                or isinstance(exc, PermissionError)
                or errno_code == 13
            )
            if is_transient and attempt < retries:
                sleep_time = delay * (1.5 ** (attempt - 1))
                logger.warning(
                    "Retrying read after transient lock on %s (attempt %d/%d, sleep %.2fs): %s",
                    target,
                    attempt,
                    retries,
                    sleep_time,
                    exc,
                )
                time.sleep(sleep_time)
                continue
            logger.error("Failed to read JSON from %s after %d attempt(s): %s", target, attempt, exc)
            return default
        except Exception as exc:
            logger.error("Failed to parse JSON from %s: %s", target, exc)
            return default

    return default


def parse_iso_datetime(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 formatted datetime string into a timezone-aware datetime."""
    if not ts:
        return None
    cleaned = str(ts).strip()
    if not cleaned:
        return None
    # Support 'Z' as UTC
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def to_eastern_date(dt_or_iso: datetime | str | None) -> str | None:
    """Convert a UTC datetime or ISO timestamp to US Eastern calendar date (YYYY-MM-DD).

    Essential for NFL slates where late night games (Thursday, Sunday, Monday)
    kick off at 00:15–01:15 UTC of the next day.
    """
    if dt_or_iso is None:
        return None

    if isinstance(dt_or_iso, str):
        dt = parse_iso_datetime(dt_or_iso)
    else:
        dt = dt_or_iso

    if dt is None:
        return None

    eastern_tz: Any
    try:
        eastern_tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        # Fallback to standard US Eastern winter UTC-5 if zoneinfo database is unavailable
        from datetime import timezone, timedelta
        eastern_tz = timezone(timedelta(hours=-5))

    eastern_dt = dt.astimezone(eastern_tz)
    return eastern_dt.strftime("%Y-%m-%d")


def format_signed_line(line: float | int | None) -> str | None:
    """Format a handicap or spread line with explicit sign (e.g. -3.5, +3.5, PK)."""
    if line is None:
        return None
    val = float(line)
    if val == 0.0:
        return "PK"
    if val > 0:
        return f"+{val:g}"
    return f"{val:g}"
