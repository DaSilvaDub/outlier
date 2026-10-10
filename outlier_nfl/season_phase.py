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
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

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


def event_season_type(event: Mapping[str, Any]) -> str | None:
    """``REG``/``POST``/``PRE`` for one schedule event, or None when it cannot be told."""
    for key in _TYPE_KEYS:
        raw = event.get(key)
        if raw not in (None, ""):
            token = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
            return _ALIASES.get(token, token)
    try:
        week = int(event.get("week"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if 1 <= week <= LAST_REGULAR_SEASON_WEEK:
        return "REG"
    return "POST" if week > LAST_REGULAR_SEASON_WEEK else None


def slate_season_type(events: Iterable[Mapping[str, Any]]) -> str | None:
    """The slate's single phase, ``MIXED`` when events disagree, None when empty/unknown."""
    kinds = {event_season_type(e) for e in events}
    if not kinds:
        return None
    if len(kinds) > 1:
        return "MIXED"
    return next(iter(kinds))


def season_phase_receipt(events: Sequence[Mapping[str, Any]], season: int | None) -> StageReceipt:
    """Required receipt: OK only for a regular-season slate of a supported season."""
    if not events:
        return receipt("season_phase", "EMPTY", received=0)
    kinds: dict[str, int] = {}
    for e in events:
        k = event_season_type(e) or "UNKNOWN"
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
