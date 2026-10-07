"""Run identity and the prediction-time (``as_of_utc``) boundary for one NFL run.

Forensic report 2026-10-06, F01: a slate date is not a source-availability
cutoff, and a missing cutoff used to mean "all history". Every source that can
carry information from after the prediction must be bounded by
``RunContext.as_of_utc``; when the bound cannot be verified the source is
reported UNAVAILABLE in a :class:`SourceRecord`, never loaded unbounded.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from outlier_nfl.utils import nfl_season_for_date

EASTERN = ZoneInfo("America/New_York")

RunMode = Literal["live", "fixture", "retrospective"]
SourceStatus = Literal["AVAILABLE", "UNAVAILABLE", "REFUSED"]


def parse_utc(value: datetime | str) -> datetime:
    """Aware UTC datetime from a datetime or ISO string; naive input is rejected."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"as_of_utc must be timezone-aware, got {value!r}")
    return dt.astimezone(UTC)


def try_parse_utc(value: Any) -> datetime | None:
    """Like :func:`parse_utc` but None for missing, unparseable or naive values."""
    if value in (None, ""):
        return None
    try:
        return parse_utc(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SourceRecord:
    """Availability of one input source for this run (written to the summary)."""

    name: str
    status: SourceStatus
    reason: str | None = None
    rows_in: int | None = None
    rows_admitted: int | None = None
    max_week_admitted: int | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunContext:
    """Who/when of one pipeline run; threaded through every source stage."""

    slate_date: str
    window: str | None
    season: int | None
    as_of_utc: datetime
    mode: RunMode
    first_kickoff_utc: datetime | None

    @property
    def as_of_iso(self) -> str:
        return self.as_of_utc.isoformat()


def event_kickoff_utc(event: Mapping[str, Any]) -> datetime | None:
    return try_parse_utc(event.get("scheduledTime") or event.get("startTime"))


def make_run_context(
    *,
    slate_date: str,
    window: str | None,
    as_of_utc: datetime,
    fixture: bool,
    slate_events: Iterable[Mapping[str, Any]] = (),
) -> RunContext:
    """Context for one run; ``retrospective`` once any slate game has kicked off."""
    kickoffs = [k for k in (event_kickoff_utc(e) for e in slate_events) if k is not None]
    first = min(kickoffs) if kickoffs else None
    mode: RunMode
    if fixture:
        mode = "fixture"
    elif first is not None and as_of_utc >= first:
        mode = "retrospective"
    else:
        mode = "live"
    return RunContext(
        slate_date=slate_date,
        window=window,
        season=nfl_season_for_date(slate_date),
        as_of_utc=as_of_utc,
        mode=mode,
        first_kickoff_utc=first,
    )


def schedule_kickoff_utc(gameday: Any, gametime: Any) -> datetime | None:
    """nflverse ``gameday``/``gametime`` (US Eastern local) as an aware UTC datetime.

    A missing ``gametime`` reads as 00:00 Eastern, i.e. as early as the game
    could possibly be, so cutoffs err toward treating the game as started.
    """
    if not gameday:
        return None
    clock = str(gametime or "00:00").strip() or "00:00"
    try:
        local = datetime.fromisoformat(f"{str(gameday).strip()[:10]}T{clock[:5]}")
    except ValueError:
        return None
    return local.replace(tzinfo=EASTERN).astimezone(UTC)


def before_week_from_schedule(
    schedule_records: Iterable[Mapping[str, Any]],
    slate_date: str,
    as_of_utc: datetime,
) -> int | None:
    """First regular-season week whose data is NOT admissible for this run.

    Week-bound sources (NGS, PBP, player weeks) may use weeks strictly below the
    returned value: weeks before the slate's week whose games had all kicked off
    by ``as_of_utc``. Same-week games that finished earlier (e.g. Thursday for a
    Sunday slate) are deliberately excluded. Returns None when the schedule has
    no usable rows, so callers report the source UNAVAILABLE.
    """
    by_week: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for rec in schedule_records:
        try:
            week = int(rec.get("week") or 0)
        except (TypeError, ValueError):
            continue
        if week >= 1:
            by_week[week].append(rec)
    if not by_week:
        return None
    after_last = max(by_week) + 1
    slate_week = min(
        (
            w
            for w, games in by_week.items()
            if any(str(g.get("gameday") or "") >= slate_date for g in games)
        ),
        default=after_last,
    )

    def started(g: Mapping[str, Any]) -> bool:
        kick = try_parse_utc(g.get("kickoff_utc")) or schedule_kickoff_utc(
            g.get("gameday"), g.get("gametime")
        )
        return kick is not None and kick < as_of_utc

    pending = min((w for w, games in by_week.items() if not all(started(g) for g in games)),
                  default=after_last)
    return min(slate_week, pending)
