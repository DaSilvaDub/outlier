import json
from pathlib import Path

import pytest

from outlier_scrapers import cards
from outlier_scrapers.cards import (
    PUBLIC_MONEY_DIVERGENCE_FLAG_PP,
    _align_main_lines,
    _spread_sign_conflict,
    assemble_game_card,
    build_cards_payload,
    build_indexes,
    game_stats_side_flag,
    hit_rates_from_game_stats,
    movement_corroboration,
    proxy_market_edge,
    public_money_component_from_divergence,
    public_money_divergence,
    public_money_signal_flags,
    recency_hit_pct,
    signal_score,
    snapshot_skew,
    two_way_fair,
)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_two_way_fair_removes_vig_symmetric():
    fair = two_way_fair(-110, -110)
    assert fair is not None
    assert round(fair["OVER"], 1) == 50.0
    assert round(fair["UNDER"], 1) == 50.0
    assert fair["overround"] > 100.0  # both -110 carry vig


def test_two_way_fair_none_when_price_missing():
    assert two_way_fair(-110, None) is None


def test_proxy_market_edge_positive_and_labelled():
    # fair 50%, but a +120 book implies only 45.45% -> +4.55% market edge.
    edge = proxy_market_edge("OVER", [{"book": "X", "odds": 120}], 50.0)
    assert edge is not None
    assert edge["edge_pct"] > 0
    assert edge["source"] == "proxy_market"
    assert edge["book"] == "X"


def test_proxy_market_edge_none_without_fair():
    assert proxy_market_edge("OVER", [{"book": "X", "odds": 120}], None) is None


def test_recency_hit_pct_weighted_blend():
    val = recency_hit_pct({"l5_pct": 100, "l10_pct": 90, "l20_pct": 50, "season_pct": 50})
    assert val == 82.0  # .4*100 + .3*90 + .2*50 + .1*50


def test_public_money_divergence_happy_path_and_aliases():
    info = public_money_divergence({"percentage": 40, "money": 70})
    assert info == {"tickets_pct": 40.0, "money_pct": 70.0, "divergence_pct": 30.0}
    aliased = public_money_divergence({"public_money_pct": 10, "money_pct": 10})
    assert aliased is not None
    assert aliased["divergence_pct"] == 0.0


def test_public_money_divergence_fail_closed():
    assert public_money_divergence(None) is None
    assert public_money_divergence({}) is None
    assert public_money_divergence({"percentage": 50}) is None
    assert public_money_divergence({"percentage": True, "money": 50}) is None
    assert public_money_divergence({"percentage": -1, "money": 50}) is None
    assert public_money_divergence({"percentage": 50, "money": 150}) is None
    assert public_money_divergence({"percentage": float("nan"), "money": 50}) is None


def test_public_money_component_and_flags():
    assert public_money_component_from_divergence(None) is None
    assert public_money_component_from_divergence(30.0) == 80.0
    assert public_money_component_from_divergence(-60.0) == 0.0
    assert public_money_component_from_divergence(60.0) == 100.0
    thr = PUBLIC_MONEY_DIVERGENCE_FLAG_PP
    assert public_money_signal_flags(thr) == ["sharp_money_support"]
    assert public_money_signal_flags(-thr) == ["public_money_heavy"]
    assert public_money_signal_flags(thr - 1) == []
    assert public_money_signal_flags(-(thr - 1)) == []
    assert public_money_signal_flags(None) == []


def test_insight_without_hit_rate_does_not_mint_support():
    score, conflict = cards.insight_component(
        "OVER",
        [{"side": "OVER", "relevancy": 90, "text": "vague lean"}],
    )
    assert conflict is False
    assert score is None


def test_insight_with_hit_rate_still_scores():
    score, conflict = cards.insight_component(
        "OVER",
        [{"side": "OVER", "relevancy": 90, "hit_rate_pct": 80.0}],
    )
    assert conflict is False
    assert score == pytest.approx(85.0)


def test_signal_score_legacy_equivalence_without_public_money():
    """No-PM path must match the pre-feature four-way composite."""
    base = {
        "l5_pct": 80,
        "l10_pct": 70,
        "l20_pct": 60,
        "season_pct": 50,
        "orf_score": 0.6,
    }
    scored = signal_score("OVER", base, {"line_delta_from_open": -1.0}, [])
    hit = recency_hit_pct(base)
    orf_c = 60.0
    insight_c, _ = cards.insight_component("OVER", [])
    assert insight_c is None
    movement_c = 50.0 + 25.0 * 1.0  # corro = 1.0 for falling line on OVER
    expected = (0.40 * hit + 0.20 * orf_c + 0.20 * movement_c) / 0.80
    assert scored["composite"] == round(min(max(expected, 0.0), 100.0), 3)
    assert scored["hit_component"] == hit
    assert scored["public_money_component"] == ""
    assert scored["public_money_divergence_pct"] == ""


