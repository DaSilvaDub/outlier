"""Automated tests for Matchup Advantage Rankings engine and reporting stage."""

import csv
from pathlib import Path

from outlier_nfl.matchup_rankings import (
    calc_advantage_score,
    calc_prob_exceeds,
    generate_matchup_rankings_pipeline,
    MatchupRankingsEngine,
    norm_cdf,
    render_positional_markdown,
    render_top_10_overall_markdown,
)
from outlier_nfl.stage_receipts import failing, receipt, run_status


def test_math_helpers():
    """Verify statistical and scoring calculations."""
    # Standard normal CDF
    assert abs(norm_cdf(0.0) - 0.5) < 1e-6
    assert norm_cdf(1.96) > 0.97
    assert norm_cdf(-1.96) < 0.03

    # Tail probability exceeds baseline
    p1 = calc_prob_exceeds(mu=110.0, baseline=100.0, sigma=20.0)
    p2 = calc_prob_exceeds(mu=120.0, baseline=100.0, sigma=20.0)
    assert p2 > p1
    assert p1 > 0.5

    # Degenerate sigma
    assert calc_prob_exceeds(105.0, 100.0, 0.0) == 1.0
    assert calc_prob_exceeds(95.0, 100.0, 0.0) == 0.0

    # Composite advantage score
    score_high = calc_advantage_score(boost_pct=40.0, prob_exceeds=0.88, volume_score=92.0, def_vuln_score=90.0)
    score_low = calc_advantage_score(boost_pct=5.0, prob_exceeds=0.55, volume_score=60.0, def_vuln_score=65.0)
    assert 0.0 <= score_high <= 100.0
    assert 0.0 <= score_low <= 100.0
    assert score_high > score_low


def test_inactive_player_filtering():
    """Verify inactive and IR players are strictly excluded from rankings."""
    engine = MatchupRankingsEngine(
        props=[],
        matchup_scripts=[
            {
                "home_team": "JAX",
                "away_team": "PHI",
                "event_id": "e1",
                "total": 42.0,
                "home_spread": -8.0,
            }
        ],
        usage_players=[
            {"player": "Saquon Barkley", "team": "PHI", "position": "RB", "rush_yds_pg": 85.0, "carries_pg": 18.0},
            {"player": "Will Shipley", "team": "PHI", "position": "RB", "rush_yds_pg": 24.5, "carries_pg": 5.2},
        ],
        rosters={"PHI": {"starting_qb": "Jalen Hurts"}},
        tapes={"PHI": {"qb": "Jalen Hurts", "pass_yards": 180.0}, "JAX": {"opp_rush_yards_allowed": 110.0}},
        weather_records=[],
        inactives_by_team={"PHI": ["Saquon Barkley"]},
        date_str="2026-10-11",
    )

    rbs = engine.evaluate_rbs()
    ranked_names = [rb.player for rb in rbs]
    assert "Saquon Barkley" not in ranked_names
    assert "Will Shipley" in ranked_names


