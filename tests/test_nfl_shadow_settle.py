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
    assert report.clv["status"] == "ok"
    assert "clv" not in report.blockers
    # model_p present on at least one fixture row → model_prob blocker cleared
    assert "model_prob" not in report.blockers

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
    assert payload["clv"]["status"] == "ok"
    md = out_md.read_text(encoding="utf-8")
    assert "hit rate" in md.lower()
    assert "clv" in md.lower()
    captured = capsys.readouterr().out
    assert "Shadow settle" in captured or "shadow settle" in captured.lower()
    assert render_markdown(settle_predictions([], [])).startswith("#")


def test_clv_and_model_p_labels_when_fields_present():
    predictions = load_prediction_snapshot(FIXTURES / "predictions_tier1.json", source="tier1")
    assert any(p.close_implied is not None for p in predictions)
    assert any(p.model_p is not None for p in predictions)
    events = load_boxscores(FIXTURES / "boxscores_det_buf.json")
    report = settle_predictions(predictions, events)
    assert report.clv["status"] == "ok"
    assert report.clv["n"] == 6
    assert report.clv["mean_clv_implied_pts"] == pytest.approx(0.0)
    assert "clv" not in report.blockers
    assert report.n_scored_model >= 1
    assert report.brier_model is not None
    assert report.brier_label == "market_implied"
    md = render_markdown(report)
    assert "Brier (model_p)" in md
    assert "mean_implied_pts" in md or "CLV: **ok**" in md


def test_enrich_close_snapshot_best_is_honest(tmp_path: Path):
    from outlier_nfl.enrich_close import enrich_prediction_payload, CLOSE_SOURCE_SNAPSHOT
    from outlier_nfl.calibration import MODEL_P_SOURCE_EMPIRICAL_HIT_RATE

    raw = json.loads((FIXTURES / "predictions_tier1.json").read_text(encoding="utf-8"))
    # Strip close / model fields to simulate legacy artifact
    for row in raw["records"]:
        for key in (
            "close_line",
            "close_odds",
            "close_implied",
            "close_source",
            "model_p",
            "model_p_source",
            "p_model",
        ):
            row.pop(key, None)
    enriched = enrich_prediction_payload(
        raw, mode="snapshot_best", attach_model_p="empirical_hit_rate"
    )
    row0 = enriched["records"][0]
    assert row0["close_source"] == CLOSE_SOURCE_SNAPSHOT
    assert row0["close_odds"] == row0["best_odds"]
    assert row0["close_line"] == row0["line"]
    # Empirical model_p from L10 — never a copy of implied_probability
    assert row0["model_p"] == row0["l10_hit_rate"]
    assert row0["model_p_source"] == MODEL_P_SOURCE_EMPIRICAL_HIT_RATE
    assert abs(float(row0["model_p"]) - float(row0["implied_probability"]) / 100.0) > 1e-6
    assert enriched["model_p_enrichment"]["n_with_model_p"] == len(enriched["records"])


def test_enrich_close_pass_does_not_invent_model_p():
    from outlier_nfl.enrich_close import enrich_prediction_payload

    raw = json.loads((FIXTURES / "predictions_tier1.json").read_text(encoding="utf-8"))
    for row in raw["records"]:
        for key in ("model_p", "model_p_source", "p_model"):
            row.pop(key, None)
    enriched = enrich_prediction_payload(raw, mode="snapshot_best", attach_model_p="pass")
    assert all(r.get("model_p") is None for r in enriched["records"])


def test_nflverse_week_row_maps_into_simplified_schema():
    from outlier_nfl.boxscore_nflverse import _player_stats_from_week_row, events_to_simplified_payload
    from outlier_nfl.boxscore import NflBoxScoreEvent
    from datetime import date

    stats = _player_stats_from_week_row(
        {
            "passing_yards": "250",
            "rushing_yards": "20",
            "receiving_yards": "0",
            "receptions": "0",
            "carries": "3",
            "completions": "22",
            "attempts": "30",
            "passing_tds": "2",
            "rushing_tds": "0",
            "receiving_tds": "0",
            "def_sacks": "0",
            "def_tackles_solo": "0",
            "def_tackle_assists": "0",
            "fg_made": "",
            "pat_made": "",
            "special_teams_tds": "0",
        }
    )
    assert stats["PASSING:YDS"] == 250.0
    assert stats["RUSHING:YDS"] == 20.0
    event = NflBoxScoreEvent(
        provider_event_id="x",
        event_date=date(2026, 9, 20),
        away="CLE",
        home="TB",
        away_score=23,
        home_score=19,
        players={"DESHAUNWATSON": stats},
    )
    payload = events_to_simplified_payload([event])
    assert payload["provider"] == "nflverse"
    assert payload["events"][0]["away"] == "CLE"