def test_signal_score_five_way_with_public_money_raises_composite():
    base = {
        "l5_pct": 50,
        "l10_pct": 50,
        "l20_pct": 50,
        "season_pct": 50,
        "orf_score": 0.5,
    }
    no_pm = signal_score("OVER", base, None, [])
    sharp = signal_score(
        "OVER",
        {**base, "public_money": {"percentage": 30, "money": 70}},
        None,
        [],
    )
    assert sharp["public_money_divergence_pct"] == 40.0
    assert sharp["public_money_component"] == 90.0
    assert sharp["composite"] > no_pm["composite"]


def test_assemble_game_card_signal_includes_public_money_not_card_flags():
    home = {
        **_spread_prop_row("HOME", -1.5),
        "public_money": {"position": "HOME", "percentage": 20, "money": 55},
    }
    away = {
        **_spread_prop_row("AWAY", 1.5),
        "public_money": {"position": "AWAY", "percentage": 80, "money": 45},
    }
    card = assemble_game_card("gm1", build_indexes([home, away], [], [], []))
    home_sig = card["sides"]["HOME"]["signal"]
    assert home_sig["public_money_divergence_pct"] == 35.0
    assert home_sig["public_money_component"] == 85.0
    # Public-money tokens must never land on card.flags (actionability path).
    flags = card.get("flags") or []
    assert "sharp_money_support" not in flags
    assert "public_money_heavy" not in flags


# --- Spread sign-conflict guard (fix: a corrupted feed could ship both sides -
# the same sign, e.g. HOME 1.5 / AWAY 1.5, and it would look legitimate) -------

def test_spread_sign_conflict_false_for_mirror_pair():
    assert _spread_sign_conflict(-1.5, 1.5) is False
    assert _spread_sign_conflict(1.5, -1.5) is False
    assert _spread_sign_conflict(0.0, 0.0) is False  # pick'em


def test_spread_sign_conflict_true_for_same_sign():
    assert _spread_sign_conflict(1.5, 1.5) is True
    assert _spread_sign_conflict(-1.5, -1.5) is True


def test_spread_sign_conflict_true_for_mismatched_magnitude():
    assert _spread_sign_conflict(-1.5, 2.5) is True


def test_spread_sign_conflict_false_when_one_side_missing():
    assert _spread_sign_conflict(None, 1.5) is False
    assert _spread_sign_conflict(-1.5, None) is False


def _spread_prop_row(position, line):
    return {
        "market_id": "gm1",
        "position": position,
        "proposition": "SPREAD",
        "line": line,
        "best_odds": -170,
        "outcome_id": f"o{position}",
        "league": "MLB",
    }


def _line_row(line, *, books=1):
    return {"line": line, "books": [{"book": str(index)} for index in range(books)]}


def test_align_main_lines_mirrors_spread_to_stronger_side():
    home = _line_row(-8.5)
    away_main = _line_row(7.5)
    away_mirror = _line_row(8.5)
    main = {"HOME": home, "AWAY": away_main}

    _align_main_lines(
        main,
        {"HOME": [home], "AWAY": [away_main, away_mirror]},
        {},
        {"HOME": {"current_line": -8.5}},
        "SPREAD",
    )

    assert main == {"HOME": home, "AWAY": away_mirror}


def test_align_main_lines_matches_total_to_stronger_side():
    over = _line_row(180.5)
    under_main = _line_row(179.5)
    under_match = _line_row(180.5)
    main = {"OVER": over, "UNDER": under_main}

    _align_main_lines(
        main,
        {"OVER": [over], "UNDER": [under_main, under_match]},
        {},
        {"OVER": {"current_line": 180.5}},
        "TOTAL",
    )

    assert main == {"OVER": over, "UNDER": under_match}


def test_align_main_lines_ev_priority_beats_movement():
    home = _line_row(-7.5)
    home_for_away = _line_row(-8.5)
    away = _line_row(8.5)
    away_for_home = _line_row(7.5)
    main = {"HOME": home, "AWAY": away}

    _align_main_lines(
        main,
        {"HOME": [home, home_for_away], "AWAY": [away, away_for_home]},
        {"HOME": [{"side": "HOME", "current_line": -7.5}]},
        {"AWAY": {"current_line": 8.5}},
        "SPREAD",
    )

    assert main == {"HOME": home, "AWAY": away_for_home}


def test_align_main_lines_movement_priority_beats_default():
    home = _line_row(-7.5)
    away = _line_row(8.5)
    away_for_home = _line_row(7.5)
    main = {"HOME": home, "AWAY": away}

    _align_main_lines(
        main,
        {"HOME": [home], "AWAY": [away, away_for_home]},
        {},
        {"HOME": {"current_line": -7.5}},
        "SPREAD",
    )

    assert main == {"HOME": home, "AWAY": away_for_home}


def test_align_main_lines_uses_most_books_for_target_line():
    home = _line_row(-7.5)
    away = _line_row(8.5)
    thin_target = _line_row(7.5, books=1)
    deep_target = _line_row(7.5, books=3)
    main = {"HOME": home, "AWAY": away}

    _align_main_lines(
        main,
        {"HOME": [home], "AWAY": [away, thin_target, deep_target]},
        {"HOME": [{"side": "HOME", "current_line": -7.5}]},
        {},
        "SPREAD",
    )

    assert main["AWAY"] is deep_target


