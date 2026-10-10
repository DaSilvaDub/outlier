"""Explicit season phase of a slate, and the supported-phase gate (forensic report F29, #224).

The adapters, tape builders and graders (``external/schedule.py``, ``ngs.py``,
``pbp.py``, ``tape_nflverse.py``, ``scorecard.py``) only implement the regular
season of the modern (2025+) data schema. Until they cover more, a slate is
published only when its phase is positively known to be ``REG`` of a 2025+
season. Postseason, preseason, a phase the schedule does not state, and pre-2025
seasons (legacy depth-chart schema) are rejected before publication with an
``UNSUPPORTED`` ``season_phase`` receipt.

The phase is carried separately from the week: an event's own season-type
field wins (``seasonType``/``season_type``/``gameType``/``game_type``); else
weeks 1-18 are ``REG`` and 19+ are ``POST``. A January week-18 game is ``REG``.
When both are absent, the already-loaded nflverse schedule supplies ``game_type``
for the same Eastern kickoff date, season and team pair in either order
(neutral sites). No match or more than one match remains unsupported.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from outlier_nfl.config import normalize_team
from outlier_nfl.matchup import _event_team_codes
from outlier_nfl.run_context import EASTERN, event_kickoff_utc
from outlier_nfl.stage_receipts import StageReceipt, receipt

SUPPORTED_SEASON_TYPES = frozenset({"REG"})
LAST_REGULAR_SEASON_WEEK = 18
# First season whose depth-chart/roster shape the modern parsers implement.
FIRST_SUPPORTED_SEASON = 2025

_TYPE_KEYS = ("seasonType", "season_type", "gameType", "game_type")
_ALIASES = {
    "REG": "REG", "REGULAR": "REG", "REGULAR_SEASON": "REG",
    "POST": "POST", "POSTSEASON": "POST", "PLAYOFF": "POST", "PLAYOFFS": "POST",
    "WC": "POST", "WILDCARD": "POST", "WILD_CARD": "POST", "DIV": "POST", "DIVISIONAL": "POST",
    "CON": "POST", "CONF": "POST", "CONFERENCE": "POST", "SB": "POST", "SUPERBOWL": "POST",
    "SUPER_BOWL": "POST",
    "PRE": "PRE", "PRESEASON": "PRE", "HOF": "PRE",
}


def event_season_type(
    event: Mapping[str, Any], schedule_records: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    """``REG``/``POST``/``PRE`` for one schedule event, or None when it cannot be told."""
    for key in _TYPE_KEYS:
        raw = event.get(key)
        if raw not in (None, ""):
            token = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
            return _ALIASES.get(token, token)
    try:
        week = int(event.get("week"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        week = 0
    if 1 <= week <= LAST_REGULAR_SEASON_WEEK:
        return "REG"
    if week > LAST_REGULAR_SEASON_WEEK:
        return "POST"
    kickoff = event_kickoff_utc(event)
    home, away = _event_team_codes(event)
    if kickoff is None or not normalize_team(home) or not normalize_team(away):
        return None
    day = kickoff.astimezone(EASTERN).date().isoformat()
    # Neutral-site games (London, Germany, ...) can list home/away differently in
    # the API and nflverse: match the unordered team pair, plus kickoff date and
    # season. Exactly one row must match; zero or several leave the game unknown.
    pair = frozenset({normalize_team(home), normalize_team(away)})
    season = event.get("season")
    matches = [
        row for row in schedule_records
        if row.get("gameday") == day
        and frozenset({normalize_team(row.get("home_team")),
                       normalize_team(row.get("away_team"))}) == pair
        and (season in (None, "") or row.get("season") in (None, "")
             or str(row.get("season")) == str(season))
    ]
    if len(matches) != 1:
        return None
    return event_season_type({"game_type": matches[0].get("game_type")})


def slate_season_type(
    events: Iterable[Mapping[str, Any]], schedule_records: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    """The slate's single phase, ``MIXED`` when events disagree, None when empty/unknown."""
    kinds = {event_season_type(e, schedule_records) for e in events}
    if not kinds:
        return None
    if len(kinds) > 1:
        return "MIXED"
    return next(iter(kinds))


def season_phase_receipt(
    events: Sequence[Mapping[str, Any]], season: int | None,
    schedule_records: Sequence[Mapping[str, Any]] = (),
) -> StageReceipt:
    """Required receipt: OK only for a regular-season slate of a supported season."""
    if not events:
        return receipt("season_phase", "EMPTY", received=0)
    kinds: dict[str, int] = {}
    for e in events:
        k = event_season_type(e, schedule_records) or "UNKNOWN"
        kinds[k] = kinds.get(k, 0) + 1
    unsupported = {k: n for k, n in kinds.items() if k not in SUPPORTED_SEASON_TYPES}
    if unsupported:
        detail = ", ".join(f"{n} {k}" for k, n in sorted(unsupported.items()))
        return receipt(
            "season_phase", "UNSUPPORTED",
            reason=f"{detail} event(s); only regular-season (REG) slates are supported",
            expected=len(events), received=len(events) - sum(unsupported.values()),
        )
    if season is None or season < FIRST_SUPPORTED_SEASON:
        return receipt(
            "season_phase", "UNSUPPORTED",
            reason=f"season {season}: pre-{FIRST_SUPPORTED_SEASON} (legacy depth-chart schema) "
            "is not supported",
            expected=len(events), received=0,
        )
    return receipt("season_phase", "OK", expected=len(events), received=len(events))
