"""Unit tests for deterministic game totals projection math."""

from datetime import datetime, timezone

import pytest

from outlier_scrapers.game_totals import (
    MIN_EDGE_TOTALS,
    aggregate_line_p_over,
    build_game_totals,
    build_market_ladder,
    compute_side_edge,
    devig_book_pair,
    interpolate_fair_total,
    pick_best_side,
)
from outlier_scrapers.sizing import compute_sizing


def _norm_record(
    market_id: str,
    line: float,
    position: str,
    books: list[dict],
    *,
    market_type: str = "GAMELINE",
    proposition: str = "TOTAL",
    event_id: str = "E1",
    event_starts_at: str | None = "2099-12-31T00:00:00Z",
) -> dict:
    return {
        "market_id": market_id,
        "event_id": event_id,
        "event_starts_at": event_starts_at,
        "market_type": market_type,
        "proposition": proposition,
        "market": proposition,
        "scope": "full_game",
        "line": line,
        "position": position,
        "matchup": "A @ B",
        "books": books,
    }


def test_devig_book_pair_symmetric():
    pair = devig_book_pair(-110, -110)
    assert pair is not None
    p_over, p_under = pair
    assert abs(p_over + p_under - 1.0) < 0.01
    assert abs(p_over - 0.5) < 0.02


def test_aggregate_line_requires_two_books():
    over = {"dk": -110, "fd": -108}
    under = {"dk": -110}
    p_over, count, flags = aggregate_line_p_over(over, under)
    assert count == 1
    assert "SINGLE_BOOK" in flags


def test_aggregate_line_deduplicates_operator_aliases():
    over = {"BetRivers": -110, "Unibet": -108, "DraftKings": -105}
    under = {"BetRivers": -110, "Unibet": -112, "DraftKings": -115}
    p_over, count, flags = aggregate_line_p_over(over, under)
    assert p_over is not None
    assert count == 2
    assert flags == []


@pytest.mark.parametrize(
    ("over_price", "under_price"),
    [(-200, -200), (100, 100)],
)
def test_aggregate_line_rejects_invalid_overround(over_price, under_price):
    p_over, count, flags = aggregate_line_p_over(
        {"DraftKings": over_price, "FanDuel": over_price},
        {"DraftKings": under_price, "FanDuel": under_price},
    )
    assert p_over is None
    assert count == 0
    assert "NO_VALID_CONSENSUS" in flags


def test_aggregate_line_missing_side():
    p_over, count, flags = aggregate_line_p_over({"dk": -110}, {})
    assert p_over is None
    assert "MISSING_SIDE" in flags


def test_interpolate_fair_total_brackets_half():
    ladder_p = {8.0: 0.58, 8.5: 0.42}
    fair, flags = interpolate_fair_total(ladder_p)
    assert not flags
    assert fair == 8.0 or fair == 8.5  # near 8.25 rounded to 0.5


def test_interpolate_fair_total_non_bracketing():
    fair, flags = interpolate_fair_total({8.0: 0.6, 8.5: 0.55})
    assert fair is None
    assert "NON_BRACKETING_LADDER" in flags


def test_pick_best_side_prefers_higher_edge():
    side, price, edge = pick_best_side(0.55, -110, -110)
    assert side == "OVER"
    assert edge is not None and edge > 0


