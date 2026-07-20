"""Per-line totals probability modeling and L10 hit rate extraction.

Calculates the blended probability of an outcome by combining the consensus market
implied probability (devig) with the recent-games trend (L10 hit rate), weighting
the trend according to its sample size. Also provides utilities to backfill this
data onto existing candidate rows.
"""

from typing import Any
from outlier_scrapers.normalizer import percent_number
from outlier_scrapers.sizing import compute_sizing

FLAG_SHORT_SAMPLE = "SHORT_SAMPLE"
FLAG_AMBIGUOUS_STATS_SIDE = "AMBIGUOUS_STATS_SIDE"

def _summary_stat_for_team(rec: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the home/away summary-stat blob matching the record's team.

    Returns (stat_dict, flag). Ambiguous side -> (None, AMBIGUOUS_STATS_SIDE);
    missing stats -> (None, None).
    """
    stats = rec.get("stats")
    if not isinstance(stats, dict):
        return None, None
    home = stats.get("homeSummaryStat")
    away = stats.get("awaySummaryStat")
    home = home if isinstance(home, dict) else None
    away = away if isinstance(away, dict) else None
    if home is None and away is None:
        return None, None
    if home is not None and away is None:
        return home, None
    if away is not None and home is None:
        return away, None
    # Both present: match team against "away @ home" matchup.
    team = str(rec.get("team") or "").strip().lower()
    matchup = str(rec.get("matchup") or "")
    if team and " @ " in matchup:
        away_name, _, home_name = matchup.partition(" @ ")
        if team == away_name.strip().lower():
            return away, None
        if team == home_name.strip().lower():
            return home, None
    return None, FLAG_AMBIGUOUS_STATS_SIDE

def extract_l10(rec: dict[str, Any]) -> dict[str, Any] | None:
    """L10 hit rate for one OVER outcome at its line.

    Prefers the exact ``l10Results`` boolean array; falls back to the ``l10``
    fraction. Returns None when no usable l10 signal exists.
    """
    stat, flag = _summary_stat_for_team(rec)
    if stat is None:
        return {"flag": flag} if flag else None
    results = stat.get("l10Results")
    if isinstance(results, list) and results:
        bools = [bool(v) for v in results]
        hits, total = sum(bools), len(bools)
        return {
            "hits": hits,
            "total": total,
            "pct": round(100.0 * hits / total, 3),
            "source": "l10Results",
            "flag": FLAG_SHORT_SAMPLE if total < 10 else None,
        }
    pct = percent_number(stat.get("l10"))
    if pct is None:
        return None
    return {"hits": None, "total": None, "pct": pct, "source": "l10", "flag": None}

def compute_blended_prob(p_market: float | None, p_l10: float | None, l10_total: int | None) -> float | None:
    """Blend market consensus probability with L10 trend, weighted by sample size."""
    if p_market is None:
        return p_l10
    if p_l10 is None:
        return p_market
        
    total_games = l10_total if l10_total is not None else 10  # assume 10 if fallback to 'l10' string
    
    # Weight is 2.5% per game, maxing out at 25% for 10 games
    l10_weight = min(0.25, 0.025 * total_games)
    
    return p_market * (1.0 - l10_weight) + p_l10 * l10_weight

def backfill_candidate_totals(candidate_rows: list[dict[str, Any]], totals_rows: list[dict[str, Any]]) -> None:
    """Backfill missing model_prob and independent stats on totals candidate rows."""
    totals_index = {
        (str(r.get("sport") or ""), str(r.get("market_id"))): r 
        for r in totals_rows if r.get("market_id")
    }
    
    for row in candidate_rows:
        if row.get("market_type") not in ("GAMELINE", "TEAM_PROP"):
            continue
            
        market_id = row.get("market_id")
        sport = str(row.get("sport") or "")
        key = (sport, str(market_id))
        
        if key not in totals_index:
            continue
            
        # Only backfill if the candidate lacks a model_prob (e.g. proxy fallback logic)
        if row.get("model_prob") not in (None, ""):
            continue
            
        totals_row = totals_index[key]
        
        row["market_consensus_prob"] = totals_row.get("market_consensus_prob")
        row["independent_model_prob"] = totals_row.get("independent_model_prob")
        row["final_blended_prob"] = totals_row.get("final_blended_prob")
        row["model_prob"] = totals_row.get("final_blended_prob")
        
        if totals_row.get("push_prob") not in (None, ""):
            row["push_prob"] = totals_row["push_prob"]
            
        model_prob = totals_row.get("final_blended_prob")
        decimal_price = row.get("decimal_price")
        if model_prob not in (None, "") and decimal_price not in (None, ""):
            push_val = row.get("push_prob")
            push_prob = float(str(push_val)) if push_val not in (None, "") else 0.0
            sizing = compute_sizing(
                decimal_price=float(str(decimal_price)), 
                model_prob=float(str(model_prob)), 
                push_prob=push_prob
            )
            row["implied_prob"] = sizing.implied_prob
            row["edge_pct"] = sizing.edge_pct
            row["kelly_025_units"] = sizing.kelly_025_units
            row["max_units"] = sizing.max_units
            
            if totals_row.get("actionable") == "true":
                row["recommended_units_pre_news"] = sizing.recommended_units_pre_news
            else:
                row["recommended_units_pre_news"] = ""