def test_engine_evaluation_and_reports(tmp_path: Path):
    """Test full execution of generate_matchup_rankings_pipeline."""
    props = [
        {
            "player_name": "Brock Purdy",
            "market": "PASS_YDS",
            "position": "OVER",
            "is_consensus_line": True,
            "scope": "full_game",
            "line": 249.5,
            "best_odds": -110,
            "implied_probability": 52.4,
        },
        {
            "player_name": "Will Shipley",
            "market": "RUSH_YDS",
            "position": "OVER",
            "is_consensus_line": True,
            "scope": "full_game",
            "line": 39.5,
            "best_odds": 155,
            "implied_probability": 39.2,
        },
    ]

    matchup_scripts = [
        {
            "home_team": "SEA",
            "away_team": "SF",
            "event_id": "e_sf_sea",
            "total": 46.5,
            "home_spread": 3.0,
        },
        {
            "home_team": "JAX",
            "away_team": "PHI",
            "event_id": "e_phi_jax",
            "total": 42.0,
            "home_spread": -8.0,
        },
    ]

    usage_players = [
        {"player": "Will Shipley", "team": "PHI", "position": "RB", "rush_yds_pg": 24.5, "carries_pg": 5.2, "rec_yds_pg": 10.0, "targets_pg": 2.0},
        {"player": "Brandin Cooks", "team": "SF", "position": "WR", "rec_yds_pg": 45.0, "targets_pg": 5.5},
        {"player": "George Kittle", "team": "SF", "position": "TE", "rec_yds_pg": 55.0, "targets_pg": 6.0},
    ]

    rosters = {
        "SF": {"starting_qb": "Brock Purdy"},
        "SEA": {"starting_qb": "Sam Darnold"},
        "PHI": {"starting_qb": "Jalen Hurts"},
        "JAX": {"starting_qb": "Trevor Lawrence"},
    }

    tapes = {
        "SF": {"qb": "Brock Purdy", "pass_yards": 251.8, "opp_pass_yards_allowed": 210.0, "rush_yards": 130.0, "opp_rush_yards_allowed": 100.0},
        "SEA": {"qb": "Sam Darnold", "pass_yards": 240.0, "opp_pass_yards_allowed": 255.0, "rush_yards": 100.0, "opp_rush_yards_allowed": 115.0},
        "PHI": {"qb": "Jalen Hurts", "pass_yards": 180.0, "opp_pass_yards_allowed": 230.0, "rush_yards": 110.0, "opp_rush_yards_allowed": 120.0},
        "JAX": {"qb": "Trevor Lawrence", "pass_yards": 215.0, "opp_pass_yards_allowed": 235.0, "rush_yards": 115.0, "opp_rush_yards_allowed": 115.0},
    }

    weather_records = [
        {"event_id": "e_sf_sea", "venue": "outdoor", "wind_mph": 8.0, "precip_prob": 0.0, "pass_adjustment": 0.0},
        {"event_id": "e_phi_jax", "venue": "outdoor", "wind_mph": 6.0, "precip_prob": 5.0, "pass_adjustment": 0.0},
    ]

    result = generate_matchup_rankings_pipeline(
        props=props,
        matchup_scripts=matchup_scripts,
        usage_players=usage_players,
        rosters=rosters,
        tapes=tapes,
        weather_records=weather_records,
        inactives_by_team={"PHI": ["Saquon Barkley"]},
        staged_reports_dir=tmp_path,
        date_str="2026-10-11",
        suffix="2026-10-11",
    )

    assert result["status"] == "OK"
    assert result["count"] > 0
    assert len(result["top_10"]) <= 10

    # Verify generated report files exist on disk
    expected_files = [
        "2026-10-11_Top_5_QB_Matchups.md",
        "Top_5_QB_Matchups.md",
        "2026-10-11_Top_5_RB_Matchups.md",
        "Top_5_RB_Matchups.md",
        "2026-10-11_Top_5_WR_Matchups.md",
        "Top_5_WR_Matchups.md",
        "2026-10-11_Top_5_TE_Matchups.md",
        "Top_5_TE_Matchups.md",
        "2026-10-11_Top_10_Overall_Matchups.md",
        "Top_10_Overall_Matchups.md",
        "2026-10-11_Matchup_Boost_Rankings.csv",
        "Matchup_Boost_Rankings.csv",
    ]

    for fname in expected_files:
        p = tmp_path / fname
        assert p.exists(), f"Expected file {fname} not created"
        assert p.stat().st_size > 0, f"File {fname} is empty"

    # Verify CSV structure
    csv_file = tmp_path / "Matchup_Boost_Rankings.csv"
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert "rank_overall" in reader.fieldnames
        assert "player" in reader.fieldnames
        assert "advantage_score" in reader.fieldnames
        assert "primary_stat" in reader.fieldnames
        rows = list(reader)
        assert len(rows) > 0


def test_stage_receipt_failure_behavior():
    """Verify failing matchup_rankings receipt causes non-OK run_status."""
    receipts = [
        receipt("schedule", "OK"),
        receipt("normalized_validation", "OK"),
        receipt("matchup_rankings", "FAILED", errors=["Synthetic simulation error"]),
    ]

    assert failing(receipts)
    status = run_status(receipts)
    assert status == "FAILED"


def test_write_latest_false_writes_only_suffixed_reports(tmp_path: Path):
    """Window / after-kickoff runs must not replace the rolling un-suffixed reports (F11)."""
    kwargs = dict(
        props=[], matchup_scripts=[], usage_players=[], rosters={}, tapes={}, weather_records=[],
        date_str="2026-10-11_1pm", suffix="2026-10-11_1pm",
    )
    windowed = tmp_path / "windowed"
    generate_matchup_rankings_pipeline(staged_reports_dir=windowed, write_latest=False, **kwargs)
    names = {p.name for p in windowed.iterdir()}
    assert names and all(n.startswith("2026-10-11_1pm_") for n in names)

    full = tmp_path / "full"
    generate_matchup_rankings_pipeline(staged_reports_dir=full, **kwargs)
    assert {"Matchup_Boost_Rankings.csv", "Top_10_Overall_Matchups.md"} <= {p.name for p in full.iterdir()}


