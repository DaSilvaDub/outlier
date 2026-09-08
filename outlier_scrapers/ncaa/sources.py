"""Data-source contracts for the NCAA pipeline.

This module defines *what the pipeline needs*, not how any particular vendor
supplies it. Adapters implement these protocols and the pipeline stays ignorant
of whether ratings came from a public API, a paid feed, or a CSV someone
maintains by hand.

No adapter implementation lives here and none should: the network shape of a
provider changes far more often than the model does, and mixing the two is how
a model ends up unable to run because a vendor renamed a field.

Field-level provenance is required, not optional. ``GameInputs.sources`` records
which adapter supplied each block so a surprising probability can be traced to
the feed that caused it.

Source mapping this pipeline is designed around (all of it substitutable):

===================  ====================================================
Block                Typical supplier
===================  ====================================================
Schedule / results   A season schedule endpoint keyed by season and week
Efficiency ratings   An advanced-stats provider exposing EPA, success
                     rate, explosiveness, points per drive, havoc, and
                     opponent-adjusted variants
Roster / injuries    Depth charts plus an availability report; the
                     starting-quarterback field is the one that matters
Recruiting / talent  A composite roster-talent rating
Coaching             A curated table -- no public feed grades staffs
Weather              A forecast API keyed by venue and kickoff time
Market               An odds feed exposing opening, current, best, and
                     closing prices for moneyline, spread, and total
===================  ====================================================

Two rules every adapter must follow:

1. **Return ``None`` for anything not actually observed.** A league average
   substituted for a missing field is indistinguishable from real data once it
   reaches the model, and it will quietly raise confidence on exactly the games
   the pipeline knows least about.
2. **Never let the betting line become a model input.** Market data belongs in
   :class:`~outlier_scrapers.ncaa.features.MarketQuote` and nowhere else. An
   efficiency rating fitted against the spread will reproduce the spread, and
   the edge it appears to find will be an artifact.
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, runtime_checkable

from outlier_scrapers.ncaa.features import (
    CoachingProfile,
    GameContext,
    MarketQuote,
    RosterStatus,
    TeamEfficiency,
)

__all__ = [
    "CoachingSource",
    "ContextSource",
    "EfficiencySource",
    "MarketSource",
    "RosterSource",
    "ScheduleEntry",
    "ScheduleSource",
]


class ScheduleEntry(Protocol):
    """Minimum shape of a scheduled game."""

    game_id: str
    home_team: str
    away_team: str
    kickoff_utc: str | None


@runtime_checkable
class ScheduleSource(Protocol):
    """Supplies the slate to evaluate."""

    def fetch_slate(self, season: int, week: int) -> Sequence[ScheduleEntry]:
        ...


@runtime_checkable
class EfficiencySource(Protocol):
    """Supplies opponent-adjusted team strength, keyed by team code."""

    def fetch_efficiency(self, season: int, week: int) -> Mapping[str, TeamEfficiency]:
        ...


@runtime_checkable
class RosterSource(Protocol):
    """Supplies roster quality and availability, keyed by team code."""

    def fetch_rosters(self, season: int, week: int) -> Mapping[str, RosterStatus]:
        ...


@runtime_checkable
class CoachingSource(Protocol):
    """Supplies coaching and program-stability inputs, keyed by team code."""

    def fetch_coaching(self, season: int) -> Mapping[str, CoachingProfile]:
        ...


@runtime_checkable
class ContextSource(Protocol):
    """Supplies situational and weather context, keyed by ``game_id``."""

    def fetch_context(self, season: int, week: int) -> Mapping[str, GameContext]:
        ...


@runtime_checkable
class MarketSource(Protocol):
    """Supplies prices, keyed by ``game_id``."""

    def fetch_market(self, season: int, week: int) -> Mapping[str, MarketQuote]:
        ...
