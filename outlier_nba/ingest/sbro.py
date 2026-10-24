"""Historical NBA closing lines from Sportsbook Reviews Online season pages (private research).

Each season page is one HTML table with two rows per game holding the score and the open/close
total, the open/close spread and both moneylines. Ordinary games are a visitor row (``V``) then
a home row (``H``); neutral-site games are two ``N`` rows with no home team stated.

PERMISSION, stated plainly. The site's Terms say its content "may not be used or reproduced
without express written consent". The repository owner decided on 2026-10-21 to use it for
private research; consent from the site was **not** obtained. Those are different things, and
whether this use is acceptable is the owner's decision, not something this code settles. To keep
that decision reversible, the comparison code depends only on
:class:`outlier_nba.market.closing.MarketLine`; ``closing.lines_from_csv`` is a drop-in
replacement source. Raw pages are cached outside git and only aggregates are published.

Provenance gap: the page does not say which book or time the "Close" is. It is used as *a*
closing line, not as a verified-timestamp close.

Layout per game: the row that carries the favourite holds the spread (a small number, or ``pk``
for a pick'em); the other row holds the total (100 or more). A game whose rows do not fit that
pattern is skipped and counted, never repaired by guessing. Neutral-site games are oriented
(home/away) from ESPN's game, matched on teams, date and both final scores.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import structlog

from outlier_nba.ingest.espn import USER_AGENT, EspnError, GameRow, Throttle, default_cache_dir
from outlier_nba.market.closing import MarketLine, market_probability

__all__ = ["MarketLine", "market_probability", "EspnError", "Throttle"]
logger = structlog.get_logger("outlier_nba.sbro")

PARSER_VERSION = "2"
BASE = "https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba-odds-"
TEAM_NAMES = {
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA", "Chicago": "CHI",
    "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "GoldenState": "GSW",
    "Houston": "HOU", "Indiana": "IND", "LAClippers": "LAC", "LALakers": "LAL", "Memphis": "MEM",
    "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN", "NewOrleans": "NOP", "NewYork": "NYK",
    "OklahomaCity": "OKC", "Orlando": "ORL", "Philadelphia": "PHI", "Phoenix": "PHX",
    "Portland": "POR", "Sacramento": "SAC", "SanAntonio": "SAS", "Toronto": "TOR", "Utah": "UTA",
    "Washington": "WAS",
}  # fmt: skip
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TOTAL_FLOOR = 100.0  # no NBA spread is that large; no NBA total is that small
MIN_PAGE_GAMES = 300  # a season page that parses to fewer games is not a usable archive page
DEFAULT_MIN_SEASON_GAMES = 400  # matched games a requested season needs before a run may proceed


class SbroError(EspnError):
    """The archive page or the join is not usable."""


def _num(value: str) -> float | None:
    text = value.strip().lower()
    if text in ("pk", "pick"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None


def season_url(season: str) -> str:
    return BASE + season


def http_fetch_text(url: str, *, timeout: int = 60) -> str:
    if not url.startswith(BASE):
        raise EspnError(f"refusing to fetch outside the SBRO NBA archive: {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - https archive only
        return str(response.read().decode("utf-8", errors="replace"))


def cache_dir() -> Path:
    return default_cache_dir().parent / "sbro_cache"


def _game_date(year: int, mmdd: str) -> date:
    return date(year, int(mmdd) // 100, int(mmdd) % 100)


def _lines(r1: list[str], r2: list[str]) -> tuple[dict[str, Any] | None, str]:
    """Total and spread from a game's two rows; ``(None, reason)`` if they do not fit."""
    close1, close2 = _num(r1[10]), _num(r2[10])
    open1, open2 = _num(r1[9]), _num(r2[9])
    totals = [x for x in (close1, close2) if x is not None and x >= TOTAL_FLOOR]
    if len(totals) != 1:
        return None, "total_not_identifiable"
    first_has_total = close1 is not None and close1 >= TOTAL_FLOOR
    spread = close2 if first_has_total else close1
    if spread is None:
        return None, "spread_missing"
    spread_open = open2 if first_has_total else open1
    total_close = close1 if first_has_total else close2
    total_open = open1 if first_has_total else open2
    return {
        "first_has_total": first_has_total,  # so the *second* row holds the spread
        "spread": spread,
        "spread_open": spread_open,
        "total_close": total_close,
        "total_open": total_open if total_open and total_open >= TOTAL_FLOOR else None,
    }, ""


