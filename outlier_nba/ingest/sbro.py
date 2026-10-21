"""Historical NBA closing lines from Sportsbook Reviews Online season pages (private research).

Each season page is one HTML table with two rows per game (visitor, then home) holding the
score and the open/close total, the open/close spread and both moneylines. The site's Terms
reserve its content ("may not be used or reproduced without express written consent"), so this
module is for private research only: the owner of this repository approved that use on
2026-10-21, one request per season page, the raw pages are cached locally outside git, and
only aggregate comparison numbers are published. robots.txt allows crawling.

Provenance gap, stated plainly: the page does not say which book or time the "Close" is. It is
used as *a* closing line, not as a verified-timestamp close.

Layout per game: the row that carries the favourite holds the spread (a small number, or ``pk``
for a pick'em); the other row holds the total (100 or more). A game whose two rows do not fit
that pattern is skipped and counted, never repaired by guessing.
"""

from __future__ import annotations

import gzip
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import structlog

from outlier_nba.ingest.espn import USER_AGENT, EspnError, GameRow, Throttle, default_cache_dir
from outlier_nba.market.devig import american_to_prob, devig

logger = structlog.get_logger("outlier_nba.sbro")

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


@dataclass(frozen=True)
class MarketLine:
    game_id: str
    season: str
    day: date
    home: str
    away: str
    p_home_ml: float  # de-vigged moneyline probability of the home team
    ml_home: int
    ml_away: int
    spread_home: float | None  # home handicap at the close (-4.5: home favoured by 4.5)
    total_close: float | None
    total_open: float | None
    spread_open_home: float | None


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


def fetch_seasons(
    seasons: list[str],
    *,
    cache: Path | None = None,
    fetch: Any = http_fetch_text,
    throttle: Throttle | None = None,
) -> int:
    """One request per uncached season page; returns the number of requests made."""
    root = cache or cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    throttle = throttle or Throttle(burst=1, gap=3.0, pause=3.0)
    made = 0
    for season in seasons:
        path = root / f"{season}.html.gz"
        if path.exists():
            continue
        html = fetch(season_url(season))
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