def test_align_main_lines_missing_line_cannot_gain_ev_priority_and_none_proposition_is_safe():
    missing = _line_row(None)
    matching = _line_row(8.5)
    under = _line_row(8.5)
    main = {"OVER": missing, "UNDER": under}

    _align_main_lines(
        main,
        {"OVER": [missing, matching], "UNDER": [under]},
        {"OVER": [{"side": "OVER", "current_line": None}]},
        {"UNDER": {"current_line": 8.5}},
        None,
    )

    assert main["OVER"] is matching


@pytest.mark.parametrize("assembler", [cards.assemble_card, assemble_game_card])
def test_missing_two_way_lines_do_not_create_fair_or_proxy_market(assembler):
    rows = []
    for side in ("OVER", "UNDER"):
        rows.append(
            {
                "market_id": "gm1",
                "side": side,
                "position": side,
                "proposition": "TOTAL",
                "player": "Test Player" if assembler is cards.assemble_card else None,
                "market": "PTS",
                "line": None,
                "best_odds": -110,
                "outcome_id": f"o{side}",
                "league": "WNBA",
            }
        )

    card = assembler("gm1", build_indexes(rows, [], [], []))

    assert card["fair"] is None
    assert all(side["proxy_market_edge"] is None for side in card["sides"].values())


def test_near_equal_movement_line_does_not_trigger_mismatch_flag():
    movement = [
        {"market_id": "gm1", "side": "HOME", "current_line": -8.5},
        {"market_id": "gm1", "side": "AWAY", "current_line": 8.5000000005},
    ]
    card = assemble_game_card(
        "gm1",
        build_indexes(
            [_spread_prop_row("HOME", -8.5), _spread_prop_row("AWAY", 8.5)],
            movement,
            [],
            [],
        ),
    )

    assert "movement_line_mismatch" not in card["flags"]


def test_assemble_game_card_flags_sign_conflict_end_to_end():
    # Corrupted feed: both HOME and AWAY quoted 1.5 (same sign) instead of
    # mirror-image lines. This must survive _route_and_rank's flags assignment.
    idx = build_indexes(
        [_spread_prop_row("HOME", 1.5), _spread_prop_row("AWAY", 1.5)], [], [], []
    )
    card = assemble_game_card("gm1", idx)
    assert "spread_sign_conflict" in card["flags"]


def test_assemble_game_card_no_flag_for_valid_mirror_pair():
    idx = build_indexes(
        [_spread_prop_row("HOME", -1.5), _spread_prop_row("AWAY", 1.5)], [], [], []
    )
    card = assemble_game_card("gm1", idx)
    assert "spread_sign_conflict" not in card["flags"]


def test_assemble_game_card_flags_card_vs_movement_line_mismatch():
    ev = [{
        "market_id": "gm1",
        "side": "AWAY",
        "current_line": 7.5,
        "outcome_id": "oAWAY",
        "calculated_ev_pct": 0.05,
        "calculated_ev_method": cards.EV_METHOD,
        "ev_source": "NATIVE",
        "devig_decimal": 2.0,
        "record_id": "ev1",
    }]
    idx = build_indexes(
        [_spread_prop_row("HOME", -7.5), _spread_prop_row("AWAY", 7.5)],
        [
            {
                "market_id": "gm1",
                "side": "AWAY",
                "current_line": 8.5,
                "open_line": 7.5,
            }
        ],
        ev,
        [],
    )
    card = assemble_game_card("gm1", idx)
    assert "movement_line_mismatch" in card["flags"]


def test_spread_conflict_uses_headline_mirror_anywhere_in_opposite_ladder():
    home_main = _spread_prop_row("HOME", -8.5)
    home_alt = _spread_prop_row("HOME", -7.5)
    away_ev = _spread_prop_row("AWAY", 7.5)
    ev = [{
        "market_id": "gm1",
        "side": "AWAY",
        "current_line": 7.5,
        "outcome_id": "oAWAY",
        "calculated_ev_pct": 0.05,
        "calculated_ev_method": cards.EV_METHOD,
        "ev_source": "NATIVE",
        "devig_decimal": 2.0,
        "record_id": "ev1",
    }]
    movement = [
        {"market_id": "gm1", "side": "HOME", "current_line": -8.5},
        {"market_id": "gm1", "side": "AWAY", "current_line": 8.5},
    ]
    card = assemble_game_card(
        "gm1", build_indexes([home_main, home_alt, away_ev], movement, ev, [])
    )
    assert card["headline_side"] == "AWAY"
    assert "spread_sign_conflict" not in card["flags"]
    assert "movement_line_mismatch" in card["flags"]


def test_opposite_side_movement_mismatch_does_not_flag_matching_headline():
    ev = [{
        "market_id": "gm1",
        "side": "AWAY",
        "current_line": 7.5,
        "outcome_id": "oAWAY",
        "calculated_ev_pct": 0.05,
        "calculated_ev_method": cards.EV_METHOD,
        "ev_source": "NATIVE",
        "devig_decimal": 2.0,
        "record_id": "ev1",
    }]
    movement = [
        {"market_id": "gm1", "side": "HOME", "current_line": -8.5},
        {"market_id": "gm1", "side": "AWAY", "current_line": 7.5},
    ]
    card = assemble_game_card(
        "gm1",
        build_indexes(
            [_spread_prop_row("HOME", -7.5), _spread_prop_row("AWAY", 7.5)],
            movement,
            ev,
            [],
        ),
    )
    assert card["headline_side"] == "AWAY"
    assert "movement_line_mismatch" not in card["flags"]


def test_game_card_preserves_normalized_market_type():
    rows = [
        {
            **_spread_prop_row("OVER", 85.5),
            "position": "OVER",
            "proposition": "POINTS",
            "market": "PTS",
            "market_type": "TEAM_PROP",
            "team": "LAS",
        },
        {
            **_spread_prop_row("UNDER", 85.5),
            "position": "UNDER",
            "proposition": "POINTS",
            "market": "PTS",
            "market_type": "TEAM_PROP",
            "team": "LAS",
        },
    ]
    card = assemble_game_card("gm1", build_indexes(rows, [], [], []))
    assert card["market_type"] == "TEAM_PROP"


def test_hit_rates_from_game_stats_home_away_blobs():
    rec = {
        "position": "HOME",
        "stats": {
            "homeSummaryStat": {"l5": 0.8, "l10": 0.7, "l20": 0.6, "h2h": 0.5, "curSeason": 0.55},
            "awaySummaryStat": {"l5": 0.2, "l10": 0.3, "l20": 0.4, "h2h": 0.1, "curSeason": 0.25},
        },
    }
    home = hit_rates_from_game_stats(rec, "HOME")
    away = hit_rates_from_game_stats(rec, "AWAY")
    assert home == {
        "l5_pct": 80.0,
        "l10_pct": 70.0,
        "l20_pct": 60.0,
        "h2h_pct": 50.0,
        "season_pct": 55.0,
    }
    assert away["l5_pct"] == 20.0
    assert away["l10_pct"] == 30.0


def test_hit_rates_from_game_stats_total_averages_home_away():
    rec = {
        "market_type": "GAMELINE",
        "proposition": "TOTAL",
        "position": "OVER",
        "stats": {
            "homeSummaryStat": {"l5": 0.6, "l10": 0.8, "l20": 0.4, "h2h": 0.5, "curSeason": 0.5},
            "awaySummaryStat": {"l5": 0.4, "l10": 0.4, "l20": 0.6, "h2h": 0.5, "curSeason": 0.7},
        },
    }
    rates = hit_rates_from_game_stats(rec, "OVER")
    assert rates["l5_pct"] == 50.0
    assert rates["l10_pct"] == 60.0
    assert rates["l20_pct"] == 50.0
    assert rates["season_pct"] == 60.0


def test_hit_rates_from_game_stats_team_prop_matches_team():
    rec = {
        "market_type": "TEAM_PROP",
        "proposition": "RUNS",
        "position": "OVER",
        "team": "CWS",
        "matchup": "CWS @ TB",
        "stats": {
            "homeSummaryStat": {"l5": 0.9, "l10": 0.9, "l20": 0.9, "h2h": 0.9, "curSeason": 0.9},
            "awaySummaryStat": {"l5": 0.1, "l10": 0.2, "l20": 0.3, "h2h": 0.0, "curSeason": 0.4},
        },
    }
    rates = hit_rates_from_game_stats(rec, "OVER")
    assert rates["l5_pct"] == 10.0
    assert rates["l10_pct"] == 20.0
    assert rates["season_pct"] == 40.0


def test_game_stats_side_flag_ambiguous_team_prop():
    rec = {
        "market_type": "TEAM_PROP",
        "proposition": "RUNS",
        "position": "OVER",
        "team": "NYY",  # neither side of the matchup
        "matchup": "CWS @ TB",
        "stats": {
            "homeSummaryStat": {"l5": 0.9},
            "awaySummaryStat": {"l5": 0.1},
        },
    }
    assert game_stats_side_flag(rec, "OVER") == "AMBIGUOUS_STATS_SIDE"


def test_game_stats_side_flag_none_for_unambiguous_side():
    rec = {
        "position": "HOME",
        "stats": {"homeSummaryStat": {"l5": 0.9}, "awaySummaryStat": {"l5": 0.1}},
    }
    assert game_stats_side_flag(rec, "HOME") is None


