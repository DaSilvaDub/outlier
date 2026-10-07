"""F01: predictive sources must not admit information from after the prediction time.

Forensic report 2026-10-06, F01: external metrics were loaded through week 22,
usage read every week when the schedule was missing, and snapshot movement read
rows captured after the run. Each test injects post-cutoff data and requires it
to have no effect.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from outlier_nfl import external, usage
from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl.best_bets import TraceInputs, build_best_bets
from outlier_nfl.external import ExternalLoad, schedule
from outlier_nfl.run_context import before_week_from_schedule, parse_utc
from outlier_nfl.snapshots import append_snapshot, load_snapshots, movement_index, snapshot_path
from scripts import nfl_snapshot_diff as snap

REPO = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO / "tests" / "fixtures" / "nfl"
AS_OF = datetime(2026, 10, 4, 16, tzinfo=UTC)  # noon ET, Week 4 Sunday


class _Client:
    def __init__(self, routes: dict[str, list[dict[str, str]] | Exception]):
        self.routes = routes

    def fetch_csv(self, url: str, timeout: float = 120.0) -> list[dict[str, str]]:
        for key, rows in self.routes.items():
            if key in url:
                if isinstance(rows, Exception):
                    raise rows
                return [dict(r) for r in rows]
        return []


def _game(week: int, gameday: str, gametime: str = "13:00", **extra: str) -> dict[str, str]:
    row = {"season": "2026", "game_type": "REG", "week": str(week), "gameday": gameday,
           "gametime": gametime, "home_team": "KC", "away_team": "BAL",
           "home_score": "27", "away_score": "20", "spread_line": "3.5", "total_line": "47.5",
           "temp": "71", "wind": "8", "roof": "outdoors", "stadium": "Arrowhead"}
    row.update(extra)
    return row


SCHEDULE = [_game(1, "2026-09-13"), _game(2, "2026-09-20"), _game(3, "2026-09-27"),
            _game(4, "2026-10-04"), _game(5, "2026-10-11"), _game(18, "2027-01-03")]


def _ngs(week: int, separation: float) -> dict[str, str]:
    return {"season": "2026", "season_type": "REG", "week": str(week),
            "player_display_name": "Travis Kelce", "player_gsis_id": "00-k",
            "player_position": "TE", "team_abbr": "KC", "avg_separation": str(separation)}


def _play(week: int) -> dict[str, str]:
    return {"season": "2026", "season_type": "REG", "week": str(week), "posteam": "KC",
            "defteam": "BAL", "play_type": "pass", "epa": "0.5", "success": "1",
            "two_point_attempt": "0"}


def test_ngs_and_pbp_exclude_weeks_at_or_after_the_slate_week():
    """A Week 4 slate admits Weeks 1-3 only; Week 4, 5 and 18 rows are future."""
    client = _Client({
        "games.csv": SCHEDULE,
        "ngs_receiving": [_ngs(1, 2.5), _ngs(3, 2.7), _ngs(4, 4.4), _ngs(18, 6.0)],
        "ngs_passing": [], "ngs_rushing": [],
        "play_by_play": [_play(2), _play(5)],
    })
    loaded = external.load_external_metrics(  # type: ignore[arg-type]
        2026, slate_date="2026-10-04", as_of_utc=AS_OF, client=client
    )
    assert loaded.before_week == 4
    ngs_weeks = sorted(r["week"] for r in loaded.records if r["source"] == "ngs")
    pbp_weeks = sorted({r["week"] for r in loaded.records if r["source"] == "pbp"})
    assert ngs_weeks == [1, 3]
    assert pbp_weeks == [2]
    src = {s.name: s for s in loaded.sources}
    assert src["external:ngs:receiving"].max_week_admitted == 3


def test_cutoff_also_excludes_earlier_weeks_not_finished_by_as_of():
    """A replay as of Monday afternoon cannot use that night's Week 3 game."""
    rows = [*SCHEDULE[:2],
            {"week": 3, "gameday": "2026-09-27", "gametime": "13:00"},
            {"week": 3, "gameday": "2026-09-28", "gametime": "20:15"},  # MNF
            *SCHEDULE[3:]]
    monday_noon = datetime(2026, 9, 28, 16, tzinfo=UTC)
    assert before_week_from_schedule(rows, "2026-10-04", monday_noon) == 3
    tuesday = datetime(2026, 9, 29, 14, tzinfo=UTC)
    assert before_week_from_schedule(rows, "2026-10-04", tuesday) == 4
    assert before_week_from_schedule([], "2026-10-04", tuesday) is None



