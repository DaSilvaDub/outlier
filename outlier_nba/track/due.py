"""Whether a scheduled NBA capture is worth running now.

One capture snapshots the whole open slate (~25k rows, ~175 MB of ledger plus
bundle), so a fixed hourly schedule is costly. A close is verified only from a
capture in :data:`clv.VERIFIED_WINDOW` before tip, so a scheduler ticking every
15 minutes asks this and captures only when an upcoming tip would otherwise lack
such a capture, or when the last capture is stale enough to have missed slate
changes. Tips come from the latest capture alone, so a rescheduled game follows
its new time.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from outlier_nba.track.clv import VERIFIED_WINDOW

DEFAULT_REFRESH = timedelta(hours=12)


@dataclass(frozen=True)
class CaptureDue:
    due: bool
    reason: str
    last_capture: datetime | None
    next_tip: datetime | None


def _parse(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def capture_due(
    db_path: Path,
    now: datetime,
    *,
    window: timedelta = VERIFIED_WINDOW,
    refresh_after: timedelta = DEFAULT_REFRESH,
) -> CaptureDue:
    if not db_path.exists():
        return CaptureDue(True, "no ledger yet", None, None)
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        try:
            (last_raw,) = conn.execute(
                "SELECT MAX(captured_at) FROM market_snapshots WHERE sport = 'NBA'"
            ).fetchone()
        except sqlite3.OperationalError:
            return CaptureDue(True, "ledger has no snapshot table yet", None, None)
        if last_raw is None:
            return CaptureDue(True, "no prior NBA capture", None, None)
        tips = sorted(
            _parse(raw)
            for (raw,) in conn.execute(
                "SELECT DISTINCT event_starts_at FROM market_snapshots "
                "WHERE sport = 'NBA' AND captured_at = ?",
                (last_raw,),
            )
        )
    last = _parse(last_raw)
    upcoming = [t for t in tips if t > now]
    next_tip = upcoming[0] if upcoming else None
    uncovered = [t for t in upcoming if t - now <= window and last < t - window]
    if uncovered:
        return CaptureDue(
            True, f"tip {uncovered[0].isoformat()} has no capture in its window", last, next_tip
        )
    if now - last >= refresh_after:
        return CaptureDue(True, f"last capture is stale ({now - last} old)", last, next_tip)
    return CaptureDue(False, "no tip needs a capture yet", last, next_tip)