def test_assemble_game_card_flags_ambiguous_team_prop_stats_end_to_end():
    rows = [
        {
            **_spread_prop_row("OVER", 85.5),
            "position": "OVER",
            "proposition": "RUNS",
            "market_type": "TEAM_PROP",
            "team": "NYY",
            "matchup": "CWS @ TB",
            "stats": {
                "homeSummaryStat": {"l5": 0.9},
                "awaySummaryStat": {"l5": 0.1},
            },
        },
        {
            **_spread_prop_row("UNDER", 85.5),
            "position": "UNDER",
            "proposition": "RUNS",
            "market_type": "TEAM_PROP",
            "team": "NYY",
            "matchup": "CWS @ TB",
            "stats": {
                "homeSummaryStat": {"l5": 0.9},
                "awaySummaryStat": {"l5": 0.1},
            },
        },
    ]
    card = assemble_game_card("gm1", build_indexes(rows, [], [], []))
    assert "ambiguous_stats_side" in card["flags"]
    # No leak into the per-side public contract.
    assert "stats_flag" not in card["sides"]["OVER"]


def test_hit_rates_from_game_stats_missing_returns_nones():
    rates = hit_rates_from_game_stats({"stats": {}}, "HOME")
    assert rates == {
        "l5_pct": None,
        "l10_pct": None,
        "l20_pct": None,
        "h2h_pct": None,
        "season_pct": None,
    }


def test_assemble_game_card_wires_hit_rates_into_signal():
    home = {
        **_spread_prop_row("HOME", -1.5),
        "stats": {
            "homeSummaryStat": {
                "l5": 1.0,
                "l10": 1.0,
                "l20": 1.0,
                "h2h": 1.0,
                "curSeason": 1.0,
            }
        },
    }
    away = {
        **_spread_prop_row("AWAY", 1.5),
        "stats": {
            "awaySummaryStat": {
                "l5": 0.0,
                "l10": 0.0,
                "l20": 0.0,
                "h2h": 0.0,
                "curSeason": 0.0,
            }
        },
    }
    card = assemble_game_card("gm1", build_indexes([home, away], [], [], []))
    home_view = card["sides"]["HOME"]
    away_view = card["sides"]["AWAY"]

    assert home_view["hit_rates"]["l5_pct"] == 100.0
    assert home_view["hit_rates"]["l10_pct"] == 100.0
    assert home_view["hit_rates"]["h2h_pct"] == 100.0
    assert away_view["hit_rates"]["l5_pct"] == 0.0

    # Board B hit component is 40% of the composite; with perfect hit rates and
    # no movement/insights/orf, HOME should outrank AWAY on signal composite.
    assert home_view["signal"]["hit_pct"] == 100.0
    assert away_view["signal"]["hit_pct"] == 0.0
    assert home_view["signal"]["composite"] > away_view["signal"]["composite"]

    # Direct unit check that recency-weighted hit rates feed signal_score.
    side_data = {
        "l5_pct": 100.0,
        "l10_pct": 100.0,
        "l20_pct": 100.0,
        "season_pct": 100.0,
    }
    scored = signal_score("HOME", side_data, None, [])
    assert scored["hit_pct"] == 100.0
    assert scored["composite"] > 50.0


def test_movement_corroboration_direction():
    toward = movement_corroboration("OVER", {"line_delta_from_open": -1.0, "odds_delta_from_open": -10})
    against = movement_corroboration("OVER", {"line_delta_from_open": 1.0, "odds_delta_from_open": 20})
    assert toward == 1.0
    assert against == -1.0
    assert movement_corroboration("OVER", None) is None


def test_snapshot_skew_flags_large_gap():
    ok = snapshot_skew(
        {
            "props": "2026-06-21T12:00:00-04:00",
            "line_movement": "2026-06-21T12:30:00-04:00",
            "insights": "2026-06-21T12:10:00-04:00",
        }
    )
    assert ok["is_skewed"] is False
    bad = snapshot_skew(
        {
            "props": "2026-06-21T02:00:00-04:00",
            "line_movement": "2026-06-21T16:00:00-04:00",
            "insights": "2026-06-21T15:00:00-04:00",
        }
    )
    assert bad["is_skewed"] is True
    assert bad["skew_hours"] == 14.0


# --------------------------------------------------------------------------- #
# Main-line selection
# --------------------------------------------------------------------------- #


def test_pick_main_line_prefers_movement_tracked_line():
    rows = [
        {"line": 7.5, "best_odds": -210, "books": [{"book": "A"}]},
        {"line": 15.5, "best_odds": 100, "books": [{"book": "A"}, {"book": "B"}]},
        {"line": 19.5, "best_odds": 1600, "books": [{"book": "A"}, {"book": "B"}, {"book": "C"}]},
    ]
    chosen = cards._pick_main_side_row(rows, {"current_line": 15.5}, [])
    assert chosen["line"] == 15.5


def test_pick_main_line_falls_back_to_pickem():
    rows = [
        {"line": 7.5, "best_odds": -800, "books": [{"book": "A"}]},   # implied ~88.9
        {"line": 9.5, "best_odds": 105, "books": [{"book": "A"}]},    # implied ~48.8 (closest to 50)
        {"line": 14.5, "best_odds": 600, "books": [{"book": "A"}, {"book": "B"}]},
    ]
    chosen = cards._pick_main_side_row(rows, None, [])
    assert chosen["line"] == 9.5


