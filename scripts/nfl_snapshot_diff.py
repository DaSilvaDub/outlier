#!/usr/bin/env python
"""Fixed-slate before/after snapshot harness for the NFL pipeline (repair epic #222).

Every NFL repair phase PR must show what its change does to the pipeline's
published artifacts on the same fixed slates. This script runs those slates
against any checkout (``--repo``), writes a normalized capture of every file
each step leaves behind, and diffs two captures.

Slates
------
A  Offline fixture slate 2026-09-13 (``tests/fixtures/nfl``: KC/BAL 17:00Z,
   SF/LAR 20:25Z, DAL/PHI 00:20Z). Step A1 is a full run at 15:00Z; step A2 is
   a ``--window 1pm`` run at 16:30Z.
B  Live-shaped Week 4 replay, 2026-10-04: the same three fixture events moved
   +21 days and served through an in-memory Outlier client, so the external,
   usage, weather, tape and snapshot code paths run (fixture mode skips them).
   nflverse inputs are frozen synthetic rows "downloaded in November": Weeks
   1-9 (plus one Week 18 NGS row) are present, so anything from Week 4 on is
   future information for this slate. A snapshot row taken at 19:30Z (after
   every step) is pre-seeded. Steps:
   B1 16:00Z all sources; B2 16:10Z nflverse schedule download fails;
   B3 16:20Z no local tape file; B5 16:30Z page 2 of the bulk player props
   fails (HTTP 500 after retries); B6 16:40Z every event-markets request fails;
   B7 16:50Z the schedule labels the slate week 19 (postseason); B8 16:55Z the
   props feed returns an empty list; B9 16:56Z the forecast service returns
   empty hourly arrays; B10 16:57Z it returns only the kickoff hour; B4 18:00Z
   (after the 17:00Z kickoff). Player props are served as two token pages
   through the real ``OutlierNflApiClient`` pagination, so B5 exercises it.

A3 runs ``enrich_close --attach-model-p hierarchy --before-week 4`` (the #202
diff command) over A1's calibrated props plus integer-line copies of every prop,
a passing-only QB anytime-TD row and a row whose stale projection stamp must not
survive an empirical fallback, against ``frozen_week_stats`` (weeks 1-3).
A4 runs ``scripts/nfl_rebuild_card.py`` on A1's card when the checkout has it.
A5/A6 run ``outlier_nfl.settle`` on ``frozen_settle_boxscores`` (pushes, anytime
vs first TD, passing-only QB, a missing stat; A6: NaN line, infinite odds and
probability). A7 grades scorecard signals against rows with blank stats.
A8 runs ``extract_token_from_storage_state`` on placeholder storage states and
records only ``none`` / ``expected`` / ``other`` per case, never a value.
A9 settles quarter/half-scoped predictions against full-game stats.
B11 adds ``market_extras``: raw LONGEST_PASSING_COMPLETION / PASSING_COMPLETIONS
props, a Q1 player prop, a team rushing-yards prop, one-sided alternate
spread/team-total lines and 1ST_QUARTER / H1 game lines.
A10 builds tape roles/inactives from frozen Week-4 depth and injury rows
(morning/evening snapshots, an injury update after as_of, a wrong-season row,
unstamped rows, the real 2026 column layout in live and replay mode, a whole-day cutoff) and checks tape admission (stale, wrong
season, corrupt, missing). A11 indexes rosters and starters (backup with more
quotes, team without props, trade/offseason-move identity, inactive starter).
A12 runs the entity joins on library inputs: a prop whose team is not in its
event, numeric vs string team IDs, an ID-only team object, an outcome with no
eventId, two same-name players in one box score and in the projection index,
and a traded player. A14 joins Odds-API closes to predictions across a different provider ID for
the same game, a different game, and a different week, plus two same-name
players in one game. A15 maps quotes with verified, early, after-kickoff and
unknown timing, and enriches with supplied feeds labeled as snapshot,
synthetic, unlabeled and book close. A16 settles closes at the bet's own line,
at a moved line (87.5 vs 187.5), in fraction units, and with no close line.
A13 runs the schema checks: identical and conflicting
duplicate team-game rows, a missing opponent row, a week-stats file without
``game_id``, and duplicate normalized props. B12 adds ``identity_extras`` to the
replay feed: a foreign-team prop and an identical and a conflicting duplicate of Mahomes' passing-yards OVER.

Each step runs with the wall clock frozen and ``uuid4`` made deterministic, in
a fixed work directory, so two captures of the same code are byte-identical.

Usage
-----
    python scripts/nfl_snapshot_diff.py capture --repo <checkout> --out DIR
    python scripts/nfl_snapshot_diff.py diff BEFORE AFTER [--md OUT.md] [--fail-on-diff]
"""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404 - runs only git and this script, never shell input
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HARNESS_VERSION = 10
SLATES = ("A", "B")
DEFAULT_WORK = Path(tempfile.gettempdir()) / "nfl_snapshot_work"
SHIFT_DAYS = 21
SEASON = 2026

# ---------------------------------------------------------------------------
# Slate definitions
# ---------------------------------------------------------------------------

# Each step's "clock" is both its frozen wall clock (injected as NflPipeline's
# clock) and its as_of: every step is a run made at that instant. Only B4's
# clock is after the 17:00Z KC@BAL kickoff.
STEPS: dict[str, list[dict[str, Any]]] = {
    "A": [
        {"name": "A1_full", "clock": "2026-09-13T15:00:00+00:00", "window": None},
        {"name": "A2_window_1pm", "clock": "2026-09-13T16:30:00+00:00", "window": "1pm"},
        # Phase 3a (#225): pricing math on A1's props, no pipeline run.
        {"name": "A3_enrich_hierarchy_bw4", "clock": "2026-09-13T16:40:00+00:00",
         "kind": "enrich", "before_week": 4},
        {"name": "A4_rebuild_card", "clock": "2026-09-13T16:50:00+00:00", "kind": "rebuild"},
        # Phase 3b (#225): settlement and grading on frozen box scores.
        {"name": "A5_settle_boxscores", "clock": "2026-09-14T12:00:00+00:00", "kind": "settle",
         "case": "main"},
        {"name": "A6_settle_nonfinite", "clock": "2026-09-14T12:05:00+00:00", "kind": "settle",
         "case": "nonfinite"},
        {"name": "A7_scorecard_missing_stats", "clock": "2026-09-14T12:10:00+00:00",
         "kind": "scorecard"},
        # Phase 5a (#227): session token extraction on placeholder storage states.
        {"name": "A8_auth_extraction", "clock": "2026-09-14T12:15:00+00:00", "kind": "auth"},
        {"name": "A9_settle_scope", "clock": "2026-09-14T12:20:00+00:00", "kind": "settle",
         "case": "scope"},
        # Phase 5b (#227): tape/injury admissibility and roster provenance, no pipeline run.
        {"name": "A10_tape_admissibility", "clock": "2026-09-14T12:25:00+00:00", "kind": "tape"},
        {"name": "A11_roster_provenance", "clock": "2026-09-14T12:30:00+00:00", "kind": "roster"},
        {"name": "A12_identity_joins", "clock": "2026-09-14T12:35:00+00:00", "kind": "identity"},
        {"name": "A13_schema_integrity", "clock": "2026-09-14T12:40:00+00:00", "kind": "schema"},
        # Phase 6 (#228): close ownership/timing and same-threshold CLV.
        {"name": "A14_close_ownership", "clock": "2026-09-14T12:45:00+00:00", "kind": "close_join"},
        {"name": "A15_close_provenance", "clock": "2026-09-14T12:50:00+00:00",
         "kind": "close_provenance"},
        {"name": "A16_settle_clv", "clock": "2026-09-14T12:55:00+00:00", "kind": "settle",
         "case": "clv"},
    ],
    "B": [
        {"name": "B1_full", "clock": "2026-10-04T16:00:00+00:00"},
        {"name": "B2_schedule_unavailable", "clock": "2026-10-04T16:10:00+00:00",
         "fail_urls": ("/games.csv",)},
        {"name": "B3_no_local_tape", "clock": "2026-10-04T16:20:00+00:00", "no_tape": True},
        # Phase 2 (#224): a required stage fails or is truncated before kickoff.
        {"name": "B5_props_page2_fails", "clock": "2026-10-04T16:30:00+00:00",
         "fail_props_page": 2},
        {"name": "B6_event_markets_fail", "clock": "2026-10-04T16:40:00+00:00",
         "fail_markets": True},
        {"name": "B7_postseason_week", "clock": "2026-10-04T16:50:00+00:00", "week": 19},
        {"name": "B8_props_feed_empty", "clock": "2026-10-04T16:55:00+00:00", "empty_props": True},
        # Phase 5a (#227): the forecast service answers, but with no usable window.
        {"name": "B9_forecast_empty_hourly", "clock": "2026-10-04T16:56:00+00:00",
         "forecast": "empty"},
        {"name": "B10_forecast_partial_window", "clock": "2026-10-04T16:57:00+00:00",
         "forecast": "partial"},
        # Phase 4a (#226): raw market names, team-prop family, alternates, periods.
        {"name": "B11_market_scope", "clock": "2026-10-04T16:58:00+00:00", "market_extras": True},
        {"name": "B12_identity_extras", "clock": "2026-10-04T16:59:00+00:00",
         "identity_extras": True},
        {"name": "B4_after_kickoff", "clock": "2026-10-04T18:00:00+00:00"},
    ],
}
SLATE_DATE = {"A": "2026-09-13", "B": "2026-10-04"}
SEEDED_SNAPSHOT_TAKEN_AT = "2026-10-04T19:30:00+00:00"

# Eight teams; Week 4 must contain the three fixture games (home first).
TEAMS = ("KC", "BAL", "LAR", "SF", "PHI", "DAL", "BUF", "MIA")
WEEK4_GAMES = (("KC", "BAL", "13:00"), ("LAR", "SF", "16:25"), ("PHI", "DAL", "20:20"),
               ("BUF", "MIA", "13:00"))


def _week_games(week: int) -> list[tuple[str, str, str]]:
    """(home, away, ET kickoff) for one frozen REG week."""
    if week == 4:
        return list(WEEK4_GAMES)
    rot = TEAMS[week % 8:] + TEAMS[: week % 8]
    pairs = [(rot[i], rot[7 - i]) for i in range(4)]
    return [(h, a, "13:00" if i % 2 == 0 else "16:25") for i, (h, a) in enumerate(pairs)]


def _gameday(week: int) -> str:
    return (_dt.date(2026, 9, 13) + _dt.timedelta(days=7 * (week - 1))).isoformat()


