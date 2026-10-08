"""NFL Next Gen Stats adapter (nflverse ``nextgen_stats`` release).

Returns one record per player-week of the regular season for weeks strictly
before ``before_week`` (the run's as-of cutoff, F01); week 0 rows are nflverse
season aggregates and are skipped.
"""

from __future__ import annotations

from typing import Any

from outlier_nfl.config import normalize_team

from .common import NFLVERSE_RELEASES, Client, num

NGS_URL = NFLVERSE_RELEASES + "/nextgen_stats/ngs_{kind}.csv.gz"

# Metrics kept per kind, beyond the shared identity fields.
NGS_FIELDS: dict[str, tuple[str, ...]] = {
    "passing": (
        "attempts", "pass_yards", "avg_time_to_throw", "aggressiveness",
        "avg_intended_air_yards", "avg_air_yards_to_sticks",
        "completion_percentage_above_expectation", "passer_rating",
    ),
    "rushing": (
        "rush_attempts", "rush_yards", "efficiency", "percent_attempts_gte_eight_defenders",
        "avg_time_to_los", "rush_yards_over_expected", "rush_yards_over_expected_per_att",
        "rush_pct_over_expected",
    ),
    "receiving": (
        "targets", "receptions", "yards", "avg_cushion", "avg_separation",
        "avg_intended_air_yards", "percent_share_of_intended_air_yards",
        "avg_yac_above_expectation",
    ),
}


def fetch(client: Client, season: int, kind: str, before_week: int) -> dict[str, Any]:
    """Player-week NGS records for ``kind`` ("passing", "rushing", "receiving").

    Only weeks strictly before ``before_week`` are kept (the run's as-of cutoff).
    """
    if kind not in NGS_FIELDS:
        raise ValueError(f"Unknown NGS kind: {kind!r}")
    records: list[dict[str, Any]] = []
    for row in client.fetch_csv(NGS_URL.format(kind=kind)):
        if str(row.get("season")) != str(season) or row.get("season_type") != "REG":
            continue
        week = int(num(row.get("week")) or 0)
        if week < 1 or week >= before_week:
            continue
        team = str(row.get("team_abbr") or "")
        record: dict[str, Any] = {
            "source": "ngs",
            "kind": kind,
            "season": season,
            "week": week,
            "player": row.get("player_display_name"),
            "gsis_id": row.get("player_gsis_id"),
            "position": row.get("player_position"),
            "team": normalize_team(team) or team,
        }
        for field in NGS_FIELDS[kind]:
            record[field] = num(row.get(field))
        records.append(record)
    return {"records": records}
