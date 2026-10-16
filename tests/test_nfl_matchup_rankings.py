"""Automated tests for Matchup Advantage Rankings engine and reporting stage."""

import csv
from pathlib import Path

from outlier_nfl.matchup_rankings import (
    calc_advantage_score,
    calc_prob_exceeds,
    generate_matchup_rankings_pipeline,
    MatchupRankingsEngine,
    norm_cdf,
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


def test_te2_excluded_and_defensive_out_used():
    """Verify backup TE2 (Cole Kmet behind starter Colston Loveland) is excluded from TE rankings
    and defensive_out_by_team drives opponent injury counts instead of raw practice-squad inactives.
    """
    engine = MatchupRankingsEngine(
        props=[],
        matchup_scripts=[
            {
                "home_team": "CHI",
                "away_team": "MIN",
                "event_id": "e_chi_min",
                "total": 44.5,
                "home_spread": 2.5,
            }
        ],
        usage_players=[
            {
                "player": "Cole Kmet",
                "team": "CHI",
                "position": "TE",
                "rec_yds_pg": 12.0,
                "targets_pg": 1.75,
                "target_share": 0.0607,
            },
            {
                "player": "Colston Loveland",
                "team": "CHI",
                "position": "TE",
                "rec_yds_pg": 34.0,
                "targets_pg": 4.5,
                "target_share": 0.1462,
            },
        ],
        rosters={"CHI": {"starting_qb": "Caleb Williams"}},
        tapes={
            "CHI": {"qb": "Caleb Williams", "te": "Colston Loveland", "pass_yards": 225.0},
            "MIN": {"opp_pass_yards_allowed": 248.0},
        },
        weather_records=[{"event_id": "e_chi_min", "venue": "outdoor", "wind_mph": 12.0, "precip_prob": 20.0}],
        inactives_by_team={"MIN": [f"Practice Squad {i}" for i in range(50)]},
        defensive_out_by_team={"MIN": [{"player": "Harrison Smith", "pos": "SS", "status": "Out"}]},
        date_str="2026-10-11",
    )

    tes = engine.evaluate_tes()
    te_names = [t.player for t in tes]
    assert "Cole Kmet" not in te_names
    assert "Colston Loveland" in te_names
    loveland = next(t for t in tes if t.player == "Colston Loveland")
    assert "1 (Harrison Smith (SS))" in loveland.tactical_drivers["injuries"]
    assert "50" not in loveland.tactical_drivers["injuries"]
