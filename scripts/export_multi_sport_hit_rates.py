"""Export Outlier perfect-hit-rate player props for NFL, NBA, and NCAA football.

A prop qualifies when the player hit it in 100% of the last 5 games AND 100% of the last
10 games (both windows present), with at least ``--min-games`` games behind the L10
window when Outlier supplies the per-game results array.

This is a descriptive feed, not a selection model: at a true 50% hit rate, 5/5 happens
about 3% of the time by chance, and prices already reflect recent form. Nothing here
promotes a row to a board or sizes a stake.

Output: ``<out-dir>/<SPORT>_100_hit_rate.csv`` (default ``data/<LEAGUE>/exports``). This
script never writes to Desktop or Google Drive; ``organize_desktop_today.py`` stages the
CSV into Fresh_Data under the publication hold. A fetch failure raises, leaves the
previous CSV untouched, and makes ``main`` exit 1.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import logging
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from filter_perfect_hit_props import HR_RE, MILESTONE_RE  # noqa: E402

from outlier_scrapers.normalizer import percent_number  # noqa: E402

logger = logging.getLogger("export_hit_rates")

EASTERN = "America/New_York"
SPORTS = ("NFL", "NBA", "NCAA")
LEAGUE_TOKEN = {"NFL": "NFL", "NBA": "NBA", "NCAA": "NCAAFB"}
WEEKLY_SPORTS = frozenset({"NFL", "NCAA"})
WEEKLY_WINDOW_DAYS = 6  # weekly sports: the slate date plus the coming week
TD_RE = re.compile(r"\btouchdowns?\b|\btds?\b", re.I)

FIELDNAMES = [
    "slate_date",
    "player",
    "market_label",
    "side",
    "line",
    "team",
    "matchup",
    "l5_hit_rate",
    "l10_hit_rate",
    "season_hit_rate",
    "sample_n",
    "best_odds",
    "books",
    "orf",
    "game_time",
]


class PropsClient(Protocol):
    def fetch_schedule(self, league: str) -> dict[str, Any]: ...

    def fetch_player_props(self, league: str) -> dict[str, Any]: ...


def resolve_today_str() -> str:
    try:
        return datetime.datetime.now(ZoneInfo(EASTERN)).date().isoformat()
    except ZoneInfoNotFoundError:  # Windows without tzdata
        return datetime.date.today().isoformat()


def default_out_dir(sport: str) -> Path:
    return REPO_ROOT / "data" / LEAGUE_TOKEN[sport.upper()] / "exports"


def build_event_index(
    events: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict], dict[str, str]]:
    """Map eventId -> {matchup, scheduled_time} and teamId -> alias."""
    event_map: dict[str, dict] = {}
    team_alias: dict[str, str] = {}
    for ev in events:
        eid = ev.get("eventId")
        if not eid:
            continue
        home, away = ev.get("home") or {}, ev.get("away") or {}
        home_alias = home.get("alias") or home.get("name") or "HOME"
        away_alias = away.get("alias") or away.get("name") or "AWAY"
        event_map[eid] = {
            "matchup": f"{away_alias} @ {home_alias}",
            "scheduled_time": ev.get("scheduledTime") or "",
        }
        for team, alias in ((home, home_alias), (away, away_alias)):
            if team.get("teamId"):
                team_alias[team["teamId"]] = alias
    return event_map, team_alias


def event_slate_date(scheduled_time: str) -> datetime.date | None:
    """Eastern calendar date of an ISO-8601 start time, or None when unparseable."""
    try:
        dt = datetime.datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(ZoneInfo(EASTERN)).date()


def in_slate_window(sport: str, event_date: datetime.date, slate: datetime.date) -> bool:
    if sport in WEEKLY_SPORTS:
        return slate <= event_date <= slate + datetime.timedelta(days=WEEKLY_WINDOW_DAYS)
    return event_date == slate


def is_excluded_market(market_name: str, side: str) -> bool:
    """Milestone/longshot UNDERs (HR, touchdowns, 'to record') are hygiene drops."""
    if side != "UNDER":
        return False
    return any(rx.search(market_name) for rx in (HR_RE, MILESTONE_RE, TD_RE))


def sample_size(stats: Mapping[str, Any]) -> int | None:
    for key in ("l10Results", "l5Results"):
        results = stats.get(key)
        if isinstance(results, list):
            return len(results)
    return None


def qualifying_rates(
    stats: Mapping[str, Any], min_l5: float, min_l10: float, min_games: int
) -> tuple[float, float, int | None] | None:
    """(l5, l10, sample_n) on a 0-100 scale when the prop qualifies, else None."""
    l5, l10 = percent_number(stats.get("l5")), percent_number(stats.get("l10"))
    if l5 is None or l10 is None or l5 < min_l5 or l10 < min_l10:
        return None
    n = sample_size(stats)
    if n is not None and n < min_games:
        return None
    return l5, l10, n


def _pct(value: float | None) -> str:
    return f"{int(round(value))}%" if value is not None else ""


def build_row(
    item: Mapping[str, Any],
    rates: tuple[float, float, int | None],
    ev_info: Mapping[str, Any],
    team_alias: Mapping[str, str],
    slate_date: str,
) -> dict[str, Any]:
    outcome = item.get("outcome") or {}
    label = outcome.get("marketLabel") or ""
    player, _, market = label.partition(" - ") if " - " in label else ("", "", label)
    books = outcome.get("books") or []
    l5, l10, n = rates
    return {
        "slate_date": slate_date,
        "player": player.strip(),
        "market_label": label,
        "market_name": market.strip(),
        "side": (outcome.get("position") or outcome.get("outcomeLabel") or "").upper(),
        "line": "" if outcome.get("line") is None else str(outcome.get("line")),
        "team": team_alias.get(outcome.get("teamId") or "", ""),
        "matchup": ev_info.get("matchup") or "",
        "l5_hit_rate": _pct(l5),
        "l10_hit_rate": _pct(l10),
        "season_hit_rate": _pct(percent_number((item.get("stats") or {}).get("curSeason"))),
        "sample_n": "" if n is None else str(n),
        "best_odds": str(outcome.get("bestOdds") or ""),
        "books": ";".join(map(str, books)) if isinstance(books, list) else str(books),
        "orf": str(item.get("orf") or ""),
        "game_time": ev_info.get("scheduled_time") or "",
        "_sort": (-l10, -l5),
    }


def _event_for(
    item: Mapping[str, Any], event_map: Mapping[str, dict], sport: str, slate: datetime.date
) -> dict | None:
    """The prop's event when it is on this slate; props with no/unparseable event are dropped."""
    ev_info = event_map.get((item.get("outcome") or {}).get("eventId") or "")
    if not ev_info:
        return None
    ev_date = event_slate_date(ev_info["scheduled_time"])
    if ev_date is None or not in_slate_window(sport, ev_date, slate):
        return None
    return ev_info