def test_missing_gametime_counts_as_not_started():
    """A game with no listed time is assumed to start as late as it could that day.

    Monday 6pm ET: the Week 3 Monday game has no ``gametime``. Reading it as 00:00
    ET would count Week 3 as complete and admit its data and final score.
    """
    rows = [*SCHEDULE[:2],
            _game(3, "2026-09-27"),
            _game(3, "2026-09-28", gametime=""),
            *SCHEDULE[3:]]
    monday_evening = datetime(2026, 9, 28, 22, tzinfo=UTC)
    assert before_week_from_schedule(rows, "2026-10-04", monday_evening) == 3
    client = _Client({"games.csv": rows})
    recs = schedule.fetch(client, 2026, as_of_utc=monday_evening)["records"]  # type: ignore[arg-type]
    monday = next(r for r in recs if r["gameday"] == "2026-09-28")
    assert monday["home_score"] is None and monday["total_line"] is None
    assert monday["kickoff_utc"] == "2026-09-29T03:59:00+00:00"  # 23:59 ET

def test_schedule_blanks_postgame_fields_for_games_at_or_after_the_cutoff():
    client = _Client({"games.csv": SCHEDULE})
    recs = schedule.fetch(client, 2026, as_of_utc=AS_OF)["records"]  # type: ignore[arg-type]
    by_week = {r["week"]: r for r in recs}
    assert by_week[3]["home_score"] == 27.0 and by_week[3]["total_line"] == 47.5
    for week in (4, 5, 18):
        g = by_week[week]
        assert g["home_score"] is None and g["away_score"] is None
        assert g["spread_line"] is None and g["total_line"] is None
        assert g["temp"] is None and g["wind"] is None
        assert g["stadium"] == "Arrowhead" and g["kickoff_utc"]  # pregame facts survive
    assert by_week[4]["kickoff_utc"] == "2026-10-04T17:00:00+00:00"


def test_load_usage_refuses_a_missing_cutoff():
    """Report example: Week 9 rows moved a Week 4 receiver from 66.7 to 175.0 yds/g."""
    with pytest.raises(ValueError, match="cutoff"):
        usage.load_usage(2026, None, fetch_rows=lambda _url: [])


def _mock_client():
    return snap.FrozenOutlierClient(FIXTURES_DIR)


