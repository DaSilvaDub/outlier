"""Phase 9b (#231 part b): integration, end-to-end and NFL-edge contracts.

Test-only. Every fixture names the real source shape it was checked against:

- nflverse schedule (``nflverse/nfldata`` ``data/games.csv``, fetched live
  2026-10-10): columns ``game_id, season, game_type, week, gameday, weekday,
  gametime, away_team, home_team, location, roof, stadium, ...``. ``game_type``
  is one of ``REG/WC/DIV/CON/SB`` (CON, not CONF); ``location`` is ``Home`` or
  ``Neutral``; ``gametime`` is ``HH:MM`` US Eastern with no zone. Rows below are
  copied from the 2025 season of that file (only the columns used).
- nflverse injuries (``injuries_2025.csv``, fetched live): ``report_status`` is
  ``Out``/``Questionable``/``Doubtful`` or blank. There is no IR/PUP value.
- Outlier schedule events: the ``tests/fixtures/nfl/schedule.json`` shape
  (``eventId``, ``scheduledTime``/``startTime`` ISO UTC, ``season``, ``week``,
  ``home``/``away`` with ``alias``), served live-shaped by the harness's
  ``FrozenOutlierClient``.
- Run pointer ``nfl_run_pointer_<date>.json``: ``run_id``, ``run_dir``,
  ``manifest_sha256``; manifest ``artifacts[]`` carry ``name``, ``sha256``,
  ``published_to``.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl import usage
from outlier_nfl.matchup import load_tape_envelope
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import (
    RunContext,
    before_week_from_schedule,
    game_finished_by,
    parse_utc,
    schedule_kickoff_utc,
)
from outlier_nfl.season_phase import event_season_type, season_phase_receipt
from outlier_nfl.weather import venue_status
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"

# Real 2025 rows of nflverse games.csv (subset of columns).
COLS = ("game_id", "season", "game_type", "week", "gameday", "weekday", "gametime",
        "away_team", "home_team", "location", "roof", "stadium")
ROWS = {
    "jan_reg": ("2025_18_CAR_TB", "2025", "REG", "18", "2026-01-03", "Saturday", "16:30",
                "CAR", "TB", "Home", "outdoors", "Raymond James Stadium"),
    "wc": ("2025_19_LA_CAR", "2025", "WC", "19", "2026-01-10", "Saturday", "16:30",
           "LA", "CAR", "Home", "outdoors", "Bank of America Stadium"),
    "div": ("2025_20_BUF_DEN", "2025", "DIV", "20", "2026-01-17", "Saturday", "16:30",
            "BUF", "DEN", "Home", "outdoors", "Empower Field at Mile High"),
    "con": ("2025_21_NE_DEN", "2025", "CON", "21", "2026-01-25", "Sunday", "15:00",
            "NE", "DEN", "Home", "outdoors", "Empower Field at Mile High"),
    "sb": ("2025_22_SEA_NE", "2025", "SB", "22", "2026-02-08", "Sunday", "18:30",
           "SEA", "NE", "Neutral", "outdoors", "Levi's Stadium"),
    "neutral_intl": ("2025_04_MIN_PIT", "2025", "REG", "4", "2025-09-28", "Sunday", "09:30",
                     "MIN", "PIT", "Neutral", "outdoors", "Acrisure Stadium"),
    "edt": ("2025_08_MIA_ATL", "2025", "REG", "8", "2025-10-26", "Sunday", "13:00",
            "MIA", "ATL", "Home", "dome", "Mercedes-Benz Stadium"),
}
GAMES = {k: dict(zip(COLS, v)) for k, v in ROWS.items()}


def _event(row: dict[str, str], *, swap: bool = False, keep_type: bool = False,
           keep_week: bool = False, day: str | None = None) -> dict[str, Any]:
    """An Outlier-shaped event for a schedule row (kickoff from gameday/gametime ET)."""
    kick = schedule_kickoff_utc(day or row["gameday"], row["gametime"])
    assert kick is not None
    home, away = (row["away_team"], row["home_team"]) if swap else (row["home_team"],
                                                                     row["away_team"])
    ev: dict[str, Any] = {"eventId": row["game_id"], "scheduledTime": kick.isoformat(),
                          "season": int(row["season"]), "home": {"alias": home},
                          "away": {"alias": away}}
    if keep_type:
        ev["game_type"] = row["game_type"]
    if keep_week:
        ev["week"] = int(row["week"])
    return ev


# --- Category 8: January REG and every playoff round ------------------------------

def test_january_regular_season_game_is_reg_and_publishable():
    ev = _event(GAMES["jan_reg"], keep_week=True)
    assert event_season_type(ev) == "REG"
    assert season_phase_receipt([ev], 2025).status == "OK"


@pytest.mark.parametrize("rnd", ["wc", "div", "con", "sb"])
def test_every_playoff_round_is_post_by_type_week_and_schedule_and_is_refused(rnd):
    row = GAMES[rnd]
    by_type = _event(row, keep_type=True)
    by_week = _event(row, keep_week=True)
    by_schedule = _event(row)  # no type, no week: the nflverse row decides
    for ev in (by_type, by_week):
        assert event_season_type(ev) == "POST"
    assert event_season_type(by_schedule, [row]) == "POST"
    r = season_phase_receipt([by_schedule], 2025, [row])
    assert r.status == "UNSUPPORTED" and "POST" in (r.reason or "")


# --- Category 8: DST transitions --------------------------------------------------

@pytest.mark.parametrize(("gameday", "gametime", "utc"), [
    ("2025-10-26", "13:00", "2025-10-26T17:00:00+00:00"),  # last Sunday on EDT (UTC-4)
    ("2025-11-02", "13:00", "2025-11-02T18:00:00+00:00"),  # DST ended 02:00 that morning
    ("2025-11-02", "09:30", "2025-11-02T14:30:00+00:00"),  # early international window, EST
    ("2026-03-08", "13:00", "2026-03-08T17:00:00+00:00"),  # DST starts (offseason row shape)
    ("2025-09-28", "09:30", "2025-09-28T13:30:00+00:00"),  # real 2025_04_MIN_PIT, EDT
])
def test_eastern_gametime_converts_across_dst(gameday, gametime, utc):
    assert schedule_kickoff_utc(gameday, gametime) == parse_utc(utc)


def test_dst_boundary_does_not_start_a_game_an_hour_early():
    kick = schedule_kickoff_utc("2025-11-02", "13:00")  # 18:00Z under EST
    assert kick is not None
    before_week = before_week_from_schedule(
        [{"week": "9", "gameday": "2025-11-02", "gametime": "13:00"},
         {"week": "8", "gameday": "2025-10-26", "gametime": "13:00"}],
        "2025-11-02", parse_utc("2025-11-02T17:30:00+00:00"))
    assert before_week == 9  # week 8 finished; week 9's 1 pm EST game has not started
    assert not game_finished_by(kick, parse_utc("2025-11-02T20:30:00+00:00"))


# --- Category 8: neutral-site / international ---------------------------------------

def test_neutral_international_game_matches_swapped_teams_and_is_never_forecast():
    row = GAMES["neutral_intl"]
    ev = _event(row, swap=True)  # API lists the designated home team the other way
    assert event_season_type(ev, [row]) == "REG"
    assert venue_status(row["home_team"], row) == "neutral"
    assert venue_status("NE", GAMES["sb"]) == "neutral"  # Super Bowl site is neutral too


# --- Category 8: reschedule / postponement ------------------------------------------

def test_rescheduled_game_does_not_inherit_its_stale_schedule_row():
    """A game moved to another day must not match the old row: phase is unknown, not REG."""
    row = GAMES["edt"]
    moved = _event(row, day="2025-10-27")  # postponed to Monday; schedule not yet updated
    assert event_season_type(moved, [row]) is None
    assert season_phase_receipt([moved], 2025, [row]).status == "UNSUPPORTED"


def test_rescheduled_week_stays_pending_until_its_new_kickoff():
    rows = [{"week": "8", "gameday": "2025-10-26", "gametime": "13:00"},
            {"week": "8", "gameday": "2025-10-28", "gametime": "20:15"},  # postponed to Tue
            {"week": "9", "gameday": "2025-11-02", "gametime": "13:00"}]
    assert before_week_from_schedule(rows, "2025-11-02",
                                     parse_utc("2025-10-27T12:00:00+00:00")) == 8


# --- Category 8: bye, IR/PUP, legacy depth schema -----------------------------------

def _pw(week, team="KC", pid="P1", targets=8.0):
    return {"player_id": pid, "player_display_name": "Star Wr", "position": "WR",
            "season": "2025", "week": str(week), "season_type": "REG",
            "game_id": f"2025_{week:02d}_{team}", "team": team, "targets": str(targets),
            "target_share": "0.3", "carries": "0", "receiving_yards": "60",
            "rushing_yards": "0"}


def test_bye_week_is_not_a_missed_game():
    # Real stats_player_week shape; the team's week 3 is a bye (no rows for anyone).
    rows = [_pw(1), _pw(2), _pw(4), _pw(1, pid="P2", targets=4), _pw(2, pid="P2", targets=4),
            _pw(4, pid="P2", targets=4)]
    for r in rows[3:]:
        r["player_display_name"] = "Mate Wr"
        r["target_share"] = "0.15"
    p = usage.build_profiles(rows, before_week=5)
    star = p["P1"]
    assert (star.games, star.last_week, star.team_last_week) == (3, 4, 4)
    sigs = usage.usage_signals(p, {"KC": ["Star Wr"]}, {"KC": "e1"})
    assert any(s.tag == "VACATED_TARGETS" for s in sigs)  # bye didn't make him "already out"


def test_injury_report_has_no_ir_or_pup_status():
    """IR/PUP players drop off the weekly report (real report_status values below)."""
    from outlier_nfl import tape_nflverse as tn

    rows = [{"season": "2025", "week": "5", "team": "KC", "full_name": s,
             "report_status": s, "game_type": "REG", "position": "WR"}
            for s in ("Out", "Doubtful", "Questionable", "")]
    out = tn.inactive_players(rows, 5, season=2025)
    assert sorted(p["name"] for p in out.get("KC", [])) == ["Doubtful", "Out"]


def test_pre_2025_legacy_depth_schema_season_is_refused():
    ev = _event(GAMES["edt"], keep_week=True)
    r = season_phase_receipt([ev], 2024)
    assert r.status == "UNSUPPORTED" and "legacy depth-chart schema" in (r.reason or "")


# --- Category 3: empty tape must not validate ---------------------------------------

def test_tape_with_no_teams_is_refused(tmp_path):
    tape = tmp_path / "tape" / "prior_week.json"
    tape.parent.mkdir(parents=True)
    tape.write_text(json.dumps({"season": 2026, "before": "2026-10-04", "teams": {}}),
                    encoding="utf-8")
    ctx = RunContext(slate_date="2026-10-04", window=None, season=2026,
                     as_of_utc=parse_utc("2026-10-04T15:00:00+00:00"), mode="live",
                     first_kickoff_utc=None)
    env = load_tape_envelope(tmp_path, ctx)
    assert not env.admitted and env.source.status == "REFUSED"
    assert "no teams" in (env.source.reason or "")


# --- Category 6: live-shaped end to end ---------------------------------------------

SLATE = "2026-10-04"
PREGAME = "2026-10-04T15:00:00+00:00"
LATER = "2026-10-04T16:00:00+00:00"


def _run(tmp_path: Path, client: Any, clock: str, day: str = SLATE) -> dict[str, Any]:
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock)
    return NflPipeline(client=client, data_dir=tmp_path, clock=lambda: now).run(
        date=day, as_of_utc=clock, reports_dir=tmp_path / "reports")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_consumers_on_pointer(tmp_path: Path, day: str = SLATE) -> str:
    norm = tmp_path / "NFL" / "normalized"
    pointer = json.loads((norm / f"nfl_run_pointer_{day}.json").read_text("utf-8"))
    run_dir = tmp_path / "NFL" / "runs" / pointer["run_id"]
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == pointer["manifest_sha256"]
    manifest = json.loads(manifest_bytes)
    published = {Path(d).resolve(): a["sha256"] for a in manifest["artifacts"]
                 for d in a["published_to"]}
    # Every dated file a consumer reads is byte-identical to the pointer's bundle.
    for path in norm.glob(f"*_{day}.*"):
        if path.name.startswith("nfl_run_pointer_"):
            continue
        assert path.resolve() in published, path.name
        assert _sha(path) == published[path.resolve()], path.name
    # Run-stamped consumer inputs: export pack, scorecard, summary.
    for name in (f"nfl_high_prob_props_{day}.json", f"nfl_matchup_scripts_{day}.json",
                 f"summary_{day}.json"):
        assert json.loads((norm / name).read_text("utf-8"))["run_id"] == pointer["run_id"], name
    return pointer["run_id"]


def test_every_consumer_reads_the_committed_run(tmp_path):
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    assert run["publication"] == "published"
    assert _assert_consumers_on_pointer(tmp_path) == run["run_id"]


def test_recovery_after_a_partial_run_republishes_one_coherent_run(tmp_path):
    good = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    partial = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_props_page=2),
                   "2026-10-04T15:10:00+00:00")
    assert partial["status"] == "PARTIAL" and partial["publication"] == "bundle_only"
    assert _assert_consumers_on_pointer(tmp_path) == good["run_id"]  # nothing mixed in
    again = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), "2026-10-04T15:20:00+00:00")
    assert again["status"] == "OK" and again["publication"] == "published"
    assert _assert_consumers_on_pointer(tmp_path) == again["run_id"] != good["run_id"]


def test_empty_confirmed_slate_publishes_an_empty_card(tmp_path):
    """A schedule with no game that day: EMPTY receipts and an empty card, no props."""
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), "2026-10-06T14:00:00+00:00",
               day="2026-10-06")
    assert run["status"] == "OK" and run["events_count"] == 0 and run["props_count"] == 0
    receipts = {r["name"]: r["status"] for r in run["stage_receipts"]}
    assert receipts["event_markets"] == "EMPTY" and receipts["player_props"] == "EMPTY"
    card = json.loads((tmp_path / "NFL" / "normalized" / "nfl_best_bets_2026-10-06.json")
                      .read_text("utf-8"))
    assert card["picks"] == [] and sum(card["counts"].values()) == 0


# --- Category 5 (reviewer ask): two OS processes writing at once -------------------

def _writer_process(root: str, worker: int, n: int) -> None:
    from outlier_nfl import scorecard
    from outlier_nfl.snapshots import append_snapshot

    base = Path(root)
    for i in range(n):
        prop = {"is_consensus_line": True, "scope": "full_game", "event_id": f"w{worker}-e{i}",
                "player_name": f"Player {worker}-{i}", "market": "REC", "position": "OVER",
                "line": 4.5, "best_odds": -110, "books": [{"book": "fanduel", "odds": -110}]}
        append_snapshot(base, "2026-10-04", [prop], "2026-10-04T15:00:00+00:00",
                        run_id=f"RUN-{worker}-{i}")
        g = scorecard.GradedSignal(
            date=f"2026-{worker + 10:02d}-{i + 1:02d}", week=1, event_id=f"w{worker}-e{i}",
            tag="SCRIPT", player="P", team="KC", market="REC", side="OVER", prior_avg=4.0,
            actual=5.0, hit_vs_avg=True, line=4.5, hit_vs_line=True, run_id=f"RUN-{worker}-{i}")
        scorecard.update_ledger(base / "ledger.jsonl", [g], g.date)


def test_two_os_processes_lose_no_snapshot_or_ledger_rows(tmp_path):
    ctx = mp.get_context("spawn")  # real separate interpreters, same as two scheduled jobs
    n = 15
    procs = [ctx.Process(target=_writer_process, args=(str(tmp_path), w, n)) for w in (0, 1)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    for p in procs:
        if p.is_alive():
            p.kill()
            pytest.fail("writer process hung")
        assert p.exitcode == 0
    [snap_file] = list((tmp_path / "snapshots").glob("*.jsonl"))
    snaps = [json.loads(x) for x in snap_file.read_text("utf-8").splitlines()]  # no torn line
    assert len(snaps) == 2 * n and len({r["run_id"] for r in snaps}) == 2 * n
    ledger = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text("utf-8").splitlines()]
    assert len(ledger) == 2 * n and len({r["date"] for r in ledger}) == 2 * n
    assert not list(tmp_path.rglob("*.lock")) and not list(tmp_path.rglob("*.tmp"))

