from outlier_scrapers.slate_quality import (
    LOCAL_DEVIG_UNIT_CAP,
    apply_local_devig_unit_cap,
    classify_injuries,
    dossier_injury_section,
    playable_prop_sort_key,
    signed_line_moved_with_side,
    usage_up_under,
)


def test_classify_injuries_splits_prefixed_own_and_opponent_outs():
    flags = (
        "CHI: Skylar Diggins (Out; Right Knee; ret 2026-08-23) | "
        "SEA: Natisha Hiedeman (Out; Left Shoulder; ret 2026-08-23) | "
        "IND: Caitlin Clark (Game-time Decision; Back; ret 2026-08-16)"
    )
    view = classify_injuries(flags, "CHI")
    assert view.own_star_out is True
    assert view.opponent_star_out is True
    assert any("Diggins" in item for item in view.own_outs)
    assert any("Hiedeman" in item for item in view.opponent_outs)
    assert view.unscoped_outs == ()


def test_classify_injuries_ignores_probable_and_keeps_unprefixed_unscoped():
    flags = "Aaron Judge (60-Day IL; Out; Right Ribs Fracture) | Foo (Probable; Ankle)"
    view = classify_injuries(flags, "NYY")
    assert view.own_star_out is False
    assert view.opponent_star_out is False
    assert view.unscoped_outs
    assert not any("Foo" in item for item in view.unscoped_outs)


def test_usage_up_under_flags_mean_line_under_when_own_star_is_out():
    view = classify_injuries("CHI: Skylar Diggins (Out; Knee)", "CHI")
    row = {
        "market_type": "REB",
        "selection": "Kamilla Cardoso - Rebounds UNDER 8.5",
        "line": 8.5,
        "projection_mean": 8.6,
        "team": "CHI",
    }
    assert usage_up_under(row, view) is True
    well_under_mean = {**row, "projection_mean": 7.5}
    assert usage_up_under(well_under_mean, view) is True
    over = {**row, "selection": "Kamilla Cardoso - Rebounds OVER 8.5"}
    assert usage_up_under(over, view) is False
    no_own = classify_injuries("SEA: Natisha Hiedeman (Out; Shoulder)", "CHI")
    assert usage_up_under(row, no_own) is False


def test_signed_line_moved_with_side_treats_favorite_steam_as_clv():
    row = {
        "line": -1.5,
        "line_open": -1.5,
        "line_now": -2.5,
        "market_type": "GAMELINE",
        "proposition": "SPREAD",
        "selection": "CHI @ SEA Spread AWAY -1.5",
    }
    assert signed_line_moved_with_side(row) is True
    against = {**row, "line_now": -1.0}
    assert signed_line_moved_with_side(against) is False
    dog = {
        "line": 5.5,
        "line_open": 5.5,
        "line_now": 6.5,
        "market_type": "GAMELINE",
        "proposition": "SPREAD",
        "selection": "IND @ ATL Spread AWAY +5.5",
    }
    assert signed_line_moved_with_side(dog) is True
    over_prop = {
        "line": 8.5,
        "line_open": 8.5,
        "line_now": 9.5,
        "market_type": "REB",
        "selection": "Kamilla Cardoso - Rebounds OVER 8.5",
    }
    assert signed_line_moved_with_side(over_prop) is False


def test_local_devig_unit_cap_shrinks_only_local_devig():
    row = {
        "model_prob_source": "local_devig",
        "recommended_units_pre_news": 2.0,
        "sizing_flags": "",
    }
    apply_local_devig_unit_cap(row)
    assert row["recommended_units_pre_news"] == LOCAL_DEVIG_UNIT_CAP
    assert "local_devig_unit_cap" in row["sizing_flags"]
    outlier = {
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 2.0,
        "sizing_flags": "",
    }
    apply_local_devig_unit_cap(outlier)
    assert outlier["recommended_units_pre_news"] == LOCAL_DEVIG_UNIT_CAP
    assert "market_devig_unit_cap" in outlier["sizing_flags"]


def test_playable_prop_sort_key_orders_by_edge_not_raw_prob():
    high_prob_neg_edge = {
        "edge_pct": -0.03,
        "_rank_value": 99,
        "market_id": "threes",
        "model_prob": 0.63,
    }
    low_prob_plus_edge = {
        "edge_pct": 0.081,
        "_rank_value": 1,
        "market_id": "spread",
        "model_prob": 0.56,
    }
    rows = [high_prob_neg_edge, low_prob_plus_edge]
    rows.sort(key=playable_prop_sort_key)
    assert rows[0]["market_id"] == "spread"


def test_dossier_injury_section_explains_usage_and_side_priority():
    rows = [
        {
            "team": "CHI",
            "injury_flags": (
                "CHI: Skylar Diggins (Out; Knee) | SEA: Natisha Hiedeman (Out; Shoulder)"
            ),
        }
    ]
    section = "\n".join(dossier_injury_section(rows))
    assert "Own-team outs" in section
    assert "Opponent outs" in section
    assert "UNDER" in section
    assert "opponent star Out is the primary cover signal" in section
