from outlier_scrapers.slate_quality import (
    LOCAL_DEVIG_UNIT_CAP,
    apply_local_devig_unit_cap,
    classify_injuries,
    dossier_injury_section,
    guard_rebound_over_signal,
    low_volume_3pt_shooter,
    opponent_high_k_rate_conflict,
    pitcher_identity_flags,
    playable_prop_sort_key,
    september_pitcher_so_under_signal,
    signed_line_moved_with_side,
    star_scorer_usage_up_under,
    summarize_pitcher_identity,
    team_total_scoring_conflict,
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


def test_pitcher_identity_unconfirmed_when_lookup_missing():
    # Missing/empty probable lookup must fail closed, not skip. A silent skip
    # ships clean SO identity whenever StatsAPI export is empty.
    assert "pitcher_identity_unconfirmed" in pitcher_identity_flags(_gore_so_row(), None)
    assert "pitcher_identity_unconfirmed" in pitcher_identity_flags(_gore_so_row(), {})


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


def test_star_scorer_usage_up_under_flags_superstar_points_under():
    injuries = classify_injuries("LVA: Jackie Young (Out; Ankle)", "LVA")
    aja_points_under = {
        "market_type": "PLAYER_PROP",
        "market": "PTS",
        "proposition": "POINTS",
        "selection": "A'ja Wilson Under 27.5 Points",
        "line": 27.5,
        "team": "LVA",
    }
    assert star_scorer_usage_up_under(aja_points_under, injuries) is True

    # Low-line scorer should not trigger star scorer gate
    role_player = {
        "market_type": "PLAYER_PROP",
        "market": "PTS",
        "proposition": "POINTS",
        "selection": "Sydney Colson Under 4.5 Points",
        "line": 4.5,
        "team": "LVA",
    }
    assert star_scorer_usage_up_under(role_player, injuries) is False


def test_low_volume_3pt_shooter_flags_zero_recent_makes():
    zero_l5_row = {
        "market_type": "PLAYER_PROP",
        "market": "3PTS",
        "proposition": "3PTS",
        "selection": "Dominique Malonga Over 0.5 Three Pointers",
        "l5_pct": 0.0,
    }
    assert low_volume_3pt_shooter(zero_l5_row) is True

    cold_with_thin_liq = {
        "market_type": "PLAYER_PROP",
        "market": "3PTS",
        "proposition": "3PTS",
        "selection": "Dominique Malonga Over 0.5 Three Pointers",
        "l5_pct": 20.0,
    }
    assert low_volume_3pt_shooter(cold_with_thin_liq, dq_flags=["thin_liquidity"]) is True

    shooter_row = {
        "market_type": "PLAYER_PROP",
        "market": "3PTS",
        "proposition": "3PTS",
        "selection": "Kelsey Plum Over 2.5 Three Pointers",
        "l5_pct": 80.0,
    }
    assert low_volume_3pt_shooter(shooter_row) is False


def test_team_total_scoring_conflict_flags_excessive_share():
    conflict_row = {
        "sport": "WNBA",
        "market_type": "PLAYER_PROP",
        "market": "PTS",
        "proposition": "POINTS",
        "selection": "Aaliyah Edwards Over 14.5 Points",
        "line": 14.5,
        "team_total": 67.5,
    }
    assert team_total_scoring_conflict(conflict_row) is True

    normal_row = {
        "sport": "WNBA",
        "market_type": "PLAYER_PROP",
        "market": "PTS",
        "proposition": "POINTS",
        "selection": "Aaliyah Edwards Over 11.5 Points",
        "line": 11.5,
        "team_total": 82.0,
    }
    assert team_total_scoring_conflict(normal_row) is False


def test_opponent_high_k_rate_conflict_flags_low_buffer_whiff_under():
    conflict_row = {
        "sport": "MLB",
        "market_type": "SO",
        "market": "SO",
        "selection": "Taj Bradley Under 5.5 Strikeouts",
        "line": 5.5,
        "opponent": "LAA",
        "projection_mean": 4.8,
    }
    assert opponent_high_k_rate_conflict(conflict_row) is True

    safe_buffer_row = {
        "sport": "MLB",
        "market_type": "SO",
        "market": "SO",
        "selection": "Taj Bradley Under 6.5 Strikeouts",
        "line": 6.5,
        "opponent": "LAA",
        "projection_mean": 4.5,
    }
    assert opponent_high_k_rate_conflict(safe_buffer_row) is False


def test_september_pitcher_so_under_and_guard_rebound_signals():
    so_under = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Tarik Skubal Under 6.5 Strikeouts",
        "line": 6.5,
        "as_of": "2026-09-17T18:00:00Z",
    }
    assert september_pitcher_so_under_signal(so_under) is True

    guard_reb = {
        "market_type": "PLAYER_PROP",
        "market": "REB",
        "proposition": "REBOUNDS",
        "selection": "Julie Vanloo Over 2.5 Rebounds",
        "player_position": "PG",
        "line": 2.5,
        "l5_pct": 80.0,
    }
    assert guard_rebound_over_signal(guard_reb) is True



def test_heuristic_boosts_do_not_satisfy_predictor_gate():
    from outlier_scrapers.slate_quality import has_predictive_signal
    for flag in ("september_pitcher_so_under", "guard_rebound_over_support"):
        assert not has_predictive_signal({"signal_flags": flag})
        assert has_predictive_signal({"signal_flags": flag + ";insight_support"})


def test_low_l5_gate_does_not_need_liquidity_or_l10_confirmation():
    row = {"market_type": "PLAYER_PROP", "market": "3PTS", "selection": "Player Over 1.5 Three Pointers", "l5_pct": 10, "l10_pct": 50}
    assert low_volume_3pt_shooter(row)
    assert not low_volume_3pt_shooter({**row, "l5_pct": 40})


def test_doubtful_star_scoring_under_and_combo_exclusions():
    row = {"sport": "WNBA", "market_type": "PLAYER_PROP", "market": "PTS", "selection": "Player Under 24.5 Points", "line": 24.5}
    doubtful = classify_injuries("LVA: Jackie Young (Doubtful; Ankle)", "LVA")
    assert star_scorer_usage_up_under(row, doubtful)
    assert not star_scorer_usage_up_under(row, classify_injuries("LVA: Jackie Young (Questionable; Ankle)", "LVA"))
    combo = {**row, "market": "POINTS_REBOUNDS_ASSISTS", "selection": "Player Over 24.5 Points + Rebounds + Assists", "team_total": 65}
    assert not team_total_scoring_conflict(combo)
    assert not star_scorer_usage_up_under({**combo, "selection": "Player Under 24.5 Points + Rebounds + Assists"}, doubtful)


def test_guard_rebound_boost_requires_verified_perimeter_role():
    row = {"sport": "WNBA", "market_type": "PLAYER_PROP", "market": "REB", "selection": "Player Over 4.5 Rebounds", "line": 4.5, "l5_pct": 80}
    for role in (None, "", "C", "PF", "F"):
        assert not guard_rebound_over_signal({**row, "player_position": role})
    assert guard_rebound_over_signal({**row, "player_position": "SG"})
