from outlier_scrapers.ultimate_alt import (
    build_ultimate_alt_board,
    build_ultimate_alt_parlays,
)


def _spread(**overrides):
    row = {
        "league": "MLB",
        "event_id": "spread-event",
        "event_starts_at": "2099-08-08T19:00:00-04:00",
        "matchup": "TOR @ NYY",
        "team": "TOR",
        "market_type": "GAMELINE",
        "proposition": "SPREAD",
        "position": "AWAY",
        "market_id": "spread-market",
        "outcome_id": "spread-outcome",
        "line": 5.5,
        "selection": "TOR +5.5",
        "l5_pct": 100,
        "l10_pct": 100,
        "l10_total": 10,
        "best_book": "Novig",
        "best_price": -110,
        "decimal_price": 1.9091,
        "implied_prob": 0.52381,
        "scope": "full_game",
    }
    row.update(overrides)
    return row


def _total(**overrides):
    row = {
        "league": "WNBA",
        "event_id": "total-event",
        "event_starts_at": "2099-08-08T20:00:00-04:00",
        "matchup": "ATL @ WAS",
        "team": "ATL",
        "market_type": "TEAM_PROP",
        "proposition": "TOTAL",
        "position": "OVER",
        "market_id": "total-market",
        "outcome_id": "total-outcome",
        "line": 80.5,
        "l5_pct": 100,
        "l10_pct": 100,
        "l10_total": 10,
        "best_book": "Hard Rock",
        "best_price": -110,
        "decimal_price": 1.9091,
        "implied_prob": 0.52381,
        "scope": "full_game",
    }
    row.update(overrides)
    return row


def _player(**overrides):
    row = {
        "league": "MLB",
        "event_id": "player-event",
        "event_starts_at": "2099-08-08T21:00:00-04:00",
        "matchup": "BAL @ TEX",
        "player": "Pitcher",
        "player_id": "p1",
        "team": "TEX",
        "market": "ER",
        "position": "OVER",
        "line": 0.5,
        "best_book": "Fanatics",
        "best_odds": -110,
        "decimal_price": 1.9091,
        "implied_prob": 0.52381,
        "l5_pct": 100,
        "l10_pct": 100,
        "season_pct": 90,
        "market_id": "player-market",
        "outcome_id": "player-outcome",
    }
    row.update(overrides)
    return row


def test_board_rejects_heavy_juice_when_hit_rate_does_not_clear_price():
    board = build_ultimate_alt_board(
        spread_rows=[_spread(best_price=-1000, decimal_price=1.1, implied_prob=0.909091)],
        total_rows=[],
        player_rows=[],
    )

    assert board[0]["shadow_status"] == "REJECTED"
    assert "CONSERVATIVE_EDGE_BELOW_1_5" in board[0]["rejection_reasons"]


def test_board_ranks_qualified_markets_on_one_price_adjusted_surface():
    board = build_ultimate_alt_board(
        spread_rows=[_spread()],
        total_rows=[_total()],
        player_rows=[_player()],
    )

    assert {row["alt_type"] for row in board if row["shadow_status"] == "QUALIFIED"} == {
        "SPREAD",
        "TOTAL",
        "PLAYER_PROP",
    }
    assert all(row["actionable"] == "false" for row in board)
    assert all(row["board"].startswith("ALT_SHADOW_") for row in board)
    assert all(row["recommended_units_pre_news"] == "" for row in board)
    by_type = {row["alt_type"]: row for row in board}
    assert by_type["PLAYER_PROP"]["selection"] == "Pitcher - ER OVER 0.5"
    assert by_type["TOTAL"]["selection"] == "ATL Team Total OVER 80.5"


def test_board_preserves_internal_event_start_for_settlement_capture():
    board = build_ultimate_alt_board(
        spread_rows=[],
        total_rows=[],
        player_rows=[
            _player(
                event_starts_at="",
                _event_starts_at="2099-08-08T21:00:00-04:00",
            )
        ],
    )

    assert board[0]["event_starts_at"] == "2099-08-08T21:00:00-04:00"


def test_parlays_require_cross_event_and_multiple_alt_types():
    board = build_ultimate_alt_board(
        spread_rows=[_spread()],
        total_rows=[_total()],
        player_rows=[_player()],
    )

    parlays = build_ultimate_alt_parlays(board)

    assert parlays
    assert all("SHADOW_ONLY" in row["quality_flags"] for row in parlays)
    assert all(
        len(row["event_ids"].split(",")) == len(set(row["event_ids"].split(","))) for row in parlays
    )
    assert all(len(row["alt_types"].split(",")) >= 2 for row in parlays)


def test_same_event_candidates_never_form_parlay():
    board = build_ultimate_alt_board(
        spread_rows=[_spread(event_id="same")],
        total_rows=[_total(event_id="same")],
        player_rows=[],
    )

    assert build_ultimate_alt_parlays(board) == []


def test_board_keeps_only_best_leg_per_event_before_parlay_building():
    board = build_ultimate_alt_board(
        spread_rows=[_spread(event_id="same")],
        total_rows=[_total(event_id="same")],
        player_rows=[],
    )

    assert sum(row["shadow_status"] == "QUALIFIED" for row in board) == 1
    rejected = next(row for row in board if row["shadow_status"] == "REJECTED")
    assert rejected["rejection_reasons"] == "EVENT_EXPOSURE_CAP"
