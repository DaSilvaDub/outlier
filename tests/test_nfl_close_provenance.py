"""F13 (#228): close provenance/timing and same-threshold CLV."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outlier_nfl.enrich_close import enrich_prediction_payload, index_book_close_records
from outlier_nfl.fetch_odds_close import close_timing_status, map_event_odds_to_close_records
from outlier_nfl.settle import PredictionSnap, settle_predictions

KICKOFF = "2026-09-13T17:00:00Z"


def _book(key: str, price: int, stamp: str | None) -> dict:
    mkt: dict = {"key": "player_pass_yds", "outcomes": [
        {"name": "Over", "description": "Patrick Mahomes", "price": price, "point": 250.5}]}
    if stamp:
        mkt["last_update"] = stamp
    return {"key": key, "markets": [mkt]}


def _event(*books: dict, **extra) -> dict:
    return {"id": "oddsapi-bal-kc", "commence_time": KICKOFF, "home_team": "Kansas City Chiefs",
            "away_team": "Baltimore Ravens", "bookmakers": list(books), **extra}


@pytest.mark.parametrize("stamp,status", [
    ("2026-09-13T16:55:00Z", "verified"),
    ("2026-09-13T11:00:00Z", "early_quote"),
    ("2026-09-13T17:10:00Z", "after_kickoff"),
    (None, "unknown_timing"),
])
def test_only_a_verified_pre_kickoff_quote_is_book_close(stamp, status):
    [row] = map_event_odds_to_close_records(_event(_book("dk", -110, stamp)))
    assert row["close_status"] == status
    assert row["quote_time"] == stamp
    assert (row["close_source"] == "book_close") is (status == "verified")
    assert close_timing_status(stamp, KICKOFF) == status


def test_a_better_live_price_cannot_outrank_the_verified_close():
    [row] = map_event_odds_to_close_records(_event(
        _book("dk", -110, "2026-09-13T16:55:00Z"), _book("fd", 150, "2026-09-13T17:10:00Z")))
    assert (row["bookmaker"], row["close_odds"], row["close_source"]) == ("dk", -110, "book_close")


def test_historical_snapshot_time_is_kept_as_capture_time():
    [row] = map_event_odds_to_close_records(_event(
        _book("dk", -110, "2026-09-13T16:55:00Z"),
        _historical_meta={"timestamp": "2026-09-13T16:58:00Z"}))
    assert row["captured_at"] == "2026-09-13T16:58:00Z"


PRED = {"player_name": "Patrick Mahomes", "market": "PASS_YDS", "line": 250.5,
        "position": "OVER", "matchup": "BAL @ KC", "event_id": "e1", "best_odds": -110,
        "implied_probability": 52.38}


@pytest.mark.parametrize("source", ["pregame_snapshot_best_odds", "synthetic_moved_close",
                                    "odds_capture", None])
def test_supplied_non_book_source_is_never_attached_as_book_close(source):
    feed = {k: PRED[k] for k in ("player_name", "market", "line", "position", "matchup",
                                 "event_id")}
    feed.update(close_line=250.5, close_odds=-125, close_implied=55.56)
    if source:
        feed["close_source"] = source
    out = enrich_prediction_payload({"records": [dict(PRED)]}, mode="book_close",
                                    attach_model_p="pass",
                                    book_close_index=index_book_close_records([feed]))
    rec = out["records"][0]
    assert rec["close_source"] is None and rec["close_odds"] is None
    assert rec["close_skip_reason"] == "unverified_close_source"
    assert out["close_enrichment"]["n_unverified_close_source"] == 1


def test_synthetic_moved_feed_keeps_its_label(tmp_path: Path):
    from outlier_nfl.close_feed import snapshot_rows_to_moved_close_feed

    path = tmp_path / "p.json"
    path.write_text(json.dumps({"records": [PRED]}), encoding="utf-8")
    assert snapshot_rows_to_moved_close_feed(path)[0]["close_source"] == "synthetic_moved_close"


def _snap(line: float, close_line: float | None, close_implied: float) -> PredictionSnap:
    return PredictionSnap(
        source="t", event_id="e1", event_starts_at="2026-09-13T13:00:00-04:00",
        slate_date="2026-09-13", matchup="BAL @ KC", team="BAL", opponent="KC",
        player_name="Derrick Henry", player_id=None, market="RUSH_YDS", position="OVER",
        line=line, best_odds=-110, implied_probability=52.38, confidence_tier=None,
        calibration_tags=(), close_line=close_line, close_implied=close_implied,
        close_source="book_close")


EVENT = {"provider_event_id": "x", "event_date": "2026-09-13", "away": "BAL", "home": "KC", "away_score": 20, "home_score": 27,
         "players": {"Derrick Henry": {"RUSHING:YDS": 71}}}


def _settle(*snaps: PredictionSnap):
    from outlier_nfl.settle import load_boxscores
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.json"
        p.write_text(json.dumps({"events": [EVENT]}), encoding="utf-8")
        return settle_predictions(list(snaps), load_boxscores(p))


def test_moved_line_gives_no_price_clv_and_reports_line_movement():
    report = _settle(_snap(87.5, 187.5, 59.77))
    row = report.rows[0]
    assert row["clv_implied_pts"] is None
    assert row["close_line_move"] == 100.0
    assert report.clv["n"] == 0
    assert report.clv["n_excluded_line_mismatch"] == 1
    assert report.clv["n_line_moved"] == 1 and report.clv["mean_line_move"] == 100.0


def test_fraction_and_percent_close_units_compare_on_one_scale():
    report = _settle(_snap(65.5, 65.5, 0.5455), _snap(65.5, 65.5, 55.56))
    assert [round(r["clv_implied_pts"], 2) for r in report.rows] == [2.17, 3.18]


def test_unknown_close_line_gives_no_price_clv():
    report = _settle(_snap(65.5, None, 55.0))
    assert report.rows[0]["clv_implied_pts"] is None
    assert report.clv["n_excluded_close_line_unknown"] == 1
