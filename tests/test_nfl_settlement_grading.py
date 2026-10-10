"""Phase 3b (#225): settlement and grading correctness."""

from __future__ import annotations

from datetime import date

import pytest

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
