from __future__ import annotations

import pytest

from outlier_scrapers import slate_quality
from outlier_scrapers.pack_selection import _resolve_candidate_identity, build_row


def test_pitcher_returning_from_il_detection():
    # 60-Day IL returning today
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "Nick Pivetta",
        "selection": "Nick Pivetta - Strikeouts OVER 3.5",
        "injury_flags": (
            "SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-07): "
            "Pivetta has been cleared to take the mound in a big-league game for the first time since April 12, "
            "after throwing 4.1 scoreless innings in rehab."
        ),
    }
    assert slate_quality.pitcher_returning_from_il(row) is True

    # 15-Day IL rehab start
    row_15d = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "Shane Bieber",
        "selection": "Shane Bieber - Strikeouts OVER 5.5",
        "injury_flags": "TOR: Shane Bieber (15-Day IL; Right Shoulder; ret 2026-09-08): Scheduled for first start off injured list on limited pitch count.",
    }
    assert slate_quality.pitcher_returning_from_il(row_15d) is True

    # Pitcher who is healthy / not in injury flags
    row_healthy = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "Trevor Rogers",
        "selection": "Trevor Rogers - Strikeouts OVER 4.5",
        "injury_flags": "BAL: Colin Selby (60-Day IL; Right Shoulder Surgery; ret 2027-05-01)",
    }
    assert slate_quality.pitcher_returning_from_il(row_healthy) is False

    # Pitcher who is out for the season (not returning)
    row_out_season = {
        "sport": "MLB",
        "market_type": "SO",
        "player": "Spencer Strider",
        "selection": "Spencer Strider - Strikeouts OVER 6.5",
        "injury_flags": "ATL: Spencer Strider (60-Day IL; Right Elbow Surgery; ret 2027-05-01)",
    }
    assert slate_quality.pitcher_returning_from_il(row_out_season) is False


def test_mlb_game_prop_hits_rejected_from_candidates():
    # CIN @ LAD Hits OVER 1.5
    card = {
        "headline_side": "OVER",
        "market": "Hits",
        "market_type": "GAME_PROP",
        "market_label": "Hits",
        "proposition": "Hits",
        "sides": {
            "OVER": {
                "line": 1.5,
                "price": -105,
                "outcome_id": "out123",
            }
        },
        "card_id": "mkt123",
        "matchup": "CIN @ LAD",
    }
    # Attempt candidate identity resolution
    identity = _resolve_candidate_identity(card, ev_records=[], by_outcome={}, sport="MLB")
    assert identity is None, "MLB GAME_PROP Hits must be rejected from candidate selection"


def test_mlb_first_inning_game_prop_admitted():
    card = {
        "headline_side": "UNDER",
        "market": "NRFI",
        "market_type": "GAME_PROP",
        "market_label": "1st Inning Runs",
        "proposition": "NRFI",
        "sides": {
            "UNDER": {
                "line": 0.5,
                "price": -120,
                "outcome_id": "out456",
            }
        },
        "card_id": "mkt456",
        "matchup": "NYY @ BOS",
        "scope": "full_game",
    }
    identity = _resolve_candidate_identity(card, ev_records=[], by_outcome={}, sport="MLB")
    assert identity is not None, "NRFI first inning game prop should be admitted"


def test_pitcher_returning_from_il_over_disqualified_and_discounted():
    injury_text = (
        "SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-07): "
        "Pivetta has been cleared to take the mound in a big-league game for the first time since April 12."
    )
    over_card = {
        "board": "A",
        "headline_side": "OVER",
        "market": "SO",
        "market_type": "SO",
        "market_label": "Strikeouts",
        "proposition": "Strikeouts",
        "player": "Nick Pivetta",
        "player_id": "pivetta123",
        "team": "SD",
        "matchup": "WSH @ SD",
        "event_id": "ev_piv",
        "sides": {
            "OVER": {
                "line": 3.5,
                "price": -110,
                "outcome_id": "out_piv_o",
                "ev": {"edge_pct": 5.0, "p_over_headline": 0.58},
                "signal": {"insight_component": 1.0},
            }
        },
        "card_id": "mkt_piv",
    }
    ev_records = [
        {
            "event_id": "ev_piv",
            "market_id": "mkt_piv",
            "outcome_id": "out_piv_o",
            "side": "OVER",
            "current_line": 3.5,
            "devig_decimal": 1.91,
            "best_price": -110,
            "best_sportsbook": "DraftKings",
        }
    ]
    by_outcome = {"out_piv_o": ev_records}
    probable_pitchers = {"SD": {"pitcher": "Nick Pivetta", "confirmed": True}}

    row = build_row(
        over_card,
        ev_records,
        by_outcome,
        sport="MLB",
        odds_ts="2026-09-07T12:00:00Z",
        norm_ts="2026-09-07T12:00:00Z",
        source_ts={},
        event_starts={"ev_piv": "2026-09-07T20:00:00Z"},
        injuries={"ev_piv": injury_text},
        probable_pitchers=probable_pitchers,
    )

    assert row is not None
    assert slate_quality.PITCHER_RETURNING_FROM_IL in row["data_quality_flags"]
    assert "pitcher_rehab_pitch_limit" in row["sizing_flags"]
    # OVER on returning pitcher must not be actionable
    assert row["actionable"] == "false"
    assert row["recommended_units_pre_news"] == ""
    assert row["board"] == "A_FLAGGED"


def test_pitcher_returning_from_il_under_not_disqualified():
    injury_text = (
        "SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-07): "
        "Pivetta has been cleared to take the mound in a big-league game for the first time since April 12."
    )
    under_card = {
        "board": "A",
        "headline_side": "UNDER",
        "market": "SO",
        "market_type": "SO",
        "market_label": "Strikeouts",
        "proposition": "Strikeouts",
        "player": "Nick Pivetta",
        "player_id": "pivetta123",
        "team": "SD",
        "matchup": "WSH @ SD",
        "event_id": "ev_piv_u",
        "sides": {
            "UNDER": {
                "line": 3.5,
                "price": -110,
                "outcome_id": "out_piv_u",
                "ev": {"edge_pct": 5.0, "p_over_headline": 0.42},
                "signal": {"insight_component": 1.0},
            }
        },
        "card_id": "mkt_piv_u",
    }
    ev_records = [
        {
            "event_id": "ev_piv_u",
            "market_id": "mkt_piv_u",
            "outcome_id": "out_piv_u",
            "side": "UNDER",
            "current_line": 3.5,
            "devig_decimal": 1.91,
            "best_price": -110,
            "best_sportsbook": "DraftKings",
        }
    ]
    by_outcome = {"out_piv_u": ev_records}
    probable_pitchers = {"SD": {"pitcher": "Nick Pivetta", "confirmed": True}}

    row = build_row(
        under_card,
        ev_records,
        by_outcome,
        sport="MLB",
        odds_ts="2026-09-07T12:00:00Z",
        norm_ts="2026-09-07T12:00:00Z",
        source_ts={},
        event_starts={"ev_piv_u": "2026-09-07T20:00:00Z"},
        injuries={"ev_piv_u": injury_text},
        probable_pitchers=probable_pitchers,
    )

    assert row is not None
    assert slate_quality.PITCHER_RETURNING_FROM_IL in row["data_quality_flags"]
    # UNDER is not disqualified by rehab pitch limit
    assert "pitcher_rehab_pitch_limit" not in row["sizing_flags"]
