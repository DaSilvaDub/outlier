"""Traced NFL best bets: six pillars, verdict gating, snapshots, audit, weekly dates."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from outlier_nfl.best_bets import (
    CONTRADICTS,
    ESTIMATED,
    MISSING,
    PILLARS,
    PROVISIONAL,
    REJECTED,
    STALE,
    VALIDATED,
    VERIFIED,
    TraceInputs,
    build_best_bets,
    merge_payloads,
    render_best_bets_markdown,
)
from outlier_nfl.pipeline import NflPipeline, load_injury_report
from outlier_nfl.snapshots import (
    append_snapshot,
    load_snapshots,
    movement_index,
    snapshot_path,
    week_start,
)
from outlier_nfl.weekly import remaining_week_dates

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"

GAME_DAY = "2026-10-04"
KICKOFF = "2026-10-04T17:00:00Z"  # 1:00 PM ET Sunday


def _prop(position: str = "OVER", odds: int = -110, implied: float = 52.381, **kw):
    row = {
        "event_id": "E1",
        "event_starts_at": KICKOFF,
        "matchup": "DAL @ PHI",
        "team": "PHI",
        "opponent": "DAL",
        "player_name": "A.J. Brown",
        "player_id": "p1",
        "market": "REC_YDS",
        "market_raw": "Receiving Yards",
        "position": position,
        "line": 70.5,
        "books": [{"book": "DraftKings", "odds": odds, "odds_raw": str(odds)}],
        "best_odds": odds,
        "implied_probability": implied,
        "l5_hit_rate": 0.8,
        "l10_hit_rate": 0.7,
        "l20_hit_rate": None,
        "season_hit_rate": 0.65,
        "scope": "full_game",
        "is_consensus_line": True,
        "confidence_tier": "TIER_2_STRONG",
        "calibration_tags": [],
    }
    row.update(kw)
    return row


def _external():
    records = []
    for week, targets in ((1, 9), (2, 10), (3, 8)):
        records.append({"source": "ngs", "kind": "receiving", "week": week, "player": "A.J. Brown",
                        "team": "PHI", "targets": targets, "avg_separation": 3.4})
        for name, sep in (("Other WR", 2.6), ("Third WR", 2.9), ("Fourth WR", 3.0)):
            records.append({"source": "ngs", "kind": "receiving", "week": week, "player": name,
                            "team": "NYG", "targets": 5, "avg_separation": sep})
    # DAL leaks pass EPA; other teams spread around zero.
    for team, epa in (("DAL", 0.25), ("NYG", 0.0), ("SF", -0.1), ("KC", -0.05), ("PHI", 0.02)):
        for week in (1, 2, 3):
            records.append({"source": "pbp", "kind": "team_defense", "team": team, "week": week,
                            "plays": 60, "pass_epa_per_play": epa, "rush_epa_per_play": 0.0,
                            "epa_per_play": epa / 2})
    records.append({"source": "schedule", "week": 5, "home_team": "PHI", "away_team": "DAL"})
    return records


def _movement(open_line=69.5, last_line=72.5, snapshots=2):
    from outlier_nfl.snapshots import prop_key

    return {
        prop_key(_prop()): {
            "snapshots": snapshots, "open_taken_at": "2026-09-29T14:00:00Z", "open_line": open_line,
            "open_odds": -110, "open_implied": 52.4, "last_taken_at": "2026-10-04T14:30:00Z",
            "last_line": last_line, "last_odds": -110, "last_implied": 52.4,
        }
    }


def _inputs(props=None, run_date=GAME_DAY, movement=None, inactive=None, **kw):
    base = dict(
        props=props if props is not None else [_prop(), _prop("UNDER", odds=-110, implied=52.381)],
        run_date=run_date,
        scripts=[{"event_id": "E1", "home_team": "PHI", "away_team": "DAL", "prop_signals": []}],
        external_metrics=_external(),
        usage_players=[{"player": "A.J. Brown", "team": "PHI", "games": 3, "target_share": 0.27,
                        "carry_share": 0.0, "targets_pg": 9.0, "carries_pg": 0.0}],
        weather=[{"event_id": "E1", "venue": "outdoor", "wind_mph": 6.0, "pass_adjustment": 0.0,
                  "tags": []}],
        inactive_by_team={"PHI": [], "DAL": ["Trevon Diggs"]} if inactive is None else inactive,
        tapes={"DAL": {"pass_rush": 58.0, "pressure_rate": 0.31}},
        movement=_movement() if movement is None else movement,
    )
    base.update(kw)
    return TraceInputs(**base)


def _pick(payload, position="OVER"):
    return next(p for p in payload["picks"] if p["position"] == position)


def test_fully_traced_pick_validates_on_game_day():
    payload = build_best_bets(_inputs())
    pick = _pick(payload)
    statuses = {name: pick["pillars"][name]["status"] for name in PILLARS}
    assert statuses == {name: VERIFIED for name in PILLARS}, statuses
    assert pick["verdict"] == VALIDATED
    assert pick["rank"] == 1
    assert pick["edge"] > 0 and pick["stake_fraction"] > 0
    # Every external input reached the pick and is in its evidence.
    assert pick["pillars"]["opportunity"]["evidence"]["ngs_volume_weeks"] == 3
    assert pick["pillars"]["opportunity"]["evidence"]["usage_target_share"] == 0.27
    assert pick["pillars"]["matchup"]["evidence"]["opp_epa_z"] > 0
    assert pick["pillars"]["matchup"]["evidence"]["ngs_avg_separation"] == 3.4
    assert pick["pillars"]["matchup"]["evidence"]["separation_delta"] > 0  # applied, not just shown
    assert pick["pillars"]["injury_weather"]["evidence"]["opponent_inactive"] == ["Trevon Diggs"]
    assert pick["pillars"]["price"]["evidence"]["devig"] == "two_sided"
    assert pick["final_p"] > pick["base_p"]  # leaky DAL pass D + market moving toward OVER
    steps = [t["step"] for t in pick["trace"]]
    assert steps[0] == "base" and steps[-1] == "final_p"
    audit = payload["audit"]
    assert audit["external_sources"]["ngs"]["consumed_keys"] == 3
    assert audit["external_sources"]["pbp"]["consumed_keys"] == 1
    assert audit["usage_profiles"]["consumed"] == 1


def test_early_week_run_is_stale_and_provisional():
    pick = _pick(build_best_bets(_inputs(run_date="2026-09-29")))
    assert pick["pillars"]["injury_weather"]["status"] == STALE
    assert pick["verdict"] == PROVISIONAL
    assert pick["stake_fraction"] == 0.0
    assert "injury_weather:STALE" in pick["gaps"]


def test_single_snapshot_leaves_market_missing():
    pick = _pick(build_best_bets(_inputs(movement=_movement(snapshots=1))))
    assert pick["pillars"]["market"]["status"] == MISSING
    assert pick["verdict"] == PROVISIONAL


def test_market_moving_against_side_rejects():
    pick = _pick(build_best_bets(_inputs(movement=_movement(open_line=72.5, last_line=69.5))))
    assert pick["pillars"]["market"]["status"] == CONTRADICTS
    assert pick["verdict"] == REJECTED


def test_inactive_player_rejected():
    pick = _pick(build_best_bets(_inputs(inactive={"PHI": ["A.J. Brown"], "DAL": []})))
    assert pick["pillars"]["injury_weather"]["status"] == CONTRADICTS
    assert pick["verdict"] == REJECTED


def test_missing_injury_report_is_not_treated_as_clean():
    pick = _pick(build_best_bets(_inputs(inactive_by_team=None)))
    assert pick["pillars"]["injury_weather"]["status"] == MISSING
    assert pick["verdict"] == PROVISIONAL


def test_no_edge_at_price_rejects():
    props = [_prop(odds=-400, implied=80.0), _prop("UNDER", odds=300, implied=25.0)]
    pick = _pick(build_best_bets(_inputs(props=props)))
    assert pick["pillars"]["price"]["status"] == CONTRADICTS
    assert pick["verdict"] == REJECTED


def test_one_sided_price_is_estimated_not_verified():
    pick = _pick(build_best_bets(_inputs(props=[_prop()])))
    assert pick["pillars"]["price"]["status"] == ESTIMATED
    assert pick["pillars"]["price"]["evidence"]["devig"] == "assumed_-110_overround"
    assert pick["verdict"] == PROVISIONAL


def test_weather_signal_flows_once_and_misnamed_market_is_orphaned():
    scripts = [{
        "event_id": "E1", "home_team": "PHI", "away_team": "DAL",
        "prop_signals": [
            {"event_id": "E1", "player_name": "A.J. Brown", "team": "PHI", "market": "REC_YDS",
             "side": "UNDER", "tag": "WEATHER_WIND_HIGH", "reason": "wind", "confidence": "HIGH",
             "volume_adjustment": -0.10},
            {"event_id": "E1", "player_name": "Jalen Hurts", "team": "PHI",
             "market": "LONGEST_PASSING_COMPLETION", "side": "UNDER", "tag": "WEATHER_WIND_HIGH",
             "reason": "wind", "confidence": "HIGH", "volume_adjustment": -0.10},
        ],
    }]
    payload = build_best_bets(_inputs(scripts=scripts))
    over = _pick(payload)
    weather = over["pillars"]["injury_weather"]
    assert [s["tag"] for s in weather["evidence"]["signals"]] == ["WEATHER_WIND_HIGH"]
    assert weather["delta"] == pytest.approx(-0.025)
    assert weather["status"] == CONTRADICTS
    under = _pick(payload, "UNDER")
    assert under["pillars"]["injury_weather"]["delta"] == pytest.approx(0.025)
    orphans = payload["audit"]["signals"]["orphaned"]
    assert [(o["market"], o["reason"]) for o in orphans] == [
        ("LONGEST_PASSING_COMPLETION", "no_prop_offered")
    ]
    assert "ORPHAN WEATHER_WIND_HIGH Jalen Hurts" in render_best_bets_markdown(payload, title="t")


def test_orphan_audit_flags_market_name_mismatch_for_priced_player():
    hurts = _prop(player_name="Jalen Hurts", market="LONG_PASS", line=38.5)
    scripts = [{
        "event_id": "E1", "home_team": "PHI", "away_team": "DAL",
        "prop_signals": [
            {"event_id": "E1", "player_name": "Jalen Hurts", "team": "PHI",
             "market": "LONGEST_PASSING_COMPLETION", "side": "UNDER", "tag": "WEATHER_WIND",
             "reason": "wind", "confidence": "MEDIUM", "volume_adjustment": -0.06},
        ],
    }]
    payload = build_best_bets(_inputs(props=[_prop(), hurts], scripts=scripts))
    (orphan,) = payload["audit"]["signals"]["orphaned"]
    assert orphan["reason"] == "market_not_joined"
    assert orphan["player_markets"] == ["LONG_PASS"]
    assert "[market_not_joined]" in render_best_bets_markdown(payload, title="t")


def test_regression_signal_counts_in_historical_pillar_only():
    scripts = [{
        "event_id": "E1", "home_team": "PHI", "away_team": "DAL",
        "prop_signals": [
            {"event_id": "E1", "player_name": "A.J. Brown", "team": "PHI", "market": "REC_YDS",
             "side": "UNDER", "tag": "EFFICIENCY_HOT", "reason": "hot", "confidence": "MEDIUM",
             "volume_adjustment": -0.10},
            {"event_id": "E1", "player_name": "A.J. Brown", "team": "PHI", "market": "REC_YDS",
             "side": "OVER", "tag": "MATCHUP_COVERAGE_LEAK", "reason": "leak", "confidence": "HIGH",
             "volume_adjustment": 0.15},
        ],
    }]
    over = _pick(build_best_bets(_inputs(scripts=scripts)))
    assert over["pillars"]["historical"]["evidence"]["overridden_signals"] == [
        "OVERRIDDEN_MATCHUP_COVERAGE_LEAK"
    ]
    assert "signals" not in over["pillars"]["matchup"]["evidence"]
    assert over["pillars"]["historical"]["status"] == CONTRADICTS


def test_total_adjustment_is_capped():
    big = [{"event_id": "E1", "player_name": "A.J. Brown", "team": "PHI", "market": "REC_YDS",
            "side": "OVER", "tag": tag, "reason": "x", "confidence": "HIGH", "volume_adjustment": 0.5}
           for tag in ("VACATED_TARGETS", "MATCHUP_COVERAGE_LEAK")]
    scripts = [{"event_id": "E1", "home_team": "PHI", "away_team": "DAL", "prop_signals": big}]
    over = _pick(build_best_bets(_inputs(scripts=scripts)))
    for name in PILLARS[:-1]:
        assert abs(over["pillars"][name]["delta"]) <= 0.03 + 1e-9
    assert over["final_p"] - over["base_p"] <= 0.08 + 1e-9


def test_merge_payloads_reranks_across_dates():
    a = build_best_bets(_inputs())
    b = build_best_bets(_inputs(run_date="2026-09-29"))
    merged = merge_payloads([b, a], run_date=GAME_DAY)
    assert merged["picks"][0]["verdict"] == VALIDATED
    assert [p["rank"] for p in merged["picks"]] == list(range(1, len(merged["picks"]) + 1))
    assert merged["settings"] == a["settings"]
    b["settings"] = {**b["settings"], "pillar_cap": 0.05}
    with pytest.raises(ValueError):
        merge_payloads([a, b], run_date=GAME_DAY)


def test_epa_is_weighted_by_pass_plays_not_all_plays():
    from outlier_nfl.best_bets import _Sources

    rows = [
        {"source": "pbp", "kind": "team_defense", "team": "DAL", "week": 1, "plays": 60,
         "pass_rate": 0.9, "pass_epa_per_play": 0.4, "rush_epa_per_play": 0.0, "epa_per_play": 0.1},
        {"source": "pbp", "kind": "team_defense", "team": "DAL", "week": 2, "plays": 60,
         "pass_rate": 0.1, "pass_epa_per_play": -0.4, "rush_epa_per_play": 0.0, "epa_per_play": 0.1},
    ]
    src = _Sources(TraceInputs(props=[], run_date=GAME_DAY, external_metrics=rows))
    # 54 pass plays at +0.4 and 6 at -0.4: (54*0.4 - 6*0.4) / 60 = 0.32 (all-plays weighting gives 0.0)
    assert src.defense["DAL"]["pass_epa_per_play"] == pytest.approx(0.32)


def test_ngs_audit_checks_the_markets_own_kind():
    ext = [r for r in _external() if not (r.get("source") == "ngs" and r.get("player") == "A.J. Brown")]
    ext.append({"source": "ngs", "kind": "rushing", "week": 1, "player": "A.J. Brown", "team": "PHI",
                "rush_attempts": 1})
    payload = build_best_bets(_inputs(external_metrics=ext, usage_players=[]))
    assert payload["audit"]["candidates_without_ngs"] == ["A.J. Brown"]
    assert _pick(payload)["pillars"]["opportunity"]["status"] == MISSING


# --- snapshots --------------------------------------------------------------


@pytest.mark.parametrize(
    "day, start",
    [("2026-09-29", "2026-09-29"), ("2026-10-01", "2026-09-29"), ("2026-10-04", "2026-09-29"),
     ("2026-10-05", "2026-09-29"), ("2026-10-06", "2026-10-06")],
)
def test_week_starts_on_tuesday(day, start):
    assert week_start(day).isoformat() == start


def test_snapshots_track_first_seen_vs_latest(tmp_path):
    append_snapshot(tmp_path, GAME_DAY, [_prop(line=69.5), _prop(is_consensus_line=False, line=80.5)],
                    "2026-09-29T14:00:00Z")
    append_snapshot(tmp_path, GAME_DAY, [_prop(line=72.5)], "2026-10-04T14:30:00Z")
    path = snapshot_path(tmp_path, GAME_DAY)
    assert path.name == "nfl_prop_snapshots_2026-09-29.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"torn": ')  # crashed writer
    rows = load_snapshots(path)
    assert len(rows) == 2  # alt line skipped, torn line skipped
    (move,) = movement_index(rows).values()
    assert (move["snapshots"], move["open_line"], move["last_line"]) == (2, 69.5, 72.5)
    assert move["open_books"] == {"draftkings": -110}


# --- weekly + pipeline ------------------------------------------------------


def test_remaining_week_dates_from_today_to_monday():
    events = [{"scheduledTime": t} for t in (
        "2026-10-02T00:15:00Z",  # Thu 8:15 PM ET
        "2026-10-04T17:00:00Z",  # Sun
        "2026-10-06T00:15:00Z",  # Mon 8:15 PM ET
        "2026-10-09T00:15:00Z",  # next Thu
    )]
    assert remaining_week_dates(events, date(2026, 9, 29)) == ["2026-10-01", "2026-10-04", "2026-10-05"]
    assert remaining_week_dates(events, date(2026, 10, 4)) == ["2026-10-04", "2026-10-05"]


def test_load_injury_report_distinguishes_missing_from_empty(tmp_path):
    assert load_injury_report(tmp_path) is None
    tape = tmp_path / "tape"
    tape.mkdir()
    report = tape / "prior_week.json"
    # A tape whose injury fetch failed still carries an empty "inactive" block.
    report.write_text(json.dumps({"teams": {}, "inactive": {}, "injury_report_loaded": False}),
                      encoding="utf-8")
    assert load_injury_report(tmp_path) is None
    report.write_text(json.dumps({"teams": {}, "inactive": {}}), encoding="utf-8")  # pre-marker tape
    assert load_injury_report(tmp_path) is None
    report.write_text(json.dumps({"teams": {}, "inactive": {}, "injury_report_loaded": True}),
                      encoding="utf-8")
    assert load_injury_report(tmp_path) == {}


def test_pipeline_run_writes_snapshot_and_traced_card(tmp_path):
    pipeline = NflPipeline(data_dir=tmp_path)
    summary = pipeline.run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR, write_latest=False)
    normalized = tmp_path / "NFL" / "normalized"
    payload = json.loads((normalized / "nfl_best_bets_2026-09-13.json").read_text(encoding="utf-8"))
    assert summary["best_bets_counts"] == payload["counts"]
    assert not (normalized / "nfl_best_bets_latest.json").exists()
    assert (normalized / "nfl_best_bets_2026-09-13.md").exists()
    assert "audit" in payload and payload["picks"] == sorted(payload["picks"], key=lambda p: p["rank"])
    for pick in payload["picks"]:
        assert set(pick["pillars"]) == set(PILLARS)
        assert pick["verdict"] != VALIDATED  # one snapshot: market movement is never verified
    snapshots = list((tmp_path / "NFL" / "snapshots").glob("nfl_prop_snapshots_*.jsonl"))
    assert len(snapshots) == 1


def test_trace_failure_removes_stale_card_and_weekly_run_fails(tmp_path, monkeypatch):
    import outlier_nfl.pipeline as pipeline_mod
    from outlier_nfl.weekly import run_week

    normalized = tmp_path / "NFL" / "normalized"
    normalized.mkdir(parents=True)
    stale = normalized / "nfl_best_bets_2026-09-13.json"
    stale.write_text(json.dumps({"picks": [], "updated_at": "old"}), encoding="utf-8")

    def boom(_inputs):
        raise RuntimeError("trace exploded")

    monkeypatch.setattr(pipeline_mod, "build_best_bets", boom)
    pipeline = NflPipeline(data_dir=tmp_path)
    summary = pipeline.run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR, write_latest=False)
    assert "trace exploded" in summary["best_bets_error"]
    assert not stale.exists()
    events = json.loads((FIXTURES_DIR / "schedule.json").read_text(encoding="utf-8"))["events"]
    with pytest.raises(RuntimeError, match="trace exploded"):
        run_week(pipeline, date(2026, 9, 13), events=events, offline_fixtures_dir=FIXTURES_DIR,
                 reports_dir=tmp_path / "reports")
