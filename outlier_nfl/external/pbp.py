"""Play-by-play team EPA adapter (nflverse ``pbp`` release).

Aggregates regular-season pass and run plays (no two-point tries) into one
offense record and one defense record per team-week:
EPA/play, success rate, pass and rush EPA/play, pass rate, and pass rate over
expected (nflverse ``pass_oe``, percentage points).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from outlier_nfl.config import normalize_team

from .common import NFLVERSE_RELEASES, Client, num

PBP_URL = NFLVERSE_RELEASES + "/pbp/play_by_play_{season}.csv.gz"


class _Acc:
    __slots__ = ("plays", "epa", "success", "pass_n", "pass_epa", "rush_n", "rush_epa",
                 "poe_n", "poe")

    def __init__(self) -> None:
        self.plays = self.pass_n = self.rush_n = self.poe_n = 0
        self.epa = self.success = self.pass_epa = self.rush_epa = self.poe = 0.0

    def add(self, row: dict[str, str], epa: float) -> None:
        self.plays += 1
        self.epa += epa
        self.success += num(row.get("success")) or 0.0
        if row.get("play_type") == "pass":
            self.pass_n += 1
            self.pass_epa += epa
        else:
            self.rush_n += 1
            self.rush_epa += epa
        poe = num(row.get("pass_oe"))
        if poe is not None:
            self.poe_n += 1
            self.poe += poe

    def summary(self) -> dict[str, float | int | None]:
        def rate(total: float, n: int) -> float | None:
            return round(total / n, 4) if n else None

        return {
            "plays": self.plays,
            "epa_per_play": rate(self.epa, self.plays),
            "success_rate": rate(self.success, self.plays),
            "pass_epa_per_play": rate(self.pass_epa, self.pass_n),
            "rush_epa_per_play": rate(self.rush_epa, self.rush_n),
            "pass_rate": rate(float(self.pass_n), self.plays),
            "pass_rate_over_expected": rate(self.poe, self.poe_n),
        }


def aggregate(rows: list[dict[str, str]], season: int, through_week: int = 22) -> list[dict[str, Any]]:
    """Offense/defense team-week EPA records from raw nflverse PBP rows."""
    acc: dict[tuple[str, str, int], _Acc] = defaultdict(_Acc)
    for row in rows:
        if str(row.get("season")) != str(season) or row.get("season_type") != "REG":
            continue
        if row.get("play_type") not in ("pass", "run") or row.get("two_point_attempt") == "1":
            continue
        epa = num(row.get("epa"))
        week = int(num(row.get("week")) or 0)
        if epa is None or week < 1 or week > through_week:
            continue
        offense = str(row.get("posteam") or "")
        defense = str(row.get("defteam") or "")
        if not offense or not defense:
            continue
        acc[("offense", offense, week)].add(row, epa)
        acc[("defense", defense, week)].add(row, epa)
    records: list[dict[str, Any]] = []
    for (side, team, week), a in sorted(acc.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
        records.append(
            {
                "source": "pbp",
                "kind": f"team_{side}",
                "season": season,
                "week": week,
                "team": normalize_team(team) or team,
                **a.summary(),
            }
        )
    return records


def fetch(client: Client, season: int, through_week: int = 22) -> dict[str, Any]:
    """Team-week offense and defense EPA records for ``season``."""
    return {"records": aggregate(client.fetch_csv(PBP_URL.format(season=season)), season, through_week)}