def parse_season(html: str, season: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Raw game dicts for one season page plus counts of what was skipped and why."""
    start_year = int(season[:4])
    cells = [[c.strip() for c in _CELL.findall(r)] for r in _ROW.findall(html)]
    rows = [c for c in cells if len(c) == 13 and c[0] != "Date"]
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    games: list[dict[str, Any]] = []
    i = 0
    while i + 1 < len(rows):
        v, h = rows[i], rows[i + 1]
        if v[2] != "V" or h[2] != "H" or v[0] != h[0]:
            skip("not_a_visitor_home_pair")  # neutral-site ('N') and malformed rows
            i += 1
            continue
        i += 2
        month, dom = int(v[0]) // 100, int(v[0]) % 100
        year = start_year if month >= 8 else start_year + 1
        away, home = TEAM_NAMES.get(v[3]), TEAM_NAMES.get(h[3])
        if away is None or home is None:
            skip("unknown_team")
            continue
        close_v, close_h = _num(v[10]), _num(h[10])
        open_v, open_h = _num(v[9]), _num(h[9])
        ml_v, ml_h = _num(v[11]), _num(h[11])
        score_v, score_h = _num(v[8]), _num(h[8])
        if None in (ml_v, ml_h, score_v, score_h):
            skip("missing_moneyline_or_score")
            continue
        # which row holds the total? the one at or above TOTAL_FLOOR; pick'em spreads read "pk"
        totals = [x for x in (close_v, close_h) if x is not None and x >= TOTAL_FLOOR]
        if len(totals) != 1:
            skip("total_not_identifiable")
            continue
        v_has_total = close_v is not None and close_v >= TOTAL_FLOOR
        spread_close = close_h if v_has_total else close_v
        spread_open = open_h if v_has_total else open_v
        total_close = close_v if v_has_total else close_h
        total_open = open_v if v_has_total else open_h
        if spread_close is None:
            skip("spread_missing")
            continue
        # the small number sits on the favourite's row, so the home handicap is -x there
        home_has_spread = v_has_total
        sign = -1.0 if home_has_spread else 1.0
        games.append(
            {
                "season": season,
                "day": date(year, month, dom),
                "home": home,
                "away": away,
                "ml_home": int(ml_h),  # type: ignore[arg-type]
                "ml_away": int(ml_v),  # type: ignore[arg-type]
                "score_home": int(score_h),  # type: ignore[arg-type]
                "score_away": int(score_v),  # type: ignore[arg-type]
                "spread_home": sign * spread_close if spread_close != 0 else 0.0,
                "spread_open_home": (
                    sign * spread_open if spread_open not in (None, 0.0) else spread_open
                ),
                "total_close": total_close if total_close and total_close >= TOTAL_FLOOR else None,
                "total_open": total_open if total_open and total_open >= TOTAL_FLOOR else None,
            }
        )
    return games, skipped


def market_probability(ml_home: int, ml_away: int, method: str = "shin") -> float | None:
    quoted = [american_to_prob(ml_away), american_to_prob(ml_home)]
    if any(q is None for q in quoted):
        return None
    try:
        return float(devig([q for q in quoted if q is not None], method)[1])
    except ValueError:
        return None


def match_games(
    parsed: list[dict[str, Any]], games: list[GameRow]
) -> tuple[dict[str, MarketLine], dict[str, int]]:
    """Join to ESPN games on (Eastern date, home, away) and require the scores to agree.

    The score check is what makes the join safe: a wrong pairing almost never reproduces both
    final scores, so disagreement is counted and dropped rather than trusted.
    """
    from outlier_nba.ratings.possessions import et_day

    index: dict[tuple[int, str, str], list[GameRow]] = {}
    for g in games:
        index.setdefault((et_day(g), g.home, g.away), []).append(g)
    out: dict[str, MarketLine] = {}
    stats = {"matched": 0, "no_espn_game": 0, "score_mismatch": 0, "no_market_probability": 0}
    for p in parsed:
        hit = None
        for shift in (0, -1, 1):  # a late tip can carry the neighbouring date on either source
            for g in index.get((p["day"].toordinal() + shift, p["home"], p["away"]), []):
                if g.home_pts == p["score_home"] and g.away_pts == p["score_away"]:
                    hit = g
                    break
            if hit:
                break
        if hit is None:
            exists = any(
                (p["day"].toordinal() + s, p["home"], p["away"]) in index for s in (0, -1, 1)
            )
            stats["score_mismatch" if exists else "no_espn_game"] += 1
            continue
        prob = market_probability(p["ml_home"], p["ml_away"])
        if prob is None:
            stats["no_market_probability"] += 1
            continue
        out[hit.game_id] = MarketLine(
            game_id=hit.game_id,
            season=p["season"],
            day=p["day"],
            home=p["home"],
            away=p["away"],
            p_home_ml=prob,
            ml_home=p["ml_home"],
            ml_away=p["ml_away"],
            spread_home=p["spread_home"],
            total_close=p["total_close"],
            total_open=p["total_open"],
            spread_open_home=p["spread_open_home"],
        )
        stats["matched"] += 1
    return out, stats


def load_market(
    seasons: list[str], games: list[GameRow], cache: Path | None = None
) -> tuple[Mapping[str, MarketLine], dict[str, Any]]:
    """Closing lines for every cached season that could be parsed and matched."""
    lines: dict[str, MarketLine] = {}
    report: dict[str, Any] = {}
    for season in seasons:
        html = read_cached(season, cache)
        if html is None:
            report[season] = {"cached": False}
            continue
        parsed, skipped = parse_season(html, season)
        matched, stats = match_games(parsed, [g for g in games if g.season == season])
        lines.update(matched)
        report[season] = {"cached": True, "parsed": len(parsed), "skipped": skipped, **stats}
    return lines, report