def frozen_nflverse(future_rows: bool = True) -> dict[str, list[dict[str, str]]]:
    """Deterministic synthetic nflverse tables keyed by URL fragment.

    ``future_rows=False`` drops every row from Week 4 on (what a truly pregame
    download would have held); used to prove predictions do not depend on them.
    """
    weeks = list(range(1, 10)) if future_rows else [1, 2, 3]

    def s(x: float) -> str:
        return f"{x:.3f}".rstrip("0").rstrip(".")

    schedule: list[dict[str, str]] = []
    for w in range(1, 10):
        for gi, (home, away, gtime) in enumerate(_week_games(w)):
            played = future_rows or w <= 3
            schedule.append({
                "game_id": f"{SEASON}_{w:02d}_{away}_{home}", "season": str(SEASON),
                "game_type": "REG", "week": str(w), "gameday": _gameday(w), "gametime": gtime,
                "weekday": "Sunday", "home_team": home, "away_team": away,
                "home_score": str(20 + (w + gi) % 11) if played else "",
                "away_score": str(17 + (w * 3 + gi) % 13) if played else "",
                "location": "Home", "roof": "outdoors", "surface": "grass",
                "stadium": f"{home} Stadium", "spread_line": s(1.5 + gi),
                "total_line": s(44.5 + gi), "div_game": "0",
            })
    if future_rows:
        schedule.append({
            "game_id": f"{SEASON}_18_BAL_KC", "season": str(SEASON), "game_type": "REG",
            "week": "18", "gameday": "2027-01-03", "gametime": "13:00", "home_team": "KC",
            "away_team": "BAL", "home_score": "", "away_score": "", "location": "Home",
            "roof": "outdoors", "surface": "grass", "stadium": "KC Stadium", "div_game": "0",
        })

    receivers = (("Travis Kelce", "KC", "TE"), ("Zay Flowers", "BAL", "WR"),
                 ("Puka Nacua", "LAR", "WR"), ("George Kittle", "SF", "TE"),
                 ("A.J. Brown", "PHI", "WR"), ("CeeDee Lamb", "DAL", "WR"))
    rushers = (("Derrick Henry", "BAL", "RB"), ("Lamar Jackson", "BAL", "QB"),
               ("Isiah Pacheco", "KC", "RB"), ("Kyren Williams", "LAR", "RB"),
               ("Christian McCaffrey", "SF", "RB"))
    passers = (("Patrick Mahomes", "KC"), ("Lamar Jackson", "BAL"), ("Matthew Stafford", "LAR"),
               ("Brock Purdy", "SF"), ("Jalen Hurts", "PHI"), ("Dak Prescott", "DAL"))
    ngs_rec: list[dict[str, str]] = []
    ngs_rush: list[dict[str, str]] = []
    ngs_pass: list[dict[str, str]] = []
    ngs_weeks = weeks + ([18] if future_rows else [])
    for w in ngs_weeks:
        late = w >= 4
        for i, (name, team, pos) in enumerate(receivers):
            if w == 18 and name != "Travis Kelce":
                continue
            ngs_rec.append({
                "season": str(SEASON), "season_type": "REG", "week": str(w),
                "player_display_name": name, "player_gsis_id": f"00-rec{i}",
                "player_position": pos, "team_abbr": team,
                "targets": str(6 + i + (4 if late else 0)), "receptions": str(4 + i),
                "yards": str(55 + 5 * i + (120 if late else 0)),
                "avg_cushion": s(5.5 + 0.1 * i), "avg_separation": s(2.6 + 0.2 * i + (1.8 if late else 0)),
                "avg_intended_air_yards": s(7.0 + i), "percent_share_of_intended_air_yards": s(20 + i),
                "avg_yac_above_expectation": s(0.5 + 0.1 * i),
            })
        if w == 18:
            continue
        for i, (name, team, pos) in enumerate(rushers):
            ngs_rush.append({
                "season": str(SEASON), "season_type": "REG", "week": str(w),
                "player_display_name": name, "player_gsis_id": f"00-rush{i}",
                "player_position": pos, "team_abbr": team,
                "rush_attempts": str(14 + i + (6 if late else 0)),
                "rush_yards": str(60 + 4 * i + (60 if late else 0)), "efficiency": s(3.8 + 0.1 * i),
                "percent_attempts_gte_eight_defenders": s(20 + i), "avg_time_to_los": s(2.8),
                "rush_yards_over_expected": s(5 + i + (25 if late else 0)),
                "rush_yards_over_expected_per_att": s(0.3 + 0.05 * i + (1.0 if late else 0)),
                "rush_pct_over_expected": s(40 + i),
            })
        for i, (name, team) in enumerate(passers):
            ngs_pass.append({
                "season": str(SEASON), "season_type": "REG", "week": str(w),
                "player_display_name": name, "player_gsis_id": f"00-pass{i}",
                "player_position": "QB", "team_abbr": team,
                "attempts": str(32 + i + (8 if late else 0)),
                "pass_yards": str(240 + 6 * i + (90 if late else 0)),
                "avg_time_to_throw": s(2.7 + 0.05 * i), "aggressiveness": s(15 + i),
                "avg_intended_air_yards": s(7.5 + 0.2 * i), "avg_air_yards_to_sticks": s(-0.5),
                "completion_percentage_above_expectation": s(1.0 + i + (4 if late else 0)),
                "passer_rating": s(92 + i + (15 if late else 0)),
            })

    pbp: list[dict[str, str]] = []
    for w in weeks:
        for gi, (home, away, _t) in enumerate(_week_games(w)):
            for pi in range(8):
                for off, dfn in ((home, away), (away, home)):
                    late = w >= 4
                    epa = ((pi * 7 + gi * 3 + len(off)) % 9 - 4) / 10.0 + (0.35 if late and dfn == "BAL" else 0.0)
                    pbp.append({
                        "season": str(SEASON), "season_type": "REG", "week": str(w),
                        "game_id": f"{SEASON}_{w:02d}_{away}_{home}",
                        "play_type": "pass" if pi % 2 == 0 else "run", "two_point_attempt": "0",
                        "epa": s(epa), "success": "1" if epa > 0 else "0",
                        "posteam": off, "defteam": dfn, "pass_oe": s(pi - 3.5),
                    })

    usage_players = (
        ("00-kelce", "Travis Kelce", "KC", "TE", [60, 70, 70], [230, 230, 230, 230, 230, 225]),
        ("00-pacheco", "Isiah Pacheco", "KC", "RB", [20, 25, 15], [10, 10, 10, 10, 10, 10]),
        ("00-rice", "Rashee Rice", "KC", "WR", [80, 75, 85], [70, 70, 70, 70, 70, 70]),
        ("00-henry", "Derrick Henry", "BAL", "RB", [15, 10, 20], [12, 12, 12, 12, 12, 12]),
        ("00-flowers", "Zay Flowers", "BAL", "WR", [70, 65, 75], [60, 60, 60, 60, 60, 60]),
    )
    player_weeks: list[dict[str, str]] = []
    expected: list[dict[str, str]] = []
    for pid, name, team, pos, early, late_y in usage_players:
        for w in weeks:
            rec = (early + late_y)[w - 1]
            carries = 18 if pos == "RB" else 0
            rush = (95 if name == "Derrick Henry" else 60) if pos == "RB" else 0
            player_weeks.append({
                "season": str(SEASON), "season_type": "REG", "week": str(w),
                "player_id": pid, "player_display_name": name, "player_name": name,
                "team": team, "position": pos, "carries": str(carries),
                "rushing_yards": str(rush + (40 if w >= 4 and pos == "RB" else 0)),
                "targets": str(8 if pos != "RB" else 3), "target_share": s(0.24 if pos != "RB" else 0.08),
                "receiving_yards": str(rec),
            })
            expected.append({
                "player_id": pid, "season": str(SEASON), "week": str(w),
                "rec_yards_gained_exp": s(70.0 if pos != "RB" else 15.0),
                "rush_yards_gained_exp": s(80.0 if pos == "RB" else 0.0),
            })

    return {
        "/games.csv": schedule,
        "nextgen_stats/ngs_receiving.csv": ngs_rec,
        "nextgen_stats/ngs_rushing.csv": ngs_rush,
        "nextgen_stats/ngs_passing.csv": ngs_pass,
        "pbp/play_by_play_": pbp,
        "stats_player/stats_player_week_": player_weeks,
        "ep_weekly_": expected,
    }


TAPE_WRITTEN_AT = _dt.datetime(2026, 10, 4, 14, 0, tzinfo=_dt.UTC)


def frozen_tape(fixtures_dir: Path) -> dict[str, Any]:
    """The fixture tape wrapped in a Week-4 envelope for slate B."""
    raw = json.loads((fixtures_dir / "prior_week_tape.json").read_text(encoding="utf-8"))
    teams = raw.get("teams", raw)
    return {
        "season": SEASON, "week": "1-3", "before": "2026-10-04", "last_n": None,
        "source": "frozen snapshot harness tape", "roles_source": "existing tape",
        "grades_source": None, "inactive": {}, "injury_report_loaded": True,
        "defensive_starters_out": {}, "teams": teams,
    }


PROPS_PAGE_SIZE = 4


def _http_error(message: str, code: int) -> Exception:
    from outlier_nfl.api import OutlierNflApiError

    exc = OutlierNflApiError(message)
    exc.status_code = code  # type: ignore[attr-defined]
    return exc


# Weeks 1-3 of player stats for the fixture players (A3). Mahomes throws TDs but
# never scores one; Henry and Kelce score; Lamar Jackson is absent on purpose.
_WEEK_STATS = (
    ("Patrick Mahomes", "KC", "QB", {"passing_yards": (281, 255, 300), "passing_tds": (2, 1, 3),
                                     "attempts": (36, 33, 38), "completions": (24, 22, 26),
                                     "rushing_yards": (12, 20, 8)}),
    ("Derrick Henry", "BAL", "RB", {"rushing_yards": (88, 64, 102), "carries": (19, 16, 22),
                                    "rushing_tds": (1, 0, 2), "receptions": (1, 2, 1)}),
    ("Travis Kelce", "KC", "TE", {"receiving_yards": (71, 49, 66), "receptions": (6, 4, 5),
                                  "targets": (8, 6, 7), "receiving_tds": (0, 1, 0)}),
)
_WEEK_STAT_COLUMNS = ("passing_yards", "passing_tds", "attempts", "completions", "rushing_yards",
                      "carries", "rushing_tds", "receptions", "targets", "receiving_yards",
                      "receiving_tds", "special_teams_tds")


def frozen_week_stats(path: Path) -> Path:
    """Write the A3 nflverse ``stats_player_week`` CSV (weeks 1-3, REG)."""
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(("season", "season_type", "week", "player_display_name", "team", "position")
                   + _WEEK_STAT_COLUMNS)
        for week in (1, 2, 3):
            for name, team, pos, stats in _WEEK_STATS:
                w.writerow((SEASON, "REG", week, name, team, pos)
                           + tuple(stats.get(c, (0, 0, 0))[week - 1] for c in _WEEK_STAT_COLUMNS))
    return path