def _seattle_engine(inactives: dict[str, list[str]] | None) -> MatchupRankingsEngine:
    """Engine for one game whose usage names carry suffixes / punctuation."""
    return MatchupRankingsEngine(
        props=[],
        matchup_scripts=[
            {
                "home_team": "SEA",
                "away_team": "ARI",
                "event_id": "e_ari_sea",
                "total": 44.0,
                "home_spread": -3.0,
            }
        ],
        usage_players=[
            {"player": "Kenneth Walker III", "team": "SEA", "position": "RB",
             "rush_yds_pg": 78.0, "carries_pg": 17.0},
            {"player": "Zach Charbonnet", "team": "SEA", "position": "RB",
             "rush_yds_pg": 26.0, "carries_pg": 6.0},
            {"player": "A.J. Brown", "team": "SEA", "position": "WR",
             "rec_yds_pg": 72.0, "targets_pg": 8.0},
        ],
        rosters={"SEA": {"starting_qb": "Sam Darnold"}},
        tapes={
            "SEA": {"qb": "Sam Darnold", "pass_yards": 230.0},
            "ARI": {"opp_rush_yards_allowed": 120.0, "opp_pass_yards_allowed": 240.0},
        },
        weather_records=[],
        inactives_by_team=inactives,
        date_str="2026-10-18",
    )


def test_inactive_filtering_normalizes_suffixes_and_punctuation():
    """The injury report's full_name must exclude usage's player_display_name.

    nflverse spells the two columns differently (suffixes, initials), so an
    exact-string match let an inactive player stay in the published rankings.
    """
    engine = _seattle_engine({"SEA": ["Kenneth Walker", "AJ Brown"]})

    assert engine.is_player_inactive("Kenneth Walker III", "SEA")
    assert engine.is_player_inactive("A.J. Brown", "SEA")
    assert not engine.is_player_inactive("Zach Charbonnet", "SEA")

    assert "Kenneth Walker III" not in [c.player for c in engine.evaluate_rbs()]
    assert "A.J. Brown" not in [c.player for c in engine.evaluate_wrs()]


def test_inactive_starting_qb_is_excluded_across_name_spellings():
    """A starting QB ruled out under a differently punctuated name is not ranked."""
    assert _seattle_engine(None).evaluate_qbs()[0].player == "Sam Darnold"
    assert _seattle_engine({"SEA": ["Sam Darnold."]}).evaluate_qbs() == []


def test_vacated_backfield_boost_survives_a_name_spelling_difference():
    """The out RB's volume must still transfer when the two sources disagree."""
    def charbonnet(engine: MatchupRankingsEngine) -> float:
        rbs = {c.player: c for c in engine.evaluate_rbs()}
        return rbs["Zach Charbonnet"].matchup_proj

    with_partner_out = charbonnet(_seattle_engine({"SEA": ["Kenneth Walker"]}))
    everyone_active = charbonnet(_seattle_engine(None))
    assert with_partner_out > everyone_active


def _negative_boost_qb_engine() -> MatchupRankingsEngine:
    """One-game slate where the visiting QB draws an elite pass defense.

    A single-game slate (Thursday or Monday night) publishes every QB it finds,
    because the positional reports take the top five of whatever the slate has.
    """
    return MatchupRankingsEngine(
        props=[],
        matchup_scripts=[
            {
                "home_team": "SF",
                "away_team": "SEA",
                "event_id": "e_sea_sf",
                "total": 41.5,
                "home_spread": -1.0,
            }
        ],
        usage_players=[],
        rosters={"SF": {"starting_qb": "Brock Purdy"}, "SEA": {"starting_qb": "Sam Darnold"}},
        tapes={
            "SF": {"pass_yards": 260.0, "opp_pass_yards_allowed": 240.0, "pressure_rate": 0.30},
            "SEA": {"pass_yards": 215.0, "opp_pass_yards_allowed": 150.0, "pressure_rate": 0.40},
        },
        weather_records=[],
        inactives_by_team={},
        date_str="2026-10-19",
    )


def test_negative_boosts_render_with_their_own_sign():
    """A ranked player projected below his average must not read as a boost.

    The renderers used to hard-code the plus sign, so a QB facing a top pass
    defense was published as ``+-10.0%`` / ``+-26.0`` in ``Top_5_QB_Matchups.md``
    and ``Top_10_Overall_Matchups.md``.
    """
    engine = _negative_boost_qb_engine()
    qbs = engine.evaluate_qbs()
    down = [c for c in qbs if c.player == "Brock Purdy"]
    assert down, "the QB facing the elite pass defense must still be ranked"
    assert down[0].boost_pct < 0.0
    assert down[0].expected_increase < 0.0
    assert down[0].matchup_specific_boost < 0.0
    assert any(s.boost_pct < 0.0 for s in down[0].secondary_advantages)

    positional = render_positional_markdown("TOP 5 QUARTERBACK MATCHUP BOOSTS", qbs, "2026-10-19")
    overall = render_top_10_overall_markdown(qbs, "2026-10-19")
    for report in (positional, overall):
        assert "+-" not in report
    assert f"**{down[0].boost_pct:+}%**" in positional
    assert f"**{down[0].expected_increase:+}**" in positional
    assert f"**{down[0].boost_pct:+}%**" in overall

    # A gain still carries its plus sign.
    up = [c for c in qbs if c.player == "Sam Darnold"]
    assert up and up[0].boost_pct > 0.0
    assert f"**+{up[0].boost_pct}%**" in positional
