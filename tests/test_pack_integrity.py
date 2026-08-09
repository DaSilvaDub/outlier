"""Daily Automated Structural Integrity & Quality Auditor Tests

Guarantees that pipeline outputs are checked for structural integrity bugs before
exporting daily packs or research desk prompt cards.

Audited Gates:
1. Pack hard-ban exclusion (HR, Walks Allowed tokens); generation whitelist is normalizer-owned
2. Valid Event Slate Indexing (No UNINDEXED_SLATE_GAME false positives for indexed games)
3. Briefing Pack Deduplication (No duplicate totals in flagged cards)
4. Non-Actionable Stake Protection (Blank recommended units on actionable=false rows)
"""

from outlier_scrapers.pack import is_excluded_market, build_briefing
from outlier_scrapers.game_totals import build_game_totals
from tests.test_game_totals import _norm_record
from tests.test_pack import ev_card, make_row
from datetime import datetime, timezone

def test_audit_prohibited_markets_exclusion():
    """Gate 1: Ensure all prohibited market tokens are recognized by exclusion filter."""
    prohibited_tokens = [
        "HR", "HOME_RUNS",
        "WALKS_ALLOWED", "WALKSALLOWED", "PITCHER_WALKS", "PITCHING_WALKS", "WALKS ALLOWED"
    ]
    for token in prohibited_tokens:
        assert is_excluded_market(token, token) is True, f"Failed to exclude prohibited market token: {token}"

def test_audit_indexed_slate_game_integrity():
    """Gate 2: Ensure valid indexed slate games never receive UNINDEXED_SLATE_GAME flags."""
    games_norm = {
        "generated_at": "2026-07-26T12:00:00Z",
        "context": {
            "events": {
                "e1": {
                    "event_id": "e1",
                    "starts_at": "2026-07-26T17:35:00Z",
                    "home_team_id": "bos",
                    "away_team_id": "tor"
                }
            }
        },
        "records": [
            _norm_record("m_idx", 8.5, "OVER", [{"book": "DK", "odds": -110}], event_id="e1"),
            _norm_record("m_idx", 8.5, "UNDER", [{"book": "DK", "odds": -110}], event_id="e1"),
        ]
    }
    rows = build_game_totals([], games_norm, sport="MLB", now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert len(rows) > 0
    for r in rows:
        assert "UNINDEXED_SLATE_GAME" not in r.get("quality_flags", ""), "Indexed slate game received false positive UNINDEXED_SLATE_GAME flag"

def test_audit_briefing_totals_deduplication():
    """Gate 3: Ensure game totals board markets do not duplicate under non-actionable flagged cards."""
    totals_rows = [
        {
            "market_id": "m_total_777",
            "sport": "MLB",
            "selection": "TOR @ BOS Total O/U UNDER 8.5",
            "line": 8.5,
            "price": -110,
            "edge_pct": 0.05,
            "actionable": "true",
        }
    ]
    candidate_rows = [
        {
            "market_id": "m_total_777",
            "sport": "MLB",
            "selection": "TOR @ BOS Total O/U UNDER 8.5",
            "line": 8.5,
            "price": -120,
            "_board": "flagged",
            "actionable": "false",
            "data_quality_flags": "SOURCE_INTEGRITY_FLAG",
            "event_id": "e1",
        }
    ]
    briefing = build_briefing(rows=candidate_rows, target_date="2026-07-26", totals_rows=totals_rows)
    assert "Non-actionable flagged cards" not in briefing

def test_audit_non_actionable_zero_units_rule():
    """Gate 4: Ensure non-actionable candidate rows carry no unit recommendation."""
    card = ev_card(market_type="MONEYLINE", market="MONEYLINE")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": -112,
            "book_decimal_odds": 1.9,
            "calculated_ev_pct": -0.05,
        }
    ]

    row = make_row(card, ev)

    assert row["actionable"] == "false"
    assert row["recommended_units_pre_news"] == ""
