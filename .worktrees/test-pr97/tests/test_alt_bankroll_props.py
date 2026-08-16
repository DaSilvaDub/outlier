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


def test_bankroll_board_accepts_widened_book_pool_not_just_hard_rock():
    """A qualifying line with no Hard Rock offer at all -- but a qualifying
    price on Fanatics, Midnite, DraftKings, or Novig -- must still surface.
    Regression for a real gap: several genuinely qualifying alt lines only
    existed on these other books and were silently dropped when the board
    was Hard Rock-exclusive."""
    fanatics_only = _game(event_id="fanatics-only", book="Fanatics", odds=-250)
    midnite_only = _game(event_id="midnite-only", book="Midnite", odds=-300)
    draftkings_only = _game(event_id="dk-only", book="DraftKings", odds=-400)
    novig_only = _game(event_id="novig-only", book="Novig", odds=-500)
    still_excluded = _game(event_id="fanduel-only", book="FanDuel", odds=-250)

    rows = _board([fanatics_only, midnite_only, draftkings_only, novig_only, still_excluded])

    assert {row["event_id"] for row in rows} == {
        "fanatics-only",
        "midnite-only",
        "dk-only",
        "novig-only",
    }
    by_event = {row["event_id"]: row for row in rows}
    assert by_event["fanatics-only"]["best_book"] == "Fanatics"
    assert by_event["midnite-only"]["best_book"] == "Midnite"


def test_bankroll_board_picks_best_qualifying_price_across_allowed_books():
    """When a record has a non-qualifying Hard Rock price (outside -110/-1000)
    alongside a qualifying price on another allowed book, the qualifying
    book's price must be picked -- not silently dropped because Hard Rock's
    own price failed the window."""
    rec = _game(event_id="mixed", book="Hard Rock", odds=-105)
    rec["books"] = [
        {"book": "Hard Rock", "odds": -105},  # outside -110..-1000, must not win
        {"book": "Fanatics", "odds": -300},  # qualifies, should be picked
    ]

    rows = _board([rec])

    assert len(rows) == 1
    assert rows[0]["best_book"] == "Fanatics"
    assert rows[0]["best_price"] == -300


def test_bankroll_board_still_excludes_hardrock_r_as_a_distinct_book():
    """'Hardrock R' is a distinctly-named book in the raw feed, not a
    formatting variant of 'Hard Rock' -- must not be silently aliased."""
    rows = _board([_game(event_id="hardrock-r", book="Hardrock R", odds=-250)])
    assert rows == []


def test_bankroll_board_enforces_scope_whitelist_book_odds_and_hit_rates():
    rows = _board(
        [
            _game(),
            _game(event_id="team", market_type="TEAM_PROP", proposition="RUNS", market="R"),
            _game(event_id="partial", scope="first_inning", period_label="1I"),
            _game(event_id="bad-market", market_type="TEAM_PROP", proposition="RBI", market="RBI"),
            _game(event_id="bad-price", odds=-105),
            _game(event_id="bad-book", book="FanDuel"),
            _game(event_id="bad-l5", l5=0.7),
            _game(event_id="bad-l10", l10=0.7),
        ]
    )

    assert {row["event_id"] for row in rows} == {"e1", "team"}
    assert all(row["best_book"] == "Hard Rock" for row in rows)
    assert all(-1000 <= row["best_price"] <= -110 for row in rows)
    assert all(row["scope"] == "full_game" for row in rows)


def test_bankroll_board_accepts_full_inclusive_odds_window():
    """The odds window is [-1000, -110] — both the moderate-favorite band and
    the heavy-favorite band down to -1000 must be accepted, inclusive of both
    boundaries. (Ceiling widened from -200 to -110 so lighter-juice favorites
    qualify too.)"""
    rows = _board(
        [
            _game(event_id="moderate-favorite", odds=-250),
            _game(event_id="heavy-favorite", odds=-900),
            _game(event_id="min-boundary", odds=-1000),
            _game(event_id="max-boundary", odds=-110),
            _game(event_id="too-extreme", odds=-1001),
            _game(event_id="too-weak", odds=-109),
        ]
    )

    assert {row["event_id"] for row in rows} == {
        "moderate-favorite",
        "heavy-favorite",
        "min-boundary",
        "max-boundary",
    }


def test_bankroll_board_enforces_75_pct_hit_rate_floor():
    """L5 and L10 floors were loosened from 100%/90% to a >=75% floor on both."""
    rows = _board(
        [
            _game(event_id="min-boundary", l5=0.75, l10=0.75),
            _game(event_id="below-l5", l5=0.74, l10=0.9),
            _game(event_id="below-l10", l5=1.0, l10=0.74),
        ]
    )

    assert {row["event_id"] for row in rows} == {"min-boundary"}


def test_bankroll_game_total_uses_weaker_team_not_a_blended_average():
    """A game total depends on both teams; pooling their windows into one
    average can mask a weak side behind a strong partner (e.g. 90%/70%
    pools to a passing 80%, hiding that the 70% side alone would fail the
    75% floor). l5_pct/l10_pct must reflect the WEAKER (minimum) side, and
    the per-team breakdown must still be exposed via home_l5_pct/away_l5_pct
    etc. for transparency."""
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
    assert rows[0]["l10_pct"] == 90.0
    assert rows[0]["home_l5_pct"] == 100.0
    assert rows[0]["away_l5_pct"] == 100.0
    assert rows[0]["home_l10_pct"] == 90.0
    assert rows[0]["away_l10_pct"] == 100.0


def test_bankroll_game_total_rejected_when_weaker_team_misses_floor():
    """The exact masking scenario the site surfaced: home 90%, away 70% on
    L10 pools to a passing 80% average, but the weaker (away) side alone is
    below the 75% floor and must reject the row."""
    total = _game(proposition="TOTAL", market="TOTAL")
    total["position"] = "OVER"
    total["team"] = None
    total["stats"] = {
        "homeSummaryStat": {"l5": 1.0, "l10": 0.9},
        "awaySummaryStat": {"l5": 1.0, "l10": 0.7},
    }

    rows = _board([total])

    assert rows == []


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
