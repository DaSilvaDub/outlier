"""Run identity and the ``as_of_utc`` boundary for one NBA run (plan M0).

Modes: ``live`` and ``fixture`` may publish; ``replay`` (as_of well behind the
wall clock) and ``retrospective`` (run or as_of at/after the slate's first tip)
are bundle-only. Every reader goes through :class:`AsOfReader`, which drops rows
stamped at/after the cutoff and raises on unverifiable stamps in non-live modes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

# An as_of further behind the wall clock than this is a replay: live-fetched odds
# postdate it.
REPLAY_TOLERANCE = timedelta(minutes=15)

RunMode = Literal["live", "replay", "retrospective", "fixture"]


class LeakageError(RuntimeError):
    """A row's information time cannot be proven to precede the cutoff."""


def parse_utc(value: datetime | str) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone-aware, got {value!r}")
    return dt.astimezone(UTC)


def try_parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return parse_utc(value)
    except (TypeError, ValueError):
        return None


def nba_season_for_date(slate_date: str) -> str:
    """``"2026-27"`` for any date from August 2026 through July 2027."""
    year, month = int(slate_date[:4]), int(slate_date[5:7])
    start = year if month >= 8 else year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def event_tip_utc(event: Mapping[str, Any]) -> datetime | None:
    return try_parse_utc(
        event.get("startTime")
        or event.get("scheduledTime")
        or event.get("starts_at")
        or event.get("event_starts_at")
    )


@dataclass(frozen=True)
class RunContext:
    slate_date: str
    season: str
    as_of_utc: datetime
    mode: RunMode
    first_tip_utc: datetime | None
    run_started_utc: datetime
    replay: bool = False

    @property
    def publishes(self) -> bool:
        return self.mode in ("live", "fixture")

    @property
    def historical(self) -> bool:
        return self.mode in ("retrospective", "replay")

    @property
    def run_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.slate_date}|{self.as_of_utc.isoformat()}|{self.mode}".encode()
        ).hexdigest()[:8]
        return f"{self.slate_date}_{self.as_of_utc.strftime('%H%M%S')}_{self.mode}_{digest}"

    @property
    def publication_reason(self) -> str | None:
        if self.publishes:
            return None
        first = self.first_tip_utc.isoformat() if self.first_tip_utc else "?"
        reasons = []
        if self.mode == "retrospective":
            reasons.append(
                f"run started {self.run_started_utc.isoformat()} / as_of "
                f"{self.as_of_utc.isoformat()} is at/after first tip {first}"
            )
        if self.replay:
            reasons.append(
                f"replay: as_of {self.as_of_utc.isoformat()} is before run start "
                f"{self.run_started_utc.isoformat()}; odds were fetched live and postdate as_of"
            )
        return "; ".join(reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slate_date": self.slate_date,
            "season": self.season,
            "as_of_utc": self.as_of_utc.isoformat(),
            "run_started_utc": self.run_started_utc.isoformat(),
            "mode": self.mode,
            "replay": self.replay,
            "first_tip_utc": self.first_tip_utc.isoformat() if self.first_tip_utc else None,
            "run_id": self.run_id,
        }


def make_run_context(
    slate_date: str,
    as_of_utc: datetime,
    *,
    fixture: bool = False,
    slate_events: Iterable[Mapping[str, Any]] = (),
    run_started_utc: datetime | None = None,
) -> RunContext:
    as_of = parse_utc(as_of_utc)
    started = parse_utc(run_started_utc) if run_started_utc else as_of
    tips = [t for t in (event_tip_utc(e) for e in slate_events) if t is not None]
    first = min(tips) if tips else None
    replay = not fixture and started - as_of > REPLAY_TOLERANCE
    mode: RunMode
    if fixture:
        mode = "fixture"
    elif first is not None and max(as_of, started) >= first:
        mode = "retrospective"
    elif replay:
        mode = "replay"
    else:
        mode = "live"
    return RunContext(
        slate_date=slate_date,
        season=nba_season_for_date(slate_date),
        as_of_utc=as_of,
        mode=mode,
        first_tip_utc=first,
        run_started_utc=started,
        replay=replay,
    )


class AsOfReader:
    """Admit only rows whose information time strictly precedes the cutoff.

    ``stamp >= as_of_utc`` (and, for game-scoped rows, ``stamp >= tip``) is a
    rejection. A row with a missing or unparseable stamp raises
    :class:`LeakageError` in replay/retrospective modes; in live/fixture it is
    admitted, since this run fetched it at or before ``as_of_utc``.
    """

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.rejected_post_cutoff = 0

    def admit(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        stamp_key: str,
        tip_key: str | None = None,
    ) -> list[Mapping[str, Any]]:
        kept: list[Mapping[str, Any]] = []
        for row in rows:
            stamp = try_parse_utc(row.get(stamp_key))
            if stamp is None:
                if self.ctx.historical:
                    raise LeakageError(
                        f"row has no usable {stamp_key!r} stamp in {self.ctx.mode} mode: {row!r}"
                    )
                # live/fixture: the row was fetched by this run, at or before as_of
                kept.append(row)
                continue
            if stamp >= self.ctx.as_of_utc:
                self.rejected_post_cutoff += 1
                continue
            if tip_key:
                tip = try_parse_utc(row.get(tip_key))
                if tip is not None and stamp >= tip:
                    self.rejected_post_cutoff += 1
                    continue
            kept.append(row)
        return kept
