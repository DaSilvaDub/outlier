"""Unit tests for outlier_nfl.roster active roster indexing and player attribution."""

from __future__ import annotations

import pytest

from outlier_nfl.models import BookPrice, NflPlayerProp
from outlier_nfl.roster import (
    NFL_2026_STARTING_QBS,
    build_team_roster_index,
    get_starting_qb,
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


def test_colts_daniel_jones_starting_qb_registry() -> None:
    """Explicitly verify Daniel Jones is the 2026 starting QB for the Colts, not Anthony Richardson."""
    rosters = build_team_roster_index([], include_league_baseline=True)

    assert "IND" in rosters
    assert rosters["IND"]["starting_qb"] == "Daniel Jones"
    assert get_starting_qb("IND", rosters) == "Daniel Jones"
    assert get_starting_qb("KC", rosters) == "Patrick Mahomes"

    # Strict QB position attribution check
    assert verify_player_team_attribution("Daniel Jones", "IND", rosters, position="QB") is True
    assert verify_player_team_attribution("Anthony Richardson", "IND", rosters, position="QB") is False

    # Rodgers & Smith checks
    assert get_starting_qb("PIT", rosters) == "Aaron Rodgers"
    assert get_starting_qb("NYJ", rosters) == "Geno Smith"
    assert get_starting_qb("MIN", rosters) == "Carson Wentz"
    assert get_starting_qb("TEN", rosters) == "Cam Ward"

    # All 32 teams are indexed
    assert len(rosters) >= 32
    for team, qb in NFL_2026_STARTING_QBS.items():
        assert rosters[team]["starting_qb"] == qb
        assert get_starting_qb(team) == qb


def test_unknown_team_starting_qb_raises() -> None:
    with pytest.raises(ValueError, match="Unknown or unverified"):
        get_starting_qb("FAKE_TEAM")
