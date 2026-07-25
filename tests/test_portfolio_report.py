import json
import pytest
from pathlib import Path
from outlier_scrapers.portfolio_report import run_report, generate_text_report, parse_args

def test_parse_args():
    args = parse_args(["--from", "2024-01-01", "--to", "2024-01-03", "--packs-dir", "test_packs"])
    assert args.from_date == "2024-01-01"
    assert args.to_date == "2024-01-03"
    assert args.packs_dir == "test_packs"

def test_run_report_and_formatting(tmp_path: Path):
    packs = tmp_path / "packs"
    
    # Valid day 1
    day1 = packs / "2024-01-01"
    day1.mkdir(parents=True)
    with open(day1 / "portfolio_risk.json", "w", encoding="utf-8") as f:
        json.dump({
            "code_git_sha": "abc1234",
            "legacy_total_units": 100,
            "shadow_total_units": 90,
            "caps_binding": {"cap_A": 5},
            "zeroed_rows": 2,
            "quantization_differences": 1,
            "missing_identity_counts": 0,
            "duplicate_collapse_counts": 0,
            "book_source_exposure": {"draftkings": 50},
            "order_invariance_hash": "hash1",
            "policy_fingerprint": "fingerprint1"
        }, f)
        
    # Invalid day 2 (missing code_git_sha)
    day2 = packs / "2024-01-02"
    day2.mkdir(parents=True)
    with open(day2 / "portfolio_risk.json", "w", encoding="utf-8") as f:
        json.dump({
            "legacy_total_units": 100,
            "shadow_total_units": 100
        }, f)
        
    # Invalid day 3 (unknown sha)
    day3 = packs / "2024-01-03"
    day3.mkdir(parents=True)
    with open(day3 / "portfolio_risk.json", "w", encoding="utf-8") as f:
        json.dump({
            "code_git_sha": "unknown",
            "legacy_total_units": 100
        }, f)
        
    # Valid day 4
    day4 = packs / "2024-01-04"
    day4.mkdir(parents=True)
    with open(day4 / "portfolio_risk.json", "w", encoding="utf-8") as f:
        json.dump({
            "code_git_sha": "def5678",
            "legacy_total_units": 50,
            "shadow_total_units": 45,
            "caps_binding": {"cap_A": 2, "cap_B": 1},
            "zeroed_rows": 0,
            "quantization_differences": 0,
            "missing_identity_counts": 1,
            "duplicate_collapse_counts": 1,
            "book_source_exposure": {"draftkings": 20, "fanduel": 25},
            "order_invariance_hash": "hash2",
            "policy_fingerprint": "fingerprint1"
        }, f)

    report = run_report("2024-01-01", "2024-01-04", str(packs))
    
    assert report["valid_days"] == 2
    assert report["rejected_days"] == 2
    assert report["gaps"] == ["2024-01-02", "2024-01-03"]
    assert report["legacy_total_units"] == 150.0
    assert report["shadow_total_units"] == 135.0
    assert report["unit_retention_ratio"] == 0.9 # 135/150
    assert report["caps_binding"] == {"cap_A": 7, "cap_B": 1}
    assert report["zeroed_rows"] == 2
    assert report["quantization_differences"] == 1
    assert report["missing_identity_counts"] == 1
    assert report["duplicate_collapse_counts"] == 1
    assert report["book_source_exposure"] == {"draftkings": 70, "fanduel": 25}
    assert set(report["order_invariance_hashes"]) == {"hash1", "hash2"}
    assert set(report["policy_fingerprints"]) == {"fingerprint1"}
    assert report["consecutive_shadow_days"] == 1  # max consecutive is 1
    
    # Test text output
    text = generate_text_report(report)
    assert "Valid Days: 2" in text
    assert "Rejected/Gap Days: 2" in text
    assert "Gaps: 2024-01-02, 2024-01-03" in text
    assert "Unit Retention Ratio: 0.9000" in text
    assert "cap_A: 7" in text
    assert "draftkings: 70.00" in text