def test_build_game_totals_actionable_at_three_pct_edge():
    games_norm = {
        "generated_at": "2026-07-07T12:00:00Z",
        "records": [
            _norm_record("m1", 8.5, "OVER", [{"book": "DK", "odds": -125}, {"book": "FD", "odds": -122}]),
            _norm_record("m1", 8.5, "UNDER", [{"book": "DK", "odds": 105}, {"book": "FD", "odds": 102}]),
            _norm_record("m1", 9.0, "OVER", [{"book": "DK", "odds": 110}, {"book": "FD", "odds": 108}]),
            _norm_record("m1", 9.0, "UNDER", [{"book": "DK", "odds": -130}, {"book": "FD", "odds": -128}]),
        ],
    }
    candidates = [
        {
            "market_id": "m1",
            "market_type": "GAMELINE",
            "player_id": "",
            "selection": "A @ B Total O/U OVER 8.5",
            "line": 8.5,
            "price": -125,
            "edge_pct": 0.05,
            "_proposition": "TOTAL",
            "_event_starts_at": "2099-07-07T23:10:00+00:00",
        }
    ]
    rows = build_game_totals(
        candidates,
        games_norm,
        sport="MLB",
        now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["fair_total"] != ""
    if row["edge_pct"] != "" and float(row["edge_pct"]) >= MIN_EDGE_TOTALS:
        assert row["actionable"] == "true"


def test_build_game_totals_single_book_not_actionable():
    games_norm = {
        "records": [
            _norm_record("m2", 174.5, "OVER", [{"book": "DK", "odds": -110}]),
            _norm_record("m2", 174.5, "UNDER", [{"book": "DK", "odds": -110}]),
        ],
    }
    rows = build_game_totals([], games_norm, sport="WNBA")
    assert rows[0]["actionable"] == "false"
    assert "SINGLE_BOOK" in rows[0]["quality_flags"]


def test_build_game_totals_integer_line_push_blocked():
    games_norm = {
        "records": [
            _norm_record("m3", 8.0, "OVER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -108}]),
            _norm_record("m3", 8.0, "UNDER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -112}]),
            _norm_record("m3", 8.5, "OVER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -108}]),
            _norm_record("m3", 8.5, "UNDER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -112}]),
        ],
    }
    rows = build_game_totals([], games_norm, sport="MLB")
    row = next(r for r in rows if float(r["line"]) == 8.0)
    assert row["sizing_flags"] == "push_capable_no_prob"
    assert row["actionable"] == "false"
    # Option 2 fallback: without a derived push mass, blank the two-way edge
    # so a push-contaminated number never displays as if it were honest EV.
    assert row["edge_pct"] == ""


def test_build_game_totals_integer_line_with_push_prob():
    games_norm = {
        "records": [
            _norm_record("m3", 7.5, "OVER", [{"book": "DK", "odds": -140}, {"book": "FD", "odds": -140}]),
            _norm_record("m3", 7.5, "UNDER", [{"book": "DK", "odds": 120}, {"book": "FD", "odds": 120}]),
            _norm_record("m3", 8.0, "OVER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -110}]),
            _norm_record("m3", 8.0, "UNDER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -110}]),
            _norm_record("m3", 8.5, "OVER", [{"book": "DK", "odds": 110}, {"book": "FD", "odds": 110}]),
            _norm_record("m3", 8.5, "UNDER", [{"book": "DK", "odds": -130}, {"book": "FD", "odds": -130}]),
        ],
    }
    candidates = [{"market_id": "m3", "line": 8.0, "market_type": "GAMELINE", "_proposition": "TOTAL"}]
    rows = build_game_totals(candidates, games_norm, sport="MLB")
    row = next(r for r in rows if float(r["line"]) == 8.0)
    assert row["sizing_flags"] == ""
    assert isinstance(row["push_prob"], float)
    assert row["push_prob"] > 0
    # Option 2: display edge is push-aware via sizing.compute_sizing; gate stays closed.
    assert row["actionable"] == "false"
    assert row["edge_pct"] != ""
    assert row["decimal_price"] not in (None, "")
    assert row["best_side"] in ("OVER", "UNDER")

    # Recompute model_prob the same way Option 2 does: two-way headline p_side
    # + derived push_prob into sizing.compute_sizing. Do not trust
    # projected_over_prob here (master may still write a stale loop var — F2).
    side = row["best_side"]
    p_over, _, _ = aggregate_line_p_over(
        {"DK": -110, "FD": -110},
        {"DK": -110, "FD": -110},
    )
    assert p_over is not None
    p_side = p_over if side == "OVER" else 1.0 - p_over
    decimal = float(row["decimal_price"])
    push = float(row["push_prob"])
    expected = compute_sizing(decimal_price=decimal, model_prob=p_side, push_prob=push)
    assert expected.edge_pct is not None
    assert float(row["edge_pct"]) == pytest.approx(round(expected.edge_pct, 4))

    # And it must differ from the naive two-way edge whenever push mass is non-zero
    # (otherwise Option 2 is a no-op and the number is still push-contaminated).
    two_way_edge, _ = compute_side_edge(side, p_over, row["best_price"])
    assert two_way_edge is not None
    assert float(row["edge_pct"]) != pytest.approx(two_way_edge)



def test_build_game_totals_live_event_flag():
    games_norm = {
        "records": [
            _norm_record("m4", 8.5, "OVER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -108}], event_starts_at="2020-01-01T00:00:00Z"),
            _norm_record("m4", 8.5, "UNDER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -112}], event_starts_at="2020-01-01T00:00:00Z"),
            _norm_record("m4", 9.0, "OVER", [{"book": "DK", "odds": 100}, {"book": "FD", "odds": 102}], event_starts_at="2020-01-01T00:00:00Z"),
            _norm_record("m4", 9.0, "UNDER", [{"book": "DK", "odds": -120}, {"book": "FD", "odds": -122}], event_starts_at="2020-01-01T00:00:00Z"),
        ],
        "generated_at": "2025-01-01T00:00:00Z",
    }
    past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    candidates = [
        {
            "market_id": "m4",
            "market_type": "GAMELINE",
            "player_id": "",
            "line": 8.5,
            "_proposition": "TOTAL",
            "_event_starts_at": past,
        }
    ]
    rows = build_game_totals(
        candidates, games_norm, sport="MLB", now=datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    assert "LOCKED_OR_UNVERIFIED_EVENT" in rows[0]["quality_flags"]
    assert rows[0]["actionable"] == "false"


def test_build_game_totals_missing_start_fails_closed_without_candidate():
    now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    games_norm = {
        "generated_at": now.isoformat(),
        "records": [
            _norm_record("m6", 8.5, "OVER", [{"book": "DK", "odds": -125}, {"book": "FD", "odds": -122}], event_starts_at=None),
            _norm_record("m6", 8.5, "UNDER", [{"book": "DK", "odds": 105}, {"book": "FD", "odds": 102}], event_starts_at=None),
            _norm_record("m6", 9.0, "OVER", [{"book": "DK", "odds": 110}, {"book": "FD", "odds": 108}], event_starts_at=None),
            _norm_record("m6", 9.0, "UNDER", [{"book": "DK", "odds": -130}, {"book": "FD", "odds": -128}], event_starts_at=None),
        ],
    }
    row = build_game_totals([], games_norm, sport="MLB", now=now)[0]
    assert "LOCKED_OR_UNVERIFIED_EVENT" in row["quality_flags"]
    assert row["actionable"] == "false"


def test_build_game_totals_stale_source_fails_closed():
    now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    games_norm = {
        "generated_at": "2026-07-07T05:00:00Z",
        "records": [
            _norm_record("m7", 8.5, "OVER", [{"book": "DK", "odds": -125}, {"book": "FD", "odds": -122}]),
            _norm_record("m7", 8.5, "UNDER", [{"book": "DK", "odds": 105}, {"book": "FD", "odds": 102}]),
            _norm_record("m7", 9.0, "OVER", [{"book": "DK", "odds": 110}, {"book": "FD", "odds": 108}]),
            _norm_record("m7", 9.0, "UNDER", [{"book": "DK", "odds": -130}, {"book": "FD", "odds": -128}]),
        ],
    }
    row = build_game_totals([], games_norm, sport="MLB", now=now)[0]
    assert "STALE_DATA" in row["quality_flags"]
    assert row["actionable"] == "false"


def test_projected_over_prob_matches_headline_line():
    """projected_over_prob must be the headline line's p_over, not a stale
    leftover from the ladder-building loop. Here the headline (8.5) is inserted
    BEFORE 9.5, so a stale loop variable would report 9.5's probability and the
    over/under pair would not sum to 1 (regression guard for F2)."""
    games_norm = {
        "records": [
            _norm_record("m5", 8.5, "OVER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -110}]),
            _norm_record("m5", 8.5, "UNDER", [{"book": "DK", "odds": -110}, {"book": "FD", "odds": -110}]),
            _norm_record("m5", 9.5, "OVER", [{"book": "DK", "odds": 200}, {"book": "FD", "odds": 200}]),
            _norm_record("m5", 9.5, "UNDER", [{"book": "DK", "odds": -250}, {"book": "FD", "odds": -250}]),
        ],
    }
    candidates = [{"market_id": "m5", "line": 8.5, "market_type": "GAMELINE", "_proposition": "TOTAL"}]
    rows = build_game_totals(candidates, games_norm, sport="MLB")
    row = next(r for r in rows if float(r["line"]) == 8.5)
    over = float(row["projected_over_prob"])
    under = float(row["projected_under_prob"])
    # Over and under for the SAME (headline) line must be complementary.
    assert abs(over + under - 1.0) < 1e-6
    # The 8.5 line at -110/-110 devigs to ~0.5 over; the 9.5 line is far lower.
    assert abs(over - 0.5) < 0.05


def test_build_market_ladder_groups_sides():
    records = [
        _norm_record("m1", 8.5, "OVER", [{"book": "DK", "odds": -110}]),
        _norm_record("m1", 8.5, "UNDER", [{"book": "DK", "odds": -110}]),
    ]
    ladder = build_market_ladder(records)
    assert 8.5 in ladder
    assert "dk" in ladder[8.5]["over"]
    assert "dk" in ladder[8.5]["under"]
