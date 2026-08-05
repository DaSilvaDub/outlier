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


def test_strict_player_board_uses_hard_rock_price_and_hit_rate_contract():
    valid = _prop()
    rows = _board(
        [
            valid,
            _prop(player="Bad Price", odds=-110),
            _prop(player="Bad L5", l5=80.0),
            _prop(player="Bad L10", l10=89.0),
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
