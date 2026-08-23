from __future__ import annotations

from datetime import datetime

from outlier_scrapers.alt_player_props import (
    build_alt_player_props_board,
    build_alt_player_props_parlays,
)


def _prop(
    *,
    league: str = "MLB",
    player: str = "Player One",
    market: str = "SO",
    position: str = "OVER",
    odds: int = -600,
    book: str = "Hard Rock",
    l5: float = 100.0,
    l10: float = 90.0,
    scope: str = "full_game",
    event_id: str = "e1",
) -> dict:
    return {
        "league": league,
        "event_id": event_id,
        "market_id": f"m-{player}-{market}",
        "outcome_id": f"o-{player}-{market}-{position}",
        "player": player,
        "player_id": player.casefold(),
        "team": "HOME",
        "matchup": "AWAY @ HOME",
        "market": market,
        "position": position,
        "line": 0.5,
        "books": [{"book": book, "odds": odds}],
        "l5_pct": l5,
        "l10_pct": l10,
        "season_pct": 88.0,
        "sport_context": {
            "scope": scope,
            "event_starts_at": "2099-12-31T20:00:00Z",
            "outcome_id": f"o-{player}-{market}-{position}",
        },
    }


def _board(records: list[dict], *, league: str = "MLB") -> list[dict]:
    return build_alt_player_props_board(
        {"records": records},
        league=league,
        target_date="2099-12-31",
        now=datetime.now().astimezone(),
    )


def test_player_board_only_accepts_hard_rock():
    """Board now strictly requires Hard Rock, and ignores other books."""
    rows = _board(
        [
            _prop(player="HR Only", book="Hard Rock", odds=-250),
            _prop(player="Midnite Only", book="Midnite", odds=-300),
            _prop(player="DK Only", book="DraftKings", odds=-400),
        ]
    )

    assert {row["player"] for row in rows} == {"HR Only"}
    by_player = {row["player"]: row for row in rows}
    assert by_player["HR Only"]["best_book"] == "Hard Rock"


def test_player_board_ignores_hit_rate():
    """L5 and L10 hit rate requirements have been removed. Board is built from EV candidates."""
    rows = _board(
        [
            _prop(player="Low Hit Rate", l5=10.0, l10=20.0),
            _prop(player="High Hit Rate", l5=100.0, l10=100.0),
        ]
    )

    assert {row["player"] for row in rows} == {"Low Hit Rate", "High Hit Rate"}

def test_strict_player_board_uses_ev_over_players():
    """If ev_over_players is passed, it only accepts those players."""
    valid = _prop(player="Player One")
    
    rows = build_alt_player_props_board(
        {"records": [valid, _prop(player="Ignored")]},
        league="MLB",
        ev_over_players={"player one"}
    )
    
    assert len(rows) == 1
    assert rows[0]["player"] == "Player One"



def test_player_board_accepts_full_inclusive_odds_window():
    """The odds window is [-1000, -110] — both the moderate-favorite band and
    the heavy-favorite band down to -1000 must be accepted, inclusive of both
    boundaries. (Ceiling widened from -200 to -110 so lighter-juice favorites
    qualify too.)"""
    rows = _board(
        [
            _prop(player="Moderate Favorite", odds=-250),
            _prop(player="Heavy Favorite", odds=-900),
            _prop(player="Min Boundary", odds=-1000),
            _prop(player="Max Boundary", odds=-110),
            _prop(player="Too Extreme", odds=-1001),
            _prop(player="Too Weak", odds=-109),
        ]
    )

    assert {row["player"] for row in rows} == {
        "Moderate Favorite",
        "Heavy Favorite",
        "Min Boundary",
        "Max Boundary",
    }


def test_player_board_enforces_mlb_pitcher_k_over_and_wnba_targets():
    mlb_rows = _board(
        [
            _prop(player="K Over", market="SO", position="OVER"),
            _prop(player="K Under", market="SO", position="UNDER"),
            _prop(player="Hits", market="H", position="OVER"),
            _prop(player="Under", market="2B", position="UNDER"),
            _prop(player="RBI", market="RBI"),
        ]
    )
    assert [(row["player"], row["market"], row["position"]) for row in mlb_rows] == [
        ("K Over", "SO", "OVER")
    ]

    wnba_rows = _board(
        [
            _prop(league="WNBA", player="Guard", market="PTS"),
            _prop(league="WNBA", player="Other", market="BLK"),
        ],
        league="WNBA",
    )
    assert [row["player"] for row in wnba_rows] == ["Guard"]


def test_player_parlays_use_player_name_when_player_id_is_missing():
    rows = _board(
        [
            _prop(player="One", event_id="e1"),
            _prop(player="Two", event_id="e2"),
        ]
    )
    parlays = build_alt_player_props_parlays(rows)
    assert len(parlays) == 1
    assert parlays[0]["type"] == "Cross-Game"
    assert {parlays[0]["leg_1_player"], parlays[0]["leg_2_player"]} == {"One", "Two"}


def test_mlb_alt_k_parlays_reject_same_game_legs():
    rows = _board(
        [
            _prop(player="One", event_id="e1"),
            _prop(player="Two", event_id="e1"),
        ]
    )
    assert len(rows) == 2
    assert build_alt_player_props_parlays(rows) == []