def math_inputs(calibrated: dict[str, Any]) -> dict[str, Any]:
    """A1's calibrated props plus the integer-line and provenance cases (A3)."""
    base = [r for r in calibrated.get("records", []) if isinstance(r, dict)]
    rows = [copy.deepcopy(r) for r in base]
    for r in base:
        try:
            line = float(r["line"])
        except (KeyError, TypeError, ValueError):
            continue
        if line != int(line) and line > 1:
            rows.append({**copy.deepcopy(r), "line": float(int(line)), "harness_case": "integer_line"})
            other = "UNDER" if str(r.get("position")).upper() == "OVER" else "OVER"
            rows.append({**copy.deepcopy(r), "line": float(int(line)), "position": other,
                         "harness_case": "integer_line_other_side"})
    mahomes = next((r for r in base if r.get("player_name") == "Patrick Mahomes"), None)
    if mahomes:
        rows.append({**copy.deepcopy(mahomes), "market": "ANYTIME_TD", "position": "OVER",
                     "line": 0.5, "harness_case": "passing_only_qb_anytime_td"})
    lamar = next((r for r in base if r.get("player_name") == "Lamar Jackson"), None)
    if lamar:
        rows.append({**copy.deepcopy(lamar), "model_p": 0.61, "model_p_source":
                     "projection_nflverse_gaussian", "model_p_method": "gamelog_gaussian",
                     "model_p_n_games": 3, "harness_case": "stale_projection_stamp"})
    return {**{k: v for k, v in calibrated.items() if k != "records"}, "count": len(rows),
            "records": rows}


FROZEN_BOX_EVENT = {
    "provider_event_id": "frozen-bal-kc-20260913", "event_date": SLATE_DATE["A"],
    "away": "BAL", "home": "KC", "away_score": 20, "home_score": 27,
    "players": {
        "Patrick Mahomes": {"PASSING:YDS": 268, "PASSING:TD": 2, "RUSHING:YDS": 12, "RUSHING:TD": 0},
        "Travis Kelce": {"RECEIVING:REC": 5, "RECEIVING:YDS": 64, "RECEIVING:TD": 1},
        "Derrick Henry": {"RUSHING:YDS": 71, "RUSHING:CAR": 18, "RUSHING:TD": 1,
                          "RECEIVING:REC": 1, "RECEIVING:YDS": 4, "RECEIVING:TD": 0},
        "Lamar Jackson": {"PASSING:YDS": 210, "PASSING:TD": 1, "RUSHING:YDS": 52, "RUSHING:TD": 0},
    },
}

# (case, player, team, market, position, line, extra fields)
_SETTLE_ROWS = {
    "main": (
        ("integer_over_push", "Travis Kelce", "KC", "REC", "OVER", 5.0, {}),
        ("integer_under_push", "Travis Kelce", "KC", "REC", "UNDER", 5.0, {}),
        ("half_line_win", "Travis Kelce", "KC", "REC", "OVER", 4.5, {}),
        ("yards_under_push", "Derrick Henry", "BAL", "RUSH_YDS", "UNDER", 71.0, {}),
        ("passing_only_qb_anytime_td", "Patrick Mahomes", "KC", "ANYTIME_TD", "OVER", 0.5, {}),
        ("receiver_anytime_td", "Travis Kelce", "KC", "ANYTIME_TD", "OVER", 0.5, {}),
        ("first_td_without_order", "Derrick Henry", "BAL", "FIRST_TD", "OVER", 0.5, {}),
        ("missing_stat", "Lamar Jackson", "BAL", "REC_YDS", "OVER", 10.5, {}),
    ),
    "scope": (
        ("first_quarter_row", "Travis Kelce", "KC", "REC", "OVER", 2.5, {"scope": "first_quarter"}),
        ("first_half_row", "Derrick Henry", "BAL", "RUSH_YDS", "OVER", 40.5, {"scope": "first_half"}),
        ("full_game_row", "Travis Kelce", "KC", "REC", "OVER", 4.5, {"scope": "full_game"}),
        ("no_scope_field", "Derrick Henry", "BAL", "RUSH_YDS", "OVER", 60.5, {}),
    ),
    # A16 (F13): price CLV only at the bet's own line; units mixed on purpose.
    "clv": (
        ("same_line_pct", "Travis Kelce", "KC", "REC_YDS", "OVER", 60.5,
         {"close_line": 60.5, "close_odds": -125, "close_implied": 55.56,
          "close_source": "book_close"}),
        ("moved_line_87_5_vs_187_5", "Derrick Henry", "BAL", "RUSH_YDS", "OVER", 87.5,
         {"close_line": 187.5, "close_odds": -150, "close_implied": 59.77,
          "close_source": "book_close"}),
        ("fraction_units", "Lamar Jackson", "BAL", "PASS_YDS", "OVER", 200.5,
         {"close_line": 200.5, "close_odds": -120, "close_implied": 0.5455,
          "close_source": "book_close"}),
        ("close_line_unknown", "Patrick Mahomes", "KC", "PASS_YDS", "OVER", 250.5,
         {"close_odds": -115, "close_implied": 53.49, "close_source": "book_close"}),
    ),
    "nonfinite": (
        ("nan_line", "Travis Kelce", "KC", "REC_YDS", "OVER", float("nan"), {}),
        ("finite_control", "Travis Kelce", "KC", "REC_YDS", "OVER", 60.5, {}),
        ("infinite_odds", "Derrick Henry", "BAL", "RUSH_YDS", "OVER", 60.5,
         {"best_odds": float("inf")}),
        ("infinite_probability", "Patrick Mahomes", "KC", "PASS_YDS", "OVER", 250.5,
         {"implied_probability": float("inf")}),
    ),
}


def settle_predictions_payload(case: str) -> dict[str, Any]:
    records = []
    for name, player, team, market, pos, line, extra in _SETTLE_ROWS[case]:
        records.append({
            "event_id": "frozen-bal-kc", "event_starts_at": "2026-09-13T13:00:00-04:00",
            "matchup": "BAL @ KC", "team": team, "opponent": "BAL" if team == "KC" else "KC",
            "player_name": player, "market": market, "position": pos, "line": line,
            "best_odds": -110, "implied_probability": 52.38, "model_p": 0.55,
            "confidence_tier": "TIER_1_ANCHOR", "harness_case": name, **extra,
        })
    return {"date": SLATE_DATE["A"], "window": None, "count": len(records), "records": records}


def scorecard_inputs() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """A7: signals against week-3 rows where some stats are blank (not zero)."""
    scripts = [{"prop_signals": [
        {"event_id": "e1", "tag": "T_UNDER", "player_name": "Blank Current", "team": "KC",
         "market": "REC_YDS", "side": "UNDER"},
        {"event_id": "e1", "tag": "T_UNDER", "player_name": "Blank Prior", "team": "KC",
         "market": "REC_YDS", "side": "UNDER"},
        {"event_id": "e1", "tag": "T_TD", "player_name": "Blank TD", "team": "KC",
         "market": "ANYTIME_TD", "side": "UNDER"},
        {"event_id": "e1", "tag": "T_OVER", "player_name": "Full Row", "team": "KC",
         "market": "REC_YDS", "side": "OVER"},
    ]}]
    rows: list[dict[str, str]] = []

    def row(name: str, week: int, yds: str, rtd: str = "0", ctd: str = "0") -> None:
        rows.append({"season_type": "REG", "week": str(week), "team": "KC",
                     "player_display_name": name, "receiving_yards": yds,
                     "rushing_tds": rtd, "receiving_tds": ctd})

    for w, y in ((1, "60"), (2, "70")):
        row("Blank Current", w, y)
        row("Full Row", w, y)
        row("Blank TD", w, "10")
    row("Blank Prior", 1, "60")
    row("Blank Prior", 2, "")
    row("Blank Prior", 3, "50")
    row("Blank Current", 3, "")
    row("Full Row", 3, "80")
    row("Blank TD", 3, "12", rtd="", ctd="")
    return scripts, rows