def extract_hit_props_for_sport(
    client: PropsClient,
    sport: str,
    target_date: str,
    *,
    min_l5: float = 100.0,
    min_l10: float = 100.0,
    min_games: int = 5,
) -> list[dict[str, Any]]:
    """Qualifying rows for one sport. API errors propagate (never swallowed into [])."""
    sp = sport.upper()
    league = LEAGUE_TOKEN[sp]
    slate = datetime.date.fromisoformat(target_date)
    event_map, team_alias = build_event_index(client.fetch_schedule(league).get("events") or [])
    raw_props = client.fetch_player_props(league).get("props") or []
    logger.info("%s: %d raw props across %d events", sp, len(raw_props), len(event_map))

    rows: dict[str, dict[str, Any]] = {}
    for item in raw_props:
        rates = qualifying_rates(item.get("stats") or {}, min_l5, min_l10, min_games)
        ev_info = _event_for(item, event_map, sp, slate) if rates else None
        if not rates or not ev_info:
            continue
        row = build_row(item, rates, ev_info, team_alias, target_date)
        if is_excluded_market(row["market_name"], row["side"]):
            continue
        key = "|".join(str(row[k]) for k in ("player", "market_name", "side", "line", "matchup"))
        rows.setdefault(key, row)

    ordered = sorted(rows.values(), key=lambda r: (*r["_sort"], r["player"]))
    logger.info("%s: %d perfect-hit props for %s", sp, len(ordered), target_date)
    return ordered


def write_hit_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomic write: a crash mid-write never leaves a truncated CSV in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    logger.info("Wrote %d rows -> %s", len(rows), path)


def export_hit_rates_for_sport(
    client: PropsClient, sport: str, target_date: str, *, out_dir: Path | None = None
) -> Path:
    sp = sport.upper()
    rows = extract_hit_props_for_sport(client, sp, target_date)
    dest = (out_dir or default_out_dir(sp)) / f"{sp}_100_hit_rate.csv"
    write_hit_csv(dest, rows)
    return dest


def make_client() -> PropsClient:
    from outlier_scrapers.api import OutlierApiClient

    return OutlierApiClient()


def _slate_date(value: str) -> str:
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid slate date {value!r}; use YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sport", default="ALL", choices=[*SPORTS, "ALL"], help="Target sport")
    parser.add_argument("--date", default=None, type=_slate_date, help="Slate date YYYY-MM-DD")
    parser.add_argument("--out-dir", type=Path, default=None, help="Override output directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    date_str = args.date or resolve_today_str()
    sports = list(SPORTS) if args.sport == "ALL" else [args.sport]
    try:
        client = make_client()
    except Exception as exc:  # auth/session problems: nothing can be exported
        logger.error("Could not create Outlier client: %s", exc)
        return 1

    failures = 0
    for sp in sports:
        try:
            export_hit_rates_for_sport(client, sp, date_str, out_dir=args.out_dir)
        except Exception as exc:  # report every sport, then fail the run
            failures += 1
            logger.error("%s: hit-rate export failed, previous CSV left untouched: %s", sp, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