# --------------------------------------------------------------------------- #
# End-to-end build (synthetic latest files)
# --------------------------------------------------------------------------- #


def _prop(market_id, side, line, best_odds, outcome_id, **stats):
    return {
        "league": "WNBA",
        "event_id": "e1",
        "market_id": market_id,
        "player": stats.get("player", "Test Player"),
        "player_id": stats.get("player_id", "pl1"),
        "team": "NYL",
        "opponent": "LAS",
        "matchup": "NYL @ LAS",
        "market": stats.get("market", "REB"),
        "market_raw": "Rebounds",
        "side": side,
        "line": line,
        "books": [{"book": "DraftKings", "odds": best_odds}],
        "best_odds": best_odds,
        "l5_pct": stats.get("l5_pct", 60),
        "l10_pct": stats.get("l10_pct", 60),
        "l20_pct": stats.get("l20_pct", 60),
        "h2h_pct": stats.get("h2h_pct", 50),
        "season_pct": stats.get("season_pct", 60),
        "sport_context": {"outcome_id": outcome_id, "orf_score": 0.6, "scope": "full_game"},
    }


def _movement(market_id, side, current_line):
    return {
        "league": "WNBA",
        "market_id": market_id,
        "side": side,
        "current_line": current_line,
        "open_line": current_line,
        "open_odds": -105,
        "current_odds": -110,
        "line_delta_from_open": 0.0,
        "odds_delta_from_open": -5,
        "movement_count": 2,
        "movement_types": ["odds"],
    }


def _ev(market_id, side, ev_pct, outcome_id):
    return {
        "league": "WNBA",
        "market_id": market_id,
        "outcome_id": outcome_id,
        "side": side,
        "calculated_ev_pct": ev_pct,
        "calculated_ev_method": "AVERAGE",
        "kelly_pct": 1.6,
        "vig_pct": 5.0,
        "width_pct": 30.0,
        "devig_odds": -120,
        "current_line": 8.5,
        "current_odds": -106,
        "book": "DraftKings",
        "book_odds": -106,
        "max_bet": 250,
        "book_state": "PREMATCH",
        "ev_source": "NATIVE",
        "record_id": f"mock_record_{outcome_id}",
    }


def _insight(market_id, outcome_id, side, line):
    return {
        "league": "WNBA",
        "insight_id": "i1",
        "market_id": market_id,
        "market_outcome_id": outcome_id,
        "player_id": "pl1",
        "side": side,
        "line": line,
        "hit_rate_pct": 80.0,
        "last_n_record": "8/10",
        "relevancy": 90,
        "text": "Test Player has cleared this line in 8 of 10.",
    }


