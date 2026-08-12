import json
from pathlib import Path

content = '''
def capture_t30_pack(
    pack_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
) -> CaptureStats:
    """Dedicated ledger writer for T-30 reprice runs to avoid ON CONFLICT overwrites."""
    pack_dir = Path(pack_dir)
    if not pack_dir.exists():
        raise FeedbackError(f"Pack directory does not exist: {pack_dir}")

    fallback_timestamp = _pack_fallback_timestamp(pack_dir)
    source_rows = _load_pack_rows(pack_dir)
    snapshots: list[dict[str, Any]] = []
    
    # Generate unique T-30 snapshots
    for source, row in source_rows:
        snapshot = _snapshot_from_pack_row(
            source, row, pack_dir, fallback_timestamp, None
        )
        # Prefix snapshot_id to prevent collision with morning run
        snapshot["snapshot_id"] = f"t30_{snapshot['snapshot_id']}"
        snapshots.append(snapshot)

    conn = _connect(Path(db_path))
    if conn is None:
        raise FeedbackError("capture_t30_pack requires a valid SQLite connection")
        
    try:
        for snapshot in snapshots:
            values = [snapshot[field] for field in MARKET_SNAPSHOT_FIELDS]
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
                    insight_component, movement_component, orf_component, pack_path,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    market_consensus_prob = excluded.market_consensus_prob,
                    edge = excluded.edge,
                    decimal_price = excluded.decimal_price,
                    implied_prob = excluded.implied_prob,
                    board = excluded.board,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units
                """,
                values,
            )
        conn.commit()
    finally:
        conn.close()
        
    return CaptureStats(
        total_rows=len(snapshots),
        opportunities=len([s for s in snapshots if str(s.get("board", "")).upper() == "A"]),
        recommendations=len([s for s in snapshots if str(s.get("board", "")).upper() == "A" and float(s.get("portfolio_units") or 0.0) > 0.0]),
    )
'''

with open("outlier_scrapers/feedback.py", "a", encoding="utf-8") as f:
    f.write(content)
