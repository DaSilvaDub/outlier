"""Provider-independent historical closing lines.

The model-vs-market comparison (``track/market_compare.py``) depends only on
:class:`MarketLine`, never on where the numbers came from. Any source that can fill a
``MarketLine`` per ESPN game id can be substituted: ``ingest/sbro.py`` is one, and
:func:`lines_from_csv` reads another from a plain file (columns below), so the evaluation does
not have to change if a provider has to be dropped.

CSV columns: ``game_id, season, date, home, away, ml_home, ml_away, spread_home, total_close,
total_open, spread_open_home, neutral`` (blank cell = unknown; ``date`` is ``YYYY-MM-DD``).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from outlier_nba.market.devig import american_to_prob, devig


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
    neutral: bool = False


def market_probability(ml_home: int, ml_away: int, method: str = "shin") -> float | None:
    """De-vigged probability that the home team wins, or None if a price is unusable."""
    quoted = [american_to_prob(ml_away), american_to_prob(ml_home)]
    if any(q is None for q in quoted):
        return None
    try:
        return float(devig([q for q in quoted if q is not None], method)[1])
    except ValueError:
        return None


def _opt(value: str | None) -> float | None:
    """An unknown number: a blank cell, an absent column, or a cell that is not a number.

    Archives write a missing line as text (SBRO uses ``NL``) and exporters write it as
    ``nan``, so a cell that will not parse -- or parses to something that is not a finite
    number -- says what a blank one says: the number is not known. None of them may take the
    whole file down with them.
    """
    if value is None or not value.strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _price(value: str | None) -> int | None:
    """An American price, or None where the cell is blank, absent or not a number."""
    number = _opt(value)
    return None if number is None else int(number)


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def lines_from_csv(path: Path) -> dict[str, MarketLine]:
    """Closing lines from a CSV in the documented layout; rows with unusable prices are dropped.

    A row is dropped -- never guessed at, and never raised over -- when it has no game id, no
    readable date, or a moneyline that is blank, absent or unusable. The caller decides what an
    empty result means (``backtest --closing-csv`` reports ``closing_lines_unavailable``).
    """
    out: dict[str, MarketLine] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            game_id = str(row.get("game_id") or "").strip()
            day = _day(row.get("date"))
            ml_home, ml_away = _price(row.get("ml_home")), _price(row.get("ml_away"))
            if not game_id or day is None or ml_home is None or ml_away is None:
                continue
            prob = market_probability(ml_home, ml_away)
            if prob is None:
                continue
            out[game_id] = MarketLine(
                game_id=game_id,
                season=str(row.get("season") or ""),
                day=day,
                home=str(row.get("home") or ""),
                away=str(row.get("away") or ""),
                p_home_ml=prob,
                ml_home=ml_home,
                ml_away=ml_away,
                spread_home=_opt(row.get("spread_home")),
                total_close=_opt(row.get("total_close")),
                total_open=_opt(row.get("total_open")),
                spread_open_home=_opt(row.get("spread_open_home")),
                neutral=str(row.get("neutral", "")).strip().lower() in ("1", "true", "yes"),
            )
    return out
