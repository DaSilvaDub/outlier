"""Today's NBA slate from ESPN's public JSON: schedule, DraftKings lines, injury listings.

This is a coarse, free fallback for when the Outlier feed is unavailable. The lines are a
**single book** (DraftKings, as ESPN embeds it) with **no capture timestamp**, so they are
never a verified consensus: callers must label them as such. Nothing here reads credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from outlier_nba.identity import resolve_team
from outlier_nba.ingest.espn import ESPN_BASE, Fetcher, http_fetch

# ESPN tricodes that differ from the registry's (``results.TEAM_ALIASES`` lists the same pairs).
_ESPN_TRICODES = {"NY": "NYK", "GS": "GSW", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}
IN_PROGRESS = frozenset({"STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_END_PERIOD"})
# A listing with one of these statuses can change tonight's lineup; "Available" cannot.
BLOCKING_STATUSES = frozenset({"Out", "Doubtful", "Questionable", "Day-To-Day"})


@dataclass(frozen=True)
class BookLines:
    """One book's full-game prices (American odds; ``sp_home_line`` is the home handicap)."""

    book: str
    ml_home: int
    ml_away: int
    sp_home_line: float
    sp_home_odds: int
    sp_away_odds: int
    tot_line: float
    tot_over_odds: int
    tot_under_odds: int


@dataclass(frozen=True)
class SlateGame:
    event_id: str
    home: str
    away: str
    tip: datetime
    status: str
    lines: BookLines | None
    neutral: bool | None  # None = unknown: home court is never assumed

    @property
    def scheduled(self) -> bool:
        return self.status == "STATUS_SCHEDULED"

    @property
    def in_progress(self) -> bool:
        return self.status in IN_PROGRESS


@dataclass(frozen=True)
class InjuryNote:
    player: str
    status: str
    reported: datetime | None
    note: str

    def is_fresh(self, now: datetime, hours: float) -> bool:
        """New lineup-changing news: a blocking status reported within ``hours`` of ``now``.

        An unparseable date is *not* treated as fresh: long-standing absences are already in
        recent results, and a guess either way would be unexplainable.
        """
        if self.reported is None or self.status not in BLOCKING_STATUSES:
            return False
        return now - self.reported <= timedelta(hours=hours)


def parse_american(raw: Any) -> int | None:
    """American price from ESPN's string form (``"+151"``, ``"-107"``, ``"EVEN"``)."""
    if isinstance(raw, bool) or raw is None:
        return None
    text = str(raw).strip()
    if text.upper() == "EVEN":
        return 100
    try:
        value = int(float(text.replace("+", "")))
    except ValueError:
        return None
    return value if abs(value) >= 100 else None  # |price| < 100 is a stub, not a price


def _tricode(abbr: Any) -> str | None:
    text = str(abbr or "").strip()
    return resolve_team(text) or resolve_team(_ESPN_TRICODES.get(text.upper(), text))


