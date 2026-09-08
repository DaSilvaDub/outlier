"""Input data model and matchup feature construction for NCAA football.

Spec Part 1 lists roughly ninety inputs across team strength, roster quality,
coaching, game context, and market. This module is where they land, and its
governing rule is Part 10's: *never fabricate unavailable information; label
missing information explicitly.*

Every field is therefore ``| None``. Nothing is imputed, no league average is
silently substituted, and every consumer receives a :class:`DataCoverage`
alongside the numbers describing exactly what was absent. Coverage is what
drives the confidence score down -- a model that cannot see a starting
quarterback should not report the same confidence as one that can.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "CoachingProfile",
    "DataCoverage",
    "GameContext",
    "GameInputs",
    "MarketQuote",
    "MatchupFeatures",
    "RosterStatus",
    "TeamEfficiency",
    "TeamSide",
    "build_matchup_features",
    "coverage_of",
]

QB_STATUS_HEALTHY = "starter"
QB_STATUS_QUESTIONABLE = "questionable"
QB_STATUS_DOUBTFUL = "doubtful"
QB_STATUS_OUT = "out"
QB_STATUS_UNKNOWN = "unknown"

#: Ordered from least to most alarming; ``>= QB_STATUS_DOUBTFUL`` is treated as
#: the starter being effectively unavailable.
QB_STATUSES = (
    QB_STATUS_HEALTHY,
    QB_STATUS_QUESTIONABLE,
    QB_STATUS_DOUBTFUL,
    QB_STATUS_OUT,
    QB_STATUS_UNKNOWN,
)


@dataclass(frozen=True)
class DataCoverage:
    """What the model could actually see."""

    present: int
    total: int
    missing: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return 1.0 if self.total == 0 else self.present / self.total

    def merge(self, other: "DataCoverage") -> "DataCoverage":
        return DataCoverage(
            present=self.present + other.present,
            total=self.total + other.total,
            missing=self.missing + other.missing,
        )


def coverage_of(obj: Any, *, prefix: str = "", skip: Sequence[str] = ()) -> DataCoverage:
    """Count populated fields on a dataclass instance.

    Booleans count as present when set; only ``None`` is missing. Tuple/str
    fields count as present when non-empty.
    """
    skipped = set(skip)
    present = 0
    total = 0
    missing: list[str] = []
    for f in fields(obj):
        if f.name in skipped:
            continue
        total += 1
        value = getattr(obj, f.name)
        if value is None or (isinstance(value, (str, tuple, list)) and len(value) == 0):
            missing.append(f"{prefix}{f.name}")
        else:
            present += 1
    return DataCoverage(present=present, total=total, missing=tuple(missing))


@dataclass(frozen=True)
class TeamEfficiency:
    """Opponent-adjusted team strength (spec Part 1, "Team strength").

    Rate stats are expected already adjusted for opponent quality where the
    source provides it; ``strength_of_schedule`` and
    ``opponent_adjusted_net`` exist so an unadjusted source can still be used
    with the adjustment applied downstream and audited.
    """

    team: str
    power_rating: float | None = None
    offense_epa_play: float | None = None
    defense_epa_play: float | None = None
    offense_success_rate: float | None = None
    defense_success_rate: float | None = None
    offense_explosiveness: float | None = None
    defense_explosiveness: float | None = None
    offense_points_per_drive: float | None = None
    defense_points_per_drive: float | None = None
    early_down_epa: float | None = None
    early_down_epa_allowed: float | None = None
    third_down_conversion: float | None = None
    third_down_defense: float | None = None
    red_zone_td_rate: float | None = None
    red_zone_td_rate_allowed: float | None = None
    havoc_rate: float | None = None
    havoc_rate_allowed: float | None = None
    sack_rate: float | None = None
    sack_rate_allowed: float | None = None
    pressure_rate: float | None = None
    pressure_rate_allowed: float | None = None
    turnover_worthy_play_rate: float | None = None
    takeaway_rate: float | None = None
    offensive_line_grade: float | None = None
    defensive_front_grade: float | None = None
    special_teams_epa: float | None = None
    strength_of_schedule: float | None = None
    opponent_adjusted_net: float | None = None
    #: Neutral-situation seconds per play. Lower is faster.
    seconds_per_play: float | None = None
    pass_rate: float | None = None
    rush_rate: float | None = None
    rush_defense_epa: float | None = None
    pass_defense_epa: float | None = None
    games_played: int | None = None


@dataclass(frozen=True)
class RosterStatus:
    """Roster quality and availability (spec Part 1, "Roster quality")."""

    team: str
    returning_production: float | None = None
    returning_starters: int | None = None
    starting_qb_status: str | None = None
    qb_experience_starts: int | None = None
    qb_grade: float | None = None
    backup_qb_grade: float | None = None
    offensive_line_continuity: float | None = None
    skill_position_depth: float | None = None
    defensive_depth: float | None = None
    portal_additions: int | None = None
    portal_departures: int | None = None
    recruiting_talent_composite: float | None = None
    nfl_caliber_players: int | None = None
    players_out: int | None = None
    players_doubtful: int | None = None
    players_questionable: int | None = None
    key_injuries: tuple[str, ...] = ()
    suspensions: int | None = None
    late_roster_change: bool | None = None
    #: False means the injury report is known to be incomplete -- a direct
    #: confidence penalty under spec Part 10, not a reason to guess.
    injury_report_complete: bool | None = None

    @property
    def starter_unavailable(self) -> bool:
        return self.starting_qb_status in (QB_STATUS_DOUBTFUL, QB_STATUS_OUT)

    @property
    def starter_uncertain(self) -> bool:
        return self.starting_qb_status in (QB_STATUS_QUESTIONABLE, QB_STATUS_UNKNOWN, None)


@dataclass(frozen=True)
class CoachingProfile:
    """Coaching and program stability (spec Part 1, "Coaching")."""

    team: str
    head_coach_grade: float | None = None
    offensive_coordinator_grade: float | None = None
    defensive_coordinator_grade: float | None = None
    continuity_years: float | None = None
    scheme_change: bool | None = None
    coordinator_change: bool | None = None
    #: Win rate when favored by two scores or more.
    record_as_heavy_favorite: float | None = None
    lead_protection_rate: float | None = None
    garbage_time_scoring_tendency: float | None = None
    tempo_when_leading: float | None = None
    first_year_staff: bool | None = None


@dataclass(frozen=True)
class GameContext:
    """Situational and environmental context (spec Part 1, "Game context")."""

    home_team: str
    away_team: str
    kickoff_utc: str | None = None
    week: int | None = None
    neutral_site: bool | None = None
    travel_miles: float | None = None
    timezone_shift_hours: float | None = None
    rest_days_home: int | None = None
    rest_days_away: int | None = None
    short_week_home: bool | None = None
    short_week_away: bool | None = None
    bye_week_home: bool | None = None
    bye_week_away: bool | None = None
    rivalry: bool | None = None
    conference_game: bool | None = None
    look_ahead_home: bool | None = None
    look_ahead_away: bool | None = None
    letdown_home: bool | None = None
    letdown_away: bool | None = None
    season_opener: bool | None = None
    bowl_eligibility_implications: bool | None = None
    conference_title_implications: bool | None = None
    temperature_f: float | None = None
    wind_mph: float | None = None
    precipitation_chance: float | None = None
    altitude_feet: float | None = None
    surface: str | None = None
    dome: bool | None = None
    #: True only for venues whose crowd/altitude/night-game environment is a
    #: recognised edge beyond generic home advantage. Generic road status is
    #: already priced into expected margin via ``home_field_points``; this flag
    #: exists so the road penalty in ``safety.py`` does not double-count it.
    hostile_venue: bool | None = None
    #: False when the forecast is still volatile -- confidence penalty only.
    weather_forecast_stable: bool | None = None


@dataclass(frozen=True)
class MarketQuote:
    """Market state (spec Part 1, "Market information").

    Home-relative sign convention throughout: ``spread_current = -17.5`` means
    the home team is laying 17.5.
    """

    moneyline_home_open: float | None = None
    moneyline_away_open: float | None = None
    moneyline_home_current: float | None = None
    moneyline_away_current: float | None = None
    moneyline_home_best: float | None = None
    moneyline_away_best: float | None = None
    spread_open: float | None = None
    spread_current: float | None = None
    total_open: float | None = None
    total_current: float | None = None
    total_over_price: float | None = None
    total_under_price: float | None = None
    book_count: int | None = None
    captured_at: str | None = None
    hours_since_capture: float | None = None
    reverse_line_movement: bool | None = None
    closing_moneyline_home: float | None = None
    closing_moneyline_away: float | None = None
    closing_spread: float | None = None
    closing_total: float | None = None

    @property
    def spread_move(self) -> float | None:
        if self.spread_open is None or self.spread_current is None:
            return None
        return self.spread_current - self.spread_open

    @property
    def total_move(self) -> float | None:
        if self.total_open is None or self.total_current is None:
            return None
        return self.total_current - self.total_open


@dataclass(frozen=True)
class TeamSide:
    """Everything known about one participant."""

    efficiency: TeamEfficiency
    roster: RosterStatus
    coaching: CoachingProfile

    @property
    def team(self) -> str:
        return self.efficiency.team

    def coverage(self) -> DataCoverage:
        return (
            coverage_of(self.efficiency, prefix="efficiency.", skip=("team",))
            .merge(coverage_of(self.roster, prefix="roster.", skip=("team",)))
            .merge(coverage_of(self.coaching, prefix="coaching.", skip=("team",)))
        )


#: The inputs without which the models are guessing. Coverage over *these*
#: drives the confidence score; coverage over the full ~180-field surface is a
#: much weaker signal, because most of that surface is refinement rather than
#: foundation. Penalising a game for lacking ``garbage_time_scoring_tendency``
#: as heavily as for lacking a starting quarterback would make confidence
#: meaningless -- no real feed populates every field.
CRITICAL_TEAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("efficiency", "power_rating"),
    ("efficiency", "offense_epa_play"),
    ("efficiency", "defense_epa_play"),
    ("efficiency", "games_played"),
    ("roster", "starting_qb_status"),
    ("roster", "players_out"),
    ("roster", "injury_report_complete"),
)

CRITICAL_MARKET_FIELDS: tuple[str, ...] = (
    "spread_current",
    "moneyline_home_current",
    "moneyline_away_current",
)


@dataclass(frozen=True)
class GameInputs:
    """One FBS game, fully assembled and ready for the models."""

    game_id: str
    home: TeamSide
    away: TeamSide
    context: GameContext
    market: MarketQuote = field(default_factory=MarketQuote)
    #: Free-form provenance, e.g. {"ratings": "cfbd", "market": "outlier"}.
    sources: Mapping[str, str] = field(default_factory=dict)

    def coverage(self) -> DataCoverage:
        return (
            self.home.coverage()
            .merge(self.away.coverage())
            .merge(coverage_of(self.context, prefix="context.", skip=("home_team", "away_team")))
            .merge(coverage_of(self.market, prefix="market."))
        )

    def critical_coverage(self) -> DataCoverage:
        """Coverage restricted to :data:`CRITICAL_TEAM_FIELDS` and market price."""
        present = 0
        total = 0
        missing: list[str] = []
        for label, side in (("home", self.home), ("away", self.away)):
            for group, name in CRITICAL_TEAM_FIELDS:
                total += 1
                if getattr(getattr(side, group), name) is None:
                    missing.append(f"{label}.{group}.{name}")
                else:
                    present += 1
        for name in CRITICAL_MARKET_FIELDS:
            total += 1
            if getattr(self.market, name) is None:
                missing.append(f"market.{name}")
            else:
                present += 1
        return DataCoverage(present=present, total=total, missing=tuple(missing))

    def side(self, team: str) -> TeamSide:
        if team == self.home.team:
            return self.home
        if team == self.away.team:
            return self.away
        raise KeyError(f"{team!r} is not in game {self.game_id}")

    def opponent_of(self, team: str) -> TeamSide:
        return self.away if team == self.home.team else self.home


def _diff(a: float | None, b: float | None) -> float | None:
    """``a - b``, or ``None`` when either side is unavailable."""
    if a is None or b is None:
        return None
    return a - b


# Typical one-standard-deviation spread of each metric family across FBS.
# Composite features average several metrics drawn from different families, so
# each contribution is divided by its family scale first. Averaging a 0-100
# grade differential directly against a 0-1 rate differential would let the
# grade term dominate by two orders of magnitude and silently reduce the
# composite to a single metric.
GRADE_SCALE = 15.0
RATE_SCALE = 0.08
EPA_SCALE = 0.15
QB_AVAILABILITY_SCALE = 1.0
POWER_RATING_SCALE = 10.0
POINTS_PER_DRIVE_SCALE = 0.5
TALENT_SCALE = 150.0


def _mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _blend(pairs: Iterable[tuple[float | None, float]]) -> float | None:
    """Average several differentials after dividing each by its family scale.

    Returns a unitless quantity in approximate standard-deviation units, or
    ``None`` when no contributing metric was available.
    """
    scaled = [value / scale for value, scale in pairs if value is not None and scale > 0]
    if not scaled:
        return None
    return sum(scaled) / len(scaled)


@dataclass(frozen=True)
class MatchupFeatures:
    """Derived, favorite-relative differentials consumed by every model.

    All differentials are stated from the perspective of ``favorite`` -- a
    positive value always means "good for the side we are considering backing".
    This is what lets the mismatch, safety, and totals models share one feature
    vector without each re-deriving sign conventions.

    Units: single-source fields keep their native units (``quality_gap`` is in
    rating points, ``offense_epa_edge`` in EPA/play, ``talent_edge`` in
    composite points). The four composites -- ``trenches_edge``,
    ``quarterback_edge``, ``depth_edge``, ``coaching_edge`` -- are unitless,
    already divided by their family scales, and are therefore the only fields
    directly comparable to each other.
    """

    game_id: str
    favorite: str
    underdog: str
    favorite_is_home: bool
    neutral_site: bool
    quality_gap: float | None
    offense_epa_edge: float | None
    defense_epa_edge: float | None
    success_rate_edge: float | None
    explosiveness_edge: float | None
    points_per_drive_edge: float | None
    trenches_edge: float | None
    havoc_edge: float | None
    third_down_edge: float | None
    red_zone_edge: float | None
    turnover_edge: float | None
    special_teams_edge: float | None
    sos_edge: float | None
    talent_edge: float | None
    depth_edge: float | None
    coaching_edge: float | None
    quarterback_edge: float | None
    injury_edge: float | None
    opponent_adjusted_edge: float | None
    lead_protection_edge: float | None
    returning_production_edge: float | None
    rest_edge: float | None
    #: Market spread from the favorite's perspective: negative means the
    #: favorite is laying points. Reported so the board can show the market's
    #: number rather than the model's own expected margin.
    market_spread_favorite: float | None
    coverage: DataCoverage
    critical_coverage: DataCoverage
    flags: tuple[str, ...] = ()


def build_matchup_features(
    game: GameInputs,
    *,
    favorite: str | None = None,
) -> MatchupFeatures:
    """Build favorite-relative differentials for ``game``.

    ``favorite`` defaults to the market's favorite (from the current spread,
    falling back to the moneyline). When the market is silent, the higher power
    rating decides; when that is also missing, the home team is used and the row
    is flagged ``favorite_undetermined`` so nothing downstream mistakes the
    choice for a judgement.
    """
    flags: list[str] = []
    if favorite is None:
        favorite, resolution_flag = _resolve_favorite(game)
        if resolution_flag:
            flags.append(resolution_flag)

    fav = game.side(favorite)
    dog = game.opponent_of(favorite)
    favorite_is_home = favorite == game.home.team

    fav_eff, dog_eff = fav.efficiency, dog.efficiency
    fav_roster, dog_roster = fav.roster, dog.roster
    fav_coach, dog_coach = fav.coaching, dog.coaching

    # Defensive metrics are "allowed" quantities: lower is better, so the edge
    # is the opponent's value minus ours.
    defense_edge = _diff(dog_eff.defense_epa_play, fav_eff.defense_epa_play)

    trenches = _blend(
        [
            (_diff(fav_eff.offensive_line_grade, dog_eff.defensive_front_grade), GRADE_SCALE),
            (_diff(dog_eff.sack_rate_allowed, fav_eff.sack_rate_allowed), RATE_SCALE),
            (_diff(fav_eff.pressure_rate, dog_eff.pressure_rate), RATE_SCALE),
        ]
    )

    quarterback = _blend(
        [
            (_diff(fav_roster.qb_grade, dog_roster.qb_grade), GRADE_SCALE),
            (_qb_availability_edge(fav_roster, dog_roster), QB_AVAILABILITY_SCALE),
        ]
    )

    depth = _blend(
        [
            (_diff(fav_roster.skill_position_depth, dog_roster.skill_position_depth), GRADE_SCALE),
            (_diff(fav_roster.defensive_depth, dog_roster.defensive_depth), GRADE_SCALE),
        ]
    )

    coaching = _blend(
        [
            (_diff(fav_coach.head_coach_grade, dog_coach.head_coach_grade), GRADE_SCALE),
            (
                _diff(fav_coach.offensive_coordinator_grade, dog_coach.defensive_coordinator_grade),
                GRADE_SCALE,
            ),
            (_diff(fav_coach.lead_protection_rate, dog_coach.lead_protection_rate), RATE_SCALE),
        ]
    )

    injury_edge = None
    fav_burden = _availability_burden(fav_roster)
    dog_burden = _availability_burden(dog_roster)
    if fav_burden is not None and dog_burden is not None:
        injury_edge = dog_burden - fav_burden

    rest_edge = None
    if game.context.rest_days_home is not None and game.context.rest_days_away is not None:
        home_edge = float(game.context.rest_days_home - game.context.rest_days_away)
        rest_edge = home_edge if favorite_is_home else -home_edge

    features = MatchupFeatures(
        game_id=game.game_id,
        favorite=favorite,
        underdog=dog.team,
        favorite_is_home=favorite_is_home,
        neutral_site=bool(game.context.neutral_site),
        quality_gap=_diff(fav_eff.power_rating, dog_eff.power_rating),
        offense_epa_edge=_diff(fav_eff.offense_epa_play, dog_eff.offense_epa_play),
        defense_epa_edge=defense_edge,
        success_rate_edge=_diff(fav_eff.offense_success_rate, dog_eff.offense_success_rate),
        explosiveness_edge=_diff(fav_eff.offense_explosiveness, dog_eff.offense_explosiveness),
        points_per_drive_edge=_diff(
            fav_eff.offense_points_per_drive, dog_eff.offense_points_per_drive
        ),
        trenches_edge=trenches,
        havoc_edge=_diff(fav_eff.havoc_rate, dog_eff.havoc_rate),
        third_down_edge=_diff(fav_eff.third_down_conversion, dog_eff.third_down_conversion),
        red_zone_edge=_diff(fav_eff.red_zone_td_rate, dog_eff.red_zone_td_rate),
        turnover_edge=_diff(
            dog_eff.turnover_worthy_play_rate, fav_eff.turnover_worthy_play_rate
        ),
        special_teams_edge=_diff(fav_eff.special_teams_epa, dog_eff.special_teams_epa),
        sos_edge=_diff(fav_eff.strength_of_schedule, dog_eff.strength_of_schedule),
        talent_edge=_diff(
            fav_roster.recruiting_talent_composite, dog_roster.recruiting_talent_composite
        ),
        depth_edge=depth,
        coaching_edge=coaching,
        quarterback_edge=quarterback,
        injury_edge=injury_edge,
        opponent_adjusted_edge=_diff(
            fav_eff.opponent_adjusted_net, dog_eff.opponent_adjusted_net
        ),
        lead_protection_edge=_diff(
            fav_coach.lead_protection_rate, dog_coach.lead_protection_rate
        ),
        returning_production_edge=_diff(
            fav_roster.returning_production, dog_roster.returning_production
        ),
        rest_edge=rest_edge,
        market_spread_favorite=_favorite_relative_spread(game, favorite_is_home),
        coverage=game.coverage(),
        critical_coverage=game.critical_coverage(),
        flags=tuple(flags),
    )
    return features


def _favorite_relative_spread(game: GameInputs, favorite_is_home: bool) -> float | None:
    """Market spread signed from the favorite's side."""
    spread = game.market.spread_current
    if spread is None:
        spread = game.market.spread_open
    if spread is None:
        return None
    return spread if favorite_is_home else -spread


