"""Unit tests for outlier_nfl.roster active roster indexing, depth charts, and offseason movement verification."""

from __future__ import annotations

import pytest

from outlier_nfl.models import BookPrice, NflPlayerProp
from outlier_nfl.roster import (
    NFL_2026_FULL_DEPTH_CHARTS,
    NFL_2026_STARTING_QBS,
    OFFSEASON_MOVES_2026,
    build_team_roster_index,
    get_starting_qb,
    get_team_depth_chart,
    validate_analysis_text_for_roster_errors,
    verify_player_team_attribution,
)


def test_build_team_roster_index_and_attribution() -> None:
    sample_props = [
        # Pittsburgh Steelers: Aaron Rodgers
        NflPlayerProp(
            event_id="e1",
            event_starts_at="2026-09-20T17:00:00Z",
            matchup="PIT @ NE",
            team="PIT",
            opponent="NE",
            player_name="Aaron Rodgers",
            player_id="p_rodgers",
            market="PASS_YDS",
            market_raw="Passing Yards",
            position="OVER",
            line=225.5,
            books=(BookPrice(book="DRAFTKINGS", odds=-115, odds_raw="-115"),),
            best_odds=-115,
            implied_probability=53.49,
        ),
        NflPlayerProp(
            event_id="e1",
            event_starts_at="2026-09-20T17:00:00Z",
            matchup="PIT @ NE",
            team="PIT",
            opponent="NE",
            player_name="Jaylen Warren",
            player_id="p_warren",
            market="RUSH_YDS",
            market_raw="Rushing Yards",
            position="OVER",
            line=48.5,
            books=(BookPrice(book="DRAFTKINGS", odds=-110, odds_raw="-110"),),
            best_odds=-110,
            implied_probability=52.38,
        ),
        NflPlayerProp(
            event_id="e1",
            event_starts_at="2026-09-20T17:00:00Z",
            matchup="PIT @ NE",
            team="PIT",
            opponent="NE",
            player_name="DK Metcalf",
            player_id="p_metcalf",
            market="REC_YDS",
            market_raw="Receiving Yards",
            position="OVER",
            line=65.5,
            books=(BookPrice(book="DRAFTKINGS", odds=-115, odds_raw="-115"),),
            best_odds=-115,
            implied_probability=53.49,
        ),
        # New York Jets: Geno Smith
        NflPlayerProp(
            event_id="e2",
            event_starts_at="2026-09-20T17:00:00Z",
            matchup="GB @ NYJ",
            team="NYJ",
            opponent="GB",
            player_name="Geno Smith",
            player_id="p_smith",
            market="PASS_YDS",
            market_raw="Passing Yards",
            position="OVER",
            line=235.5,
            books=(BookPrice(book="FANDUEL", odds=-110, odds_raw="-110"),),
            best_odds=-110,
            implied_probability=52.38,
        ),
        NflPlayerProp(
            event_id="e2",
            event_starts_at="2026-09-20T17:00:00Z",
            matchup="GB @ NYJ",
            team="NYJ",
            opponent="GB",
            player_name="Breece Hall",
            player_id="p_hall",
            market="RUSH_YDS",
            market_raw="Rushing Yards",
            position="OVER",
            line=55.5,
            books=(BookPrice(book="FANDUEL", odds=-115, odds_raw="-115"),),
            best_odds=-115,
            implied_probability=53.49,
        ),
    ]

    rosters = build_team_roster_index(sample_props)

    assert "PIT" in rosters
    assert rosters["PIT"]["starting_qb"] == "Aaron Rodgers"
    assert "Jaylen Warren" in rosters["PIT"]["key_rbs"]
    assert "DK Metcalf" in rosters["PIT"]["key_pass_catchers"]

    assert "NYJ" in rosters
    assert rosters["NYJ"]["starting_qb"] == "Geno Smith"
    assert "Breece Hall" in rosters["NYJ"]["key_rbs"]

    # Verify attribution checks
    assert verify_player_team_attribution("Aaron Rodgers", "PIT", rosters) is True
    assert verify_player_team_attribution("Aaron Rodgers", "NYJ", rosters) is False
    assert verify_player_team_attribution("Geno Smith", "NYJ", rosters) is True
    assert verify_player_team_attribution("Geno Smith", "PIT", rosters) is False
    assert verify_player_team_attribution("Breece Hall", "NYJ", rosters) is True


