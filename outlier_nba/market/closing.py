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
    if value is None or not value.strip():
        return None
    return float(value)


def lines_from_csv(path: Path) -> dict[str, MarketLine]:
    """Closing lines from a CSV in the documented layout; rows with unusable prices are dropped."""
    out: dict[str, MarketLine] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ml_home, ml_away = int(float(row["ml_home"])), int(float(row["ml_away"]))
            prob = market_probability(ml_home, ml_away)
            if prob is None:
                continue
            out[row["game_id"]] = MarketLine(
                game_id=row["game_id"],
                season=row["season"],
                day=date.fromisoformat(row["date"]),
                home=row["home"],
                away=row["away"],
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