def _availability_burden(roster: RosterStatus) -> float | None:
    """Weighted count of unavailable and doubtful contributors.

    Questionable players are weighted lightly because in college football the
    designation is used inconsistently across programs; ``None`` when the team
    reports no availability data at all, which is a missing-data condition and
    never a zero.
    """
    parts = [
        (roster.players_out, 1.0),
        (roster.players_doubtful, 0.5),
        (roster.players_questionable, 0.25),
        (roster.suspensions, 1.0),
    ]
    known = [count * weight for count, weight in parts if count is not None]
    if not known:
        return None
    return sum(known)


def _qb_availability_edge(fav: RosterStatus, dog: RosterStatus) -> float | None:
    """Availability-only quarterback edge on a -1..1 scale."""
    scores = {
        QB_STATUS_HEALTHY: 1.0,
        QB_STATUS_QUESTIONABLE: 0.4,
        QB_STATUS_DOUBTFUL: -0.3,
        QB_STATUS_OUT: -1.0,
    }
    fav_score = scores.get(fav.starting_qb_status or "")
    dog_score = scores.get(dog.starting_qb_status or "")
    if fav_score is None or dog_score is None:
        return None
    return fav_score - dog_score


def _resolve_favorite(game: GameInputs) -> tuple[str, str | None]:
    """Pick the side to evaluate, reporting how the choice was made."""
    spread = game.market.spread_current
    if spread is None:
        spread = game.market.spread_open
    if spread is not None:
        if spread < 0:
            return game.home.team, None
        if spread > 0:
            return game.away.team, None
        return game.home.team, "pick_em_no_favorite"

    home_ml = game.market.moneyline_home_current
    away_ml = game.market.moneyline_away_current
    if home_ml is not None and away_ml is not None:
        return (game.home.team if home_ml < away_ml else game.away.team), None

    home_rating = game.home.efficiency.power_rating
    away_rating = game.away.efficiency.power_rating
    if home_rating is not None and away_rating is not None:
        return (game.home.team if home_rating >= away_rating else game.away.team), "no_market_price"

    return game.home.team, "favorite_undetermined"
