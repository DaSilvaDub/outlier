from outlier_scrapers.slate_quality import (
    LOCAL_DEVIG_UNIT_CAP,
    apply_local_devig_unit_cap,
    classify_injuries,
    dossier_injury_section,
    pitcher_identity_flags,
    playable_prop_sort_key,
    summarize_pitcher_identity,
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


def _gore_so_row(**extra):
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "MacKenzie Gore",
        "selection": "MacKenzie Gore - Strikeouts OVER 4.5",
        "team": "TEX",
        "matchup": "TB @ TEX",
    }
    row.update(extra)
    return row


def test_pitcher_identity_flags_gore_on_wrong_team():
    # 2026-09-06-style leak: Gore is the confirmed TEX starter but the card
    # attached him to WSH @ LAD. Fail closed — do not ship as a clean identity.
    probable = {
        "TEX": {"pitcher": "MacKenzie Gore", "confirmed": True},
        "WSH": {"pitcher": "Andrew Alvarez", "confirmed": True},
    }
    flags = pitcher_identity_flags(_gore_so_row(team="WSH", matchup="WSH @ LAD"), probable)
    assert "pitcher_identity_mismatch" in flags


def test_pitcher_identity_flags_clean_when_gore_matches_confirmed_starter():
    probable = {"TEX": {"pitcher": "MacKenzie Gore", "confirmed": True}}
    assert pitcher_identity_flags(_gore_so_row(), probable) == []


def test_pitcher_identity_flags_reliever_against_confirmed_starter():
    probable = {"CWS": {"pitcher": "Bryan Hudson", "confirmed": True}}
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "Sean Burke",
        "selection": "Sean Burke - Strikeouts OVER 0.5",
        "team": "CWS",
    }
    flags = pitcher_identity_flags(row, probable)
    assert "pitcher_identity_mismatch" in flags


def test_pitcher_identity_unconfirmed_when_player_not_on_slate():
    probable = {"TEX": {"pitcher": "MacKenzie Gore", "confirmed": True}}
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "Unknown Arm",
        "selection": "Unknown Arm - Strikeouts OVER 4.5",
        "team": "SEA",
    }
    flags = pitcher_identity_flags(row, probable)
    assert "pitcher_identity_unconfirmed" in flags
    assert "pitcher_identity_mismatch" not in flags


def test_pitcher_identity_parses_token_so_selection():
    # Pack rows have no player column; token market_label builds
    # "Jackson Jobe SO OVER 5.5" without a " - " delimiter.
    probable = {"DET": {"pitcher": "Jackson Jobe", "confirmed": True}}
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Jackson Jobe SO OVER 5.5",
        "team": "DET",
    }
    assert pitcher_identity_flags(row, probable) == []


def test_pitcher_identity_skips_when_no_probable_source():
    assert pitcher_identity_flags(_gore_so_row(), None) == []
    assert pitcher_identity_flags(_gore_so_row(), {}) == []


def test_pitcher_identity_skips_non_mlb_so():
    probable = {"CHI": {"pitcher": "Someone", "confirmed": True}}
    row = {
        "sport": "WNBA",
        "market_type": "PTS",
        "player": "MacKenzie Gore",
        "team": "CHI",
    }
    assert pitcher_identity_flags(row, probable) == []


def test_summarize_pitcher_identity_counts_fail_closed_rows():
    summary = summarize_pitcher_identity(
        [
            {
                "market_type": "SO",
                "player": "MacKenzie Gore",
                "team": "WSH",
                "matchup": "WSH @ LAD",
                "market_id": "m1",
                "board": "A_FLAGGED",
                "data_quality_flags": "pitcher_identity_mismatch",
            },
            {
                "market_type": "SO",
                "player": "Unknown Arm",
                "team": "SEA",
                "data_quality_flags": "pitcher_identity_unconfirmed",
            },
            {
                "market_type": "SO",
                "player": "MacKenzie Gore",
                "team": "TEX",
                "data_quality_flags": "",
            },
            {"market_type": "GAMELINE", "selection": "Spread AWAY +0.5"},
        ]
    )
    assert summary["so_rows"] == 3
    assert summary["mismatch_count"] == 1
    assert summary["unconfirmed_count"] == 1
    assert summary["fail_closed_count"] == 2
    assert summary["status"] == "fail_closed"
    assert summary["mismatch"][0]["market_id"] == "m1"


def test_summarize_pitcher_identity_ok_when_clean():
    summary = summarize_pitcher_identity(
        [
            {
                "market_type": "SO",
                "player": "MacKenzie Gore",
                "team": "TEX",
                "data_quality_flags": "",
            }
        ]
    )
    assert summary["status"] == "ok"
    assert summary["fail_closed_count"] == 0