def _write_latest(data_dir: Path, league: str, stem: str, records, extra=None):
    payload = {
        "generated_at": "2026-06-21T12:00:00-04:00",
        "league": league,
        "records": records,
    }
    if extra:
        payload.update(extra)
    out = data_dir / league / "normalized"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{league.lower()}_{stem}_latest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_build_cards_routes_boards_and_aligns_main_line(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"

    # Market mEV: has Outlier EV -> Board A. Two sides at the same line 8.5.
    # Market mSIG: no EV -> Board B. Alt OVER lines 7.5 & 15.5; movement tracks 15.5.
    props = [
        _prop("mEV", "OVER", 8.5, -105, "oEVo"),
        _prop("mEV", "UNDER", 8.5, -105, "oEVu"),
        _prop("mSIG", "OVER", 7.5, -300, "oSIG75", market="PTS", l5_pct=40),
        _prop("mSIG", "OVER", 15.5, 100, "oSIG155", market="PTS", l5_pct=90),
    ]
    movement = [_movement("mEV", "UNDER", 8.5), _movement("mSIG", "OVER", 15.5)]
    ev_records = [_ev("mEV", "UNDER", 1.55, "oEVu")]
    insights = [_insight("mSIG", "oSIG155", "OVER", 15.5)]

    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(
        data_dir, "WNBA", "line_movement", movement, extra={"ev_records": ev_records}
    )
    _write_latest(data_dir, "WNBA", "insights", insights)

    payload = build_cards_payload("WNBA")

    assert payload["coverage"]["cards_total"] == 2
    assert payload["coverage"]["board_a_cards"] == 1
    assert payload["coverage"]["board_b_cards"] == 1

    a = payload["board_a"][0]
    assert a["card_id"] == "mEV"
    assert a["headline_side"] == "UNDER"
    assert a["rank_metric"] == "calculated_ev_pct"
    assert a["rank_value"] == 1.55
    assert a["sides"]["UNDER"]["ev"]["best_ev_pct"] == 1.55
    # both sides at same line 8.5 -> proxy edge computed
    assert a["sides"]["UNDER"]["proxy_market_edge"] is not None

    b = payload["board_b"][0]
    assert b["card_id"] == "mSIG"
    assert b["headline_side"] == "OVER"
    assert b["rank_metric"] == "signal_composite"
    # main line is the movement-tracked 15.5, not the 7.5 alt
    assert b["sides"]["OVER"]["line"] == 15.5
    assert {a_["line"] for a_ in b["sides"]["OVER"]["alt_lines"]} == {7.5}
    # insight joins to the main line via outcome_id
    assert len(b["sides"]["OVER"]["insights"]) == 1


def test_board_a_card_never_lacks_ev(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    props = [_prop("m1", "OVER", 2.5, 120, "o1")]
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", [], extra={"ev_records": []})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 0
    assert payload["board_b"][0]["headline_side"] == "OVER"


def test_non_average_ev_is_excluded_from_board_a(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    props = [_prop("m1", "OVER", 8.5, -105, "o1"), _prop("m1", "UNDER", 8.5, -105, "o2")]
    ev = _ev("m1", "OVER", 9.9, "o1")
    ev["calculated_ev_method"] = "WORST"  # not AVERAGE -> ignored
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", [], extra={"ev_records": [ev]})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 0


def test_ev_side_anchors_to_ev_line_over_movement(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    # EV exists only for the 8.5 UNDER, but movement tracks 9.5. The Board A
    # headline must anchor to the EV line (8.5), not the movement line (9.5),
    # so the card never shows one line's price with another line's EV.
    props = [
        _prop("m1", "UNDER", 8.5, -110, "oEV"),
        _prop("m1", "UNDER", 9.5, 100, "o95"),
    ]
    movement = [_movement("m1", "UNDER", 9.5)]
    ev_records = [_ev("m1", "UNDER", 2.0, "oEV")]  # _ev current_line == 8.5

    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", movement, extra={"ev_records": ev_records})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 1
    side = payload["board_a"][0]["sides"]["UNDER"]
    assert side["line"] == 8.5  # anchored to EV line, not movement's 9.5
    assert side["outcome_id"] == side["ev"]["outcome_id"]
    assert side["ev"]["is_alt_line_fallback"] is False
    assert "ev_line_fallback" not in payload["board_a"][0]["flags"]
    assert {a["line"] for a in side["alt_lines"]} == {9.5}


def test_missing_props_or_line_movement_hard_fails(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"

    # Only line_movement + insights present -> props missing -> hard fail.
    _write_latest(data_dir, "WNBA", "line_movement", [], extra={"ev_records": []})
    _write_latest(data_dir, "WNBA", "insights", [])
    with pytest.raises(FileNotFoundError):
        build_cards_payload("WNBA")

    # Now props present but line_movement missing -> hard fail.
    (data_dir / "WNBA" / "normalized" / "wnba_line_movement_latest.json").unlink()
    _write_latest(data_dir, "WNBA", "props", [_prop("m1", "OVER", 2.5, 120, "o1")])
    with pytest.raises(FileNotFoundError):
        build_cards_payload("WNBA")


def test_missing_insights_is_optional_and_recorded(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    _write_latest(data_dir, "WNBA", "props", [_prop("m1", "OVER", 2.5, 120, "o1")])
    _write_latest(data_dir, "WNBA", "line_movement", [], extra={"ev_records": []})
    # no insights file written

    payload = build_cards_payload("WNBA")
    assert payload["missing_feeds"] == ["insights"]
    assert payload["coverage"]["cards_total"] == 1


def test_board_a_ev_object_schema_and_liquidity(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    props = [_prop("m1", "OVER", 8.5, -105, "o1"), _prop("m1", "UNDER", 8.5, -105, "oEV")]
    movement = [_movement("m1", "UNDER", 8.5)]
    ev1 = _ev("m1", "UNDER", 2.0, "oEV")  # DraftKings, max_bet 250
    ev2 = _ev("m1", "UNDER", 1.5, "oEV")
    ev2["book"] = "FanDuel"
    ev2["book_odds"] = -108
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", movement, extra={"ev_records": [ev1, ev2]})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    card = payload["board_a"][0]
    ev = card["sides"]["UNDER"]["ev"]
    # contract consumed by _board_a_flags and the HTML renderer
    assert {"ev_books", "ev_book_count", "devig_odds"} <= set(ev.keys())
    assert ev["ev_book_count"] == 2
    assert {b["book"] for b in ev["ev_books"]} == {"DraftKings", "FanDuel"}
    # two real books with a reported max_bet -> not thin
    assert "thin_liquidity" not in card["flags"]


def test_single_book_ev_flags_thin_liquidity(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    props = [_prop("m1", "OVER", 8.5, -105, "o1"), _prop("m1", "UNDER", 8.5, -105, "oEV")]
    ev = _ev("m1", "UNDER", 2.0, "oEV")
    ev["max_bet"] = None  # uniformly-null max_bet must NOT drive the flag
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", [_movement("m1", "UNDER", 8.5)], extra={"ev_records": [ev]})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    card = payload["board_a"][0]
    assert card["sides"]["UNDER"]["ev"]["ev_book_count"] == 1
    assert "thin_liquidity" in card["flags"]  # driven by single book, not null max_bet


def test_ev_fallback_uses_current_line_not_arbitrary_side_ev(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    # Two UNDER lines; movement/main line is 8.5. EV exists for 8.5 (current_line)
    # under a DIFFERENT outcome_id, plus a higher-EV row at 6.5. The 8.5 EV must
    # win via current_line, never the arbitrary best-side 6.5 EV.
    props = [_prop("m1", "UNDER", 8.5, -105, "oMAIN"), _prop("m1", "UNDER", 6.5, 120, "o65")]
    movement = [_movement("m1", "UNDER", 8.5)]
    ev_main = _ev("m1", "UNDER", 1.0, "NOT_OMAIN")  # current_line 8.5, outcome_id mismatched
    ev_alt = _ev("m1", "UNDER", 9.9, "o65")
    ev_alt["current_line"] = 6.5
    ev_alt["book"] = "Caesars"
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", movement, extra={"ev_records": [ev_main, ev_alt]})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    side = payload["board_a"][0]["sides"]["UNDER"]
    assert side["line"] == 8.5
    assert side["ev"]["best_ev_pct"] == 1.0  # matched by current_line, not the 9.9 at 6.5
    assert side["ev"]["is_alt_line_fallback"] is False


@pytest.mark.parametrize(
    ("export_name", "builder_name", "status_name"),
    [
        ("export_cards_for_league", "build_cards_payload", "cards_status_latest.json"),
        (
            "export_game_cards_for_league",
            "build_game_cards_payload",
            "games_cards_status_latest.json",
        ),
    ],
)
def test_card_success_status_uses_produced_payload_timestamp(
    tmp_path, monkeypatch, export_name, builder_name, status_name
):
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    produced_at = "2026-08-11T05:15:00-04:00"
    payload = {
        "league": "MLB",
        "generated_at": produced_at,
        "missing_feeds": [],
        "coverage": {"cards_total": 0, "board_a_cards": 0, "board_b_cards": 0},
        "snapshot_skew": {"is_skewed": False},
        "board_a": [],
        "board_b": [],
    }
    monkeypatch.setattr(cards, builder_name, lambda league: payload)
    monkeypatch.setattr(cards, "render_html", lambda produced: "<html></html>")

    status = getattr(cards, export_name)("MLB")

    assert status["generated_at"] == produced_at
    persisted = json.loads(
        (league_paths("MLB").reports / status_name).read_text(encoding="utf-8")
    )
    assert persisted["generated_at"] == produced_at


def test_strategy_conflict_flags_board_a_with_canonical_pts(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    props = [
        _prop("m1", "OVER", 8.5, -105, "o1", player="A. Wilson", market="PTS"),
        _prop("m1", "UNDER", 8.5, -105, "o2", player="A. Wilson", market="PTS"),
    ]
    _write_latest(tmp_path / "data", "WNBA", "props", props)
    _write_latest(
        tmp_path / "data",
        "WNBA",
        "line_movement",
        [_movement("m1", "OVER", 8.5)],
        extra={"ev_records": [_ev("m1", "OVER", 1.5, "o1")]},
    )
    _write_latest(tmp_path / "data", "WNBA", "insights", [])
    reports = tmp_path / "data" / "WNBA" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "slate_strategy_latest.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_id": "e1",
                        "directions": [
                            {
                                "scope": "PLAYER",
                                "player": "A'ja Wilson",
                                "player_key": "AJAWILSON",
                                "market_family": "PTS",
                                "side": "UNDER",
                                "confidence": "HIGH",
                                "event_id": "e1",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 1
    assert payload["coverage"]["board_b_cards"] == 0
    card = payload["board_a"][0]
    assert card["board"] == "A"
    assert card["headline_side"] == "OVER"
    assert "strategy_conflict" in card["flags"]


def test_strategy_conflict_is_scoped_to_event_id(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    props = [
        _prop("m1", "OVER", 8.5, -105, "o1", player="A'ja Wilson", market="PTS"),
        _prop("m1", "UNDER", 8.5, -105, "o2", player="A'ja Wilson", market="PTS"),
    ]
    _write_latest(tmp_path / "data", "WNBA", "props", props)
    _write_latest(
        tmp_path / "data",
        "WNBA",
        "line_movement",
        [_movement("m1", "OVER", 8.5)],
        extra={"ev_records": [_ev("m1", "OVER", 1.5, "o1")]},
    )
    _write_latest(tmp_path / "data", "WNBA", "insights", [])
    reports = tmp_path / "data" / "WNBA" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "slate_strategy_latest.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_id": "other-game",
                        "directions": [
                            {
                                "scope": "PLAYER",
                                "player_key": "AJAWILSON",
                                "market_family": "PTS",
                                "side": "UNDER",
                                "confidence": "HIGH",
                                "event_id": "other-game",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = build_cards_payload("WNBA")
    card = payload["board_a"][0]
    assert "strategy_conflict" not in card["flags"]
