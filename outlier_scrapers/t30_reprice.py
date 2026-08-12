import argparse
import csv
import logging
import sys
import copy
import os
import shutil
from pathlib import Path
from typing import Any

from outlier_scrapers import pack
from outlier_scrapers import refresh
from outlier_scrapers import paths
from outlier_scrapers.game_totals import MIN_EDGE_TOTALS
from outlier_scrapers.sizing import compute_sizing
from outlier_scrapers.feedback import capture_t30_pack

logger = logging.getLogger(__name__)

def is_key_number(line: float, sport: str) -> bool:
    if sport == "MLB":
        return line in (7.0, 7.5, 8.0, 8.5, 9.0)
    return False

def run_t30_reprice(pack_dir: Path) -> None:
    orig_csv = pack_dir / "original_recommendations.csv"
    if not orig_csv.exists():
        logger.error(f"Cannot reprice: {orig_csv} not found.")
        return
        
    with open(orig_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        
    leagues = sorted({str(r.get("sport") or "").upper() for r in rows if r.get("sport")})
    if not leagues:
        logger.error("No leagues found in original_recommendations.csv.")
        return
        
    logger.info(f"Refreshing odds for {leagues} before T-30 reprice.")
    for lg in leagues:
        args = ["--league", lg, "--props", "--games", "--line-movement", "--game-line-movement"]
        exit_code = refresh.main(args)
        if exit_code != 0:
            logger.error(f"Failed to refresh {lg} odds.")
            return
            
    games_norm = {}
    for lg in leagues:
        norm = paths.league_paths(lg).normalized
        games_norm[lg] = pack.load_json(norm / f"{lg.lower()}_games_latest.json")
    props_norm = pack.load_props_norm_by_league(leagues)
    
    records_by_market: dict[str, list[dict[str, Any]]] = {}
    ev_records_by_market: dict[str, list[dict[str, Any]]] = {}
    
    for lg in leagues:
        low = lg.lower()
        norm = paths.league_paths(lg).normalized
        
        for prefix in ["", "games_"]:
            payload = pack.load_json(norm / f"{low}_{prefix}line_movement_latest.json") or {}
            
            for r in payload.get("records", []):
                mid = str(r.get("market_id") or "")
                records_by_market.setdefault(mid, []).append(r)
                
            for r in payload.get("ev_records", []):
                mid = str(r.get("market_id") or "")
                ev_records_by_market.setdefault(mid, []).append(r)
                
    updated_rows = []
    for orig_row in rows:
        row = copy.deepcopy(orig_row)
        updated_rows.append(row)
        
        sport = str(row.get("sport") or "").upper()
        market_id = str(row.get("market_id") or "")
        market_type = str(row.get("market_type") or "").upper()
        is_total = market_type in ("GAME_TOTAL", "TEAM_TOTAL", "GAMELINE", "TEAM_PROP")
        
        recs = records_by_market.get(market_id, [])
        ev_recs = ev_records_by_market.get(market_id, [])
        
        if not recs or not ev_recs:
            row["actionable"] = "false"
            row["board"] = "MARKET_MISSING"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        matched_rec = None
        if is_total:
            selection_str = str(row.get("selection") or "").upper()
            headline_side = "OVER" if "OVER" in selection_str else ("UNDER" if "UNDER" in selection_str else "")
            
            if not headline_side:
                row["actionable"] = "false"
                row["board"] = "MARKET_MISSING"
                row["recommended_units_pre_news"] = 0.0
                continue
                
            for r in recs:
                if str(r.get("side") or "").upper() == headline_side:
                    matched_rec = r
                    break
        else:
            outcome_id = str(row.get("outcome_id") or "")
            for r in recs:
                if str(r.get("outcome_id") or "") == outcome_id:
                    matched_rec = r
                    break
                    
        if not matched_rec:
            row["actionable"] = "false"
            row["board"] = "MARKET_MISSING"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        if not matched_rec.get("is_active", True):
            row["actionable"] = "false"
            row["board"] = "MARKET_MISSING"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        # Metadata check for injury, starter change, stale sources
        metadata = matched_rec.get("sport_context", {})
        if metadata.get("stale_source"):
            row["actionable"] = "false"
            row["board"] = "KILL_STALE_SOURCE"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        if metadata.get("starter_change"):
            row["actionable"] = "false"
            row["board"] = "KILL_STARTER_CHANGE"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        if metadata.get("injury"):
            row["actionable"] = "false"
            row["board"] = "KILL_INJURY"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        original_line_str = row.get("line")
        original_line = float(original_line_str) if original_line_str not in (None, "") else None
        new_line = matched_rec.get("current_line")
        
        if original_line is not None and new_line is not None and original_line != float(new_line):
            row["actionable"] = "false"
            row["board"] = "KILL_LINE_MOVED"
            if is_key_number(original_line, sport) or is_key_number(float(new_line), sport):
                row["board"] = "KILL_KEY_NUMBER"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        # Find EV record for this outcome and original book
        outcome_id = matched_rec.get("outcome_id")
        orig_book = row.get("book")
        
        outcome_ev_recs = [er for er in ev_recs if str(er.get("outcome_id")) == str(outcome_id)]
        
        # In T-30, we stay on the recommended book if possible, otherwise we kill (don't line shop at T-30)
        book_ev_recs = [er for er in outcome_ev_recs if er.get("book") == orig_book]
        
        if not book_ev_recs:
            row["actionable"] = "false"
            row["board"] = "KILL_PRICE_MOVED"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        # Pick the EV record with the highest EV on that book (usually just 1)
        best_record = max(book_ev_recs, key=lambda er: float(er.get("calculated_ev_pct") or -999.0))
        
        if best_record.get("book_decimal_odds") is None:
            row["actionable"] = "false"
            row["board"] = "KILL_PRICE_MOVED"
            row["recommended_units_pre_news"] = 0.0
            continue
            
        decimal_price = float(best_record["book_decimal_odds"])
        
        # Explicit probability recalculation from devig_decimal
        devig_decimal = float(best_record.get("devig_decimal") or 0.0)
        if devig_decimal > 0.0:
            model_prob = 1.0 / devig_decimal
        else:
            model_prob = None
            
        push_prob_str = row.get("push_prob")
        push_prob = float(push_prob_str) if push_prob_str not in (None, "") else 0.0

        min_edge = MIN_EDGE_TOTALS if is_total else 0.02
        
        if model_prob is not None:
            sizing = compute_sizing(
                decimal_price=decimal_price,
                model_prob=model_prob,
                push_prob=push_prob,
                min_edge=min_edge,
            )
            
            row["decimal_price"] = decimal_price
            row["price"] = best_record.get("book_odds")
            row["book"] = best_record.get("book")
            row["implied_prob"] = sizing.implied_prob
            row["edge_pct"] = sizing.edge_pct
            row["kelly_025_units"] = sizing.kelly_025_units
            row["final_blended_prob"] = model_prob
            
            original_units_str = row.get("recommended_units_pre_news")
            original_units = float(original_units_str) if original_units_str not in (None, "") else 0.0
            
            if sizing.recommended_units_pre_news is not None:
                row["recommended_units_pre_news"] = min(original_units, sizing.recommended_units_pre_news)
            else:
                row["recommended_units_pre_news"] = 0.0
            
            if row["recommended_units_pre_news"] == 0.0 or (sizing.edge_pct is not None and sizing.edge_pct < min_edge):
                row["actionable"] = "false"
                row["board"] = "KILL_PRICE_MOVED"
                row["recommended_units_pre_news"] = 0.0
            else:
                row["actionable"] = "true"
        else:
            row["actionable"] = "false"
            row["board"] = "MARKET_MISSING"
            row["recommended_units_pre_news"] = 0.0

    logger.info("Computing repack snapshot...")
    snapshot = pack.compute_pack_snapshot(
        rows=updated_rows,
        out_dir=pack_dir,
        games_norm_by_league=games_norm,
        props_norm_by_league=props_norm,
        opportunity_rows=None,
    )
    
    tmp_dir = pack_dir.with_suffix(".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
        
    logger.info(f"Writing repack snapshot to {tmp_dir}...")
    pack.write_pack_snapshot(
        snapshot=snapshot,
        out_dir=tmp_dir,
        freeze_original=False
    )
    
    logger.info("Capturing T-30 snapshot to ledger...")
    try:
        capture_t30_pack(tmp_dir)
        logger.info("Ledger capture successful.")
        
        logger.info("Performing atomic swap of pack_dir...")
        import time
        # Atomic swap using os.replace or manual fallback for windows
        swap_backup = pack_dir.with_suffix(".backup_" + str(int(time.time())))
        os.rename(pack_dir, swap_backup)
        os.rename(tmp_dir, pack_dir)
        try:
            pack._retry_rmtree(swap_backup)
        except Exception:
            pass
        
    except Exception as e:
        logger.error(f"Failed to capture T-30 pack to ledger or swap directories: {e}")
        raise
    
    logger.info(f"T-30 Reprice completed successfully for {pack_dir.name}.")

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="T-30 Reprice Job")
    parser.add_argument("--pack-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    
    run_t30_reprice(args.pack_dir)
    return 0

if __name__ == "__main__":
    sys.exit(main())