def test_pipeline_skips_usage_without_a_verified_cutoff(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(nfl_pipeline, "load_external_metrics",
                        lambda *a, **k: ExternalLoad(before_week=None))
    monkeypatch.setattr(nfl_pipeline, "load_usage", lambda *a, **k: calls.append(a) or {})
    summary = nfl_pipeline.NflPipeline(client=_mock_client(), data_dir=tmp_path).run(
        date="2026-10-04", as_of_utc="2026-10-04T16:00:00+00:00",
        reports_dir=tmp_path / "reports",
    )
    assert calls == []
    usage_src = next(s for s in summary["sources"] if s["name"] == "usage")
    assert (usage_src["status"], usage_src["reason"]) == ("UNAVAILABLE", "missing verified cutoff")
    assert summary["as_of_utc"] == "2026-10-04T16:00:00+00:00"
    assert summary["before_week"] is None


def test_pipeline_passes_the_cutoff_week_to_usage(tmp_path, monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(nfl_pipeline, "load_external_metrics",
                        lambda *a, **k: ExternalLoad(before_week=4))
    monkeypatch.setattr(nfl_pipeline, "load_usage", lambda *a, **k: seen.append(a) or {})
    nfl_pipeline.NflPipeline(client=_mock_client(), data_dir=tmp_path).run(
        date="2026-10-04", as_of_utc="2026-10-04T16:00:00+00:00",
        reports_dir=tmp_path / "reports",
    )
    assert seen == [(2026, 4)]


def test_naive_as_of_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_utc("2026-10-04T12:00:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        nfl_pipeline.NflPipeline(client=_mock_client(), data_dir=tmp_path).run(
            date="2026-10-04", as_of_utc="2026-10-04T12:00:00", reports_dir=tmp_path / "r")


def test_snapshot_rows_taken_after_as_of_are_ignored(tmp_path):
    prop = {"event_id": "E1", "player_name": "A", "market": "REC_YDS", "position": "OVER",
            "scope": "full_game", "is_consensus_line": True, "line": 50.5, "best_odds": -110}
    append_snapshot(tmp_path, "2026-10-04", [prop], "2026-10-03T14:00:00+00:00")
    append_snapshot(tmp_path, "2026-10-04", [dict(prop, line=55.5)], "2026-10-04T19:30:00+00:00")
    path = snapshot_path(tmp_path, "2026-10-04")
    assert len(load_snapshots(path)) == 2
    admitted = load_snapshots(path, as_of_utc=AS_OF)
    assert [r["line"] for r in admitted] == [50.5]
    (mv,) = movement_index(admitted).values()
    assert mv["snapshots"] == 1 and mv["last_line"] == 50.5


def test_trace_drops_week_bound_rows_after_the_cutoff():
    rows = [{"source": "ngs", "kind": "receiving", "week": w, "player": "Travis Kelce",
             "team": "KC", "targets": 5.0 + w} for w in (2, 3, 5, 18)]
    rows.append({"source": "schedule", "kind": "game", "week": 5})
    prop = {"event_id": "E1", "event_starts_at": "2026-10-04T17:00:00Z", "team": "KC",
            "opponent": "BAL", "player_name": "Travis Kelce", "market": "REC", "position": "OVER",
            "line": 5.5, "books": [{"book": "DraftKings", "odds": -110}], "best_odds": -110,
            "implied_probability": 52.4, "l5_hit_rate": 0.6, "l10_hit_rate": 0.6,
            "is_consensus_line": True, "scope": "full_game"}
    out = build_best_bets(TraceInputs(props=[prop], run_date="2026-10-04", external_metrics=rows,
                                      as_of_utc=AS_OF.isoformat(), before_week=4))
    assert out["audit"]["cutoff"]["external_dropped_after_cutoff"] == 2
    assert out["audit"]["external_sources"]["ngs"]["loaded"] == 2
    assert out["audit"]["external_sources"]["schedule"]["loaded"] == 1
    none = build_best_bets(TraceInputs(props=[prop], run_date="2026-10-04", external_metrics=rows,
                                       as_of_utc=AS_OF.isoformat(), before_week=None))
    assert none["audit"]["external_sources"]["ngs"]["loaded"] == 0  # no cutoff, no weeks


def test_predictions_are_invariant_to_injected_future_rows(tmp_path):
    """Slate B replayed with and without Weeks 4-9/18 rows must publish identical picks."""
    snap.capture(REPO, tmp_path / "future", ["B"], work=tmp_path / "w1", future_rows=True)
    snap.capture(REPO, tmp_path / "pregame", ["B"], work=tmp_path / "w2", future_rows=False)
    card = "files/data/NFL/normalized/nfl_best_bets_2026-10-04.json"
    usage_file = "files/data/NFL/normalized/nfl_player_usage_2026-10-04.json"
    for step in ("B1_full", "B2_schedule_unavailable", "B4_after_kickoff"):
        a = json.loads((tmp_path / "future" / "B" / step / card).read_text())
        b = json.loads((tmp_path / "pregame" / "B" / step / card).read_text())
        assert a["picks"] == b["picks"], step
        assert a["counts"] == b["counts"], step
    a = json.loads((tmp_path / "future" / "B" / "B1_full" / usage_file).read_text())
    b = json.loads((tmp_path / "pregame" / "B" / "B1_full" / usage_file).read_text())
    assert a["players"] == b["players"]
    kelce = next(p for p in a["players"] if p["player"] == "Travis Kelce")
    assert kelce["rec_yds_pg"] == 66.7