def _parse_time(raw: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _spread_line(raw: Any) -> float:
    """Home handicap from ESPN's string form; a pick'em (``PK``) is 0."""
    text = str(raw).strip()
    if text.upper() in ("PK", "PICK", "PICKEM", "PICK'EM", "EVEN"):
        return 0.0
    return float(text.replace("+", ""))


def _neutral(competition: Mapping[str, Any]) -> bool | None:
    """Venue flag; only a real boolean counts, anything else is unknown."""
    flag = competition.get("neutralSite")
    return flag if isinstance(flag, bool) else None


def _lines(competition: Mapping[str, Any]) -> BookLines | None:
    odds = competition.get("odds") or []
    if not odds:
        return None
    o = odds[0]
    try:
        home_sp = o["pointSpread"]["home"]["close"]
        parsed = {
            "ml_home": parse_american(o["moneyline"]["home"]["close"]["odds"]),
            "ml_away": parse_american(o["moneyline"]["away"]["close"]["odds"]),
            "sp_home_odds": parse_american(home_sp["odds"]),
            "sp_away_odds": parse_american(o["pointSpread"]["away"]["close"]["odds"]),
            "tot_over_odds": parse_american(o["total"]["over"]["close"]["odds"]),
            "tot_under_odds": parse_american(o["total"]["under"]["close"]["odds"]),
        }
        sp_line = _spread_line(home_sp["line"])
        tot_line = float(str(o["total"]["over"]["close"]["line"]).lstrip("oOuU"))
        book = str(o["provider"]["name"])
    except (KeyError, TypeError, ValueError):
        return None
    if any(v is None for v in parsed.values()):
        return None  # a partial set of prices would let one side be priced against nothing
    return BookLines(book=book, sp_home_line=sp_line, tot_line=tot_line, **parsed)  # type: ignore[arg-type]


def parse_slate(payload: Mapping[str, Any]) -> tuple[list[SlateGame], list[str]]:
    """``(games, skipped_event_ids)``; an event with an unresolvable team is skipped, not guessed."""
    games: list[SlateGame] = []
    skipped: list[str] = []
    for ev in payload.get("events", []):
        event_id = str(ev.get("id", ""))
        comp = (ev.get("competitions") or [{}])[0]
        sides = {
            c.get("homeAway"): _tricode((c.get("team") or {}).get("abbreviation"))
            for c in comp.get("competitors", [])
        }
        tip = _parse_time(ev.get("date"))
        if not sides.get("home") or not sides.get("away") or tip is None:
            skipped.append(event_id)
            continue
        games.append(
            SlateGame(
                event_id=event_id,
                home=str(sides["home"]),
                away=str(sides["away"]),
                tip=tip,
                status=str((ev.get("status") or {}).get("type", {}).get("name", "")),
                lines=_lines(comp),
                neutral=_neutral(comp),
            )
        )
    return games, skipped


def team_names(payload: Mapping[str, Any]) -> dict[str, str]:
    """ESPN team display name -> registry tricode, for the teams on this scoreboard."""
    out: dict[str, str] = {}
    for ev in payload.get("events", []):
        for c in (ev.get("competitions") or [{}])[0].get("competitors", []):
            team = c.get("team") or {}
            code = _tricode(team.get("abbreviation"))
            if code and team.get("displayName"):
                out[str(team["displayName"])] = code
    return out


def parse_injuries(
    payload: Mapping[str, Any], names: Mapping[str, str]
) -> dict[str, list[InjuryNote]]:
    """Injury listings keyed by tricode, for the teams in ``names`` only."""
    out: dict[str, list[InjuryNote]] = {}
    for team in payload.get("injuries", []):
        code = names.get(str(team.get("displayName")))
        if code is None:
            continue
        for item in team.get("injuries", []):
            out.setdefault(code, []).append(
                InjuryNote(
                    player=str((item.get("athlete") or {}).get("displayName", "")),
                    status=str(item.get("status", "")),
                    reported=_parse_time(item.get("date")),
                    note=str(item.get("shortComment", "")),
                )
            )
    return out


def injury_feed_usable(payload: Mapping[str, Any]) -> bool:
    """True if the feed lists at least one injury; an empty feed proves nothing.

    ESPN omits teams with no injuries, so one missing team is normal; a feed with no
    listings at all (or no ``injuries`` key) means the data is unavailable, and every
    game must then be treated as injury-unverified rather than injury-free.
    """
    return any(team.get("injuries") for team in payload.get("injuries", []))


def fetch_feeds(day: str, fetch: Fetcher = http_fetch) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(scoreboard, injuries)`` payloads for the slate day (``YYYY-MM-DD``, Eastern)."""
    compact = date.fromisoformat(day).strftime("%Y%m%d")  # ValueError on a malformed day
    return (
        fetch(f"{ESPN_BASE}/scoreboard?dates={compact}"),
        fetch(f"{ESPN_BASE}/injuries"),
    )
