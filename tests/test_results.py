from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from outlier_scrapers import feedback, results


NOW = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)


def _scoreboard(*, completed: bool = True) -> dict:
    return {
        "events": [
            {
                "id": "espn-1",
                "status": {"type": {"completed": completed}},
                "competitions": [
                    {
                        "id": "espn-1",
                        "competitors": [
                            {
                                "homeAway": "away",
                                "score": "91",
                                "team": {"abbreviation": "ATL"},
                            },
                            {
                                "homeAway": "home",
                                "score": "87",
                                "team": {"abbreviation": "WSH"},
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _summary() -> dict:
    return {
        "boxscore": {
            "players": [
                {
                    "statistics": [
                        {
                            "name": "statistics",
                            "labels": ["MIN", "FG", "3PT", "REB", "AST", "PTS"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Allisha Gray"},
                                    "stats": ["34", "7-15", "2-5", "4", "3", "20"],
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }


def _seed(conn: sqlite3.Connection, suffix: str, selection: str, market_type: str, line: str):
    snapshot_id = f"snapshot-{suffix}"
    decision_id = f"decision-{suffix}"
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, market_type, event_starts_at, created_at
        ) VALUES (?, ?, 'WNBA', 'outlier-event', ?, ?, ?, ?, -110, 'Book', ?, ?, ?)
        """,
        (
            snapshot_id,
            "2026-08-07T20:00:00+00:00",
            f"market-{suffix}",
            f"outcome-{suffix}",
            selection,
            line,
            market_type,
            "2026-08-07T23:30:00+00:00",
            "2026-08-07T20:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO decisions (
            decision_id, snapshot_id, pipeline_verdict, final_verdict, units,
            created_at, updated_at
        ) VALUES (?, ?, 'PLAY', 'PLAY', 1, ?, ?)
        """,
        (decision_id, snapshot_id, NOW.isoformat(), NOW.isoformat()),
    )


def test_collects_final_game_and_player_results_with_strict_matching(tmp_path):
    db = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db)
    with feedback.open_database(db) as conn:
        _seed(conn, "total", "ATL @ WAS Total O/U OVER 175.5", "GAMELINE", "175.5")
        _seed(conn, "player", "Allisha Gray - Points OVER 18.5", "PLAYER_PROP", "18.5")

        def fake_fetch(url: str) -> dict:
            return _summary() if "/summary?" in url else _scoreboard()

        rows, summary = results.collect_settlement_rows(
            conn, ["WNBA"], lookback_days=3, now=NOW, fetch_json=fake_fetch
        )

    assert summary["settlement_rows"] == 2
    by_market = {row["market_id"]: row for row in rows}
    assert by_market["market-total"]["actual_result"] == "178"
    assert by_market["market-total"]["win_loss_push"] == "W"
    assert by_market["market-player"]["actual_result"] == "20"
    assert by_market["market-player"]["win_loss_push"] == "W"
    # Only one snapshot was ever captured for this outcome, so there is no
    # genuine close distinct from the take: reporting -110 here would be the
    # historical zero-CLV bug (the take silently standing in as its own
    # close). No line-movement export exists in this test, so it stays
    # unknown rather than a manufactured number.
    assert by_market["market-player"]["closing_price"] is None
    assert by_market["market-total"]["closing_price"] is None


def test_distinct_second_snapshot_is_used_as_the_close(tmp_path):
    db = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db)
    with feedback.open_database(db) as conn:
        _seed(conn, "player", "Allisha Gray - Points OVER 18.5", "PLAYER_PROP", "18.5")
        # A later, distinct capture of the same outcome/book closer to game
        # time is a genuine close and should be preferred over "unknown".
        conn.execute(
            """
            INSERT INTO market_snapshots (
                snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                selection, line, price, book, market_type, event_starts_at, created_at
            ) VALUES (
                'snapshot-player-close', '2026-08-07T23:00:00+00:00', 'WNBA',
                'outlier-event', 'market-player', 'outcome-player',
                'Allisha Gray - Points OVER 18.5', '19.5', -120, 'Book',
                'PLAYER_PROP', '2026-08-07T23:30:00+00:00', '2026-08-07T23:00:00+00:00'
            )
            """
        )

        def fake_fetch(url: str) -> dict:
            return _summary() if "/summary?" in url else _scoreboard()

        rows, _summary_stats = results.collect_settlement_rows(
            conn, ["WNBA"], lookback_days=3, now=NOW, fetch_json=fake_fetch
        )

    row = rows[0]
    assert row["closing_line"] == "19.5"
    assert row["closing_price"] == -120


def test_nonfinal_events_are_never_graded(tmp_path):
    db = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db)
    with feedback.open_database(db) as conn:
        _seed(conn, "spread", "ATL @ WAS Spread AWAY -5.5", "GAMELINE", "-5.5")
        rows, summary = results.collect_settlement_rows(
            conn,
            ["WNBA"],
            lookback_days=3,
            now=NOW,
            fetch_json=lambda _url: _scoreboard(completed=False),
        )

    assert rows == []
    assert summary["provider_event_count"] == 0
    assert summary["unmatched_event_count"] == 1


def test_spread_and_push_grading():
    event = results.FinalEvent(
        provider_event_id="1",
        sport="WNBA",
        event_date=NOW.date(),
        away="ATL",
        home="WSH",
        away_score=91,
        home_score=87,
        players={},
    )
    assert results._grade_row(
        {"selection": "ATL @ WAS Spread AWAY -4", "line": "-4", "market_type": "GAMELINE"},
        event,
    ) == (4, "PUSH")
    assert results._grade_row(
        {"selection": "ATL @ WAS Money Line HOME 0", "line": "0", "market_type": "GAMELINE"},
        event,
    ) == (-4, "L")


def test_team_total_matches_by_exact_team_alias_and_grades():
    event = results.FinalEvent(
        provider_event_id="1",
        sport="WNBA",
        event_date=NOW.date(),
        away="ATL",
        home="WSH",
        away_score=91,
        home_score=87,
        players={},
    )
    assert results._team_total_event_match(event, "WAS Team Total UNDER 87.5")
    assert results._grade_row(
        {
            "selection": "WAS Team Total UNDER 87.5",
            "line": "87.5",
            "market_type": "TEAM_PROP",
        },
        event,
    ) == (87, "W")


def test_mlb_whitelist_stat_derivations():
    stats = {
        "BATTING:H": 2,
        "BATTING:R": 1,
        "BATTING:RBI": 3,
        "BATTING:BB": 1,
        "PITCHING:K": 7,
        "PITCHING:ER": 2,
        "PITCHING:OUTS": 18,
    }
    assert results._player_actual("Hits + Runs + RBIs", stats, "MLB") == 6
    assert results._player_actual("Strikeouts", stats, "MLB") == 7
    assert results._player_actual("Batting Walks", stats, "MLB") == 1
    assert results._player_actual("Earned Runs", stats, "MLB") == 2
    assert results._player_actual("Outs", stats, "MLB") == 18
    assert results._player_actual("Walks", stats, "MLB") == 1


def test_wnba_three_pointer_and_double_double_derivations():
    stats = {"PTS": 12, "REB": 10, "AST": 4, "STL": 1, "BLK": 0, "3PT": 2}
    assert results._player_actual("Three Pointers", stats, "WNBA") == 2
    assert results._player_actual("Double Double", stats, "WNBA") == 1


def test_mlb_official_boxscore_exposes_full_whitelist_stats():
    payload = {
        "teams": {
            "away": {
                "players": {
                    "ID1": {
                        "person": {"fullName": "Test Hitter"},
                        "stats": {
                            "batting": {
                                "hits": 2,
                                "runs": 1,
                                "rbi": 3,
                                "baseOnBalls": 1,
                                "doubles": 1,
                                "totalBases": 5,
                            }
                        },
                    },
                    "ID2": {
                        "person": {"fullName": "Test Pitcher"},
                        "stats": {
                            "pitching": {
                                "strikeOuts": 7,
                                "earnedRuns": 2,
                                "inningsPitched": "6.2",
                            }
                        },
                    },
                }
            },
            "home": {"players": {}},
        }
    }
    players = results._mlb_boxscore_players(payload)
    assert players["TESTHITTER"]["BATTING:TB"] == 5
    assert players["TESTHITTER"]["BATTING:2B"] == 1
    assert results._player_actual("Hits + Runs + RBIs", players["TESTHITTER"], "MLB") == 6
    assert players["TESTPITCHER"]["PITCHING:OUTS"] == 20


def test_pdx_aliases_to_portland_for_event_match():
    event = results.FinalEvent(
        provider_event_id="1",
        sport="WNBA",
        event_date=NOW.date(),
        away="POR",
        home="PHX",
        away_score=88,
        home_score=85,
        players={},
    )
    assert results._event_match(event, "PDX @ PHX Spread AWAY +6.5")
    assert results._grade_row(
        {
            "selection": "PDX @ PHX Run Line AWAY +6.5",
            "line": "6.5",
            "market_type": "GAMELINE",
        },
        event,
    ) == (3, "W")


def test_player_last_name_fallback_matches_unique_boxscore_name():
    event = results.FinalEvent(
        provider_event_id="1",
        sport="WNBA",
        event_date=NOW.date(),
        away="CHI",
        home="SEA",
        away_score=82,
        home_score=80,
        players={"KAMILLACARDOSO": {"REB": 12, "AST": 2}},
    )
    hits = results._player_event_candidates([event], "Kamilla Cardoso - Rebounds UNDER 8.5")
    assert hits == [event]
    hits = results._player_event_candidates([event], "K. Cardoso - Rebounds UNDER 8.5")
    assert hits == [event]
    assert results._player_boxscore_key(event, "K. Cardoso - Rebounds UNDER 8.5") == "KAMILLACARDOSO"
    assert results._grade_row(
        {
            "selection": "K. Cardoso - Rebounds UNDER 8.5",
            "line": "8.5",
            "market_type": "REB",
        },
        event,
    ) == (12, "L")


def test_last_name_suffix_does_not_match_longer_name():
    event = results.FinalEvent(
        provider_event_id="1",
        sport="MLB",
        event_date=NOW.date(),
        away="NYY",
        home="TOR",
        away_score=4,
        home_score=3,
        players={"GOLDBERG": {"BATTING:H": 1}},
    )
    assert results._player_boxscore_key(event, "A. Berg - Hits OVER 0.5") is None
    assert results._player_event_candidates([event], "A. Berg - Hits OVER 0.5") == []


def test_captured_alt_lane_selections_grade_end_to_end():
    """Every alt-lane selection feedback synthesizes must be gradeable.

    The alt boards carry player/team/market columns, not the selection grammar
    this collector parses. Capture builds that grammar; if the two ever drift,
    alt rows would be captured and then sit unsettled forever, which is the
    exact blind spot the lanes were added to close.
    """

    from outlier_scrapers import feedback

    event = results.FinalEvent(
        provider_event_id="1",
        sport="MLB",
        event_date=NOW.date(),
        away="AWY",
        home="HME",
        away_score=3,
        home_score=6,
        players={"PITCHERONE": {"PITCHING:K": 7.0}},
    )
    cases = [
        (
            "alt_player_props",
            {"player": "Pitcher One", "market": "SO", "position": "OVER", "line": "4.5"},
            "PLAYER_PROP",
            (7.0, "W"),
        ),
        (
            "alt_team_totals",
            {"team": "HME", "position": "OVER", "line": "2.5"},
            "TEAM_PROP",
            (6.0, "W"),
        ),
        (
            "alt_spreads",
            {
                "matchup": "AWY @ HME",
                "position": "HOME",
                "line": "-1.5",
                "signed_line": "-1.5",
            },
            "GAMELINE",
            (3.0, "W"),
        ),
        (
            "alt_bankroll_props",
            {
                "matchup": "AWY @ HME",
                "market_type": "GAMELINE",
                "proposition": "TOTAL",
                "position": "OVER",
                "line": "7.5",
            },
            "GAMELINE",
            (9.0, "W"),
        ),
        (
            "alt_bankroll_props",
            {
                "matchup": "AWY @ HME",
                "market_type": "GAMELINE",
                "proposition": "MONEYLINE",
                "position": "HOME",
                "line": "",
            },
            "GAMELINE",
            (3.0, "W"),
        ),
    ]
    for kind, row, market_type, expected in cases:
        selection = feedback._alt_selection(kind, row)
        assert selection, f"{kind} produced no selection"
        graded = results._grade_row(
            {"selection": selection, "line": row.get("line", ""), "market_type": market_type},
            event,
        )
        assert graded == expected, f"{kind} -> {selection!r} graded {graded}, want {expected}"


def test_moneyline_without_a_line_still_grades():
    """A moneyline has no line; requiring one left every one of them unsettled."""

    event = results.FinalEvent(
        provider_event_id="1",
        sport="MLB",
        event_date=NOW.date(),
        away="AWY",
        home="HME",
        away_score=3,
        home_score=6,
        players={},
    )
    assert results._grade_row(
        {"selection": "AWY @ HME Money Line AWAY", "line": "", "market_type": "GAMELINE"},
        event,
    ) == (-3, "L")


def test_a_player_prop_without_a_line_is_still_refused():
    event = results.FinalEvent(
        provider_event_id="1",
        sport="MLB",
        event_date=NOW.date(),
        away="AWY",
        home="HME",
        away_score=3,
        home_score=6,
        players={"PITCHERONE": {"PITCHING:K": 7.0}},
    )
    assert (
        results._grade_row(
            {
                "selection": "Pitcher One - SO OVER 4.5",
                "line": "",
                "market_type": "PLAYER_PROP",
            },
            event,
        )
        is None
    )


def test_the_single_row_grader_never_picks_up_a_parlay(tmp_path):
    """A parlay spans events; grading it off one box score would be wrong.

    feedback.settle_parlays owns parlays, so the pending-row query that feeds
    _grade_row must exclude them outright rather than relying on their
    selection text happening not to match any grading branch.
    """

    from datetime import timedelta

    from outlier_scrapers import feedback

    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    starts_at = NOW.isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO market_snapshots (snapshot_id, captured_at, sport, event_id, "
            "market_id, outcome_id, selection, line, market_type, event_starts_at, "
            "selected, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "snap-parlay",
                starts_at,
                "MLB",
                "parlay-event",
                "alt_team_total_parlays",
                "parlay-outcome",
                "HME o2.5 + AWY o3.5",
                "",
                feedback.PARLAY_MARKET_TYPE,
                starts_at,
                1,
                starts_at,
            ),
        )
        conn.execute(
            "INSERT INTO market_snapshots (snapshot_id, captured_at, sport, event_id, "
            "market_id, outcome_id, selection, line, market_type, event_starts_at, "
            "selected, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "snap-single",
                starts_at,
                "MLB",
                "e1",
                "m1",
                "o1",
                "HME Team Total OVER 2.5",
                "2.5",
                "TEAM_PROP",
                starts_at,
                1,
                starts_at,
            ),
        )
        for decision_id, snapshot_id in (
            ("dec-parlay", "snap-parlay"),
            ("dec-single", "snap-single"),
        ):
            conn.execute(
                "INSERT INTO decisions (decision_id, snapshot_id, pipeline_verdict, units, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (decision_id, snapshot_id, "PLAY", 1.0, starts_at, starts_at),
            )
        conn.commit()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        pending = results._pending_rows(
            conn, ["MLB"], NOW - timedelta(days=1), NOW + timedelta(days=1)
        )

    assert [row["snapshot_id"] for row in pending] == ["snap-single"]
