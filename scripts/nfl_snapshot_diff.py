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

HARNESS_VERSION = 6
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


def _run_math_step(step: dict[str, Any], work: Path, repo: Path) -> str | None:
    """A3/A4: run the pricing-math tools on A1's outputs. Returns an error or None."""
    norm = work / "data" / "NFL" / "normalized"
    if step["kind"] == "auth":
        return _run_auth_step(work)
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


class FrozenOutlierClient:
    """In-memory stand-in for ``OutlierNflApiClient`` serving the shifted fixtures.

    Player props go through the real client's pagination (``_fetch_paginated``)
    as token pages of ``PROPS_PAGE_SIZE``; ``fail_props_page`` makes that page
    fail as an exhausted HTTP 500, ``fail_markets`` fails every event-markets call.
    """

    def __init__(self, fixtures_dir: Path, shift_days: int = SHIFT_DAYS, week: int = 4,
                 fail_props_page: int | None = None, fail_markets: bool = False,
                 empty_props: bool = False, market_extras: bool = False) -> None:
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