def test_clv_blocked_without_close_fields():
    raw = json.loads((FIXTURES / "predictions_tier1.json").read_text(encoding="utf-8"))
    for row in raw["records"]:
        for key in ("close_line", "close_odds", "close_implied", "close_source", "model_p", "model_p_source", "p_model"):
            row.pop(key, None)
    tmp = FIXTURES / "_tmp_no_close.json"
    try:
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        predictions = load_prediction_snapshot(tmp, source="tier1")
        events = load_boxscores(FIXTURES / "boxscores_det_buf.json")
        report = settle_predictions(predictions, events)
        assert report.clv["status"] == "blocked"
        assert "clv" in report.blockers
        assert "model_prob" in report.blockers
        assert report.brier_model is None
    finally:
        if tmp.exists():
            tmp.unlink()


def test_prob_01_allows_extremes_for_brier():
    """Empirical L10=1.0 must still score Brier (logloss clips internally)."""
    from outlier_nfl.settle import _prob_01, _brier, _logloss

    assert _prob_01(1.0) == 1.0
    assert _prob_01(0.0) == 0.0
    assert _prob_01(80.0) == 0.8
    assert _prob_01(101.0) is None
    assert _brier(1.0, won=True) == 0.0
    assert _brier(1.0, won=False) == 1.0
    assert _logloss(1.0, won=False) > 0.0



def test_enrich_laplace_shrinks_overconfident_l10():
    from outlier_nfl.enrich_close import enrich_prediction_payload
    from outlier_nfl.calibration import (
        MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE,
        shrink_hit_rate,
    )

    raw = json.loads((FIXTURES / "predictions_tier1.json").read_text(encoding="utf-8"))
    for row in raw["records"]:
        for key in ("model_p", "model_p_source", "p_model"):
            row.pop(key, None)
    enriched = enrich_prediction_payload(
        raw,
        mode="snapshot_best",
        attach_model_p="empirical_hit_rate_laplace",
        alpha=2.0,
        overwrite_model_p=True,
    )
    row0 = enriched["records"][0]
    l10 = float(row0["l10_hit_rate"])
    assert row0["model_p"] == round(shrink_hit_rate(l10, 10, alpha=2.0), 6)
    assert row0["model_p_source"] == MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE
    assert row0["model_p"] != l10 or l10 not in (0.0, 1.0)


def test_book_close_mode_refuses_without_feed():
    from outlier_nfl.enrich_close import enrich_prediction_payload, main
    import pytest

    raw = json.loads((FIXTURES / "predictions_tier1.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="book_close"):
        enrich_prediction_payload(raw, mode="book_close", attach_model_p="pass")

    with pytest.raises(SystemExit, match="close-feed"):
        main(
            [
                "--predictions",
                str(FIXTURES / "predictions_tier1.json"),
                "--out",
                str(FIXTURES / "_should_not_write.json"),
                "--mode",
                "book_close",
            ]
        )


def test_book_close_feed_stamps_source(tmp_path: Path):
    from outlier_nfl.enrich_close import enrich_prediction_payload, CLOSE_SOURCE_BOOK, load_book_close_feed

    raw = json.loads((FIXTURES / "predictions_tier1.json").read_text(encoding="utf-8"))
    row = raw["records"][0]
    feed = {
        "records": [
            {
                "player_name": row["player_name"],
                "market": row["market"],
                "line": row["line"],
                "position": row["position"],
                "matchup": row["matchup"],
                "event_id": row["event_id"],
                "close_line": row["line"],
                "close_odds": -115,
                "close_implied": 53.488,
            }
        ]
    }
    feed_path = tmp_path / "closes.json"
    feed_path.write_text(json.dumps(feed), encoding="utf-8")
    index = load_book_close_feed(feed_path)
    # Strip existing close so feed must win
    for r in raw["records"]:
        for key in ("close_line", "close_odds", "close_implied", "close_source"):
            r.pop(key, None)
    enriched = enrich_prediction_payload(
        raw, mode="book_close", attach_model_p="pass", book_close_index=index
    )
    matched = [r for r in enriched["records"] if r.get("close_source") == CLOSE_SOURCE_BOOK]
    assert len(matched) == 1
    assert matched[0]["close_odds"] == -115
    # Unmatched rows must not be silently labeled book_close from snapshot
    unmatched = [r for r in enriched["records"] if r.get("close_source") != CLOSE_SOURCE_BOOK]
    assert len(unmatched) == len(enriched["records"]) - 1
    assert all(r.get("close_odds") is None for r in unmatched)


def test_projection_gamelog_rate_fixture():
    from outlier_nfl.projection import (
        project_hit_probability,
        MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE,
        week_stat_value,
    )

    rows = [
        {"rushing_yards": "90", "receiving_yards": "10", "week": "1"},
        {"rushing_yards": "70", "receiving_yards": "5", "week": "2"},
    ]
    assert week_stat_value("RUSH_YDS", rows[0]) == 90.0
    result = project_hit_probability(
        player_name="Jahmyr Gibbs",
        market="RUSH_YDS",
        line=80.5,
        position="OVER",
        week_rows=rows,
        alpha=1.0,
    )
    assert result is not None
    # 1 of 2 overs → (1+1)/(2+2) = 0.5 with α=1
    assert result.model_p == 0.5
    assert result.n_games == 2
    assert result.source == MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE
