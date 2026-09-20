"""Unit tests for outlier_nfl.roster active roster indexing and player attribution."""

from __future__ import annotations

from outlier_nfl.models import BookPrice, NflPlayerProp
from outlier_nfl.roster import build_team_roster_index, verify_player_team_attribution


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
    """'Most Passing Yards' is an aggregate label; 'Mostert' is an active ball-carrier."""
    rosters = build_team_roster_index(
        [
            _prop("MIA", "Raheem Mostert", "RUSH_YDS"),
            _prop("MIA", "Most Rushing Yards", "RUSH_YDS"),
            _prop("MIA", "Tua Tagovailoa", "PASS_YDS"),
        ]
    )

    assert rosters["MIA"]["key_rbs"] == ["Raheem Mostert"]
    assert verify_player_team_attribution("Raheem Mostert", "MIA", rosters) is True


def test_attribution_fails_closed_when_a_team_has_no_indexed_starting_qb() -> None:
    """An empty QB slot must not make every player verify as correctly attributed."""
    rosters = build_team_roster_index([_prop("NE", "Rhamondre Stevenson", "RUSH_YDS")])

    assert rosters["NE"]["starting_qb"] is None
    assert verify_player_team_attribution("Patrick Mahomes", "NE", rosters) is False
    assert verify_player_team_attribution("Rhamondre Stevenson", "NE", rosters) is True


def test_qb_priced_only_on_completions_or_passing_tds_is_still_indexed() -> None:
    """PASS_COMP / PASS_TD are the canonical codes normalize_market emits."""
    rosters = build_team_roster_index(
        [_prop("GB", "Jordan Love", "PASS_COMP"), _prop("GB", "Jordan Love", "PASS_TD")]
    )

    assert rosters["GB"]["starting_qb"] == "Jordan Love"