def test_colts_daniel_jones_and_chiefs_kenneth_walker_registry() -> None:
    """Explicitly verify Daniel Jones on Colts and Kenneth Walker III on Chiefs."""
    rosters = build_team_roster_index([], include_league_baseline=True)

    # Colts check
    assert rosters["IND"]["starting_qb"] == "Daniel Jones"
    assert get_starting_qb("IND", rosters) == "Daniel Jones"
    assert verify_player_team_attribution("Daniel Jones", "IND", rosters, position="QB") is True
    assert verify_player_team_attribution("Anthony Richardson", "IND", rosters, position="QB") is False

    # Chiefs check
    assert rosters["KC"]["starting_qb"] == "Patrick Mahomes"
    assert "Kenneth Walker III" in rosters["KC"]["key_rbs"]
    assert verify_player_team_attribution("Kenneth Walker III", "KC", rosters) is True
    assert verify_player_team_attribution("Kenneth Walker III", "SEA", rosters) is False

    # Offseason movement registry checks
    assert OFFSEASON_MOVES_2026["Kenneth Walker III"]["current_team"] == "KC"
    assert "SEA" in OFFSEASON_MOVES_2026["Kenneth Walker III"]["former_teams"]
    assert OFFSEASON_MOVES_2026["Daniel Jones"]["current_team"] == "IND"
    assert "NYG" in OFFSEASON_MOVES_2026["Daniel Jones"]["former_teams"]

    # Hollywood Brown checks
    assert "Hollywood Brown" in rosters["PHI"]["key_pass_catchers"]
    assert "Hollywood Brown" not in rosters["KC"]["key_pass_catchers"]
    assert verify_player_team_attribution("Hollywood Brown", "PHI", rosters) is True
    assert verify_player_team_attribution("Hollywood Brown", "KC", rosters) is False
    assert OFFSEASON_MOVES_2026["Hollywood Brown"]["current_team"] == "PHI"
    assert "KC" in OFFSEASON_MOVES_2026["Hollywood Brown"]["former_teams"]

    # Full 32 teams check
    assert len(rosters) == 32
    assert len(NFL_2026_FULL_DEPTH_CHARTS) == 32


def test_validate_analysis_text_catches_roster_hallucinations() -> None:
    # Text with violations
    bad_text_1 = "Kenneth Walker III on the Seahawks has a tough matchup tonight."
    errors_1 = validate_analysis_text_for_roster_errors(bad_text_1)
    assert len(errors_1) == 1
    assert "Kenneth Walker III" in errors_1[0]
    assert "SEA" in errors_1[0]

    bad_text_2 = "Anthony Richardson is starting at quarterback for the Colts."
    errors_2 = validate_analysis_text_for_roster_errors(bad_text_2)
    assert len(errors_2) == 1
    assert "Anthony Richardson" in errors_2[0]

    bad_text_3 = "Aaron Rodgers on the Jets will look to pass downfield."
    errors_3 = validate_analysis_text_for_roster_errors(bad_text_3)
    assert len(errors_3) == 1
    assert "Aaron Rodgers" in errors_3[0]

    bad_text_4 = "Hollywood Brown on the Chiefs is a key receiving target tonight."
    errors_4 = validate_analysis_text_for_roster_errors(bad_text_4)
    assert len(errors_4) == 1
    assert "Hollywood Brown" in errors_4[0]
    assert "PHI" in errors_4[0]

    # Valid text with verified current teams
    good_text = (
        "Daniel Jones is the starting quarterback for the Indianapolis Colts. "
        "The Kansas City Chiefs signed Kenneth Walker III in the offseason to lead the backfield. "
        "Aaron Rodgers is leading the Pittsburgh Steelers. "
        "Hollywood Brown is playing for the Philadelphia Eagles."
    )
    errors_good = validate_analysis_text_for_roster_errors(good_text)
    assert errors_good == []


def test_unknown_team_starting_qb_raises() -> None:
    with pytest.raises(ValueError, match="Unknown or unverified"):
        get_starting_qb("FAKE_TEAM")
