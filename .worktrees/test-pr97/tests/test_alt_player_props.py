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
    market: str = "H",
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


def test_player_board_accepts_widened_book_pool_not_just_hard_rock():
    """A qualifying prop with no Hard Rock offer at all -- but a qualifying
    price on Fanatics, Midnite, DraftKings, or Novig -- must still surface.
    Regression for a real gap: several genuinely qualifying alt player props
    only existed on these other books and were silently dropped when the
    board was Hard Rock-exclusive."""
    rows = _board(
        [
            _prop(player="Fanatics Only", book="Fanatics", odds=-250),
            _prop(player="Midnite Only", book="Midnite", odds=-300),
            _prop(player="DK Only", book="DraftKings", odds=-400),
            _prop(player="Novig Only", book="Novig", odds=-500),
            _prop(player="Still Excluded", book="FanDuel", odds=-250),
        ]
    )

    assert {row["player"] for row in rows} == {
        "Fanatics Only",
        "Midnite Only",
        "DK Only",
        "Novig Only",
    }
    by_player = {row["player"]: row for row in rows}
    assert by_player["Fanatics Only"]["best_book"] == "Fanatics"
    assert by_player["Midnite Only"]["best_book"] == "Midnite"


def test_player_board_picks_best_qualifying_price_across_allowed_books():
    """A non-qualifying Hard Rock price alongside a qualifying price on
    another allowed book must not cause the row to be dropped -- the
    qualifying book's price should be picked instead."""
    rec = _prop(player="Mixed", book="Hard Rock", odds=-105)
    rec["books"] = [
        {"book": "Hard Rock", "odds": -105},  # outside -110..-1000, must not win
        {"book": "Fanatics", "odds": -300},  # qualifies, should be picked
    ]

    rows = _board([rec])

    assert len(rows) == 1
    assert rows[0]["best_book"] == "Fanatics"
    assert rows[0]["best_odds"] == -300


def test_strict_player_board_uses_hard_rock_price_and_hit_rate_contract():
    valid = _prop()
    rows = _board(
        [
            valid,
            _prop(player="Bad Price", odds=-105),
            _prop(player="Bad L5", l5=70.0),
            _prop(player="Bad L10", l10=70.0),
            _prop(player="Wrong Book", book="FanDuel"),
            _prop(player="Similar Book", book="Hardrock R"),
            _prop(player="Partial", scope="first_inning"),
            {**_prop(player="Inactive"), "is_active": False},
        ]
    )

    assert len(rows) == 1
    assert rows[0]["player"] == "Player One"
    assert rows[0]["best_book"] == "Hard Rock"
    assert rows[0]["best_odds"] == -600
    assert rows[0]["l5_pct"] == 100.0
    assert rows[0]["l10_pct"] == 90.0


def test_player_board_enforces_75_pct_hit_rate_floor():
    """L5 and L10 floors were loosened from 100%/90% to a >=75% floor on both."""
    rows = _board(
        [
            _prop(player="Min Boundary", l5=75.0, l10=75.0),
            _prop(player="Below L5", l5=74.0, l10=90.0),
            _prop(player="Below L10", l5=100.0, l10=74.0),
        ]
    )

    assert {row["player"] for row in rows} == {"Min Boundary"}


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


def test_player_board_enforces_mlb_doubles_under_and_wnba_targets():
    mlb_rows = _board(
        [
            _prop(player="Under", market="2B", position="UNDER"),
            _prop(player="Over", market="2B", position="OVER"),
            _prop(player="RBI", market="RBI"),
        ]
    )
    assert [(row["player"], row["position"]) for row in mlb_rows] == [("Under", "UNDER")]

    wnba_rows = _board(
        [
            _prop(league="WNBA", player="Guard", market="PTS"),
            _prop(league="WNBA", player="Other", market="BLK"),
        ],
        league="WNBA",
    )
    assert [row["player"] for row in wnba_rows] == ["Guard"]


def test_player_parlays_use_player_name_when_player_id_is_missing():
    rows = _board([_prop(player="One"), _prop(player="Two", market="SO")])
    parlays = build_alt_player_props_parlays(rows)
    assert len(parlays) == 1
    assert {parlays[0]["leg_1_player"], parlays[0]["leg_2_player"]} == {"One", "Two"}