def _run_settle_step(step: dict[str, Any], work: Path) -> str | None:
    import dataclasses
    import os

    out_dir = work / "data" / "NFL" / "settle"
    out_dir.mkdir(parents=True, exist_ok=True)
    if step["kind"] == "scorecard":
        from outlier_nfl import scorecard

        scripts, rows = scorecard_inputs()
        graded, skipped = scorecard.grade_signals(SLATE_DATE["A"], 3, scripts, rows)
        payload = {"graded": [dataclasses.asdict(g) for g in graded], "skipped": skipped}
        (out_dir / "scorecard_missing_stats.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return None
    from outlier_nfl import settle

    _freeze_clock(step["clock"])  # settle may be imported only now
    case = step["case"]
    (out_dir / f"predictions_{case}.json").write_text(
        json.dumps(settle_predictions_payload(case), indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "boxscores_frozen.json").write_text(
        json.dumps({"source": "frozen harness", "events": [FROZEN_BOX_EVENT]}, indent=2,
                   sort_keys=True), encoding="utf-8")
    cwd = os.getcwd()
    os.chdir(work)  # relative paths keep the report independent of the work dir
    try:
        settle.main(["--predictions", f"data/NFL/settle/predictions_{case}.json",
                     "--boxscores", "data/NFL/settle/boxscores_frozen.json", "--source",
                     "calibrated", "--all-calibrated",
                     "--out-json", f"data/NFL/settle/settle_{case}.json",
                     "--out-md", f"data/NFL/settle/settle_{case}.md"])
    finally:
        os.chdir(cwd)
    return None


# Placeholder credentials only (not real secrets); A8 never writes a token value.
# Assembled at runtime so no JWT literal sits in the source (secret scanners);
# the header and claims are an unsigned placeholder, not a credential.
_PH_JWT = ".".join(base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
                  for part in ({"alg": "none"}, {"sub": "placeholder"})) + ".placeholder-signature"
_PH_OPAQUE = "placeholder-opaque-session-token-0000"  # nosec B105


def auth_cases() -> dict[str, tuple[dict[str, Any], str | None]]:
    """A8 storage states -> (state, expected placeholder or None)."""
    app = "https://app.outlier.bet"
    cookie = {"name": "session", "value": "placeholder-cookie", "domain": "app.outlier.bet"}
    return {
        "empty_storage": ({"cookies": [], "origins": []}, None),
        "cookie_only_long_origin": ({"cookies": [cookie],
                                     "origins": [{"origin": app, "localStorage": []}]}, None),
        "long_unrelated_local_storage": ({"cookies": [cookie], "origins": [{"origin": app,
            "localStorage": [{"name": "lastMessage",
                              "value": "a long unrelated message that is not a credential"}]}]},
            None),
        "nested_auth_entry": ({"cookies": [cookie], "origins": [{"origin": app, "localStorage": [
            {"name": "persist:root",
             "value": json.dumps({"auth": json.dumps({"accessToken": _PH_OPAQUE})})}]}]},
            _PH_OPAQUE),
        "jwt_access_token": ({"cookies": [], "origins": [{"origin": app, "localStorage": [
            {"name": "access_token", "value": _PH_JWT}]}]}, _PH_JWT),
    }


def _run_auth_step(work: Path) -> str | None:
    from outlier_nfl.api import extract_token_from_storage_state

    out: dict[str, str] = {}
    for case, (state, expected) in auth_cases().items():
        tok = extract_token_from_storage_state(state)
        out[case] = "none" if tok is None else ("expected" if tok == expected else "other")
    dest = work / "data" / "NFL" / "math" / "auth_extraction.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    return None


def frozen_forecast(kind: str, url: str) -> dict[str, Any]:
    """B9/B10 Open-Meteo answers: empty hourly arrays, or only the kickoff hour."""
    if kind == "empty":
        return {"hourly": {"time": [], "wind_speed_10m": [], "temperature_2m": []}}
    from urllib.parse import parse_qs, urlparse

    day = parse_qs(urlparse(url).query)["start_date"][0]
    hour = {"2026-10-04": "17:00"}.get(day, "00:00")
    return {"hourly": {"time": [f"{day}T{hour}"], "wind_speed_10m": [8.0],
                       "wind_gusts_10m": [14.0], "temperature_2m": [61.0],
                       "precipitation_probability": [5.0], "precipitation": [0.0]}}


# ---------------------------------------------------------------------------
# A10/A11 (phase 5b): tape/injury admissibility and roster provenance
# ---------------------------------------------------------------------------

A10_AS_OF = _dt.datetime(2026, 10, 4, 14, 0, tzinfo=_dt.UTC)


def a10_depth_rows() -> list[dict[str, str]]:
    """KC Week-4 depth: a morning snapshot and an evening one (after A10_AS_OF)."""
    rows = []
    for dt, qb in (("2026-10-04T12:00:00Z", "Patrick Mahomes"), ("2026-10-04T23:00:00Z", "Evening Backup")):
        for name, pos, slot, rank in ((qb, "QB", "QB", "1"), ("Isiah Pacheco", "RB", "RB", "1"),
                                      ("Travis Kelce", "TE", "TE", "1"), ("Rashee Rice", "WR", "LWR", "1"),
                                      ("Xavier Worthy", "WR", "SWR", "1"), ("Kelce Backup", "TE", "TE", "2")):
            rows.append({"dt": dt, "team": "KC", "player_name": name, "gsis_id": "",
                         "pos_abb": pos, "pos_slot": slot, "pos_rank": rank, "pos_grp": "Offense"})
    return rows


# Real nflverse injuries_2026.csv header (verified 2026-10-10 against the
# release file): no ``date_modified`` column. 2024 had one; 2025/2026 do not.
INJURIES_2026_COLUMNS = (
    "season", "season_type", "game_type", "team", "week", "gsis_id", "position", "full_name",
    "first_name", "last_name", "report_primary_injury", "report_secondary_injury",
    "report_status", "practice_primary_injury", "practice_secondary_injury", "practice_status",
)


def a10_injury_rows(case: str) -> list[dict[str, str]]:
    def row(name: str, modified: str | None, season: str = "2026") -> dict[str, str]:
        r = {"season": season, "season_type": "REG", "week": "4", "team": "KC", "full_name": name,
             "gsis_id": "", "position": "WR", "report_status": "Out"}
        if modified is not None:
            r["date_modified"] = modified
        return r

    base = [row("Travis Kelce", "2026-10-02T20:00:00Z")]
    if case.startswith("future_update"):
        return base + [row("Rashee Rice", "2026-10-04T18:00:00Z")]
    if case == "wrong_season":
        return base + [row("Xavier Worthy", "2025-10-03T20:00:00Z", season="2025")]
    if case.startswith("layout_2026"):
        r = {c: "" for c in INJURIES_2026_COLUMNS}
        r.update({"season": "2026", "season_type": "REG", "game_type": "REG", "team": "KC",
                  "week": "4", "position": "TE", "full_name": "Travis Kelce",
                  "first_name": "Travis", "last_name": "Kelce", "report_status": "Out"})
        return [r]
    if case == "unstamped":
        return [row("Travis Kelce", None)]
    return base


def _run_tape_step(work: Path) -> str | None:
    import outlier_nfl.tape_nflverse as tn
    from outlier_nfl.matchup import tape_inadmissible_reason
    from outlier_nfl.run_context import RunContext

    games = frozen_nflverse()["/games.csv"]
    real_fetch = tn.fetch_csv
    out: dict[str, Any] = {"roles": {}, "admission": {}}
    # (case, as_of, run mode passed when _auto_roles accepts one)
    # The step runs with datetime frozen (a subclass swapped into each module), so
    # build as_of from the module's class: a plain datetime would fail its
    # isinstance check and silently take the whole-day branch of _after_as_of.
    as_of_now = tn.datetime(2026, 10, 4, 14, 0, tzinfo=_dt.UTC)
    evening = tn.datetime(2026, 10, 5, 0, 0, tzinfo=_dt.UTC)
    cases = (("point_in_time", as_of_now, "live"), ("future_update", as_of_now, "replay"),
             ("wrong_season", as_of_now, "live"), ("unstamped", as_of_now, "replay"),
             ("layout_2026_live", as_of_now, "live"),
             ("layout_2026_replay", as_of_now, "replay"),
             ("evening_cutoff", evening, "live"),
             ("whole_day_no_as_of", None, None))
    import inspect

    takes_mode = "run_mode" in inspect.signature(tn._auto_roles).parameters
    try:
        for case, as_of, mode in cases:
            inj = a10_injury_rows(case)
            tn.fetch_csv = (  # type: ignore[assignment]
                lambda url, timeout=60.0, _i=inj: copy.deepcopy(_i) if "injuries" in url
                else a10_depth_rows())
            extra = {"run_mode": mode} if takes_mode else {}
            res = tn._auto_roles(SEASON, _dt.date(2026, 10, 4), games, as_of, **extra)
            roles, injuries = res[0], res[1]
            out["roles"][case] = {
                "qb": roles.get("KC", {}).get("qb"), "te": roles.get("KC", {}).get("te"),
                "wr_deep": roles.get("KC", {}).get("wr_deep"),
                "inactive": None if injuries is None
                else sorted(p["name"] for p in injuries.get("KC", [])),
                "injury_report_status": res[3] if len(res) > 3 else None,
            }
    finally:
        tn.fetch_csv = real_fetch  # type: ignore[assignment]
    ctx = RunContext(slate_date="2026-10-04", window=None, season=SEASON,
                     as_of_utc=A10_AS_OF, mode="live", first_kickoff_utc=None)
    tape_dir = work / "data" / "NFL" / "a10"
    tape_dir.mkdir(parents=True, exist_ok=True)
    envelopes = {
        "fresh": {"season": SEASON, "before": "2026-10-04"},
        "one_week_old": {"season": SEASON, "before": "2026-09-27"},
        "stale_two_weeks": {"season": SEASON, "before": "2026-09-20"},
        "wrong_season": {"season": SEASON - 1, "before": "2025-12-28"},
        "no_before": {"season": SEASON},
    }
    for name, env in envelopes.items():
        path = tape_dir / f"{name}.json"
        path.write_text(json.dumps(env), encoding="utf-8")
        out["admission"][name] = tape_inadmissible_reason(env, path, ctx) or "admitted"
    dest = work / "data" / "NFL" / "math" / "tape_admissibility.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    return None


def _a11_prop(team: str, name: str, market: str, books: int) -> dict[str, Any]:
    return {"team": team, "player_name": name, "market": market,
            "books": [{"book": f"B{i}", "odds": -110} for i in range(books)]}


def _run_roster_step(work: Path) -> str | None:
    import inspect

    from outlier_nfl import matchup, roster

    props = [_a11_prop("KC", "Patrick Mahomes", "PASS_YDS", 2),
             _a11_prop("KC", "Backup Passer", "PASS_YDS", 5),
             _a11_prop("KC", "Backup Passer", "PASS_TD", 5),
             _a11_prop("KC", "Travis Kelce", "REC_YDS", 4)]
    params = inspect.signature(roster.build_team_roster_index).parameters
    kwargs: dict[str, Any] = {}
    if "tape_roles" in params:
        kwargs["tape_roles"] = {"KC": {"qb": "Patrick Mahomes"}}
    idx = roster.build_team_roster_index(props, **kwargs)
    bare = roster.build_team_roster_index(props)

    def qb(entry: dict[str, Any] | None) -> dict[str, Any]:
        e = entry or {}
        return {k: e.get(k) for k in ("starting_qb", "starting_qb_status", "starting_qb_source")}

    out = {
        "teams_count_bare": len(bare),
        "kc_bare": qb(bare.get("KC")),
        "kc_with_tape_roles": qb(idx.get("KC")),
        "ind_without_props": qb(bare.get("IND")),
        # A supplied index says LAR; the 2026 offseason-move table says IND.
        "trade_supplied_identity": roster.verify_player_team_attribution(
            "Daniel Jones", "LAR", {"LAR": {"starting_qb": "Daniel Jones", "key_rbs": [],
                                            "key_pass_catchers": []}}, position="QB"),
        "team_missing_from_index_qb": roster.verify_player_team_attribution(
            "Patrick Mahomes", "KC", {}, position="QB"),
        # No tape role: does a static 2026 chart fill the gap?
        "rb1_without_tape_role": matchup._role_player({}, "KC", "rb1", ()),
        "qb_without_tape_role_starter_out": matchup._role_player(
            {}, "KC", "qb", ("patrick mahomes",)),
    }
    dest = work / "data" / "NFL" / "math" / "roster_provenance.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    return None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


def _try(fn: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # recorded as the outcome of the case
        return {"error": type(exc).__name__, "message": str(exc)[:160]}


A12_GAMES = [{"game_id": "2026_01_BUF_KC", "season": "2026", "week": "1", "gameday": "2026-09-13",
              "game_type": "REG", "away_team": "BUF", "home_team": "KC", "away_score": "20",
              "home_score": "27"}]


def _a12_stat(name: str, pid: str, team: str, yards: str, week: str = "1",
              gid: str = "2026_01_BUF_KC") -> dict[str, str]:
    return {"game_id": gid, "season": "2026", "week": week, "season_type": "REG",
            "player_id": pid, "player_display_name": name, "player_name": name,
            "team": team, "recent_team": team, "position": "WR", "receiving_yards": yards,
            "receptions": "4"}


def _run_identity_step(work: Path) -> str | None:
    from outlier_nfl import boxscore, boxscore_nflverse, projection
    from outlier_nfl.normalizer import build_schedule_index
    from outlier_nfl.props import extract_player_props

    sched = {"events": [{"eventId": "ev-buf-kc", "id": "ev-buf-kc",
                         "scheduledTime": "2026-09-13T17:00:00+00:00",
                         "home": {"teamId": 12, "alias": "KC", "name": "Kansas City Chiefs"},
                         "away": {"teamId": 2, "alias": "BUF", "name": "Buffalo Bills"}}]}
    index = build_schedule_index(sched)

    def prop(**over: Any) -> dict[str, Any]:
        o = {"eventId": "ev-buf-kc", "marketId": "m1", "outcomeId": "o1", "proposition":
             "RECEIVING_YARDS", "position": "OVER", "line": 60.5, "playerName": "Test Player",
             "playerId": "p1", "bestOdds": -110}
        o.update(over)
        return {"outcome": {k: v for k, v in o.items() if v is not None}}

    def teams(rows: list[Any]) -> Any:
        return [{"event_id": r.event_id, "team": r.team, "opponent": r.opponent} for r in rows]

    props_cases = {
        "foreign_team_mia": prop(teamId="MIA"),
        "string_team_id_for_numeric": prop(teamId="12"),
        "numeric_team_id": prop(teamId=2),
        "id_only_team_object": prop(teamId=None, team={"teamId": 12}),
        "no_event_id_outcome_id_only": prop(eventId=None, id="ev-buf-kc"),
    }
    out: dict[str, Any] = {"props": {k: _try(lambda v=v: teams(extract_player_props(
        {"props": [v]}, index))) for k, v in props_cases.items()}}

    d = work / "data" / "NFL" / "math" / "a12"
    games = _write_csv(d / "games.csv", A12_GAMES)
    same_name = [_a12_stat("Josh Allen", "00-A1", "BUF", "80"),
                 _a12_stat("Josh Allen", "00-A2", "KC", "30")]
    stats = _write_csv(d / "same_name.csv", same_name)

    def box() -> Any:
        ev = boxscore_nflverse.load_nflverse_events(season=2026, week=1, stats_csv=stats,
                                                    games_csv=games, cache_dir=d)
        res = boxscore.resolve_player_stats(ev[0], "Josh Allen")
        return {"player_keys": sorted(ev[0].players),
                "resolve_josh_allen": {"stats": res[0], "reason": res[1]}}

    out["boxscore_same_name"] = _try(box)

    proj_rows = same_name + [_a12_stat("Josh Allen", "00-A1", "BUF", "70", week="2",
                                       gid="2026_02_BUF_MIA"),
                             _a12_stat("Traded Guy", "00-T1", "NYJ", "40", week="1",
                                       gid="2026_01_NYJ_NE"),
                             _a12_stat("Traded Guy", "00-T1", "KC", "55", week="2",
                                       gid="2026_02_KC_LV")]
    proj = _write_csv(d / "proj.csv", proj_rows)

    def proj_index() -> Any:
        idx = projection.load_week_stats_index(proj, before_week=3)
        res: dict[str, Any] = {}
        for name, team in (("Josh Allen", "BUF"), ("Josh Allen", "KC"), ("Traded Guy", "KC")):
            rec = {"player_name": name, "team": team, "market": "REC_YDS", "line": 50.5,
                   "position": "OVER"}
            hit = _try(lambda rec=rec: projection.attach_projection_model_p_record(
                dict(rec), week_index=idx, overwrite=True))
            res[f"{name}|{team}"] = (
                {k: v for k, v in sorted(hit.items()) if k not in rec}
                if isinstance(hit, dict) and "error" not in hit else hit)
        return {"rows_per_key": {k: len(v) for k, v in sorted(idx.items())},
                "attach": res}

    out["projection_index"] = _try(proj_index)
    dest = work / "data" / "NFL" / "math" / "identity_joins.json"
    dest.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return None


def _run_schema_step(work: Path) -> str | None:
    from outlier_nfl import boxscore_nflverse, schema, tape_nflverse

    def team_row(team: str, opp: str, rush: str, gid: str = "2026_01_BUF_KC") -> dict[str, str]:
        return {"game_id": gid, "season": "2026", "week": "1", "season_type": "REG",
                "team": team, "opponent_team": opp, "rushing_yards": rush,
                "passing_yards": "200", "def_sacks": "2", "attempts": "30",
                "sacks_suffered": "2"}

    def tape(rows: list[dict[str, str]]) -> Any:
        pg = tape_nflverse.build_per_game(rows, A12_GAMES, 2026)
        return {t: [(g["opponent"], g["rush_yards"], g["opp_rush_yards_allowed"]) for g in v]
                for t, v in sorted(pg.items())}

    kc, buf = team_row("KC", "BUF", "120"), team_row("BUF", "KC", "90")
    out: dict[str, Any] = {"tape": {
        "clean": _try(lambda: tape([kc, buf])),
        "identical_duplicate_kc": _try(lambda: tape([kc, dict(kc), buf])),
        "conflicting_duplicate_kc": _try(lambda: tape([kc, team_row("KC", "BUF", "150"), buf])),
        "missing_opponent_row": _try(lambda: tape([kc])),
    }}
    d = work / "data" / "NFL" / "math" / "a13"
    games = _write_csv(d / "games.csv", A12_GAMES)
    no_gid = [{k: v for k, v in _a12_stat("Travis Kelce", "00-K", "KC", "70").items()
               if k != "game_id"}]
    dup = [_a12_stat("Travis Kelce", "00-K", "KC", "70")] * 2
    conflict = [_a12_stat("Travis Kelce", "00-K", "KC", "70"),
                _a12_stat("Travis Kelce", "00-K", "KC", "95")]

    def box(rows: list[dict[str, str]], name: str) -> Any:
        ev = boxscore_nflverse.load_nflverse_events(
            season=2026, week=1, stats_csv=_write_csv(d / name, rows), games_csv=games,
            cache_dir=d)
        return [{"id": e.provider_event_id, "players": e.players} for e in ev]

    out["boxscore"] = {
        "stats_without_game_id": _try(lambda: box(no_gid, "nogid.csv")),
        "identical_duplicate_player": _try(lambda: box(dup, "dup.csv")),
        "conflicting_duplicate_player": _try(lambda: box(conflict, "conflict.csv")),
    }

    def nprop(odds: int, team: str = "KC", matchup: str = "BUF @ KC") -> dict[str, Any]:
        return {"event_id": "ev1", "player_name": "Travis Kelce", "player_id": "p-k",
                "market": "REC_YDS", "line": 60.5, "position": "OVER", "team": team,
                "opponent": "BUF", "matchup": matchup, "implied_probability": 52.4,
                "books": [{"book": "DRAFTKINGS", "odds": odds}], "best_odds": odds}

    out["normalized"] = {
        "identical_duplicate": schema.validate_normalized_dataset(
            [nprop(-110), nprop(-110)], dataset_type="props"),
        "conflicting_duplicate": schema.validate_normalized_dataset(
            [nprop(-110), nprop(120)], dataset_type="props"),
        "team_not_in_matchup": schema.validate_normalized_dataset(
            [nprop(-110, team="MIA")], dataset_type="props"),
    }
    dest = work / "data" / "NFL" / "math" / "schema_integrity.json"
    dest.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return None


KICKOFF_A14 = "2026-09-13T17:00:00Z"


def _odds_event(home: str, away: str, commence: str, books: list[dict[str, Any]],
                eid: str = "oddsapi-bal-kc") -> dict[str, Any]:
    return {"id": eid, "commence_time": commence, "home_team": home, "away_team": away,
            "bookmakers": books}


def _odds_book(key: str, price: int, last_update: str | None, player: str = "Patrick Mahomes",
               point: float = 250.5, market: str = "player_pass_yds") -> dict[str, Any]:
    mkt: dict[str, Any] = {"key": market, "outcomes": [
        {"name": "Over", "description": player, "price": price, "point": point}]}
    book: dict[str, Any] = {"key": key, "title": key, "markets": [mkt]}
    if last_update is not None:
        mkt["last_update"] = last_update
        book["last_update"] = last_update
    return book


def _a14_pred(matchup: str, starts: str, event_id: str, team: str = "KC",
              player_id: str = "") -> dict[str, Any]:
    return {"player_name": "Patrick Mahomes", "market": "PASS_YDS", "line": 250.5,
            "position": "OVER", "matchup": matchup, "event_id": event_id,
            "event_starts_at": starts, "team": team, "player_id": player_id}


def _run_close_join_step(work: Path) -> str | None:
    """A14 (F12): close rows must not cross a known game/date owner."""
    from outlier_nfl import enrich_close, fetch_odds_close

    ev = _odds_event("Kansas City Chiefs", "Baltimore Ravens", KICKOFF_A14,
                     [_odds_book("draftkings", -110, "2026-09-13T16:55:00Z")])
    preds = {
        "different_provider_ids_same_game": _a14_pred("BAL @ KC", "2026-09-13T13:00:00-04:00",
                                                      "outlier-bal-kc"),
        "single_incompatible_game": _a14_pred("LAR @ SF", "2026-09-13T16:05:00-04:00",
                                              "outlier-lar-sf", team="SF"),
        "same_player_other_week": _a14_pred("BAL @ KC", "2026-09-20T13:00:00-04:00",
                                            "outlier-bal-kc-w2"),
    }
    out: dict[str, Any] = {}
    for case, pred in preds.items():
        def run(pred: dict[str, Any] = pred) -> Any:
            aligned = fetch_odds_close.build_close_feed_from_event_odds(
                ev, predictions={"records": [pred]})
            row = aligned[0]
            idx = enrich_close.index_book_close_records(
                fetch_odds_close.build_close_feed_from_event_odds(ev))
            hit = enrich_close.lookup_book_close_row(idx, pred)
            return {"aligned": row.get("aligned_to_predictions"),
                    "aligned_event_id": row.get("event_id"),
                    "alignment_skipped": row.get("alignment_skipped"),
                    "lookup_close_odds": None if hit is None else hit.get("close_odds")}
        out[case] = _try(run)

    def same_name() -> Any:
        a = _a14_pred("BAL @ KC", "2026-09-13T13:00:00-04:00", "outlier-bal-kc",
                      player_id="p-1")
        b = {**a, "team": "BAL", "player_id": "p-2"}
        aligned = fetch_odds_close.build_close_feed_from_event_odds(
            ev, predictions={"records": [a, b]})
        return {"aligned": aligned[0].get("aligned_to_predictions"),
                "alignment_skipped": aligned[0].get("alignment_skipped")}

    out["two_same_name_players_one_game"] = _try(same_name)
    dest = work / "data" / "NFL" / "math" / "close_ownership.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return None


def _run_close_provenance_step(work: Path) -> str | None:
    """A15 (F13): only a verified pre-kickoff quote is ``book_close``."""
    from outlier_nfl import close_feed, enrich_close, fetch_odds_close

    timing = {
        "verified_5min_before": "2026-09-13T16:55:00Z",
        "early_quote_6h_before": "2026-09-13T11:00:00Z",
        "after_kickoff_quote": "2026-09-13T17:10:00Z",
        "unknown_timing": None,
    }
    out: dict[str, Any] = {"mapper": {}}
    for case, stamp in timing.items():
        rows = fetch_odds_close.map_event_odds_to_close_records(_odds_event(
            "Kansas City Chiefs", "Baltimore Ravens", KICKOFF_A14,
            [_odds_book("draftkings", -110, stamp)]))
        out["mapper"][case] = [{k: r.get(k) for k in ("close_source", "close_status",
                                                     "close_odds", "quote_time")} for r in rows]
    mixed = fetch_odds_close.map_event_odds_to_close_records(_odds_event(
        "Kansas City Chiefs", "Baltimore Ravens", KICKOFF_A14,
        [_odds_book("draftkings", -110, "2026-09-13T16:55:00Z"),
         _odds_book("fanduel", 150, "2026-09-13T17:10:00Z")]))
    out["mapper"]["verified_book_vs_better_live_price"] = [
        {k: r.get(k) for k in ("close_source", "close_status", "close_odds", "bookmaker")}
        for r in mixed]

    pred = _a14_pred("BAL @ KC", "2026-09-13T13:00:00-04:00", "outlier-bal-kc")
    pred.update({"best_odds": -110, "implied_probability": 52.38})
    feeds = {
        "supplied_snapshot_source": "pregame_snapshot_best_odds",
        "supplied_synthetic_source": "synthetic_moved_close",
        "supplied_without_source": None,
        "supplied_book_close": "book_close",
    }
    out["enrich_book_close"] = {}
    for case, src in feeds.items():
        feed_row = {k: pred[k] for k in ("player_name", "market", "line", "position", "matchup",
                                         "event_id")}
        feed_row.update({"close_line": 250.5, "close_odds": -125, "close_implied": 55.56})
        if src is not None:
            feed_row["close_source"] = src
        enriched = enrich_close.enrich_prediction_payload(
            {"records": [dict(pred)]}, mode="book_close", attach_model_p="pass",
            book_close_index=enrich_close.index_book_close_records([feed_row]))
        rec = enriched["records"][0]
        out["enrich_book_close"][case] = {k: rec.get(k) for k in (
            "close_source", "close_odds", "close_skip_reason")}
    d = work / "data" / "NFL" / "math" / "a15"
    d.mkdir(parents=True, exist_ok=True)
    (d / "preds.json").write_text(json.dumps({"records": [pred]}), encoding="utf-8")
    out["synthetic_moved_feed_source"] = sorted({
        str(r.get("close_source")) for r in close_feed.snapshot_rows_to_moved_close_feed(
            d / "preds.json")})
    dest = work / "data" / "NFL" / "math" / "close_provenance.json"
    dest.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return None


def _run_math_step(step: dict[str, Any], work: Path, repo: Path) -> str | None:
    """A3/A4: run the pricing-math tools on A1's outputs. Returns an error or None."""
    norm = work / "data" / "NFL" / "normalized"
    if step["kind"] == "auth":
        return _run_auth_step(work)
    if step["kind"] == "tape":
        return _run_tape_step(work)
    if step["kind"] == "roster":
        return _run_roster_step(work)
    if step["kind"] == "identity":
        return _run_identity_step(work)
    if step["kind"] == "schema":
        return _run_schema_step(work)
    if step["kind"] == "close_join":
        return _run_close_join_step(work)
    if step["kind"] == "close_provenance":
        return _run_close_provenance_step(work)
    if step["kind"] in ("settle", "scorecard"):
        return _run_settle_step(step, work)
    out_dir = work / "data" / "NFL" / "math"
    out_dir.mkdir(parents=True, exist_ok=True)
    if step["kind"] == "enrich":
        from outlier_nfl import enrich_close

        cal = json.loads((norm / f"nfl_calibrated_props_{SLATE_DATE['A']}.json").read_text("utf-8"))
        inputs = out_dir / "math_inputs.json"
        inputs.write_text(json.dumps(math_inputs(cal), indent=2, sort_keys=True), encoding="utf-8")
        stats = frozen_week_stats(out_dir / "stats_player_week_frozen.csv")
        enrich_close.main([
            "--predictions", str(inputs), "--out", str(out_dir / "hierarchy_bw4.json"),
            "--attach-model-p", "hierarchy", "--nflverse-week-stats", str(stats),
            "--before-week", str(step["before_week"]),
        ])
        return None
    script = repo / "scripts" / "nfl_rebuild_card.py"
    if not script.exists():
        return "rebuild script not present in this checkout"
    import runpy

    argv = sys.argv
    sys.argv = [str(script), "--card", str(norm / f"nfl_best_bets_{SLATE_DATE['A']}.json"),
                "--version", "phase3a"]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (0, None):
            return f"rebuild exited {exc.code}"
    finally:
        sys.argv = argv
    return None


_EVENT = "nfl-event-2026-w1-kc-bal"


def _odds(*american: int) -> list[dict[str, Any]]:
    books = ("DRAFTKINGS", "FANDUEL")
    return [{"book": b, "american": a, "decimal": round(1 + (a / 100 if a > 0 else 100 / -a), 2)}
            for b, a in zip(books, american)]


def _market(mid: str, mtype: str, prop: str, period: str | None, outcomes: list[dict[str, Any]],
            label: str = "") -> dict[str, Any]:
    return {"marketId": mid, "eventId": _EVENT, "marketType": mtype, "proposition": prop,
            "label": label or prop.title(), "periodLabel": period, "includeOvertime": False,
            "books": ["DRAFTKINGS", "FANDUEL"], "outcomes": outcomes}


def _extra_markets() -> list[dict[str, Any]]:
    """B9 game lines: team rushing yards, one-sided alternates, period lines."""
    return [
        _market("m-kc-team-rush", "TEAM_PROP", "RUSHING_YARDS", None, [
            {"outcomeId": "o-kc-rush-o", "position": "OVER", "line": 120.5, "teamId": "kc-chiefs",
             "odds": _odds(-110, -112)},
            {"outcomeId": "o-kc-rush-u", "position": "UNDER", "line": 120.5, "teamId": "kc-chiefs",
             "odds": _odds(-110, -108)}]),
        # One-sided alternates: no opposite side is quoted at these lines.
        _market("m-spread-alt", "GAMELINE", "SPREAD", None, [
            {"outcomeId": "o-spread-bal-alt", "position": "AWAY", "line": 10.5,
             "odds": _odds(-400, -380)}]),
        _market("m-kc-tt-alt", "TEAM_PROP", "POINTS", None, [
            {"outcomeId": "o-kc-tt-alt-o", "position": "OVER", "line": 34.5, "teamId": "kc-chiefs",
             "odds": _odds(400, 380)}]),
        _market("m-spread-q1", "GAMELINE", "SPREAD", "1ST_QUARTER", [
            {"outcomeId": "o-q1-kc", "position": "HOME", "line": -0.5, "odds": _odds(-120, -118)},
            {"outcomeId": "o-q1-bal", "position": "AWAY", "line": 0.5, "odds": _odds(100, 100)}]),
        _market("m-total-h1", "GAMELINE", "TOTAL", "H1", [
            {"outcomeId": "o-h1-o", "position": "OVER", "line": 23.5, "odds": _odds(-110, -110)},
            {"outcomeId": "o-h1-u", "position": "UNDER", "line": 23.5, "odds": _odds(-110, -110)}]),
    ]


def _extra_props(props: dict[str, Any]) -> list[dict[str, Any]]:
    """B9 player props cloned from Mahomes' passing-yards row."""
    base = next(p for p in props["props"] if p["outcome"]["proposition"] == "PASSING_YARDS"
                and p["outcome"]["position"] == "OVER")
    out = []
    for suffix, prop, line, period in (("lpc", "LONGEST_PASSING_COMPLETION", 38.5, None),
                                       ("pcomp", "PASSING_COMPLETIONS", 22.5, None),
                                       ("q1py", "PASSING_YARDS", 70.5, "Q1")):
        for pos in ("OVER", "UNDER"):
            row = copy.deepcopy(base)
            o = row["outcome"]
            o.update({"marketId": f"m-mahomes-{suffix}", "outcomeId": f"o-mahomes-{suffix}-{pos[0]}",
                      "proposition": prop, "position": pos, "line": line,
                      "marketLabel": f"Patrick Mahomes - {prop.replace('_', ' ').title()}"})
            if period:
                o["periodLabel"] = period
            out.append(row)
    return out


def _identity_props(props: dict[str, Any]) -> list[dict[str, Any]]:
    """B12 props cloned from Mahomes' passing-yards OVER (F17/F26).

    (A prop with no eventId is already refused by the raw props schema, which
    fails the whole stage, so it is exercised in A12 instead.)"""
    base = next(p for p in props["props"] if p["outcome"]["proposition"] == "PASSING_YARDS"
                and p["outcome"]["position"] == "OVER")
    foreign = copy.deepcopy(base)
    foreign["outcome"].update({"marketId": "m-foreign-py", "outcomeId": "o-foreign-py-o",
                               "teamId": "mia-dolphins", "playerId": "p-foreign-1",
                               "playerName": "Foreign Passer",
                               "marketLabel": "Foreign Passer - Passing Yards"})
    identical = copy.deepcopy(base)
    conflicting = copy.deepcopy(base)
    conflicting["outcome"]["bookOdds"] = {"DRAFTKINGS": {"odds": 120, "american": 120,
                                                         "decimal": 2.2}}
    conflicting["outcome"]["bestOdds"] = 120
    conflicting["outcome"]["books"] = ["DRAFTKINGS"]
    return [foreign, identical, conflicting]


class FrozenOutlierClient:
    """In-memory stand-in for ``OutlierNflApiClient`` serving the shifted fixtures.

    Player props go through the real client's pagination (``_fetch_paginated``)
    as token pages of ``PROPS_PAGE_SIZE``; ``fail_props_page`` makes that page
    fail as an exhausted HTTP 500, ``fail_markets`` fails every event-markets call.
    """

    def __init__(self, fixtures_dir: Path, shift_days: int = SHIFT_DAYS, week: int = 4,
                 fail_props_page: int | None = None, fail_markets: bool = False,
                 empty_props: bool = False, market_extras: bool = False,
                 identity_extras: bool = False) -> None:
        sched = json.loads((fixtures_dir / "schedule.json").read_text(encoding="utf-8"))
        for ev in sched.get("events", []):
            for key in ("scheduledTime", "startTime"):
                if ev.get(key):
                    t = _dt.datetime.fromisoformat(ev[key]) + _dt.timedelta(days=shift_days)
                    ev[key] = t.isoformat()
            ev["week"] = week
        self._schedule = sched
        self._markets = json.loads((fixtures_dir / "event_markets.json").read_text(encoding="utf-8"))
        self._props = json.loads((fixtures_dir / "player_props.json").read_text(encoding="utf-8"))
        self._fail_props_page = fail_props_page
        self._fail_markets = fail_markets
        if empty_props:
            self._props = {**self._props, "props": []}
        if market_extras:
            self._markets = {**self._markets,
                             "markets": self._markets["markets"] + _extra_markets()}
            self._props = {**self._props, "props": self._props["props"] + _extra_props(self._props)}
        if identity_extras:
            self._props = {**self._props,
                           "props": self._props["props"] + _identity_props(self._props)}

    def fetch_schedule(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        return copy.deepcopy(self._schedule)

    def fetch_event_markets(self, event_id: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        if self._fail_markets:
            raise _http_error(f"HTTP 500 for frozen event markets {event_id}", 500)
        return copy.deepcopy(self._markets)

    def _props_page(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = self._props.get("props", [])
        pages = max(1, -(-len(rows) // PROPS_PAGE_SIZE))
        token = (params or {}).get("pageToken")
        num = int(str(token).rsplit("-", 1)[-1]) if token else 1
        if num == self._fail_props_page:
            raise _http_error(f"HTTP 500 for {path} page {num}", 500)
        chunk = rows[(num - 1) * PROPS_PAGE_SIZE: num * PROPS_PAGE_SIZE]
        nxt = f"frozen-page-{num + 1}" if num < pages else None
        return {"props": copy.deepcopy(chunk),
                "_page": {"nextPageToken": nxt, "pageNumber": num, "pages": pages,
                          "total": len(rows)}}

    def fetch_player_props(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        from outlier_nfl.api import OutlierNflApiClient

        # Placeholder, never sent anywhere: fetch_json is replaced just below.
        real = OutlierNflApiClient(bearer_token="frozen-harness")  # nosec B106
        real.fetch_json = self._props_page  # type: ignore[method-assign]
        return real.fetch_player_props()


# ---------------------------------------------------------------------------
# Determinism helpers (applied inside the slate subprocess)
# ---------------------------------------------------------------------------

_REAL_DATETIME = _dt.datetime


def _frozen_datetime_class(clock: _dt.datetime) -> type:
    class FrozenDateTime(_REAL_DATETIME):
        @classmethod
        def now(cls, tz: _dt.tzinfo | None = None) -> _dt.datetime:  # type: ignore[override]
            return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)

        @classmethod
        def utcnow(cls) -> _dt.datetime:  # type: ignore[override]
            return clock.astimezone(_dt.UTC).replace(tzinfo=None)

    return FrozenDateTime


def _freeze_clock(clock_iso: str) -> None:
    clock = _REAL_DATETIME.fromisoformat(clock_iso)
    frozen = _frozen_datetime_class(clock)
    for name, mod in list(sys.modules.items()):
        if not (name == "outlier_nfl" or name.startswith("outlier_nfl.")) or mod is None:
            continue
        if getattr(mod, "datetime", None) is _REAL_DATETIME or (
            isinstance(getattr(mod, "datetime", None), type)
            and getattr(mod.datetime, "__name__", "") == "FrozenDateTime"
        ):
            mod.datetime = frozen


def _deterministic_uuid() -> None:
    import uuid

    counter = {"n": 0}

    def fake_uuid4() -> uuid.UUID:
        counter["n"] += 1
        return uuid.UUID(int=counter["n"])

    uuid.uuid4 = fake_uuid4  # type: ignore[assignment]


def _rows_for(url: str, tables: dict[str, list[dict[str, str]]], fail: Iterable[str]) -> list[dict[str, str]]:
    for frag in fail:
        if frag in url:
            raise OSError(f"frozen harness: simulated download failure for {url}")
    for frag, rows in tables.items():
        if frag in url:
            return [dict(r) for r in rows]
    raise OSError(f"frozen harness: no frozen table for {url}")


# ---------------------------------------------------------------------------
# Running one slate (subprocess entry point)
# ---------------------------------------------------------------------------


def run_slate(slate: str, work: Path, out: Path, repo: Path, *, future_rows: bool = True) -> None:
    """Run every step of ``slate`` in ``work`` and write captures under ``out``."""
    import inspect
    import logging

    logging.disable(logging.CRITICAL)
    _deterministic_uuid()
    import outlier_nfl
    import outlier_nfl.external.common as ext_common
    import outlier_nfl.pipeline as pipeline_mod
    from outlier_nfl.snapshots import append_snapshot

    fixtures = repo / "tests" / "fixtures" / "nfl"
    if work.exists():
        shutil.rmtree(work)
    (work / "data").mkdir(parents=True)
    (work / "reports").mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "harness_version": HARNESS_VERSION, "slate": slate, "slate_date": SLATE_DATE[slate],
        "repo": str(repo), "outlier_nfl": str(Path(outlier_nfl.__file__).resolve()),
        "git_head": _git_head(repo), "future_rows": future_rows,
        "steps": [s["name"] for s in STEPS[slate]],
    }
    if not meta["outlier_nfl"].startswith(str(repo.resolve())):
        raise SystemExit(f"outlier_nfl imported from {meta['outlier_nfl']}, not {repo}")
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    tables = frozen_nflverse(future_rows)
    nfl_dir = work / "data" / "NFL"
    tape_path = nfl_dir / "tape" / "prior_week.json"
    if slate == "B":
        tape_path.parent.mkdir(parents=True, exist_ok=True)
        tape_path.write_text(json.dumps(frozen_tape(fixtures), indent=2), encoding="utf-8")
        # Written at the scheduled Sunday 10:00 ET refresh, before every step's clock.
        tape_written = TAPE_WRITTEN_AT.timestamp()
        os.utime(tape_path, (tape_written, tape_written))
        # A price snapshot taken after every step's clock: a later run's row that
        # a replay must not see.
        seed = {
            "event_id": "nfl-event-2026-w1-kc-bal", "event_starts_at": "2026-10-04T17:00:00+00:00",
            "matchup": "BAL @ KC", "team": "KC", "player_name": "Patrick Mahomes",
            "market": "PASS_YDS", "position": "OVER", "line": 255.5, "scope": "full_game",
            "best_odds": -125, "implied_probability": 0.5556, "is_consensus_line": True,
            "books": [{"book": "FANDUEL", "odds": -125}],
        }
        append_snapshot(nfl_dir, SLATE_DATE["B"], [seed], SEEDED_SNAPSHOT_TAKEN_AT)

    run_params = inspect.signature(pipeline_mod.NflPipeline.run).parameters
    init_params = inspect.signature(pipeline_mod.NflPipeline.__init__).parameters
    previous: dict[str, str] = {}
    run_ids: dict[str, str] = {}
    for step in STEPS[slate]:
        _freeze_clock(step["clock"])
        if step.get("kind"):
            try:
                error = _run_math_step(step, work, repo)
            except Exception as exc:  # noqa: BLE001 - a failure is a captured result too
                error = f"{type(exc).__name__}: {exc}"
            previous = _capture_step(work, out / step["name"], previous, run_ids,
                                     {"summary_status": None, "error": error})
            continue
        fail = tuple(step.get("fail_urls", ()))
        ext_common.Client.fetch_csv = (  # type: ignore[method-assign]
            lambda self, url, timeout=120.0, _f=fail: _rows_for(url, tables, _f)
        )
        real_usage = _ORIGINALS.setdefault("load_usage", pipeline_mod.load_usage)
        real_weather = _ORIGINALS.setdefault("load_slate_weather", pipeline_mod.load_slate_weather)
        pipeline_mod.load_usage = (  # type: ignore[assignment]
            lambda *a, _real=real_usage, _f=fail, **k: _real(
                *a, **{**k, "fetch_rows": lambda url: _rows_for(url, tables, _f)})
        )

        def _no_forecast(url: str, *_a: Any, _kind: Any = step.get("forecast"), **_k: Any) -> Any:
            if _kind:
                return frozen_forecast(str(_kind), url)
            raise OSError("frozen harness: no forecast service")

        pipeline_mod.load_slate_weather = (  # type: ignore[assignment]
            lambda *a, _real=real_weather, **k: _real(*a, **{**k, "fetch_json": _no_forecast})
        )
        moved_tape: Path | None = None
        if step.get("no_tape") and tape_path.exists():
            moved_tape = work / "tape_parked.json"
            tape_path.replace(moved_tape)
        kwargs: dict[str, Any] = {"date": SLATE_DATE[slate], "reports_dir": work / "reports"}
        if step.get("window"):
            kwargs["window"] = step["window"]
        init: dict[str, Any] = {"data_dir": work / "data"}
        if "clock" in init_params:
            # The run's wall clock is the step's clock, which is also its as_of: each
            # step is a run made at that instant. B4 is after kickoff by its clock.
            step_now = _REAL_DATETIME.fromisoformat(step["clock"])
            init["clock"] = lambda _now=step_now: _now
        if slate == "A":
            kwargs["offline_fixtures_dir"] = fixtures
            pipeline = pipeline_mod.NflPipeline(**init)
        else:
            client = FrozenOutlierClient(
                fixtures, week=int(step.get("week", 4)),
                fail_props_page=step.get("fail_props_page"),
                fail_markets=bool(step.get("fail_markets")),
                empty_props=bool(step.get("empty_props")),
                market_extras=bool(step.get("market_extras")),
                identity_extras=bool(step.get("identity_extras")),
            )
            pipeline = pipeline_mod.NflPipeline(client=client, **init)
        if "as_of_utc" in run_params:
            kwargs["as_of_utc"] = step["clock"]
        try:
            summary = pipeline.run(**kwargs)
            error = None
        except Exception as exc:  # noqa: BLE001 - a refusal is a captured result too
            summary, error = {}, f"{type(exc).__name__}: {exc}"
        if moved_tape is not None:
            moved_tape.replace(tape_path)
        if summary.get("run_id"):
            run_ids[str(summary["run_id"])] = f"RUN-{step['name']}"
        previous = _capture_step(work, out / step["name"], previous, run_ids,
                                 {"summary_status": summary.get("status"), "error": error})


_ORIGINALS: dict[str, Any] = {}


def _git_head(repo: Path) -> str | None:
    try:
        git = shutil.which("git")
        if git is None:
            return None
        return subprocess.run(  # nosec B603 - fixed argv, no shell  # nosemgrep
            [git, "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# ---------------------------------------------------------------------------
# Capture / normalization
# ---------------------------------------------------------------------------


def _normalize_text(text: str, work: Path, run_ids: dict[str, str]) -> str:
    text = text.replace(str(work.resolve()), "<WORK>").replace(str(work), "<WORK>")
    for rid, token in sorted(run_ids.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(rid, token)
    return text


_HASH_KEYS = frozenset({"sha256", "manifest_sha256"})


def _scrub_bundle_hashes(rel: str, text: str) -> str:
    """Blank hashes and sizes a run bundle takes over raw bytes (they embed work paths
    and run IDs).

    The inventory already compares every artifact's normalized content, so the
    manifest's and pointers' own hashes and byte counts add nothing but
    machine-specific noise (a longer work dir changes every size).
    """
    name = rel.rsplit("/", 1)[-1]
    if not (name == "manifest.json" or name.startswith("nfl_run_pointer_")):
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: "<SHA256>" if k in _HASH_KEYS else "<BYTES>" if k == "bytes" else scrub(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return node

    return json.dumps(scrub(payload), indent=2, ensure_ascii=False)


def _normalize_rel(rel: str, run_ids: dict[str, str]) -> str:
    for rid, token in sorted(run_ids.items(), key=lambda kv: -len(kv[0])):
        rel = rel.replace(rid, token)
    return rel


def _capture_step(work: Path, dest: Path, previous: dict[str, str], run_ids: dict[str, str],
                  status: dict[str, Any]) -> dict[str, str]:
    """Write normalized copies of every file in ``work`` plus an inventory."""
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "files").mkdir(parents=True)
    # Run IDs not reported by a summary (e.g. a failed run) still get stable tokens.
    runs_root = work / "data" / "NFL" / "runs"
    if runs_root.is_dir():
        for d in sorted(runs_root.iterdir()):
            if d.is_dir() and d.name not in run_ids:
                run_ids[d.name] = f"RUN-{dest.name}-{len(run_ids)}"
    inventory: dict[str, Any] = {}
    current: dict[str, str] = {}
    for path in sorted(p for p in work.rglob("*") if p.is_file()):
        rel_raw = path.relative_to(work).as_posix()
        if rel_raw == "tape_parked.json":
            continue
        rel = _normalize_rel(rel_raw, run_ids)
        raw = path.read_bytes()
        try:
            text = _scrub_bundle_hashes(rel, _normalize_text(raw.decode("utf-8"), work, run_ids))
            data = text.encode("utf-8")
        except UnicodeDecodeError:
            text, data = None, raw
        digest = hashlib.sha256(data).hexdigest()
        current[rel] = digest
        entry: dict[str, Any] = {
            "sha256": digest, "bytes": len(data),
            "written_this_step": previous.get(rel) != digest,
        }
        if text is not None and rel.endswith(".json"):
            try:
                payload = json.loads(text)
                entry["count"] = _payload_count(payload)
            except json.JSONDecodeError:
                pass
        elif text is not None and rel.endswith(".jsonl"):
            entry["count"] = sum(1 for line in text.splitlines() if line.strip())
        inventory[rel] = entry
        target = dest / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (dest / "inventory.json").write_text(
        json.dumps({"status": status, "files": inventory}, indent=2, sort_keys=True), encoding="utf-8"
    )
    return current


def _payload_count(payload: Any) -> int | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("count"), int):
            return payload["count"]
        for key in ("records", "picks", "players"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    if isinstance(payload, list):
        return len(payload)
    return None


def capture(repo: Path, out: Path, slates: Iterable[str] = SLATES, work: Path = DEFAULT_WORK,
            future_rows: bool = True) -> None:
    """Capture each slate in a fresh subprocess importing ``outlier_nfl`` from ``repo``."""
    repo = repo.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for slate in slates:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONHASHSEED"] = "0"
        cmd = [sys.executable, str(Path(__file__).resolve()), "_run-slate", "--slate", slate,
               "--repo", str(repo), "--work", str(work / slate), "--out", str(out / slate)]
        if not future_rows:
            cmd.append("--no-future-rows")
        # argv is this interpreter re-running this script; no shell, no external input.
        subprocess.run(  # nosec B603  # nosemgrep
            cmd, check=True, env=env, cwd=str(work.parent if work.parent.exists() else "/")
        )


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

KEY_FIELDS = ("event_id", "game_id", "source", "kind", "player_id", "gsis_id", "player_name",
              "player", "team", "market", "position", "side", "line", "scope", "week", "taken_at",
              "tag")


def record_key(rec: Any) -> str:
    if not isinstance(rec, dict):
        return json.dumps(rec, sort_keys=True)
    parts = [f"{k}={rec[k]}" for k in KEY_FIELDS if k in rec and rec[k] not in (None, "")]
    return "|".join(parts) or json.dumps(rec, sort_keys=True)[:120]


def _keyed(records: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for r in records:
        out.setdefault(record_key(r), []).append(r)
    return out


def diff_records(before: list[Any], after: list[Any], sample: int = 5) -> dict[str, Any]:
    kb, ka = _keyed(before), _keyed(after)
    removed = [k for k in kb if k not in ka]
    added = [k for k in ka if k not in kb]
    changed_fields: Counter[str] = Counter()
    changed: list[str] = []
    for k in kb:
        if k in ka and kb[k] != ka[k]:
            changed.append(k)
            b, a = kb[k][0], ka[k][0]
            if isinstance(b, dict) and isinstance(a, dict):
                for f in sorted(set(b) | set(a)):
                    if b.get(f) != a.get(f):
                        changed_fields[f] += 1
    return {
        "count_before": len(before), "count_after": len(after),
        "removed": len(removed), "added": len(added), "changed": len(changed),
        "removed_sample": removed[:sample], "added_sample": added[:sample],
        "changed_sample": changed[:sample], "changed_fields": dict(changed_fields.most_common()),
    }


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return text


def _short(v: Any, n: int = 80) -> str:
    s = json.dumps(v, sort_keys=True, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def diff_file(bpath: Path, apath: Path) -> dict[str, Any]:
    b, a = _load(bpath), _load(apath)
    out: dict[str, Any] = {}
    if isinstance(b, list) and isinstance(a, list):
        out["rows"] = diff_records(b, a)
    elif isinstance(b, dict) and isinstance(a, dict):
        lists, scalars = {}, {}
        for k in sorted(set(b) | set(a)):
            bv, av = b.get(k), a.get(k)
            if bv == av:
                continue
            if isinstance(bv, list) and isinstance(av, list) and any(isinstance(x, dict) for x in bv + av):
                lists[k] = diff_records(bv, av)
            else:
                scalars[k] = {"before": _short(bv), "after": _short(av)}
        out["lists"], out["keys"] = lists, scalars
    else:
        bl, al = str(b).splitlines(), str(a).splitlines()
        out["text"] = {"lines_before": len(bl), "lines_after": len(al),
                       "lines_changed": sum(1 for x, y in zip(bl, al) if x != y) + abs(len(bl) - len(al))}
    return out


def diff_captures(before: Path, after: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"slates": {}}
    for slate_dir in sorted(p for p in after.iterdir() if p.is_dir()):
        slate = slate_dir.name
        bslate = before / slate
        sr: dict[str, Any] = {"steps": {}}
        meta_b = json.loads((bslate / "meta.json").read_text()) if (bslate / "meta.json").exists() else {}
        meta_a = json.loads((slate_dir / "meta.json").read_text())
        sr["meta"] = {"before": meta_b.get("git_head"), "after": meta_a.get("git_head")}
        for step in meta_a.get("steps", []):
            inv_b = json.loads((bslate / step / "inventory.json").read_text()) if (bslate / step).exists() else {"files": {}, "status": {}}
            inv_a = json.loads((slate_dir / step / "inventory.json").read_text())
            fb, fa = inv_b["files"], inv_a["files"]
            st: dict[str, Any] = {
                "status": {"before": inv_b.get("status"), "after": inv_a.get("status")},
                "only_before": sorted(set(fb) - set(fa)),
                "only_after": sorted(set(fa) - set(fb)),
                "changed": {},
                "written_flag_changed": [],
            }
            for rel in sorted(set(fb) & set(fa)):
                if fb[rel].get("written_this_step") != fa[rel].get("written_this_step"):
                    st["written_flag_changed"].append(
                        {"file": rel, "before": fb[rel].get("written_this_step"),
                         "after": fa[rel].get("written_this_step")})
                if fb[rel]["sha256"] != fa[rel]["sha256"]:
                    entry: dict[str, Any] = {"count_before": fb[rel].get("count"),
                                             "count_after": fa[rel].get("count")}
                    try:
                        entry.update(diff_file(bslate / step / "files" / rel,
                                               slate_dir / step / "files" / rel))
                    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                        entry["error"] = str(exc)
                    st["changed"][rel] = entry
            st["written_before"] = sorted(r for r, e in fb.items() if e.get("written_this_step"))
            st["written_after"] = sorted(r for r, e in fa.items() if e.get("written_this_step"))
            sr["steps"][step] = st
        report["slates"][slate] = sr
    return report


def has_differences(report: dict[str, Any]) -> bool:
    for sr in report["slates"].values():
        for st in sr["steps"].values():
            if st["only_before"] or st["only_after"] or st["changed"] or st["written_flag_changed"]:
                return True
            if st["status"]["before"] != st["status"]["after"]:
                return True
    return False


def render_markdown(report: dict[str, Any], title: str = "NFL snapshot diff") -> str:
    lines = [f"# {title}", ""]
    for slate, sr in report["slates"].items():
        lines += [f"## Slate {slate} ({SLATE_DATE.get(slate, '')})",
                  f"before `{sr['meta']['before']}` → after `{sr['meta']['after']}`", ""]
        for step, st in sr["steps"].items():
            lines += [f"### {step}", ""]
            if st["status"]["before"] != st["status"]["after"]:
                lines.append(f"- status: `{st['status']['before']}` → `{st['status']['after']}`")
            wb, wa = set(st["written_before"]), set(st["written_after"])
            if wb - wa:
                lines.append(f"- written by this step **before only** ({len(wb - wa)}): "
                             + ", ".join(f"`{x}`" for x in sorted(wb - wa)))
            if wa - wb:
                lines.append(f"- written by this step **after only** ({len(wa - wb)}): "
                             + ", ".join(f"`{x}`" for x in sorted(wa - wb)))
            if st["only_before"]:
                lines.append(f"- files only before ({len(st['only_before'])}): "
                             + ", ".join(f"`{x}`" for x in st["only_before"]))
            if st["only_after"]:
                lines.append(f"- files only after ({len(st['only_after'])}): "
                             + ", ".join(f"`{x}`" for x in st["only_after"]))
            for rel, e in st["changed"].items():
                cnt = ""
                if e.get("count_before") != e.get("count_after"):
                    cnt = f" count {e.get('count_before')} → {e.get('count_after')}"
                lines.append(f"- changed `{rel}`{cnt}")
                for lk, ld in (e.get("lists") or {}).items():
                    lines.append(
                        f"  - `{lk}`: {ld['count_before']} → {ld['count_after']} rows "
                        f"(-{ld['removed']} +{ld['added']} ~{ld['changed']}); fields: "
                        + ", ".join(f"{f}×{n}" for f, n in list(ld["changed_fields"].items())[:8]))
                    for s in ld["removed_sample"][:3]:
                        lines.append(f"    - removed: `{s}`")
                    for s in ld["added_sample"][:3]:
                        lines.append(f"    - added: `{s}`")
                if e.get("rows"):
                    ld = e["rows"]
                    lines.append(f"  - rows: {ld['count_before']} → {ld['count_after']} "
                                 f"(-{ld['removed']} +{ld['added']} ~{ld['changed']})")
                    for s in ld["removed_sample"][:3]:
                        lines.append(f"    - removed: `{s}`")
                for k, v in list((e.get("keys") or {}).items())[:10]:
                    lines.append(f"  - `{k}`: {v['before']} → {v['after']}")
                if e.get("text"):
                    t = e["text"]
                    lines.append(f"  - text lines {t['lines_before']} → {t['lines_after']}, "
                                 f"{t['lines_changed']} changed")
            if not any((st["only_before"], st["only_after"], st["changed"], wb ^ wa)):
                lines.append("- no differences")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="Run the fixed slates against a checkout.")
    c.add_argument("--repo", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--slate", choices=("A", "B", "all"), default="all")
    c.add_argument("--work", type=Path, default=DEFAULT_WORK)
    c.add_argument("--no-future-rows", action="store_true")
    d = sub.add_parser("diff", help="Diff two captures.")
    d.add_argument("before", type=Path)
    d.add_argument("after", type=Path)
    d.add_argument("--md", type=Path, default=None)
    d.add_argument("--json", type=Path, default=None)
    d.add_argument("--fail-on-diff", action="store_true")
    d.add_argument("--title", default="NFL snapshot diff")
    r = sub.add_parser("_run-slate", help=argparse.SUPPRESS)
    r.add_argument("--slate", choices=SLATES, required=True)
    r.add_argument("--repo", type=Path, required=True)
    r.add_argument("--work", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--no-future-rows", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "capture":
        slates = SLATES if args.slate == "all" else (args.slate,)
        capture(args.repo, args.out, slates, args.work, future_rows=not args.no_future_rows)
        return 0
    if args.cmd == "_run-slate":
        run_slate(args.slate, args.work, args.out, args.repo.resolve(),
                  future_rows=not args.no_future_rows)
        return 0
    report = diff_captures(args.before, args.after)
    md = render_markdown(report, args.title)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(md, encoding="utf-8")
    else:
        print(md)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    differs = has_differences(report)
    print(f"differences: {'yes' if differs else 'none'}", file=sys.stderr)
    return 1 if (args.fail_on_diff and differs) else 0


if __name__ == "__main__":
    sys.exit(main())
