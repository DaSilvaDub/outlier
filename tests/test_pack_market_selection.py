"""build_selection must keep the side token the settlement grader needs."""

from datetime import date

from outlier_scrapers import results
from outlier_scrapers.pack_market import build_selection


def _wnba_event() -> results.FinalEvent:
    return results.FinalEvent(
        provider_event_id="1",
        sport="WNBA",
        event_date=date(2026, 10, 25),
        away="LVA",
        home="SEA",
        away_score=80,
        home_score=75,
        players={"JACKIEYOUNG": {"PTS": 20, "REB": 4, "AST": 5, "TO": 5}},
    )


def test_side_survives_a_market_label_that_merely_contains_it():
    """"Turnovers" contains "over": the OVER side must still be spelled out.

    A bare substring test read the label as an OVER already present, so the
    OVER row lost its side token while the UNDER twin kept one.
    """
    assert (
        build_selection("Jackie Young", "Jackie Young - Turnovers", "OVER", 3.5, "TURNOVERS")
        == "Jackie Young - Turnovers OVER 3.5"
    )
    assert (
        build_selection("Jackie Young", "Jackie Young - Turnovers", "UNDER", 3.5, "TURNOVERS")
        == "Jackie Young - Turnovers UNDER 3.5"
    )


def test_side_survives_a_player_name_that_merely_contains_it():
    assert (
        build_selection("Grover Stewart", "Grover Stewart - Tackles", "OVER", 3.5, "TACKLES")
        == "Grover Stewart - Tackles OVER 3.5"
    )


def test_a_side_already_spelled_out_is_still_not_doubled():
    """The dedup this guard exists for: no "Foo Over OVER 5.5"."""
    assert build_selection("Player One", "Player One Over", "OVER", 5.5, "POINTS") == (
        "Player One Over 5.5"
    )
    assert build_selection("Player One", "Player One Under", "UNDER", 5.5, "POINTS") == (
        "Player One Under 5.5"
    )


def test_both_sides_of_a_turnovers_prop_settle():
    """The point of the side token: results._grade_row needs a literal side."""
    event = _wnba_event()
    graded = {
        side: results._grade_row(
            {
                "selection": build_selection(
                    "Jackie Young", "Jackie Young - Turnovers", side, 3.5, "TURNOVERS"
                ),
                "line": "3.5",
                "market_type": "TO",
            },
            event,
        )
        for side in ("OVER", "UNDER")
    }
    assert graded == {"OVER": (5, "W"), "UNDER": (5, "L")}
