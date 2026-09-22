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


def _prop(team: str, player_name: str, market: str) -> NflPlayerProp:
    return NflPlayerProp(
        event_id="e1",
        event_starts_at="2026-09-20T17:00:00Z",
        matchup="MIA @ NE",
        team=team,
        opponent="NE",
        player_name=player_name,
        player_id=None,
        market=market,
        market_raw=market,
        position="OVER",
        line=40.5,
        books=(BookPrice(book="DRAFTKINGS", odds=-110, odds_raw="-110"),),
        best_odds=-110,
        implied_probability=52.38,
    )


def test_surname_containing_most_is_not_treated_as_an_aggregate_market() -> None:
    """'Most Passing Yards' is an aggregate label; 'Mostert' is an active ball-carrier.

    Baseline off so the assertion covers what the feed produced, not the static
    depth chart merged in on top of it.
    """
    rosters = build_team_roster_index(
        [
            _prop("MIA", "Raheem Mostert", "RUSH_YDS"),
            _prop("MIA", "Most Rushing Yards", "RUSH_YDS"),
            _prop("MIA", "Malik Willis", "PASS_YDS"),
        ],
        include_league_baseline=False,
    )

    assert rosters["MIA"]["key_rbs"] == ["Raheem Mostert"]
    assert verify_player_team_attribution("Raheem Mostert", "MIA", rosters) is True


def test_attribution_fails_closed_when_a_team_has_no_indexed_starting_qb() -> None:
    """An empty QB slot must not make every player verify as correctly attributed.

    Baseline off is what leaves the slot empty: with it on, every team inherits a
    starter and this path is never exercised.
    """
    rosters = build_team_roster_index(
        [_prop("NE", "Rhamondre Stevenson", "RUSH_YDS")],
        include_league_baseline=False,
    )

    assert rosters["NE"]["starting_qb"] is None
    assert verify_player_team_attribution("Patrick Mahomes", "NE", rosters) is False
    assert verify_player_team_attribution("Rhamondre Stevenson", "NE", rosters) is True


def test_qb_priced_only_on_completions_or_passing_tds_is_still_indexed() -> None:
    """PASS_COMP / PASS_TD are the canonical codes normalize_market emits."""
    rosters = build_team_roster_index(
        [_prop("GB", "Jordan Love", "PASS_COMP"), _prop("GB", "Jordan Love", "PASS_TD")]
    )

    assert rosters["GB"]["starting_qb"] == "Jordan Love"
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

    # A.J. Brown checks
    assert "A.J. Brown" in rosters["NE"]["key_pass_catchers"]
    assert "A.J. Brown" not in rosters["PHI"]["key_pass_catchers"]
    assert verify_player_team_attribution("A.J. Brown", "NE", rosters) is True
    assert verify_player_team_attribution("A.J. Brown", "PHI", rosters) is False
    assert OFFSEASON_MOVES_2026["A.J. Brown"]["current_team"] == "NE"
    assert "PHI" in OFFSEASON_MOVES_2026["A.J. Brown"]["former_teams"]
    assert OFFSEASON_MOVES_2026["AJ Brown"]["current_team"] == "NE"

    # Newly audited 2026 moves
    assert verify_player_team_attribution("Keenan Allen", "IND", rosters) is True
    assert verify_player_team_attribution("Keenan Allen", "CHI", rosters) is False
    assert verify_player_team_attribution("Romeo Doubs", "NE", rosters) is True
    assert verify_player_team_attribution("Romeo Doubs", "GB", rosters) is False
    assert verify_player_team_attribution("Rico Dowdle", "PIT", rosters) is True
    assert verify_player_team_attribution("Rico Dowdle", "DAL", rosters) is False
    assert verify_player_team_attribution("Kenneth Gainwell", "TB", rosters) is True
    assert verify_player_team_attribution("Kenneth Gainwell", "PHI", rosters) is False
    assert verify_player_team_attribution("Michael Pittman Jr.", "PIT", rosters) is True
    assert verify_player_team_attribution("Michael Pittman Jr.", "IND", rosters) is False
    assert verify_player_team_attribution("Wan'Dale Robinson", "TEN", rosters) is True
    assert verify_player_team_attribution("Wan'Dale Robinson", "NYG", rosters) is False
    assert verify_player_team_attribution("Tank Bigsby", "PHI", rosters) is True
    assert verify_player_team_attribution("Tank Bigsby", "JAX", rosters) is False
    assert verify_player_team_attribution("Jonnu Smith", "GB", rosters) is True
    assert verify_player_team_attribution("Jonnu Smith", "MIA", rosters) is False
    assert verify_player_team_attribution("Jordan Mason", "MIN", rosters) is True
    assert verify_player_team_attribution("Jordan Mason", "SF", rosters) is False
    assert verify_player_team_attribution("Jauan Jennings", "MIN", rosters) is True
    assert verify_player_team_attribution("Jauan Jennings", "SF", rosters) is False
    assert verify_player_team_attribution("Noah Fant", "NO", rosters) is True
    assert verify_player_team_attribution("Noah Fant", "SEA", rosters) is False
    assert verify_player_team_attribution("Malik Willis", "MIA", rosters) is True
    assert verify_player_team_attribution("Malik Willis", "GB", rosters) is False
    assert verify_player_team_attribution("Tua Tagovailoa", "ATL", rosters) is True
    assert verify_player_team_attribution("Tua Tagovailoa", "MIA", rosters) is False
    assert rosters["MIA"]["starting_qb"] == "Malik Willis"
    assert OFFSEASON_MOVES_2026["Tua Tagovailoa"]["current_team"] == "ATL"
    assert "MIA" in OFFSEASON_MOVES_2026["Tua Tagovailoa"]["former_teams"]
    assert OFFSEASON_MOVES_2026["Malik Willis"]["current_team"] == "MIA"
    assert "GB" in OFFSEASON_MOVES_2026["Malik Willis"]["former_teams"]

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

    bad_text_5 = "A.J. Brown on the Eagles is their top target."
    errors_5 = validate_analysis_text_for_roster_errors(bad_text_5)
    assert len(errors_5) == 1
    assert "A.J. Brown" in errors_5[0]
    assert "NE" in errors_5[0]

    bad_text_6 = "Keenan Allen on the Bears will lead their receiver room."
    errors_6 = validate_analysis_text_for_roster_errors(bad_text_6)
    assert len(errors_6) == 1
    assert "Keenan Allen" in errors_6[0]
    assert "IND" in errors_6[0]

    bad_text_7 = "Tua Tagovailoa on the Dolphins will start under center this week."
    errors_7 = validate_analysis_text_for_roster_errors(bad_text_7)
    assert len(errors_7) == 1
    assert "Tua Tagovailoa" in errors_7[0]
    assert "ATL" in errors_7[0]

    # Valid text with verified current teams
    good_text = (
        "Daniel Jones is the starting quarterback for the Indianapolis Colts. "
        "The Kansas City Chiefs signed Kenneth Walker III in the offseason to lead the backfield. "
        "Aaron Rodgers is leading the Pittsburgh Steelers. "
        "Hollywood Brown is playing for the Philadelphia Eagles. "
        "A.J. Brown is the top wide receiver for the New England Patriots. "
        "Keenan Allen plays wide receiver for the Indianapolis Colts. "
        "Malik Willis starts at quarterback for the Miami Dolphins."
    )
    errors_good = validate_analysis_text_for_roster_errors(good_text)
    assert errors_good == []


def test_unknown_team_starting_qb_raises() -> None:
    with pytest.raises(ValueError, match="Unknown or unverified"):
        get_starting_qb("FAKE_TEAM")
