"""Phase 3b (#225): settlement and grading correctness."""

from __future__ import annotations

from datetime import date

import pytest

from outlier_nfl import scorecard as sc
from outlier_nfl.boxscore import NflBoxScoreEvent, player_actual
from outlier_nfl.settle import PredictionSnap, settle_predictions


def _event(players: dict[str, dict[str, float]]) -> NflBoxScoreEvent:
    return NflBoxScoreEvent(provider_event_id="e", event_date=date(2026, 9, 13), away="BAL",
                            home="KC", away_score=20, home_score=27, players=players)


def _snap(market: str, line: float, position: str = "OVER", **kw) -> PredictionSnap:
    base = dict(source="calibrated", event_id="e", event_starts_at=None, slate_date="2026-09-13",
                matchup="BAL @ KC", team="BAL", opponent="KC", player_name="Derrick Henry",
                player_id=None, market=market, position=position, line=line,
                implied_probability=None, confidence_tier=None, calibration_tags=(),
                best_odds=None, window=None)
    base.update(kw)
    return PredictionSnap(**base)


HENRY = {"DERRICKHENRY": {"RUSHING:TD": 1.0, "RECEIVING:TD": 0.0, "RUSHING:YDS": 71.0}}


# F09 -----------------------------------------------------------------------

@pytest.mark.parametrize("market", ["FIRST_TD", "FIRSTTD", "LAST_TOUCHDOWN"])
def test_first_td_is_not_settled_from_anytime_totals(market: str) -> None:
    assert player_actual(market, HENRY["DERRICKHENRY"]) is None
    report = settle_predictions([_snap(market, 0.5)], [_event(HENRY)])
    row = report.rows[0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "unsupported_market")
    assert report.skip_reasons == {"unsupported_market": 1}


def test_anytime_td_still_settles() -> None:
    report = settle_predictions([_snap("ANYTIME_TD", 0.5)], [_event(HENRY)])
    assert (report.rows[0]["status"], report.rows[0]["result"]) == ("settled", "W")


# F14 -----------------------------------------------------------------------




def _rows(name: str, values: dict[int, str], col: str = "receiving_yards") -> list[dict[str, str]]:
    return [{"season_type": "REG", "week": str(w), "team": "KC", "player_display_name": name,
             col: v, "rushing_tds": "0", "receiving_tds": "0"} for w, v in values.items()]


def _signal(name: str, market: str = "REC_YDS", side: str = "UNDER") -> list[dict]:
    return [{"prop_signals": [{"event_id": "e1", "tag": "T", "player_name": name, "team": "KC",
                               "market": market, "side": side}]}]


def test_missing_stat_is_none_not_zero() -> None:
    assert sc._actual({}, "REC_YDS") is None
    assert sc._actual({"receiving_yards": ""}, "REC_YDS") is None
    assert sc._actual({"rushing_tds": "1"}, "ANYTIME_TD") is None
    assert sc._actual({"receiving_yards": "0"}, "REC_YDS") == 0.0


def test_blank_current_stat_is_skipped_not_an_under_win() -> None:
    graded, skipped = sc.grade_signals("d", 3, _signal("A"), _rows("A", {1: "60", 2: "70", 3: ""}))
    assert graded == []
    assert skipped[0]["reason"] == "stat missing from this week's box score"


def test_blank_prior_game_is_left_out_of_the_average() -> None:
    rows = _rows("A", {1: "60", 2: "", 3: "50"})
    graded, skipped = sc.grade_signals("d", 3, _signal("A"), rows)
    assert graded == [] and skipped[0]["reason"] == "fewer than 2 prior games and no line"
    rows = _rows("A", {1: "60", 2: "", 3: "70", 4: "50"})
    graded, _ = sc.grade_signals("d", 4, _signal("A"), rows)
    assert graded[0].prior_avg == 65.0 and graded[0].hit_vs_avg is True


def test_direction_baselines_ignore_missing_stats() -> None:
    rows = _rows("A", {1: "60", 2: "70", 3: ""}) + _rows("B", {1: "60", 2: "70", 3: "80"})
    assert sc.direction_baselines(3, rows, ["REC_YDS"]) == {("REC_YDS", "OVER"): 1.0,
                                                           ("REC_YDS", "UNDER"): 0.0}
