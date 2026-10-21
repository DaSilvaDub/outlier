"""Whether a scheduled NBA capture is worth running now.

One capture snapshots the whole open slate (~25k rows, ~175 MB of ledger plus
bundle), so a fixed hourly schedule is costly. A close is verified only from a
capture in :data:`clv.VERIFIED_WINDOW` before tip, so a scheduler ticking every
15 minutes asks this and captures only when a tip ``lead`` minutes out has no
capture in its window yet. Firing late in the window lets one capture cover every
tip in the following hour and gives the freshest close, while leaving a second
tick to retry a failed run. Tips come from the latest capture alone, so a
rescheduled game follows its new time.

A slate refresh (no NBA rows yet, or the last capture >12h old) is throttled
against the last *attempt*: an empty slate writes no rows, so measuring from rows
alone would capture on every tick through an off day.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from outlier_nba.run_context import try_parse_utc
from outlier_nba.track.clv import VERIFIED_WINDOW

DEFAULT_LEAD = timedelta(minutes=30)
DEFAULT_REFRESH = timedelta(hours=12)


@dataclass(frozen=True)
class CaptureDue:
    due: bool
    reason: str
    last_capture: datetime | None
    next_tip: datetime | None


def attempt_marker(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".last-capture-attempt")


def read_last_attempt(db_path: Path) -> datetime | None:
    marker = attempt_marker(db_path)
    return try_parse_utc(marker.read_text(encoding="utf-8").strip()) if marker.exists() else None


def record_attempt(db_path: Path, at: datetime) -> None:
    marker = attempt_marker(db_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(at.isoformat(), encoding="utf-8")


def _latest_capture(db_path: Path) -> tuple[datetime | None, list[datetime]]:
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        try:
            (last_raw,) = conn.execute(
                "SELECT MAX(captured_at) FROM market_snapshots WHERE sport = 'NBA'"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None, []
            raise
        if last_raw is None:
            return None, []
        tips = conn.execute(
            "SELECT DISTINCT event_starts_at FROM market_snapshots "
            "WHERE sport = 'NBA' AND captured_at = ?",
            (last_raw,),
        ).fetchall()
    parsed = (try_parse_utc(raw) for (raw,) in tips)
    return try_parse_utc(last_raw), sorted(t for t in parsed if t is not None)


def capture_due(
    db_path: Path,
    now: datetime,
    *,
    last_attempt: datetime | None = None,
    window: timedelta = VERIFIED_WINDOW,
    lead: timedelta = DEFAULT_LEAD,
    refresh_after: timedelta = DEFAULT_REFRESH,
) -> CaptureDue:
    last, tips = _latest_capture(db_path) if db_path.exists() else (None, [])
    upcoming = [t for t in tips if t > now]
    next_tip = upcoming[0] if upcoming else None
    uncovered = [t for t in upcoming if t - now <= lead and (last is None or last < t - window)]
    if uncovered:
        return CaptureDue(
            True, f"tip {uncovered[0].isoformat()} has no capture in its window", last, next_tip
        )
    refreshed = last_attempt is not None and now - last_attempt < refresh_after
    if last is None and not refreshed:
        return CaptureDue(True, "no prior NBA capture", None, next_tip)
    if last is not None and now - last >= refresh_after and not refreshed:
        return CaptureDue(True, f"last capture is stale ({now - last} old)", last, next_tip)
    return CaptureDue(False, "no tip needs a capture yet", last, next_tip)
