"""Resilient file I/O, cloud-sync lock mitigation, and date utilities for outlier_nfl.

Provides atomic JSON writing that streams directly to disk (never keeping massive
serialized strings in memory) and handles Windows [WinError 32] transient locks
from OneDrive or file synchronizers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import math
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


def _unlink_with_retry(path: Path, retries: int = 5, delay: float = 0.2) -> bool:
    """Remove a file, retrying on the same transient locks a replace hits.

    Used for the backup the last resort below leaves behind. A scanner holding
    that file open for a moment must not turn a one-off into a hidden sibling
    that stays in the directory for good.
    """
    for attempt in range(1, retries + 1):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt < retries:
                time.sleep(delay * (1.5 ** (attempt - 1)))
                continue
            logger.warning("Could not remove %s: %s", path, exc)
    return False


def _restore_backup(backup: Path, dst: Path) -> None:
    """Put the previous file back, but never over a newer one.

    Moving ``dst`` aside frees the name, and a concurrent ``safe_write_json``
    for the same destination -- which this module explicitly supports -- can
    land its own write there before the rollback runs. Replacing
    unconditionally would resurrect stale contents over that newer write.
    ``os.link`` refuses a name that already exists, so it settles the question
    in one step instead of a check that can go out of date.
    """
    try:
        os.link(backup, dst)
    except FileExistsError:
        # A newer write got there first. It wins; the superseded copy goes.
        pass
    except OSError:
        # No hard links here (another filesystem, or a platform without them),
        # so there is nothing atomic to use and a check is the best available.
        if not dst.exists():
            try:
                backup.replace(dst)
                return  # the rename consumed the backup
            except OSError:
                logger.error(
                    "Could not restore %s after a failed replace; its previous contents "
                    "are preserved in %s and must be moved back by hand.",
                    dst,
                    backup,
                )
                return  # keep the backup: it is the only copy left
    _unlink_with_retry(backup)


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
            # Last resort: the destination itself is what blocks the replace.
            # Move it aside rather than delete it -- unlinking commits to losing
            # the previous good file, and the retry that was supposed to put the
            # new one in its place can fail too, leaving the destination missing
            # entirely. Renamed aside, the old contents can be put back.
            if attempt == retries and dst.exists():
                backup = dst.with_name(f".{dst.name}.{os.getpid()}.{time.time_ns()}.bak")
                moved_aside = False
                try:
                    dst.replace(backup)
                    moved_aside = True
                except OSError:
                    pass
                if moved_aside:
                    try:
                        src.replace(dst)
                    except OSError:
                        _restore_backup(backup, dst)
                    else:
                        _unlink_with_retry(backup)
                        return
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


def _first_sunday(year: int, month: int) -> int:
    """Day of the month the first Sunday falls on, for the US DST boundaries below."""
    first_weekday = datetime(year, month, 1, tzinfo=timezone.utc).weekday()  # Mon=0
    return 1 + (6 - first_weekday) % 7


def _in_us_eastern_dst(moment: datetime) -> bool:
    """Whether a moment falls in US Eastern daylight time, by the rule since 2007.

    DST runs from 2am local on the second Sunday in March to 2am local on the
    first Sunday in November. The boundaries are compared in UTC (07:00 UTC at
    the spring start, when Eastern is still -5; 06:00 UTC at the autumn end,
    when it is still -4), which avoids having to reason about the local clock
    while it is the thing being determined.
    """
    utc = moment.astimezone(timezone.utc)
    year = utc.year
    start = datetime(year, 3, _first_sunday(year, 3) + 7, 7, tzinfo=timezone.utc)
    end = datetime(year, 11, _first_sunday(year, 11), 6, tzinfo=timezone.utc)
    return start <= utc < end


def to_eastern_datetime(dt_or_iso: datetime | str | None) -> datetime | None:
    """Convert a UTC datetime or ISO timestamp to US Eastern timezone-aware datetime."""
    if dt_or_iso is None:
        return None

    if isinstance(dt_or_iso, str):
        dt = parse_iso_datetime(dt_or_iso)
    else:
        dt = dt_or_iso

    if dt is None:
        return None

    # A naive datetime means UTC here, matching this function's contract and what
    # parse_iso_datetime() already does for a naive ISO string. Without this,
    # astimezone() reads it as the *host's* local clock, so the same kickoff
    # resolves to a different Eastern slate date depending on the machine the
    # pipeline runs on -- a UTC runner and a US Pacific workstation disagree by a
    # day on any game that kicks off after 20:00 ET.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    eastern_tz: Any
    try:
        eastern_tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        eastern_tz = timezone(timedelta(hours=-4 if _in_us_eastern_dst(dt) else -5))

    return dt.astimezone(eastern_tz)


def to_eastern_date(dt_or_iso: datetime | str | None) -> str | None:
    """Convert a UTC datetime or ISO timestamp to US Eastern calendar date (YYYY-MM-DD).

    Essential for NFL slates where late night games (Thursday, Sunday, Monday)
    kick off at 00:15–01:15 UTC of the next day.
    """
    eastern_dt = to_eastern_datetime(dt_or_iso)
    return eastern_dt.strftime("%Y-%m-%d") if eastern_dt else None


def matches_kickoff_window(dt_or_iso: datetime | str | None, window: str | None) -> bool:
    """Check if an event's kickoff time falls into a specific window (e.g. '1pm', '4pm', 'snf')."""
    if not window:
        return True
    w = window.strip().lower()
    eastern_dt = to_eastern_datetime(dt_or_iso)
    if not eastern_dt:
        return False
    hour = eastern_dt.hour
    minute = eastern_dt.minute

    if w in ("1pm", "early", "13:00", "1:00", "13", "1"):
        return hour == 13
    elif w in ("4pm", "late", "16:00", "4:00", "16", "4", "afternoon"):
        return hour in (16, 17)
    elif w in ("snf", "night", "prime", "primetime", "8pm", "20:00", "8:00"):
        return hour in (20, 21)
    elif ":" in w:
        parts = w.split(":")
        try:
            target_h, target_m = int(parts[0]), int(parts[1])
            return hour == target_h and abs(minute - target_m) <= 15
        except ValueError:
            return False
    return True


def coerce_float(value: Any) -> float | None:
    """Float from feed data, or None when the value is not a finite number.

    Feed fields arrive as numbers, as numeric strings, and occasionally as junk
    (``"N/A"``, ``"-"``, ``""``). Junk in one optional field is one unusable
    value, never a reason to abort the slate, so this returns None rather than
    raising. ``bool`` is rejected: ``True`` is not a price or a hit rate.

    ``OverflowError`` is caught alongside the parse errors: ``json`` parses an
    integer literal of any length, and ``float()`` raises it -- not
    ``ValueError`` -- for one too large to convert.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def coerce_odds(value: Any) -> int | None:
    """American odds as an int, or None when the feed value is not one.

    Same contract as :func:`coerce_float`. Leading ``+`` is accepted
    (``"+150"``), and a whole-number float (``-110.0``, which is how JSON often
    carries a price) keeps its value; a fractional one is not American odds and
    is rejected rather than silently truncated.
    """
    parsed = coerce_float(value)
    if parsed is None or parsed != int(parsed):
        return None
    return int(parsed)


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
