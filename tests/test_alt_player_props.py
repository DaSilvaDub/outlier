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
    line: float = 0.5,
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
        "line": line,
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


def test_player_board_accepts_allowed_books_and_excludes_others():
    """Board accepts Hard Rock, Fanatics, Midnite, DraftKings, Novig and excludes others."""
    rows = _board(
        [
            _prop(player="HR Only", book="Hard Rock", odds=-250, event_id="e1"),
            _prop(player="Midnite Only", book="Midnite", odds=-300, event_id="e2"),
            _prop(player="DK Only", book="DraftKings", odds=-400, event_id="e3"),
            _prop(player="Fanatics Only", book="Fanatics", odds=-150, event_id="e4"),
            _prop(player="Novig Only", book="Novig", odds=-200, event_id="e5"),
            _prop(player="FD Excluded", book="FanDuel", odds=-250, event_id="e6"),
        ]
    )

    assert {row["player"] for row in rows} == {
        "HR Only",
        "Midnite Only",
        "DK Only",
        "Fanatics Only",
        "Novig Only",
    }
    by_player = {row["player"]: row for row in rows}
    assert by_player["Fanatics Only"]["best_book"] == "Fanatics"
    assert by_player["Fanatics Only"]["best_odds"] == -150
    assert by_player["Fanatics Only"]["model_prob"] is not None
    assert by_player["Fanatics Only"]["edge_pct"] is not None


def test_player_board_selects_optimal_floor_line_over_extreme_juice():
    """A playable floor line at -150 is preferred over an extreme -700 line for the same player."""
    p1_floor = _prop(player="Dustin May", line=2.5, odds=-150, book="Fanatics", l5=80.0, l10=70.0)
    p1_deep = _prop(player="Dustin May", line=1.5, odds=-700, book="Hard Rock", l5=100.0, l10=80.0)

    rows = _board([p1_floor, p1_deep])
    assert len(rows) == 1
    assert rows[0]["line"] == 2.5
    assert rows[0]["best_odds"] == -150
    assert rows[0]["best_book"] == "Fanatics"


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
        ev_over_players={"player one"},
    )

    assert len(rows) == 1
    assert rows[0]["player"] == "Player One"


def test_strict_player_board_matches_ev_over_players_by_name():
    """ev_over_players matches by casefolded player name even if player_id differs."""
    valid = _prop(player="Dustin May")
    valid["player_id"] = "different_hash"

    rows = build_alt_player_props_board(
        {"records": [valid, _prop(player="Ignored")]},
        league="MLB",
        ev_over_players={"dustin may"},
    )

    assert len(rows) == 1
    assert rows[0]["player"] == "Dustin May"



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


def test_wnba_ast_reb_min_line_3_5():
    """WNBA AST and REB props must be at least line 3.5 to qualify as floor props."""
    rows = _board(
        [
            _prop(league="WNBA", player="Ast Low", market="AST", line=1.5, event_id="e1"),
            _prop(league="WNBA", player="Ast Borderline Low", market="AST", line=2.5, event_id="e2"),
            _prop(league="WNBA", player="Ast Valid", market="AST", line=3.5, event_id="e3"),
            _prop(league="WNBA", player="Reb Low", market="REB", line=2.5, event_id="e4"),
            _prop(league="WNBA", player="Reb Valid", market="REB", line=3.5, event_id="e5"),
            _prop(league="WNBA", player="Pts Low Still Allowed", market="PTS", line=2.5, event_id="e6"),
        ],
        league="WNBA",
    )
    accepted_players = {row["player"] for row in rows}
    assert accepted_players == {"Ast Valid", "Reb Valid", "Pts Low Still Allowed"}


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
