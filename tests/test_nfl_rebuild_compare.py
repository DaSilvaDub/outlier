"""#232 part a: rebuild comparison labels each difference's cause; sportsbook-only edge.

Pick shape: ``nfl_best_bets.json`` ``picks[]`` from ``best_bets.py`` (fields
checked against a real fixture-run card: event_id, player_name, market,
position, line, best_odds, best_book (upper-case, e.g. "FANDUEL"), final_p,
ev_per_unit, verdict, pillars{name: {status}}).
"""

from __future__ import annotations

import copy

import pytest

from outlier_nfl import rebuild_compare as rc


def _pick(**kw):
    p = {"event_id": "e1", "player_name": "Joe Wr", "market": "REC_YDS", "position": "OVER",
         "line": 55.5, "best_odds": -110, "best_book": "FANDUEL", "final_p": 0.60,
         "ev_per_unit": 0.1455, "verdict": "VALIDATED",
         "pillars": {"price": {"status": "VERIFIED"}, "injury_weather": {"status": "VERIFIED"}}}
    p.update(kw)
    return p


def _card(*picks):
    return {"picks": list(picks)}


@pytest.mark.parametrize(("change", "cause"), [
    ({"line": 60.5}, "line_changed"),
    ({"best_odds": -125}, "price_changed"),
    ({"best_book": "DRAFTKINGS"}, "price_changed"),
    ({"final_p": 0.55}, "probability_changed"),
    ({"pillars": {"price": {"status": "VERIFIED"}, "injury_weather": {"status": "MISSING"}}},
     "evidence_changed:injury_weather"),
    ({"ev_per_unit": 0.05}, "ev_formula"),
    ({"verdict": "PROVISIONAL"}, "verdict_rule"),
])
def test_each_cause_is_labelled(change, cause):
    r = rc.compare_cards(_card(_pick()), _card(_pick(**change)))
    [d] = r["differences"]
    assert cause in d["causes"]


def test_unchanged_pick_is_not_a_difference():
    r = rc.compare_cards(_card(_pick()), _card(copy.deepcopy(_pick())))
    assert r["differences"] == [] and r["unchanged"] == 1


def test_one_sided_picks():
    r = rc.compare_cards(_card(_pick()), _card(_pick(player_name="Other Wr")))
    assert sorted(d["causes"][0] for d in r["differences"]) == [
        "new_candidate", "not_rebuilt_as_candidate"]


def test_ev_formula_only_when_price_and_probability_held():
    r = rc.compare_cards(_card(_pick()), _card(_pick(best_odds=-130, ev_per_unit=0.05)))
    assert r["differences"][0]["causes"] == ["price_changed"]


def test_edge_is_against_sportsbook_prices_only():
    assert rc.sportsbook_edge(_pick(final_p=0.6, best_odds=-110)) == round(0.6 - 110 / 210, 4)
    assert rc.sportsbook_edge(_pick(best_book="Hard Rock")) is not None
    for book in ("PRIZEPICKS", "Underdog", "SLEEPER", None, "MYSTERYBOOK"):
        assert rc.sportsbook_edge(_pick(best_book=book)) is None
    assert rc.sportsbook_edge(_pick(best_odds=None)) is None
    assert rc.sportsbook_edge(_pick(best_odds=50)) is None  # not a valid American price


def test_sign_flip_and_negative_validated_list():
    neg = _pick(final_p=0.50)  # -110 implies 0.5238: negative sportsbook edge
    dfs = _pick(player_name="Dfs Guy", best_book="PRIZEPICKS", final_p=0.40)
    r = rc.compare_cards(_card(neg, dfs), _card(_pick(final_p=0.60, verdict="REJECTED"), dfs))
    [d] = r["differences"]
    assert "sportsbook_edge_sign_flip" in d["causes"]
    assert r["verdict_transitions"] == {"VALIDATED->REJECTED": 1}
    assert [x["player_name"] for x in r["original_validated_negative_sportsbook_edge"]] == [
        "Joe Wr"]  # the DFS-priced pick has no sportsbook edge, so it isn't listed


def test_duplicate_keys_match_by_line():
    a = [_pick(line=50.5), _pick(line=60.5)]
    b = [_pick(line=60.5, final_p=0.7), _pick(line=50.5)]
    r = rc.compare_cards(_card(*a), _card(*b))
    [d] = r["differences"]
    assert d["original"]["line"] == 60.5 and d["causes"] == ["probability_changed"]


def test_markdown_lists_causes_and_basis():
    r = rc.compare_cards(_card(_pick()), _card(_pick(final_p=0.5)))
    md = rc.render_markdown(r, {"slate": "2026-10-04", "rebuild_id": "r1"})
    assert "sportsbook prices only" in md and "probability_changed" in md
