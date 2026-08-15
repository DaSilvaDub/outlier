import json
import sqlite3
from outlier_scrapers.feedback import initialize_database, replay_portfolio

def test_portfolio_replay(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    
    # insert dummy data
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO market_snapshots (snapshot_id, captured_at, sport, event_id, market_id, outcome_id, selection, book, edge, board, created_at) VALUES ('snap1', '2023-01-01T12:00:00Z', 'NBA', 'evt1', 'mkt1', 'out1', 'sel1', 'book1', 0.05, 'A', '2023-01-01T12:00:00Z')")
        conn.execute("INSERT INTO decisions (decision_id, snapshot_id, units, created_at, updated_at) VALUES ('dec1', 'snap1', 1.0, '2023-01-01T12:00:00Z', '2023-01-01T12:00:00Z')")
        
        conn.execute("INSERT INTO market_snapshots (snapshot_id, captured_at, sport, event_id, market_id, outcome_id, selection, book, edge, board, created_at) VALUES ('snap2', '2023-01-01T13:00:00Z', 'NBA', 'evt1', 'mkt2', 'out2', 'sel2', 'book1', 0.04, 'A', '2023-01-01T13:00:00Z')")
        conn.execute("INSERT INTO decisions (decision_id, snapshot_id, units, created_at, updated_at) VALUES ('dec2', 'snap2', 1.0, '2023-01-01T13:00:00Z', '2023-01-01T13:00:00Z')")
    
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({
        "stake_increment": 0.1,
        "max_wager_units": 1.0,
        "max_daily_units": 1.5,
        "max_event_units": 3.0,
        "max_player_units": 1.0,
        "max_team_units": 2.0,
        "max_market_type_units": 5.0,
        "max_correlated_cluster_units": 2.0,
        "max_book_units": 5.0
    }))
    
    result = replay_portfolio(db_path, "2023-01-01", "2023-01-02", policy_path)
    
    assert "2023-01-01" in result
    day_res = result["2023-01-01"]
    
    assert day_res["legacy_total_units"] == 2.0
    assert day_res["replayed_total_units"] == 1.5  # capped by daily
