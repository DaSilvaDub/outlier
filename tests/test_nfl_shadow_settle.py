"""Fixture-backed shadow settle for NFL Tier-1 / matchup tags (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outlier_nfl.boxscore import (
    grade_side,
    parse_espn_boxscore_players,
    parse_simplified_events,
    player_actual,
)
from outlier_nfl.settle import (
    load_boxscores,
    load_prediction_snapshot,
    main,
    render_markdown,
    settle_predictions,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nfl" / "settle"


def test_player_actual_prefers_group_qualified_keys():
    stats = {
        "PASSING:YDS": 248.0,
        "RUSHING:YDS": 72.0,
        "RECEIVING:YDS": 53.0,
        "RECEIVING:REC": 5.0,
        "RUSHING:TD": 2.0,
        "RECEIVING:TD": 0.0,
        "YDS": 999.0,  # ambiguous bare key must not win
    }
    assert player_actual("PASS_YDS", stats) == 248.0
    assert player_actual("RUSH_YDS", stats) == 72.0
    assert player_actual("REC_YDS", stats) == 53.0
    assert player_actual("REC", stats) == 5.0
    assert player_actual("PASS_RUSH_YDS", stats) == 320.0
    assert player_actual("ANYTIME_TD", stats) == 2.0
    assert player_actual("UNKNOWN_MARKET", stats) is None


def test_grade_side_over_under_push_and_yes():
    assert grade_side(88.0, 87.5, "OVER") == "W"
    assert grade_side(52.0, 87.5, "OVER") == "L"
    assert grade_side(87.5, 87.5, "OVER") == "P"
    assert grade_side(50.0, 87.5, "UNDER") == "W"
    assert grade_side(2.0, 0.5, "YES") == "W"
    assert grade_side(0.0, 0.5, "YES") == "L"


def test_parse_simplified_and_espn_shaped_boxscore():
    events = load_boxscores(FIXTURES / "boxscores_det_buf.json")
    assert len(events) == 1
    event = events[0]
    assert event.away == "DET" and event.home == "BUF"
    assert event.home_score == 41
    assert "JAHMYRGIBBS" in event.players
    assert event.players["JAHMYRGIBBS"]["RUSHING:YDS"] == 52.0

    espn_shaped = {
        "boxscore": {
            "players": [
                {
                    "statistics": [
                        {
                            "name": "passing",
                            "labels": ["YDS", "TD"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Josh Allen"},
                                    "stats": ["248", "3"],
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }
    players = parse_espn_boxscore_players(espn_shaped)
    assert players["JOSHALLEN"]["PASSING:YDS"] == 248.0
    assert players["JOSHALLEN"]["PASSING:TD"] == 3.0


def test_settle_tier1_fixture_metrics():
    predictions = load_prediction_snapshot(FIXTURES / "predictions_tier1.json", source="tier1")
    events = load_boxscores(FIXTURES / "boxscores_det_buf.json")
    report = settle_predictions(predictions, events)

    assert report.n_predictions == 6
    assert report.n_settled == 6
    assert report.n_skipped == 0
    # Gibbs 52 vs 87.5 O → L; ARSB 142 vs 79.5 O → W; Shakir 5 vs 3.5 O → W;
    # Cook 135 vs 78.5 O → W; Jamo 33 vs 59.5 O → L; Allen 248 vs 249.5 O → L
    assert report.n_wins == 3
    assert report.n_losses == 3
    assert report.n_pushes == 0
    assert report.hit_rate == pytest.approx(0.5)
    assert report.n_scored_prob == 6
    assert report.brier is not None and 0.0 < report.brier < 1.0
    assert report.logloss is not None and report.logloss > 0.0
    assert report.clv["status"] == "blocked"
    assert "clv" in report.blockers
    assert "model_prob" in report.blockers

    tier1 = report.by_tier["TIER_1_ANCHOR"]
    assert tier1["wins"] + tier1["losses"] == 5  # Jamo is TIER_2 in fixture


def test_settle_skips_missing_event_and_unsupported_stat():
    predictions = load_prediction_snapshot(FIXTURES / "predictions_tier1.json", source="tier1")
    # Empty box scores → every row skipped with event_not_found
    report = settle_predictions(predictions, [])
    assert report.n_settled == 0
    assert report.n_skipped == 6
    assert report.skip_reasons["event_not_found"] == 6

    events = parse_simplified_events(
        {
            "events": [
                {
                    "provider_event_id": "x",
                    "event_date": "2026-09-17",
                    "away": "DET",
                    "home": "BUF",
                    "away_score": 31,
                    "home_score": 41,
                    "players": {"Jahmyr Gibbs": {"RECEIVING:REC": 1}},  # no rush yards
                }
            ]
        }
    )
    gibbs_only = [p for p in predictions if p.player_name == "Jahmyr Gibbs"]
    report2 = settle_predictions(gibbs_only, events)
    assert report2.n_skipped == 1
    assert report2.skip_reasons["unsupported_or_missing_stat"] == 1


def test_matchup_snapshot_loads_tagged_rows_only_when_calibrated_filtered(tmp_path: Path):
    matchup = load_prediction_snapshot(FIXTURES / "predictions_matchup.json", source="matchup")
    assert len(matchup) >= 2
    assert all(
        any(tag.startswith("MATCHUP_") for tag in snap.calibration_tags) for snap in matchup
    )


def test_cli_writes_json_and_markdown(tmp_path: Path, capsys):
    out_json = tmp_path / "settle.json"
    out_md = tmp_path / "settle.md"
    rc = main(
        [
            "--predictions",
            str(FIXTURES / "predictions_tier1.json"),
            "--boxscores",
            str(FIXTURES / "boxscores_det_buf.json"),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ]
    )
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["n_settled"] == 6
    assert payload["clv"]["status"] == "blocked"
    md = out_md.read_text(encoding="utf-8")
    assert "hit rate" in md.lower()
    assert "blocked" in md.lower()
    captured = capsys.readouterr().out
    assert "Shadow settle" in captured or "shadow settle" in captured.lower()
    assert render_markdown(settle_predictions([], [])).startswith("#")