def parse_season(html: str, season: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Raw game dicts for one season page plus counts of what was skipped and why."""
    cells = [[c.strip() for c in _CELL.findall(r)] for r in _ROW.findall(html)]
    rows = [c for c in cells if len(c) == 13 and c[0] != "Date"]
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    games: list[dict[str, Any]] = []
    # The table is chronological and dates carry no year. The calendar year rolls over exactly
    # once, from the autumn months to January; a month rule would misdate the 2019-20 bubble
    # (August to October 2020) as 2019.
    year, prev_month = int(season[:4]), 0
    if rows and int(rows[0][0]) // 100 <= 6:  # a page that starts after the new year
        year += 1
    i = 0
    while i + 1 < len(rows):
        a, b = rows[i], rows[i + 1]
        kind = (a[2], b[2])
        if kind not in (("V", "H"), ("N", "N")) or a[0] != b[0]:
            skip("not_a_pair")
            i += 1
            continue
        i += 2
        month = int(a[0]) // 100
        if prev_month >= 10 and month <= 6:
            year += 1
        prev_month = month
        neutral = kind == ("N", "N")
        team_a, team_b = TEAM_NAMES.get(a[3]), TEAM_NAMES.get(b[3])
        if team_a is None or team_b is None:
            skip("unknown_team")
            continue
        ml_a, ml_b, sc_a, sc_b = _num(a[11]), _num(b[11]), _num(a[8]), _num(b[8])
        if None in (ml_a, ml_b, sc_a, sc_b):
            skip("missing_moneyline_or_score")
            continue
        found, reason = _lines(a, b)
        if found is None:
            skip(reason)
            continue
        spread, sp_open = found["spread"], found["spread_open"]
        # the small number sits on the favourite's row: the second row unless the first holds the total
        fav_is_second = found["first_has_total"]
        base: dict[str, Any] = {
            "season": season,
            "day": _game_date(year, a[0]),
            "total_close": found["total_close"],
            "total_open": found["total_open"],
            "neutral": neutral,
        }
        if neutral:
            games.append(
                {
                    **base,
                    "a": {"team": team_a, "score": int(sc_a), "ml": int(ml_a)},  # type: ignore[arg-type]
                    "b": {"team": team_b, "score": int(sc_b), "ml": int(ml_b)},  # type: ignore[arg-type]
                    "favourite": "b" if fav_is_second else "a",
                    "spread_abs": spread,
                    "spread_open_abs": sp_open,
                }
            )
            continue
        # visitor first, home second: the home handicap is -x if the home row is the favourite
        sign = -1.0 if fav_is_second else 1.0
        games.append(
            {
                **base,
                "home": team_b,
                "away": team_a,
                "ml_home": int(ml_b),  # type: ignore[arg-type]
                "ml_away": int(ml_a),  # type: ignore[arg-type]
                "score_home": int(sc_b),  # type: ignore[arg-type]
                "score_away": int(sc_a),  # type: ignore[arg-type]
                "spread_home": sign * spread if spread != 0 else 0.0,
                "spread_open_home": sign * sp_open if sp_open not in (None, 0.0) else sp_open,
            }
        )
    return games, skipped


def validate_page(html: str, season: str, min_games: int = MIN_PAGE_GAMES) -> int:
    """Number of parseable games; raises if the page is not a usable archive table."""
    parsed, _ = parse_season(html, season)
    if len(parsed) < min_games:
        raise SbroError(
            f"{season}: page parsed to {len(parsed)} games (need {min_games}); "
            "the markup may have changed or this is not the archive table"
        )
    return len(parsed)


def fetch_seasons(
    seasons: Sequence[str],
    *,
    cache: Path | None = None,
    fetch: Any = http_fetch_text,
    throttle: Throttle | None = None,
    min_games: int = MIN_PAGE_GAMES,
) -> int:
    """One request per uncached season page; a page is cached only if it validates."""
    root = cache or cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    throttle = throttle or Throttle(burst=1, gap=3.0, pause=3.0)
    made = 0
    for season in seasons:
        path = root / f"{season}.html.gz"
        if path.exists():
            continue
        html = fetch(season_url(season))
        validate_page(html, season, min_games)  # raises before anything is cached
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            fh.write(html)
        os.replace(tmp, path)
        made += 1
        throttle.wait()
    return made


def read_cached(season: str, cache: Path | None = None) -> str | None:
    path = (cache or cache_dir()) / f"{season}.html.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, EOFError, ValueError):
        return None


def market_line(
    g: GameRow,
    day: date,
    season: str,
    *,
    ml_home: int,
    ml_away: int,
    spread_home: float | None,
    spread_open_home: float | None,
    total_close: float | None,
    total_open: float | None,
    neutral: bool,
) -> MarketLine | None:
    prob = market_probability(ml_home, ml_away)
    if prob is None:
        return None
    return MarketLine(
        game_id=g.game_id,
        season=season,
        day=day,
        home=g.home,
        away=g.away,
        p_home_ml=prob,
        ml_home=ml_home,
        ml_away=ml_away,
        spread_home=spread_home,
        total_close=total_close,
        total_open=total_open,
        spread_open_home=spread_open_home,
        neutral=neutral,
    )


def _find(
    index: dict[tuple[int, str, str], list[GameRow]],
    day: date,
    teams: tuple[str, str],
    scores: Mapping[str, int],
) -> GameRow | None:
    """ESPN game on (or one day either side of) ``day`` with these teams and both scores."""
    for shift in (0, -1, 1):
        for home, away in (teams, teams[::-1]):
            for g in index.get((day.toordinal() + shift, home, away), []):
                if g.home_pts == scores[g.home] and g.away_pts == scores[g.away]:
                    return g
    return None


def match_games(
    parsed: list[dict[str, Any]], games: list[GameRow]
) -> tuple[dict[str, MarketLine], dict[str, int]]:
    """Join to ESPN games on (Eastern date, teams) and require both final scores to agree.

    Ordinary games must also agree on which team was at home. Neutral-site rows state no home
    team, so they are oriented from ESPN's game. The score check is what makes either join
    safe: a wrong pairing almost never reproduces both final scores, so disagreement is counted
    and dropped rather than trusted.
    """
    from outlier_nba.ratings.possessions import et_day

    index: dict[tuple[int, str, str], list[GameRow]] = {}
    for g in games:
        index.setdefault((et_day(g), g.home, g.away), []).append(g)
    out: dict[str, MarketLine] = {}
    stats = {
        "matched": 0,
        "matched_neutral": 0,
        "no_espn_game": 0,
        "score_mismatch": 0,
        "no_market_probability": 0,
    }
    for p in parsed:
        if p["neutral"]:
            a, b = p["a"], p["b"]
            hit = _find(
                index,
                p["day"],
                (a["team"], b["team"]),
                {a["team"]: a["score"], b["team"]: b["score"]},
            )
            line = None
            if hit is not None:
                side = {a["team"]: a, b["team"]: b}
                fav = a["team"] if p["favourite"] == "a" else b["team"]
                x = p["spread_abs"]
                xo = p["spread_open_abs"]
                sign = -1.0 if fav == hit.home else 1.0
                line = market_line(
                    hit, p["day"], p["season"],
                    ml_home=side[hit.home]["ml"], ml_away=side[hit.away]["ml"],
                    spread_home=sign * x if x != 0 else 0.0,
                    spread_open_home=sign * xo if xo not in (None, 0.0) else xo,
                    total_close=p["total_close"], total_open=p["total_open"], neutral=True,
                )  # fmt: skip
        else:
            hit = None
            for shift in (0, -1, 1):
                for g in index.get((p["day"].toordinal() + shift, p["home"], p["away"]), []):
                    if g.home_pts == p["score_home"] and g.away_pts == p["score_away"]:
                        hit = g
                        break
                if hit:
                    break
            line = None
            if hit is not None:
                line = market_line(
                    hit, p["day"], p["season"],
                    ml_home=p["ml_home"], ml_away=p["ml_away"],
                    spread_home=p["spread_home"], spread_open_home=p["spread_open_home"],
                    total_close=p["total_close"], total_open=p["total_open"], neutral=False,
                )  # fmt: skip
        if hit is None:
            teams = (p["a"]["team"], p["b"]["team"]) if p["neutral"] else (p["home"], p["away"])
            known = any(
                (p["day"].toordinal() + s, h, w) in index
                for s in (0, -1, 1)
                for h, w in (teams, teams[::-1])
            )
            stats["score_mismatch" if known else "no_espn_game"] += 1
            continue
        if line is None:
            stats["no_market_probability"] += 1
            continue
        out[hit.game_id] = line
        stats["matched"] += 1
        stats["matched_neutral"] += int(p["neutral"])
    return out, stats


def load_market(
    seasons: Sequence[str], games: list[GameRow], cache: Path | None = None
) -> tuple[Mapping[str, MarketLine], dict[str, Any]]:
    """Closing lines for every cached season, plus a per-season reproducibility record.

    The record holds the parser version, a hash of the cached page, what was parsed, excluded
    and matched, but none of the page's content, so it can be published.
    """
    lines: dict[str, MarketLine] = {}
    report: dict[str, Any] = {"parser_version": PARSER_VERSION, "seasons": {}}
    for season in seasons:
        html = read_cached(season, cache)
        if html is None:
            report["seasons"][season] = {"cached": False}
            continue
        parsed, skipped = parse_season(html, season)
        matched, stats = match_games(parsed, [g for g in games if g.season == season])
        lines.update(matched)
        report["seasons"][season] = {
            "cached": True,
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "html_chars": len(html),
            "parsed": len(parsed),
            "parsed_neutral": sum(1 for p in parsed if p["neutral"]),
            "skipped": skipped,
            **stats,
        }
    return lines, report


def coverage_problems(
    report: Mapping[str, Any], min_season_games: int = DEFAULT_MIN_SEASON_GAMES
) -> list[str]:
    """Why a market comparison cannot be trusted as requested (empty list means it can)."""
    problems: list[str] = []
    total = 0
    for season, info in report["seasons"].items():
        if not info.get("cached"):
            problems.append(f"{season}: page not cached (run sbro-fetch)")
            continue
        total += int(info["matched"])
        if info["matched"] < min_season_games:
            problems.append(
                f"{season}: only {info['matched']} matched games (need {min_season_games})"
            )
    if total == 0:
        problems.insert(0, "no closing-line games matched in any requested season")
    return problems
