"""Independent nflverse gamelog rate projections for ``model_p``.

Builds P(selected side hits the prop line) from prior-week player stats
(``stats_player_week``), independent of Outlier L5/L10 empirical hit rates.
Never copies book ``implied_probability``.

When prior weeks are thin (e.g. Week 2 slate with only Week 1 history), the
rate is Laplace-smoothed. Callers should fall back to shrunk/raw empirical
when this returns None (unsupported market, no matching player, or no prior
games).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from outlier_nfl.boxscore import _token
from outlier_nfl.calibration import DEFAULT_LAPLACE_ALPHA, shrink_hit_rate

MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE = "projection_nflverse_rate"
MODEL_P_SOURCE_EXTERNAL = "external"

# Outlier market → nflverse week-stats column (or synthetic).
MARKET_STAT_COLUMN: dict[str, str | None] = {
    "PASS_YDS": "passing_yards",
    "PASS_TD": "passing_tds",
    "PASS_ATT": "attempts",
    "PASS_COMP": "completions",
    "PASSING_COMPLETIONS": "completions",
    "PASSING_ATTEMPTS": "attempts",
    "RUSH_YDS": "rushing_yards",
    "RUSH_ATT": "carries",
    "RUSH_TD": "rushing_tds",
    "REC_YDS": "receiving_yards",
    "REC": "receptions",
    "REC_TD": "receiving_tds",
    "TARGETS": "targets",
    "SACKS": "def_sacks",
    "SOLO_TACKLES": "def_tackles_solo",
    "ASSISTS": "def_tackle_assists",
    "DEFENSIVE_TACKLES_ASSISTS": None,  # solo+assists composite
    "ANYTIME_TD": None,
    "RUSH_REC_YDS": None,
    "PASS_RUSH_YDS": None,
    "KICK_PTS": None,
}


@dataclass(frozen=True)
class ProjectionResult:
    model_p: float
    n_games: int
    source: str = MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE
    method: str = "gamelog_rate_laplace"


def _f(row: Mapping[str, Any], key: str) -> float:
    raw = row.get(key)
    if raw is None or raw == "":
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def week_stat_value(market: str, row: Mapping[str, Any]) -> float | None:
    """Return the numeric realization for ``market`` from one week-stats row."""
    m = (market or "").upper()
    if m == "ANYTIME_TD":
        return (
            _f(row, "rushing_tds")
            + _f(row, "receiving_tds")
            + _f(row, "passing_tds")
            + _f(row, "special_teams_tds")
        )
    if m == "RUSH_REC_YDS":
        return _f(row, "rushing_yards") + _f(row, "receiving_yards")
    if m == "PASS_RUSH_YDS":
        return _f(row, "passing_yards") + _f(row, "rushing_yards")
    if m == "DEFENSIVE_TACKLES_ASSISTS":
        return _f(row, "def_tackles_solo") + _f(row, "def_tackle_assists")
    if m == "KICK_PTS":
        # nflverse: fg_made * 3 + pat_made (approx; no distance tiers)
        return 3.0 * _f(row, "fg_made") + _f(row, "pat_made")
    col = MARKET_STAT_COLUMN.get(m)
    if col is None:
        return None
    raw = row.get(col)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def load_week_stats_index(
    csv_path: Path | str,
    *,
    before_week: int,
    season_type: str = "REG",
) -> dict[str, list[dict[str, Any]]]:
    """Index prior-week rows by tokenized player display name.

    Only rows with ``week < before_week`` are kept (no leakage into the slate).
    """
    path = Path(csv_path)
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                week = int(float(row.get("week") or 0))
            except (TypeError, ValueError):
                continue
            if week >= before_week or week < 1:
                continue
            st = (row.get("season_type") or season_type).upper()
            if season_type and st != season_type.upper():
                continue
            name = row.get("player_display_name") or row.get("player_name") or ""
            key = _token(name)
            if not key:
                continue
            index[key].append(dict(row))
    return dict(index)


def project_hit_probability(
    *,
    player_name: str,
    market: str,
    line: float,
    position: str,
    week_rows: Sequence[Mapping[str, Any]],
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    min_games: int = 1,
) -> ProjectionResult | None:
    """Laplace-smoothed empirical P(hit) from prior week realizations."""
    if not week_rows or len(week_rows) < min_games:
        return None
    values: list[float] = []
    for row in week_rows:
        value = week_stat_value(market, row)
        if value is None:
            return None
        values.append(value)
    if len(values) < min_games:
        return None
    pos = (position or "").upper()
    if pos in {"OVER", "YES"}:
        hits = sum(1 for value in values if value > float(line))
    elif pos == "UNDER":
        hits = sum(1 for value in values if value < float(line))
    else:
        return None
    n = len(values)
    raw_rate = hits / n
    model_p = shrink_hit_rate(raw_rate, n, alpha=alpha, beta=None)
    return ProjectionResult(model_p=round(model_p, 6), n_games=n)


def attach_projection_model_p_record(
    record: dict[str, Any],
    *,
    week_index: Mapping[str, Sequence[Mapping[str, Any]]],
    overwrite: bool = False,
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    min_games: int = 1,
) -> dict[str, Any]:
    """Fill ``model_p`` from nflverse gamelog rate when missing / overwrite.

    Does nothing (leaves existing) when projection is unavailable — callers
    should then fall back to shrunk/raw empirical. Never copies market implied.
    """
    if record.get("model_p") is None and record.get("p_model") is not None:
        record["model_p"] = record.get("p_model")
    if record.get("model_p") is not None and not overwrite:
        return record
    key = _token(record.get("player_name") or "")
    rows = week_index.get(key) or []
    try:
        line = float(record["line"])
    except (KeyError, TypeError, ValueError):
        return record
    result = project_hit_probability(
        player_name=str(record.get("player_name") or ""),
        market=str(record.get("market") or ""),
        line=line,
        position=str(record.get("position") or ""),
        week_rows=rows,
        alpha=alpha,
        min_games=min_games,
    )
    if result is None:
        return record
    record["model_p"] = result.model_p
    record["model_p_source"] = result.source
    record["model_p_n_games"] = result.n_games
    return record


def attach_model_p_hierarchy_record(
    record: dict[str, Any],
    *,
    week_index: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    overwrite: bool = True,
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    min_games: int = 1,
) -> dict[str, Any]:
    """projection_nflverse_rate → empirical_hit_rate_laplace → empirical_hit_rate.

    ``overwrite=True`` by default so hierarchy replaces a stale raw empirical
    stamp when re-enriching historical packs.
    """
    from outlier_nfl.calibration import attach_empirical_model_p_record

    if overwrite:
        record.pop("model_p", None)
        record.pop("model_p_source", None)
        record.pop("p_model", None)
        record.pop("model_p_n_games", None)

    if week_index is not None:
        attach_projection_model_p_record(
            record,
            week_index=week_index,
            overwrite=True,
            alpha=alpha,
            min_games=min_games,
        )
        if record.get("model_p") is not None and record.get("model_p_source") == (
            MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE
        ):
            return record

    attach_empirical_model_p_record(
        record, overwrite=True, method="laplace", alpha=alpha
    )
    if record.get("model_p") is not None:
        return record
    attach_empirical_model_p_record(record, overwrite=True, method="raw")
    return record
