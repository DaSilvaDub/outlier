from __future__ import annotations

import csv
from datetime import datetime

from outlier_scrapers.alt_bankroll_props import build_alt_bankroll_board
from outlier_scrapers.pack import write_pack


def _game(
    *,
    market_type: str = "GAMELINE",
    proposition: str = "MONEYLINE",
    market: str = "MONEYLINE",
    odds: int = -600,
    book: str = "Hard Rock",
    l5: float = 1.0,
    l10: float = 0.9,
    scope: str = "full_game",
    period_label: str | None = None,
    event_id: str = "e1",
) -> dict:
    return {
        "league": "MLB",
        "event_id": event_id,
        "event_starts_at": "2099-12-31T20:00:00Z",
        "market_id": f"m-{event_id}-{proposition}",
        "outcome_id": f"o-{event_id}-{proposition}",
        "market_type": market_type,
        "proposition": proposition,
        "market": market,
        "position": "HOME" if proposition == "MONEYLINE" else "OVER",
        "line": 0.0 if proposition == "MONEYLINE" else 4.5,
        "scope": scope,
        "period_label": period_label,
        "is_active": True,
        "team": "HOME",
        "matchup": "AWAY @ HOME",
        "books": [{"book": book, "odds": odds}],
        "stats": {"homeSummaryStat": {"l5": l5, "l10": l10}},
    }


def _board(records: list[dict]) -> list[dict]:
    return build_alt_bankroll_board(
        {"generated_at": "2099-12-31T12:00:00Z", "records": records},
        league="MLB",
        target_date="2099-12-31",
        now=datetime.now().astimezone(),
    )


def test_bankroll_board_enforces_scope_whitelist_book_odds_and_hit_rates():
    rows = _board(
        [
            _game(),
            _game(event_id="team", market_type="TEAM_PROP", proposition="RUNS", market="R"),
            _game(event_id="partial", scope="first_inning", period_label="1I"),
            _game(event_id="bad-market", market_type="TEAM_PROP", proposition="RBI", market="RBI"),
            _game(event_id="bad-price", odds=-110),
            _game(event_id="bad-book", book="FanDuel"),
            _game(event_id="bad-l5", l5=0.8),
            _game(event_id="bad-l10", l10=0.8),
        ]
    )

    assert {row["event_id"] for row in rows} == {"e1", "team"}
    assert all(row["best_book"] == "Hard Rock" for row in rows)
    assert all(-1000 <= row["best_price"] <= -500 for row in rows)
    assert all(row["scope"] == "full_game" for row in rows)


def test_bankroll_game_total_combines_home_and_away_windows():
    total = _game(proposition="TOTAL", market="TOTAL")
    total["position"] = "OVER"
    total["team"] = None
    total["stats"] = {
        "homeSummaryStat": {"l5": 1.0, "l10": 0.9},
        "awaySummaryStat": {"l5": 1.0, "l10": 1.0},
    }

    rows = _board([total])

    assert len(rows) == 1
    assert rows[0]["l5_pct"] == 100.0
    assert rows[0]["l10_pct"] == 95.0


def test_write_pack_emits_player_and_league_bankroll_csvs(tmp_path):
    games = {"generated_at": "now", "records": [_game()]}
    player = {
        "league": "MLB",
        "event_id": "e1",
        "market_id": "pm1",
        "outcome_id": "po1",
        "player": "Player One",
        "team": "HOME",
        "matchup": "AWAY @ HOME",
        "market": "H",
        "position": "OVER",
        "line": 0.5,
        "books": [{"book": "Hard Rock", "odds": -600}],
        "l5_pct": 100.0,
        "l10_pct": 90.0,
        "season_pct": 80.0,
        "sport_context": {
            "scope": "full_game",
            "event_starts_at": "2099-12-31T20:00:00Z",
            "outcome_id": "po1",
        },
    }
    out_dir = tmp_path / "staging-name-does-not-contain-date"

    write_pack(
        [],
        out_dir,
        games_norm_by_league={"MLB": games},
        props_norm_by_league={"MLB": {"records": [player]}},
        target_date="2099-12-31",
    )

    with (out_dir / "mlb_alt_bankroll_props.csv").open(newline="", encoding="utf-8") as handle:
        bankroll_rows = list(csv.DictReader(handle))
    with (out_dir / "alt_player_props.csv").open(newline="", encoding="utf-8") as handle:
        player_rows = list(csv.DictReader(handle))
    assert [row["event_id"] for row in bankroll_rows] == ["e1"]
    assert [row["player"] for row in player_rows] == ["Player One"]
