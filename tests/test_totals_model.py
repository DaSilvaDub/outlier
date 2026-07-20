import pytest
from outlier_scrapers.totals_model import compute_blended_prob, extract_l10, backfill_candidate_totals

def test_compute_blended_prob():
    # Only market
    assert compute_blended_prob(0.6, None, None) == 0.6
    
    # Only L10
    assert compute_blended_prob(None, 0.7, 10) == 0.7
    
    # 10 games = 25% L10 weight
    # 0.6 * 0.75 + 0.7 * 0.25 = 0.45 + 0.175 = 0.625
    assert compute_blended_prob(0.6, 0.7, 10) == pytest.approx(0.625)
    
    # 4 games = 10% L10 weight
    # 0.6 * 0.90 + 0.7 * 0.10 = 0.54 + 0.07 = 0.61
    assert compute_blended_prob(0.6, 0.7, 4) == pytest.approx(0.61)
    
    # 20 games (cap at 25%) = 25% L10 weight
    # 0.6 * 0.75 + 0.7 * 0.25 = 0.625
    assert compute_blended_prob(0.6, 0.7, 20) == pytest.approx(0.625)

def test_extract_l10():
    # Ambiguous
    rec = {
        "team": "LAL",
        "matchup": "LAL vs BOS",
        "stats": {
            "homeSummaryStat": {"l10Results": [1, 1, 0, 0, 1]},
            "awaySummaryStat": {"l10Results": [0, 0, 0, 0, 0]}
        }
    }
    assert extract_l10(rec) == {"flag": "AMBIGUOUS_STATS_SIDE"}
    
    # Clear team matching (LAL is away)
    rec["matchup"] = "LAL @ BOS"
    res = extract_l10(rec)
    assert res is not None
    assert res["hits"] == 0
    assert res["total"] == 5
    assert res["pct"] == 0.0
    assert res["flag"] == "SHORT_SAMPLE"
    
    # Clear team matching (LAL is home)
    rec["matchup"] = "BOS @ LAL"
    res = extract_l10(rec)
    assert res is not None
    assert res["hits"] == 3
    assert res["total"] == 5
    assert res["pct"] == 60.0
    
def test_backfill_candidate_totals():
    candidates = [
        {
            "market_id": "total_1",
            "sport": "NBA",
            "market_type": "GAMELINE",
            "model_prob": "",
            "decimal_price": "1.90"
        },
        {
            "market_id": "total_2",
            "sport": "NBA",
            "market_type": "GAMELINE",
            "model_prob": 0.5, # already has one
            "decimal_price": "1.90"
        }
    ]
    
    totals = [
        {
            "market_id": "total_1",
            "sport": "NBA",
            "market_consensus_prob": 0.55,
            "independent_model_prob": 0.60,
            "final_blended_prob": 0.56,
            "push_prob": 0.02,
            "actionable": "true"
        }
    ]
    
    backfill_candidate_totals(candidates, totals)
    
    c1 = candidates[0]
    assert c1["market_consensus_prob"] == 0.55
    assert c1["independent_model_prob"] == 0.60
    assert c1["final_blended_prob"] == 0.56
    assert c1["model_prob"] == 0.56
    assert c1["push_prob"] == 0.02
    assert "edge_pct" in c1
    
    c2 = candidates[1]
    assert c2.get("final_blended_prob") is None
