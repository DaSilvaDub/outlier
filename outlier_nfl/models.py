"""Strongly typed, immutable domain models for outlier_nfl.

Defines immutable dataclasses for sportsbook prices, game lines,
player props, and event metadata. Completely decoupled from outlier_scrapers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BookPrice:
    """Individual sportsbook price and odds quotation."""

    book: str
    odds: int
    odds_raw: str
    decimal: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NflGameLine:
    """Normalized NFL game line (spread, game total, team total, or moneyline)."""

    event_id: str
    event_starts_at: str | None
    matchup: str
    home_team: str
    away_team: str
    market_type: str  # e.g. GAMELINE, TEAM_PROP
    market: str  # e.g. SPREAD, TOTAL, POINTS, ML
    proposition: str
    position: str  # HOME, AWAY, OVER, UNDER
    line: float | None
    signed_line: str | None  # e.g. -3.5, +3.5
    selection: str  # e.g. "KC -3.5", "Over 47.5"
    team: str | None  # Team attribution for spreads / team totals
    books: tuple[BookPrice, ...] | list[BookPrice]
    best_odds: int | None
    implied_probability: float | None  # Percentage e.g. 52.381
    scope: str = "full_game"
    market_id: str | None = None
    outcome_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.books, list):
            object.__setattr__(self, "books", tuple(self.books))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["books"] = [b.to_dict() if isinstance(b, BookPrice) else b for b in self.books]
        return result


@dataclass(frozen=True)
class NflPlayerProp:
    """Normalized NFL player proposition record."""

    event_id: str
    event_starts_at: str | None
    matchup: str
    team: str | None
    opponent: str | None
    player_name: str
    player_id: str | None
    market: str  # e.g. PASS_YDS, RUSH_YDS, REC_YDS, ANYTIME_TD
    market_raw: str
    position: str  # OVER, UNDER, YES
    line: float
    books: tuple[BookPrice, ...] | list[BookPrice]
    best_odds: int | None
    implied_probability: float | None  # Percentage e.g. 52.381
    l5_hit_rate: float | None = None
    l10_hit_rate: float | None = None
    l20_hit_rate: float | None = None
    season_hit_rate: float | None = None
    scope: str = "full_game"
    market_id: str | None = None
    outcome_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.books, list):
            object.__setattr__(self, "books", tuple(self.books))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["books"] = [b.to_dict() if isinstance(b, BookPrice) else b for b in self.books]
        return result


@dataclass(frozen=True)
class NflEvent:
    """Structured NFL schedule event."""

    event_id: str
    start_time: str | None
    status: str | None
    season: int | None
    week: int | None
    venue: str | None
    home_team: str
    away_team: str
    matchup: str
    home_team_id: str | None = None
    away_team_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NflExtractionSummary:
    """Execution summary of an NFL extraction run."""

    status: str
    date: str
    timestamp_utc: str
    events_count: int
    game_lines_count: int
    player_props_count: int
    totals_count: int
    spreads_count: int
    team_totals_count: int
    player_props_breakdown: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
