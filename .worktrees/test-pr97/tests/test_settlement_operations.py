import sqlite3
from pathlib import Path
import pytest

from outlier_scrapers import feedback

def _candidate(
    *,
    market_id: str = "m1",
    outcome_id: str = "o1",
    line: float = 10.5,
    selected: bool = True,
    actionable: bool = True,
) -> dict:
    return {
        "sport": "WNBA",
        "event_id": "e1",
        "market_id": market_id,
        "outcome_id": outcome_id,
        "market_type": "PLAYER_PROP",
        "player_id": "p1",
        "selection": f"Player Points OVER {line}",
        "line": str(line),
        "price": "100",
        "decimal_price": "2.0",
        "book": "DK",
        "as_of": "2026-07-13T16:00:00+00:00",
        "model_prob": "0.60",
        "market_consensus_prob": "0.60",
        "final_blended_prob": "0.60",
        "push_prob": "0.0",
        "implied_prob": "0.50",
        "edge_pct": "0.10",
        "recommended_units_pre_news": "2.0",
        "data_quality_flags": "",
        "board": "A" if actionable else "B",
        "actionable": "true" if actionable else "false",
        "selected": "true" if selected else "false",
    }


def _seed_db(tmp_path: Path, rows: list[dict]):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    now = feedback._utc_now()
    
    with sqlite3.connect(db_path) as conn:
        for i, row in enumerate(rows):
            snapshot_id = feedback._stable_id("snapshot", str(i))
            decision_id = feedback._stable_id("decision", snapshot_id)
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    player_id, selection, line, price, book, market_consensus_prob,
                    independent_model_prob, final_blended_prob, blend_market_weight,
                    blend_model_weight, blend_weight_source, blend_model_version,
                    blend_segment, push_prob, edge, data_quality_flags,
                    data_quality_tier, event_starts_at, hours_before_game, odds_range,
                    time_before_game, market_type, model_prob_source, decimal_price,
                    implied_prob, board, selected, signal_flags, hit_rate_component,
                    insight_component, movement_component, orf_component, pack_path, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    snapshot_id, now, row.get("sport", "WNBA"), row.get("event_id", "e1"), 
                    row.get("market_id", "m1"), row.get("outcome_id", "o1"), row.get("player_id", "p1"), 
                    row.get("selection", "sel"), row.get("line", "10.5"), 100, 
                    row.get("book", "DK"), 0.5, 0.5, 0.5, 0.5, 0.5, "", "", "", 0.0, 0.1, "", "", "", 1.0, "", "", 
                    "", "", 2.0, 0.5, "", 1, "", 0.0, 0.0, 0.0, 0.0, "", now
                )
            )
            units = float(row.get("recommended_units_pre_news", "2.0"))
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict,
                    A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                    units, kill_reason, news_override, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (decision_id, snapshot_id, "PLAY", "", "", "", "", "PLAY", units, "", "", now, now)
            )
    return db_path


def test_settlement_reconciliation_summary(tmp_path):
    rows = [
        _candidate(line=10.5),
        _candidate(line=11.5)
    ]
    db_path = _seed_db(tmp_path, rows)
    
    settlements = [
        # Match correctly
        {"sport": "WNBA", "event_id": "e1", "market_family": "m1", "subject_id": "p1", "line": "10.5", "book": "DK", "win_loss_push": "W", "actual_result": "12", "closing_line": "11.5", "closing_price": "-110"},
        # Match correctly with alternative keys
        {"sport": "WNBA", "event_id": "e1", "market_id": "m1", "player_id": "p1", "line": "11.5", "book": "DK", "win_loss_push": "L", "actual_result": "10"},
        # Unmatched
        {"sport": "WNBA", "event_id": "e2", "market_id": "m1", "player_id": "p1", "line": "10.5", "book": "DK", "win_loss_push": "W", "actual_result": "12"},
    ]

    stats = feedback.import_settlements(db_path, settlements)
    
    assert stats["unmatched_count"] == 1
    assert stats["ambiguous_count"] == 0
    assert stats["duplicate_count"] == 0
    assert stats["updated_count"] == 2

    # Duplicate run should just hit duplicate_count
    stats2 = feedback.import_settlements(db_path, settlements)
    assert stats2["unmatched_count"] == 1
    assert stats2["ambiguous_count"] == 0
    assert stats2["duplicate_count"] == 2
    assert stats2["updated_count"] == 0


def test_durable_identity_matching(tmp_path):
    # Same event and market but different lines
    rows = [
        _candidate(line=10.5, outcome_id="o1"),
        _candidate(line=11.5, outcome_id="o2")
    ]
    db_path = _seed_db(tmp_path, rows)
    
    # We rely on line matching
    settlements = [
        {"sport": "WNBA", "event_id": "e1", "market_family": "m1", "subject_id": "p1", "line": "11.5", "book": "DK", "win_loss_push": "W"}
    ]
    stats = feedback.import_settlements(db_path, settlements)
    assert stats["updated_count"] == 1
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        settlement = conn.execute("SELECT * FROM settlements").fetchone()
        assert settlement["outcome_id"] == "o2"


def test_win_loss_push_clv_pnl(tmp_path):
    rows = [
        _candidate(line=10.5)
    ]
    db_path = _seed_db(tmp_path, rows)
    
    settlements = [
        {
            "sport": "WNBA", 
            "event_id": "e1", 
            "market_family": "m1", 
            "subject_id": "p1", 
            "line": "10.5", 
            "book": "DK", 
            "win_loss_push": "W",
            "closing_line": "11.5", 
            "closing_price": "-110"
        }
    ]
    stats = feedback.import_settlements(db_path, settlements)
    assert stats["updated_count"] == 1
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        settlement = conn.execute("SELECT * FROM settlements").fetchone()
        
        assert settlement["win_loss_push"] == "W"
        assert settlement["pnl"] == 2.0  # 2 units * (2.0 - 1.0)
        assert settlement["clv_line"] == pytest.approx(1.0)
        assert settlement["clv_price"] == pytest.approx(2.0 / (1.0 + 100.0 / 110.0) - 1.0)


def test_ambiguous_matching(tmp_path):
    # Two identical records
    rows = [
        _candidate(line=10.5),
        _candidate(line=10.5)
    ]
    db_path = _seed_db(tmp_path, rows)
    
    settlements = [
        {"sport": "WNBA", "event_id": "e1", "market_family": "m1", "subject_id": "p1", "line": "10.5", "book": "DK", "win_loss_push": "W"}
    ]
    stats = feedback.import_settlements(db_path, settlements)
    
    assert stats["unmatched_count"] == 0
    assert stats["ambiguous_count"] == 1
    assert stats["updated_count"] == 0
