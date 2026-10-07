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
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from outlier_nfl.utils import nfl_season_for_date

EASTERN = ZoneInfo("America/New_York")
# How long after kickoff a game is assumed to still be in progress. Regulation
# runs about 3h15m; 4h covers overtime and delays, so a week's data and a game's
# postgame fields are admitted only once its last game has surely ended.
GAME_END_BUFFER = timedelta(hours=4)

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "slate_date": self.slate_date,
            "window": self.window,
            "season": self.season,
            "as_of_utc": self.as_of_iso,
            "mode": self.mode,
            "first_kickoff_utc": (
                self.first_kickoff_utc.isoformat() if self.first_kickoff_utc else None
            ),
        }


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

    A missing or unparseable ``gametime`` reads as 23:59 Eastern on ``gameday``,
    the latest the game could start. Every caller uses the kickoff as an
    admissibility bound ("has this game started / finished by as_of?"), so an
    unknown time must never make a game count as started early and let its
    week's data or postgame fields in.
    """
    if not gameday:
        return None
    day = str(gameday).strip()[:10]
    clock = str(gametime or "").strip()[:5]
    try:
        local = datetime.fromisoformat(f"{day}T{clock}") if clock else None
    except ValueError:
        local = None
    if local is None:
        try:
            local = datetime.fromisoformat(f"{day}T23:59")
        except ValueError:
            return None
    return local.replace(tzinfo=EASTERN).astimezone(UTC)


def game_finished_by(kickoff: datetime | None, as_of_utc: datetime) -> bool:
    """True once a game kicking off at ``kickoff`` has surely ended by ``as_of_utc``."""
    return kickoff is not None and kickoff + GAME_END_BUFFER <= as_of_utc


def before_week_from_schedule(
    schedule_records: Iterable[Mapping[str, Any]],
    slate_date: str,
    as_of_utc: datetime,
) -> int | None:
    """First regular-season week whose data is NOT admissible for this run.

    Week-bound sources (NGS, PBP, player weeks) may use weeks strictly below the
    returned value: weeks before the slate's week whose games had all finished
    (kickoff + ``GAME_END_BUFFER``) by ``as_of_utc``. Same-week games that finished earlier (e.g. Thursday for a
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

    def finished(g: Mapping[str, Any]) -> bool:
        kick = try_parse_utc(g.get("kickoff_utc")) or schedule_kickoff_utc(
            g.get("gameday"), g.get("gametime")
        )
        return game_finished_by(kick, as_of_utc)

    pending = min((w for w, games in by_week.items() if not all(finished(g) for g in games)),
                  default=after_last)
    return min(slate_week, pending)
