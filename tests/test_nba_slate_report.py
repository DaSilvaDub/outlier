"""Slate report: gates, caps, portfolio maths and rendering on deterministic draws (offline)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from outlier_nba import slate_report as sr
from outlier_nba import team_run
from outlier_nba.ingest.espn_slate import BookLines, InjuryNote, SlateGame
from outlier_nba.ratings.model import TeamParams
from outlier_nba.sim.engine import SimDraws, SimParams, simulate
from tests.nba_synth import synth_history

NOW = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
TIP = NOW + timedelta(hours=2)
CFG = sr.SlateConfig(n_draws=20_000)
PARAMS = TeamParams(lam=20.0, tau=1e6, carry=0.5)


def _draws(
    e_home: float = 115.0, e_away: float = 110.0, n: int = 20_000, seed: int = 0
) -> SimDraws:
    params = SimParams.single(
        log_poss=float(np.log(99.0)), sigma_p=0.04, e_home=e_home, e_away=e_away,
        sigma_e=11.0, rho=0.1, ot_ratio=1.8,
    )  # fmt: skip
    return simulate(params, n, np.random.default_rng(seed))


def _lines(**kw) -> BookLines:
    base = {
        "book": "DraftKings", "ml_home": -160, "ml_away": 140, "sp_home_line": -3.5,
        "sp_home_odds": -105, "sp_away_odds": -115, "tot_line": 224.5,
        "tot_over_odds": -110, "tot_under_odds": -110,
    }  # fmt: skip
    return BookLines(**{**base, **kw})


def _game(
    lines: BookLines | None = None,
    tip: datetime = TIP,
    status: str = "STATUS_SCHEDULED",
    neutral: bool | None = False,
):
    return SlateGame("ev1", "BOS", "NYK", tip, status, lines or _lines(), neutral)


def _by(cands, market, side):
    (hit,) = [c for c in cands if c.market == market and c.side == side]
    return hit


@pytest.fixture(scope="module")
def draws() -> SimDraws:
    return _draws()


# ---------------------------------------------------------------- evaluate_game


def test_every_quoted_side_of_every_market_is_priced(draws) -> None:
    cands = sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG)
    assert {(c.market, c.side) for c in cands} == {
        ("ML", "HOME"), ("ML", "AWAY"), ("SPREAD", "HOME"), ("SPREAD", "AWAY"),
        ("TOTAL", "OVER"), ("TOTAL", "UNDER"),
    }  # fmt: skip
    for market, a, b in (
        ("ML", "HOME", "AWAY"),
        ("SPREAD", "HOME", "AWAY"),
        ("TOTAL", "OVER", "UNDER"),
    ):
        ca, cb = _by(cands, market, a), _by(cands, market, b)
        assert ca.p_win + cb.p_win + ca.p_push == pytest.approx(1.0, abs=1e-9)
        assert ca.p_market + cb.p_market == pytest.approx(1.0, abs=1e-9)  # devigged, two-way


def test_a_modest_model_edge_clears_every_gate_and_is_sized_by_quarter_kelly(draws) -> None:
    cands = sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG)
    spread = _by(cands, "SPREAD", "HOME")
    assert 0.02 <= spread.edge <= 0.10
    assert spread.fails == ()
    assert spread.edge_stress > 0 and spread.edge_stress < spread.edge
    assert 0 < spread.units_raw <= CFG.cap_bet
    assert spread.kelly_units >= spread.units_raw


def test_an_edge_that_vanishes_under_a_small_adverse_shift_is_rejected_by_F9(draws) -> None:
    # same line at -110/-110: EV is in the 2-10% band (+4.8%) but turns negative after 0.75 pt
    cands = sr.evaluate_game(
        draws, _game(_lines(sp_home_odds=-110, sp_away_odds=-110)), (), (), now=NOW, cfg=CFG
    )
    spread = _by(cands, "SPREAD", "HOME")
    assert sr.SlateConfig().min_edge <= spread.edge <= sr.SlateConfig().max_edge
    assert spread.edge_stress <= 0
    assert [f.split()[0] for f in spread.fails] == ["F9"]
    assert spread.units_raw == 0


def test_the_opposite_side_of_an_edge_is_negative_and_fails_the_edge_gate(draws) -> None:
    cands = sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG)
    other = _by(cands, "SPREAD", "AWAY")
    assert other.edge < 0
    assert any(f.startswith("F6") for f in other.fails)
    assert other.units_raw == 0


def test_a_started_game_is_rejected_by_F1_whatever_the_edge(draws) -> None:
    started = _game(tip=NOW - timedelta(minutes=5), status="STATUS_IN_PROGRESS")
    cands = sr.evaluate_game(draws, started, (), (), now=NOW, cfg=CFG)
    assert all(any(f.startswith("F1") for f in c.fails) for c in cands)


def test_a_tip_inside_the_pre_tip_buffer_is_rejected_by_F1(draws) -> None:
    soon = _game(tip=NOW + timedelta(minutes=sr.SlateConfig().pretip_buffer_min - 1))
    cands = sr.evaluate_game(draws, soon, (), (), now=NOW, cfg=CFG)
    assert all(any(f.startswith("F1") for f in c.fails) for c in cands)


def test_a_long_shot_price_outside_the_window_is_rejected_by_F4(draws) -> None:
    cands = sr.evaluate_game(
        draws, _game(_lines(ml_away=320, ml_home=-420)), (), (), now=NOW, cfg=CFG
    )
    assert any(f.startswith("F4") for f in _by(cands, "ML", "AWAY").fails)
    assert any(f.startswith("F4") for f in _by(cands, "ML", "HOME").fails)


def test_an_implausibly_large_edge_is_rejected_by_F7_and_F8_not_bet(draws) -> None:
    # market total 8 points under the model: a huge "edge" that is far more likely model error
    cands = sr.evaluate_game(draws, _game(_lines(tot_line=216.5)), (), (), now=NOW, cfg=CFG)
    over = _by(cands, "TOTAL", "OVER")
    assert over.edge > sr.SlateConfig().max_edge
    assert {f.split()[0] for f in over.fails} >= {"F7", "F8"}
    assert over.units_raw == 0


def test_fresh_injury_news_on_either_team_rejects_the_whole_game_via_F10(draws) -> None:
    news = InjuryNote("Star Player", "Out", NOW - timedelta(hours=3), "")
    for home_side, away_side in (((news,), ()), ((), (news,))):
        cands = sr.evaluate_game(draws, _game(), home_side, away_side, now=NOW, cfg=CFG)
        assert all(any(f.startswith("F10") for f in c.fails) for c in cands)


def test_old_injury_listings_do_not_trigger_F10(draws) -> None:
    old = InjuryNote("Long Term", "Out", NOW - timedelta(days=20), "")
    cands = sr.evaluate_game(draws, _game(), (old,), (), now=NOW, cfg=CFG)
    assert not any(f.startswith("F10") for c in cands for f in c.fails)


def test_a_heavily_juiced_market_is_rejected_by_F11(draws) -> None:
    cands = sr.evaluate_game(
        draws, _game(_lines(sp_home_odds=-150, sp_away_odds=-150)), (), (), now=NOW, cfg=CFG
    )
    assert all(any(f.startswith("F11") for f in c.fails) for c in cands if c.market == "SPREAD")


def test_the_break_even_line_is_the_worst_line_still_at_non_negative_ev(draws) -> None:
    spread = _by(sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG), "SPREAD", "HOME")
    assert (
        spread.breakeven is not None and spread.breakeven <= -3.5
    )  # a worse line for the home side
    ev_at = lambda line: sr.price_outcome(draws, "SPREAD", "HOME", line).ev(spread.decimal)  # noqa: E731
    assert ev_at(spread.breakeven) >= 0
    assert ev_at(spread.breakeven - 0.5) < 0


def test_moneyline_has_no_break_even_line(draws) -> None:
    ml = _by(sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG), "ML", "HOME")
    assert ml.breakeven is None


def test_the_anchored_edge_lies_between_the_market_price_and_the_model_edge(draws) -> None:
    cands = sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG)
    for c in cands:
        at_market = (1 - c.p_push) * (c.p_market * c.decimal - 1)
        lo, hi = sorted((at_market, c.edge))
        assert lo - 1e-9 <= c.edge_blend <= hi + 1e-9
    huge = sr.evaluate_game(draws, _game(_lines(tot_line=216.5)), (), (), now=NOW, cfg=CFG)
    big = _by(huge, "TOTAL", "OVER")
    assert big.edge_blend < big.edge * 0.3  # most of a huge model-only edge disappears


# ---------------------------------------------------------------- apply_caps


def _cand(**kw) -> sr.Candidate:
    base = {
        "event_id": "e1", "game": "AAA@BBB", "market": "SPREAD", "side": "HOME", "line": -3.5,
        "team": "BBB", "odds": -110, "decimal": 1.909, "p_win": 0.56, "p_push": 0.0,
        "p_model": 0.56, "p_market": 0.5, "edge": 0.05, "edge_stress": 0.02, "edge_blend": 0.0,
        "kelly_units": 1.0, "units_raw": 1.0, "fair_odds": "-127", "breakeven": -4.5,
        "overround": 1.045, "fails": (), "units": 0.0,
    }  # fmt: skip
    return sr.Candidate(**{**base, **kw})


def test_caps_hold_a_per_bet_game_and_day_limit_in_ev_order() -> None:
    cands = [
        _cand(event_id="e1", market="SPREAD", side="HOME", edge=0.06),
        _cand(event_id="e1", market="TOTAL", side="OVER", line=224.5, team="", edge=0.05),
        _cand(event_id="e2", edge=0.04), _cand(event_id="e3", edge=0.035),
        _cand(event_id="e4", edge=0.03),
    ]  # fmt: skip
    out = sr.apply_caps(cands, sr.SlateConfig())
    units = {(c.event_id, c.market): c.units for c in out}
    assert units[("e1", "SPREAD")] == 1.0
    assert units[("e1", "TOTAL")] == 0.5  # 1.5u game cap
    assert units[("e2", "SPREAD")] == 1.0
    assert units[("e3", "SPREAD")] == 0.5  # 3.0u day cap: 1.0 + 0.5 + 1.0 = 2.5, then 0.5 left
    assert units[("e4", "SPREAD")] == 0.0
    assert sum(units.values()) <= sr.SlateConfig().cap_day
    assert any(f.startswith("F12") for c in out if c.units == 0 for f in c.fails)


def test_moneyline_and_spread_on_the_same_side_are_one_exposure() -> None:
    cands = [
        _cand(market="SPREAD", side="HOME", edge=0.05),
        _cand(market="ML", side="HOME", line=None, edge=0.07),
        _cand(market="TOTAL", side="OVER", line=224.5, team="", edge=0.04),
    ]
    out = {(c.market, c.side): c for c in sr.apply_caps(cands, sr.SlateConfig())}
    assert out[("ML", "HOME")].units == 1.0  # the better-EV expression of that side is kept
    assert out[("SPREAD", "HOME")].units == 0.0
    assert any(f.startswith("F12") for f in out[("SPREAD", "HOME")].fails)
    assert out[("TOTAL", "OVER")].units == 0.5  # a total is a separate exposure (game cap left)


def test_rejected_candidates_keep_zero_units_and_their_reasons() -> None:
    bad = _cand(fails=("F9 fails adverse stress",), units_raw=0.0)
    (out,) = sr.apply_caps([bad], sr.SlateConfig())
    assert out.units == 0.0 and out.fails == ("F9 fails adverse stress",)


def test_caps_do_not_mutate_their_input() -> None:
    cands = [_cand(), _cand(event_id="e2", market="ML", side="AWAY", line=None)]
    before = [(c.units, c.fails) for c in cands]
    sr.apply_caps(cands, sr.SlateConfig())
    assert [(c.units, c.fails) for c in cands] == before


def test_units_floor_to_the_half_unit_grid() -> None:
    out = sr.apply_caps([_cand(units_raw=0.9)], sr.SlateConfig())
    assert out[0].units == 0.5


# ---------------------------------------------------------------- portfolio


def _fixed(margins: list[int]) -> SimDraws:
    m = np.array([margins], dtype=np.int64)
    return SimDraws(
        home=100 + m, away=np.full_like(m, 100), ot=np.zeros_like(m), poss=np.full(m.shape, 99.0)
    )


def test_portfolio_profit_distribution_is_exact_on_fixed_draws() -> None:
    d = _fixed([5, 5, -5, -5])  # home covers -3.5 half the time
    bet = _cand(units=1.0, decimal=2.0, edge=0.0, edge_blend=0.0, line=-3.5)
    p = sr.portfolio_stats([bet], {"e1": d})
    assert p.n_bets == 1 and p.units == 1.0
    assert p.p_profit == pytest.approx(0.5) and p.worst == -1.0 and p.p95 == 1.0
    assert p.ev_units == pytest.approx(0.0)


def test_portfolio_adds_same_game_bets_on_the_same_draws() -> None:
    d = _fixed([5, 5, -5, -5])
    spread = _cand(units=1.0, decimal=2.0, market="SPREAD", side="HOME", line=-3.5, edge=0.0)
    away = _cand(units=1.0, decimal=2.0, market="SPREAD", side="AWAY", line=-3.5, edge=0.0)
    p = sr.portfolio_stats([spread, away], {"e1": d})
    assert p.worst == 0.0 and p.p95 == 0.0  # opposite sides of one game always net to zero


def test_an_empty_card_has_an_empty_portfolio() -> None:
    p = sr.portfolio_stats([], {})
    assert p.n_bets == 0 and p.units == 0.0 and p.p_profit is None


def test_pushes_refund_the_stake() -> None:
    d = _fixed([3, 3, 3, 3])
    bet = _cand(units=1.0, decimal=1.9, market="SPREAD", side="HOME", line=-3.0)
    assert sr.portfolio_stats([bet], {"e1": d}).worst == 0.0


# ---------------------------------------------------------------- run_slate + rendering


def _receipt() -> dict:
    return {
        "fetched_at": "2026-10-21T12:00:00+00:00",
        "covered_days": ["20261017", "20261018", "20261019", "20261020", "20261021"],
        "final_games_by_season": {},
    }


@pytest.fixture(scope="module")
def state():
    return team_run.compute_team_state(
        synth_history(), "2026-10-21", PARAMS, "test", feed_receipt=_receipt()
    )


def _slate() -> list[SlateGame]:
    return [
        SlateGame("g1", "BOS", "NYK", TIP, "STATUS_SCHEDULED", _lines(), False),
        SlateGame(
            "g2",
            "LAL",
            "GSW",
            TIP + timedelta(hours=2),
            "STATUS_SCHEDULED",
            _lines(sp_home_line=-1.5),
            False,
        ),
        SlateGame("g3", "MIA", "PHI", NOW - timedelta(hours=3), "STATUS_IN_PROGRESS", None, False),
    ]


def test_run_slate_projects_every_game_and_prices_only_games_with_lines(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    assert [p.event_id for p in res.projections] == ["g1", "g2", "g3"]
    assert len(res.candidates) == 12  # 6 outcomes x the two games that carry lines
    started = [p for p in res.projections if p.event_id == "g3"][0]
    assert started.started and started.lines is None
    for p in res.projections:
        assert 0.0 < p.p_home < 1.0 and p.total_sd > 0 and p.total_p10 < p.total_mean < p.total_p90


def test_run_slate_is_deterministic_for_a_seed(state) -> None:
    a = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    b = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    assert sr.result_to_dict(a) == sr.result_to_dict(b)
    c = sr.run_slate(
        state, _slate(), {}, now=NOW, day="2026-10-21", cfg=replace(CFG, seed=CFG.seed + 1)
    )
    assert sr.result_to_dict(a) != sr.result_to_dict(c)


def test_run_slate_never_exceeds_the_caps(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    per_game: dict[str, float] = {}
    for c in res.candidates:
        assert c.units <= CFG.cap_bet
        per_game[c.event_id] = per_game.get(c.event_id, 0.0) + c.units
    assert all(u <= CFG.cap_game for u in per_game.values())
    assert sum(per_game.values()) <= CFG.cap_day


def test_injury_notes_flow_to_the_projection_and_to_F10(state) -> None:
    news = InjuryNote("Star Player", "Out", NOW - timedelta(hours=2), "")
    res = sr.run_slate(state, _slate(), {"NYK": [news]}, now=NOW, day="2026-10-21", cfg=CFG)
    g1 = [p for p in res.projections if p.event_id == "g1"][0]  # BOS (home) vs NYK (away)
    assert g1.injuries_away == (news,) and g1.injuries_home == ()
    assert all(
        any(f.startswith("F10") for f in c.fails) for c in res.candidates if c.event_id == "g1"
    )


def test_result_dict_is_json_serialisable_and_carries_the_run_identity(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    data = json.loads(json.dumps(sr.result_to_dict(res)))
    assert (
        data["day"] == "2026-10-21" and data["n_draws"] == CFG.n_draws and data["seed"] == CFG.seed
    )
    assert len(data["candidates"]) == 12 and len(data["board"]) == 3


def test_report_has_all_nine_sections_and_the_honesty_caveats(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    text = sr.render_markdown(res)
    for head in (
        "## A. Execution Summary", "## B. Complete Game Projection Board",
        "## C. Master Value Board", "## D. Top Bet Analysis", "## E. Best Opportunity by NBA Market",
        "## F. Rejected Opportunity Board", "## G. Official NBA Daily Bet Card",
        "## H. Daily Portfolio Analysis", "## I. Final NBA Slate Verdict",
    ):  # fmt: skip
        assert head in text
    assert "single book" in text.lower() and "DraftKings" in text
    assert "not modeled" in text.lower()  # player props
    assert "unpromoted" in text.lower()
    assert "NYK" in text and "BOS" in text


def test_report_with_no_qualifying_bet_says_so_and_does_not_crash(state) -> None:
    cfg = replace(CFG, min_edge=0.5, max_edge=0.9)  # nothing can qualify
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=cfg)
    assert not [c for c in res.candidates if c.units > 0]
    text = sr.render_markdown(res)
    assert "No plays" in text and "PASS" in text


def test_report_with_a_play_lists_it_on_the_card_with_its_anchored_ev(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    play = _cand(event_id="g1", game="NYK@BOS", team="BOS", units=1.0, edge_blend=-0.022)
    forced = replace(
        res,
        candidates=(play,),
        portfolio=sr.portfolio_stats([play], {"g1": _draws()}),
    )
    text = sr.render_markdown(forced)
    card = text.split("## G.")[1].split("## H.")[0]
    assert "NYK@BOS" in card and "1.0u" in card and "-2.2%" in card
    assert "market-anchored" in text.lower()
    assert "No plays" not in text


def test_write_outputs_names_files_by_day_and_round_trips(state, tmp_path: Path) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    md, js = sr.write_outputs(res, tmp_path)
    assert md.name == "NBA_Master_Report_2026-10-21_1800ET.md"
    assert js.name == "nba_master_2026-10-21_1800ET.json"
    assert json.loads(js.read_text(encoding="utf-8"))["day"] == "2026-10-21"
    assert md.read_text(encoding="utf-8").startswith("# NBA Master Betting Pipeline")


def test_defaults_match_the_v3_spec() -> None:
    cfg = sr.SlateConfig()
    assert (cfg.n_draws, cfg.cap_bet, cfg.cap_game, cfg.cap_day) == (50_000, 1.0, 1.5, 3.0)
    assert (cfg.min_edge, cfg.max_edge) == (0.02, 0.10)


# ---------------------------------------------------------------- review follow-ups


def test_the_price_window_is_closed_at_the_favourite_end_and_open_at_the_long_shot_end(
    draws,
) -> None:
    def has_f4(**kw) -> dict[str, bool]:
        cands = sr.evaluate_game(draws, _game(_lines(**kw)), (), (), now=NOW, cfg=CFG)
        return {c.side: any(f.startswith("F4") for f in c.fails) for c in cands if c.market == "ML"}

    assert has_f4(ml_home=-250, ml_away=140) == {"HOME": False, "AWAY": False}
    assert has_f4(ml_home=-251, ml_away=141)["HOME"] is True
    assert has_f4(ml_home=-300, ml_away=149)["AWAY"] is False
    assert has_f4(ml_home=-300, ml_away=150)["AWAY"] is True  # +150 itself is outside "< +150"


def test_the_gate_text_states_the_window_it_actually_applies(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    assert "−250 ≤ price < +150" in sr.render_markdown(res)


def test_away_spread_and_under_break_even_lines_move_in_the_worse_direction(draws) -> None:
    cands = sr.evaluate_game(
        draws, _game(_lines(sp_home_line=-8.5, tot_line=230.5)), (), (), now=NOW, cfg=CFG
    )
    away = _by(cands, "SPREAD", "AWAY")
    under = _by(cands, "TOTAL", "UNDER")
    assert away.edge > 0 and under.edge > 0
    # the home handicap rising (-8.5 -> -7.5) is worse for the away side; a lower total for the under
    assert away.breakeven is not None and away.breakeven >= -8.5
    assert under.breakeven is not None and under.breakeven <= 230.5
    for c, step in ((away, 0.5), (under, -0.5)):
        assert sr.price_outcome(draws, c.market, c.side, c.breakeven).ev(c.decimal) >= 0
        assert sr.price_outcome(draws, c.market, c.side, c.breakeven + step).ev(c.decimal) < 0
        moved = sr._shifted(draws, c.market, c.side, 0.75)
        assert sr.price_outcome(moved, c.market, c.side, c.line).ev(c.decimal) == pytest.approx(
            c.edge_stress
        )
        assert c.edge_stress < c.edge  # the shift always moves against the bet


def test_an_unknown_venue_is_rejected_because_home_court_cannot_be_assumed(draws) -> None:
    unknown = sr.evaluate_game(draws, _game(neutral=None), (), (), now=NOW, cfg=CFG)
    assert all(any(f.startswith("F3") for f in c.fails) for c in unknown)
    known = sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG)
    assert not any(f.startswith("F3") for c in known for f in c.fails)


def test_a_neutral_site_game_is_simulated_without_home_court_advantage(state) -> None:
    home = SlateGame("h", "BOS", "NYK", TIP, "STATUS_SCHEDULED", _lines(), False)
    neutral = replace(home, event_id="n", neutral=True)
    res = sr.run_slate(state, [home, neutral], {}, now=NOW, day="2026-10-21", cfg=CFG)
    by_id = {p.event_id: p for p in res.projections}
    assert by_id["h"].margin_mean - by_id["n"].margin_mean > 1.0  # roughly the home-court points
    assert by_id["h"].p_home > by_id["n"].p_home + 0.03


def test_a_missing_neutral_flag_is_simulated_without_home_court(state) -> None:
    home = SlateGame("h", "BOS", "NYK", TIP, "STATUS_SCHEDULED", _lines(), False)
    neutral = replace(home, event_id="n", neutral=True)
    unknown = replace(home, event_id="u", neutral=None)
    res = sr.run_slate(state, [home, neutral, unknown], {}, now=NOW, day="2026-10-21", cfg=CFG)
    by_id = {p.event_id: p for p in res.projections}
    assert by_id["h"].margin_mean - by_id["n"].margin_mean > 1.0
    assert by_id["u"].margin_mean == pytest.approx(by_id["n"].margin_mean, abs=0.75)
    unknown_bets = [c for c in res.candidates if c.event_id == "u"]
    assert unknown_bets and all(
        c.units == 0.0 and any(f.startswith("F3") for f in c.fails) for c in unknown_bets
    )


def test_an_unverified_injury_feed_rejects_everything_via_F10(draws) -> None:
    cands = sr.evaluate_game(draws, _game(), (), (), now=NOW, cfg=CFG, injuries_verified=False)
    assert all(any(f.startswith("F10") and "feed" in f for f in c.fails) for c in cands)
    assert all(c.units_raw == 0 for c in cands)


def test_run_slate_passes_the_injury_feed_status_through(state) -> None:
    res = sr.run_slate(
        state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG, injuries_verified=False
    )
    assert res.candidates and all(c.units == 0 for c in res.candidates)
    assert "injury feed" in sr.render_markdown(res).lower()


def test_opposite_sides_of_one_game_are_never_both_taken() -> None:
    cands = [
        _cand(market="ML", side="HOME", line=None, edge=0.07),
        _cand(market="SPREAD", side="AWAY", team="AAA", edge=0.05),
    ]
    out = {(c.market, c.side): c for c in sr.apply_caps(cands, sr.SlateConfig())}
    assert out[("ML", "HOME")].units == 1.0
    assert out[("SPREAD", "AWAY")].units == 0.0
    assert any("opposite" in f for f in out[("SPREAD", "AWAY")].fails)


def _forced(state, **proj_kw):
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    projections = tuple(replace(p, **proj_kw) if p.event_id == "g1" else p for p in res.projections)
    return replace(res, projections=projections)


def _row(text: str) -> str:
    return next(ln for ln in text.splitlines() if ln.startswith("| NYK@BOS"))


def test_section_b_spread_difference_is_positive_when_the_model_likes_the_home_side_more(
    state,
) -> None:
    # model: home by 6.5; the book's home line is -3.5 -> 3.0 points more bullish on home
    row = _row(sr.render_markdown(_forced(state, margin_mean=6.5)))
    assert "spr +3.0" in row


def test_finished_and_postponed_games_are_not_labelled_tipped(state) -> None:
    for status, label in (("STATUS_FINAL", "final"), ("STATUS_POSTPONED", "postponed")):
        row = _row(sr.render_markdown(_forced(state, status=status, started=True)))
        assert f"| {label} |" in row


def test_a_pick_em_side_is_never_rendered_as_minus_zero(state) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    play = _cand(event_id="g1", game="NYK@BOS", team="NYK", side="AWAY", line=0.0, units=1.0)
    forced = replace(
        res, candidates=(play,), portfolio=sr.portfolio_stats([play], {"g1": _draws()})
    )
    text = sr.render_markdown(forced)
    assert "NYK +0" in text and "NYK -0" not in text


def test_the_model_blindness_commentary_appears_only_when_F7_or_F8_rejected_something(
    state,
) -> None:
    res = sr.run_slate(state, _slate(), {}, now=NOW, day="2026-10-21", cfg=CFG)
    empty = sr.portfolio_stats([], {})
    plain = _cand(
        event_id="g1", fails=("F9 fails 0.75-pt adverse stress",), units_raw=0.0, edge=0.05
    )
    quiet = sr.render_markdown(replace(res, candidates=(plain,), portfolio=empty))
    assert "usually means" not in quiet and "missing-information" not in quiet
    loud_c = _cand(
        event_id="g1",
        fails=("F7 edge > 10% (implausible)", "F8 model-market gap > 7 pts"),
        units_raw=0.0,
        edge=0.2,
    )
    loud = sr.render_markdown(replace(res, candidates=(loud_c,), portfolio=empty))
    assert "usually means" in loud
    assert "F7" in loud.split("Largest rejected edges")[1]
