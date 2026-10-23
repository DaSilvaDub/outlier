"""Shadow team run end to end on fixtures: nothing live, nothing paid, nothing actionable."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest

from outlier_nba import team_run
from outlier_nba.ratings.model import TeamParams
from outlier_nba.track import promotion
from tests import nba_fixtures as fx
from tests.nba_synth import synth_history

NOW = fx.STARTED_TIP + timedelta(minutes=30)  # 17:30Z on 2026-10-21, before the 23:30Z tip
PARAMS = TeamParams(lam=20.0, tau=1e6, carry=0.5)


@pytest.fixture(scope="module")
def games():
    return synth_history()


@pytest.fixture
def promo(tmp_path: Path) -> Path:
    path = tmp_path / "promo.json"
    promotion.initialize(path)
    return path


def _receipt(**kw):
    """A refresh receipt from the evening of 2026-10-20 that saw no 2026-27 final yet."""
    base = {
        "fetched_at": "2026-10-21T12:00:00+00:00",
        "covered_days": ["20261017", "20261018", "20261019", "20261020", "20261021"],
        "final_games_by_season": {},
    }
    return {**base, **kw}


def _run(tmp_path, games, promo, **kw):
    kw.setdefault("feed_receipt_loader", lambda: None)
    client = kw.pop("client", None) or fx.FakeClient()
    return team_run.run_team_shadow(
        client,
        games_loader=lambda: games,
        nba_dir=tmp_path / "nba",
        clock=fx.clock_at(NOW),
        params=PARAMS,
        n_draws=2000,
        promotion_path=promo,
        **kw,
    )


def _rows(tmp_path: Path, result) -> list[dict[str, str]]:
    path = tmp_path / "nba" / "runs" / result.run_id / "team_shadow.csv"
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def test_shadow_run_prices_every_team_market_and_stays_shadow(tmp_path, games, promo) -> None:
    result = _run(tmp_path, games, promo)
    assert result.exit_code == 0, result.withheld_reason
    rows = _rows(tmp_path, result)
    assert {r["market"] for r in rows} >= {"ML", "SPREAD", "TOTAL"}
    assert all(
        r["board"] == "SHADOW" and r["units"] == "0" and r["actionable"] == "false" for r in rows
    )
    priced = [r for r in rows if r["model_prob"]]
    assert len(priced) >= len(rows) * 0.8
    for r in priced:
        assert 0.0 < float(r["model_prob"]) < 1.0
        w, p, loss = float(r["model_win"]), float(r["model_push"]), float(r["model_loss"])
        assert w + p + loss == pytest.approx(1.0, abs=1e-5)


def test_model_weight_zero_means_final_equals_market(tmp_path, games, promo) -> None:
    rows = _rows(tmp_path, _run(tmp_path, games, promo))
    both = [r for r in rows if r["market_prob"] and r["final_prob"]]
    assert both
    for r in both:
        assert float(r["model_weight"]) == 0.0
        assert r["final_prob"] == r["market_prob"]


class _OneSidedClient(fx.FakeClient):
    """Adds an OVER-only alternate total: nothing to devig against."""

    def fetch_event_markets(self, event_id: str, market_type: str):
        out = super().fetch_event_markets(event_id, market_type)
        if event_id == fx.EV1 and market_type == "GAMELINE":
            alt = fx._market(
                fx.EV1, "m-alt-total", "GAMELINE", "TOTAL",
                [fx._game_outcome("o-alt-over-240", "OVER", 240.5, {"DRAFTKINGS": +400})],
            )  # fmt: skip
            out["markets"] = [*out["markets"], alt]
        return out


def test_row_with_no_market_probability_has_no_final_probability(tmp_path, games, promo) -> None:
    result = team_run.run_team_shadow(
        _OneSidedClient(), games_loader=lambda: games, nba_dir=tmp_path / "nba",
        clock=fx.clock_at(NOW), params=PARAMS, n_draws=2000, promotion_path=promo,
    )  # fmt: skip
    rows = _rows(tmp_path, result)
    missing = [r for r in rows if r["outcome_id"] == "o-alt-over-240"]
    assert len(missing) == 1
    r = missing[0]
    assert r["model_prob"] != ""  # the model can price it; the market cannot anchor it
    assert r["final_prob"] == "" and "MISSING_MARKET_PROB" in r["flags"]
    assert r["edge_final"] == "" and r["units"] == "0"


def _rec(team):
    from outlier_nba.ingest.outlier import OutcomeRecord

    tip = fx.TIP1
    return OutcomeRecord(
        "TEAM", "e", "m", "o", "TEAM_TOTAL", "OVER", 112.5, (), tip, "NYK", "BOS", team=team
    )


def test_team_total_side_is_mapped_to_the_right_team() -> None:
    assert team_run._side_for_team(_rec("BOS")) == "HOME"
    assert team_run._side_for_team(_rec("NYK")) == "AWAY"
    assert team_run._side_for_team(_rec("Boston Celtics")) == "HOME"
    assert team_run._side_for_team(_rec("MIA")) is None
    assert team_run._side_for_team(_rec(None)) is None


def test_team_total_rows_are_priced_for_the_quoted_team(tmp_path, games, promo) -> None:
    rows = _rows(tmp_path, _run(tmp_path, games, promo))
    tt = [r for r in rows if r["market"] == "TEAM_TOTAL"]
    assert tt and all(r["team"] == "BOS" and r["model_prob"] for r in tt)
    over = next(r for r in tt if r["side"] == "OVER")
    under = next(r for r in tt if r["side"] == "UNDER")
    assert float(over["model_win"]) + float(under["model_win"]) + float(over["model_push"]) == (
        pytest.approx(1.0, abs=1e-5)
    )


def test_ledger_and_published_pointer_untouched(tmp_path, games, promo) -> None:
    result = _run(tmp_path, games, promo)
    assert not (tmp_path / "ledger.sqlite3").exists()
    manifest = json.loads(
        (tmp_path / "nba" / "runs" / result.run_id / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["context"]["mode"] in ("live", "fixture")


def _stale_in_season(games):
    """The 2026-27 season has started but its newest result is 11+ days old."""
    return [g for g in games if g.season != "2026-27" or g.start_utc.day <= 9]


def test_stale_in_season_results_withhold_publication(tmp_path, games, promo) -> None:
    result = _run(tmp_path, _stale_in_season(games), promo)
    assert result.exit_code == 3 and not result.published
    assert "team_state" in (result.withheld_reason or "")


def test_season_opener_runs_on_last_seasons_final_ratings(tmp_path, games, promo) -> None:
    opener = [g for g in games if g.season != "2026-27"]  # no result from this season yet
    result = _run(tmp_path, opener, promo, feed_receipt_loader=_receipt)
    assert result.exit_code == 0, result.withheld_reason
    state = json.loads(
        (tmp_path / "nba" / "runs" / result.run_id / "team_model_state.json").read_text("utf-8")
    )
    assert state["prior_only_season_opener"] is True


def test_prior_only_state_is_refused_once_the_opening_window_has_passed(games) -> None:
    opener = [g for g in games if g.season != "2026-27"]
    with pytest.raises(team_run.TeamStateError, match="refresh the store"):
        team_run.compute_team_state(opener, "2026-12-15", PARAMS, "given", feed_receipt=_receipt())
    ok = team_run.compute_team_state(opener, "2026-10-21", PARAMS, "given", feed_receipt=_receipt())
    assert ok.prior_only


def test_season_opener_without_a_verifying_refresh_fails_closed(games) -> None:
    opener = [g for g in games if g.season != "2026-27"]
    bad_receipts = [
        None,
        {},
        _receipt(fetched_at="2026-10-15T12:00:00+00:00"),  # refresh is days old
        _receipt(fetched_at="garbage"),
        _receipt(covered_days=["20261001"]),  # never looked at the previous day
        _receipt(final_games_by_season={"2026-27": 4}),  # the season has games the store lacks
    ]
    for receipt in bad_receipts:
        with pytest.raises(team_run.TeamStateError, match="verified refresh"):
            team_run.compute_team_state(opener, "2026-10-21", PARAMS, "given", feed_receipt=receipt)


def test_a_stopped_feed_after_the_season_began_cannot_pass_as_an_opener(tmp_path, games, promo):
    """Store has no 2026-27 results, but the refresh says 2026-27 games were played."""
    opener = [g for g in games if g.season != "2026-27"]
    stalled = _receipt(final_games_by_season={"2026-27": 12})
    result = _run(tmp_path, opener, promo, feed_receipt_loader=lambda: stalled)
    assert result.exit_code == 3 and "team_state" in (result.withheld_reason or "")


def test_strict_boolean_parsing() -> None:
    for value, want in [
        (True, True), (False, False), (1, True), (0, False), ("true", True), ("False", False),
        (" no ", False), ("Y", True), (2, None), ("maybe", None), ([], None), (None, None),
    ]:  # fmt: skip
        assert team_run.parse_flag(value) is want, value
    assert team_run.event_neutral({"neutralSite": "false"}) is False
    assert team_run.event_neutral({"neutralSite": "true"}) is True
    assert team_run.event_neutral({"neutralSite": "unknown"}) is None
    assert team_run.event_neutral({}) is None


def test_string_false_does_not_make_a_game_neutral(tmp_path, games, promo) -> None:
    sched = fx.schedule()
    for ev in sched["events"]:
        ev["neutralSite"] = "false"
    rows = _rows(tmp_path, _run(tmp_path, games, promo, client=fx.FakeClient(sched=sched)))
    ml = next(r for r in rows if r["market"] == "ML" and r["side"] == "HOME")
    assert "HOME_COURT_ASSUMED" not in ml["flags"]


def test_team_identity_accepts_every_field_the_normalizer_does() -> None:
    assert team_run.side_team({"alias": "BOS"}) == "BOS"
    assert team_run.side_team({"abbr": "NY"}) == "NYK"
    assert team_run.side_team({"abbreviation": "GS"}) == "GSW"
    assert team_run.side_team({"teamName": "Knicks"}) == "NYK"
    assert team_run.side_team({"name": "Celtics"}) == "BOS"
    assert team_run.side_team({"market": "Boston", "name": "Celtics"}) == "BOS"
    assert team_run.side_team({"alias": "???"}) is None
    assert team_run.side_team(None) is None


def test_events_without_an_alias_are_still_simulated(tmp_path, games, promo) -> None:
    sched = fx.schedule()
    for ev in sched["events"]:
        for side in ("home", "away"):
            ev[side] = {"abbr": ev[side]["alias"], "teamId": ev[side]["teamId"]}
    result = _run(tmp_path, games, promo, client=fx.FakeClient(sched=sched))
    assert result.exit_code == 0, result.withheld_reason
    rows = _rows(tmp_path, result)
    assert any(r["model_prob"] for r in rows)


def test_stage_fails_when_no_row_gets_a_model_probability(tmp_path, games, promo, monkeypatch):
    monkeypatch.setattr(
        team_run,
        "simulate_events",
        lambda state, events, *a, **k: ({str(e["eventId"]): None for e in events}, {}),
    )
    result = _run(tmp_path, games, promo)
    assert result.exit_code == 3 and not result.published
    assert "price_team_markets=FAILED" in (result.withheld_reason or "")


def test_stage_is_incomplete_when_some_events_have_no_simulation(
    tmp_path, games, promo, monkeypatch
):
    real = team_run.simulate_events

    def partial(state, events, slate_date, n, seed, records=()):
        draws, neutral = real(state, events, slate_date, n, seed, records)
        draws[fx.EV2] = None  # one of the two open games cannot be modelled
        return draws, neutral

    monkeypatch.setattr(team_run, "simulate_events", partial)
    result = _run(tmp_path, games, promo)
    assert result.exit_code == 3 and not result.published
    assert "price_team_markets=INCOMPLETE" in (result.withheld_reason or "")


def test_neutral_flag_from_the_schedule_removes_home_court(tmp_path, games, promo) -> None:
    sched = fx.schedule()
    for ev in sched["events"]:
        if ev["eventId"] == fx.EV1:
            ev["neutralSite"] = True
    plain = _rows(tmp_path / "a", _run(tmp_path / "a", games, promo))
    neutral = _rows(
        tmp_path / "b",
        team_run.run_team_shadow(
            fx.FakeClient(sched=sched),
            games_loader=lambda: games,
            nba_dir=tmp_path / "b" / "nba",
            clock=fx.clock_at(NOW),
            params=PARAMS,
            n_draws=6000,
            promotion_path=promo,
        ),
    )

    def pick(rows):
        return next(
            r
            for r in rows
            if r["event_id"] == fx.EV1 and r["market"] == "ML" and r["side"] == "HOME"
        )

    assert float(pick(neutral)["model_prob"]) < float(pick(plain)["model_prob"]) - 0.01
    assert "HOME_COURT_ASSUMED" not in pick(neutral)["flags"]
    assert "HOME_COURT_ASSUMED" in pick(plain)["flags"]


def test_quoted_outcomes_and_fair_ladders_share_one_simulation(tmp_path, games, promo) -> None:
    result = _run(tmp_path, games, promo)
    rows = _rows(tmp_path, result)
    path = tmp_path / "nba" / "runs" / result.run_id / "team_fair_ladders.csv"
    ladder = {
        (r["ladder"], float(r["line"]), r["side"]): float(r["p_win"])
        for r in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
        if r["event_id"] == fx.EV1
    }
    shared = 0
    for r in rows:
        if r["event_id"] == fx.EV1 and r["market"] == "TOTAL" and r["line"]:
            key = ("TOTAL", float(r["line"]), r["side"])
            if key in ladder and float(r["model_push"]) == 0.0:
                assert ladder[key] == pytest.approx(float(r["model_win"]), abs=5e-6)
                shared += 1
    assert shared >= 1


def test_missing_store_withholds_publication(tmp_path, promo) -> None:
    def loader():
        return team_run.load_store_games(tmp_path / "nope.sqlite3")

    result = team_run.run_team_shadow(
        fx.FakeClient(), games_loader=loader, nba_dir=tmp_path / "nba",
        clock=fx.clock_at(NOW), params=PARAMS, n_draws=1000, promotion_path=promo,
    )  # fmt: skip
    assert result.exit_code == 3


def test_a_promoted_family_blends_the_model_in(tmp_path, games, promo) -> None:
    promotion.evaluate(
        {
            "team.ML": promotion.Evidence(
                n_settled=5000, distinct_dates=120, calibration_gap=0.01,
                oos_logloss_beats_market=True, median_clv=0.01, fitted_weight=0.2,
                n_games=900, clv_n=400, clv_dates=60, clv_coverage=0.9,
            )
        },
        promo,
    )  # fmt: skip
    rows = _rows(tmp_path, _run(tmp_path, games, promo))
    ml = [r for r in rows if r["market"] == "ML" and r["model_prob"] and r["market_prob"]]
    assert ml and all(float(r["model_weight"]) == pytest.approx(0.2) for r in ml)
    assert any(r["final_prob"] != r["market_prob"] for r in ml)
    assert all(r["units"] == "0" for r in rows)  # still no stake: sizing is not wired


def test_ladder_file_is_monotone_and_priced_from_the_same_model(tmp_path, games, promo) -> None:
    result = _run(tmp_path, games, promo)
    path = tmp_path / "nba" / "runs" / result.run_id / "team_fair_ladders.csv"
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    totals = sorted(
        (float(r["line"]), float(r["p_win"]))
        for r in rows
        if r["ladder"] == "TOTAL" and r["side"] == "OVER" and r["event_id"] == fx.EV1
    )
    assert len(totals) == 21
    probs = [p for _l, p in totals]
    assert probs == sorted(probs, reverse=True)


def test_ratings_table_lists_all_thirty_teams(games) -> None:
    state = team_run.compute_team_state([g for g in games], "2026-10-21", PARAMS, "given")
    table = team_run.ratings_rows(state)
    assert len(table) == 30
    assert table[0]["net_per100"] >= table[-1]["net_per100"]


def test_state_for_a_new_season_with_no_games_uses_the_prior_chain(games) -> None:
    prior_only = [g for g in games if g.season != "2026-27"]
    state = team_run.compute_team_state(
        prior_only, "2026-10-01", PARAMS, "given", max_age_days=10_000
    )
    assert state.asof.rating.n_games == 0
    assert abs(float(state.asof.rating.off.sum())) > 0  # carried from the prior season, not zero
