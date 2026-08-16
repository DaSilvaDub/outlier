"""config/verdict_policy.json load contract (step 14)."""

from __future__ import annotations

import json

import pytest

from outlier_scrapers import verdict_policy


def test_missing_file_yields_defaults(tmp_path):
    policy = verdict_policy.load_verdict_policy(tmp_path / "no-such.json")
    assert policy.mode == "shadow"
    assert policy.repair_attempts == 1
    assert policy.reject_fail_ratio == 0.5
    assert policy.enforce_mlb_whitelist is True
    assert any(item.market == "BB" and item.scope == "PLAYER_PROP" for item in policy.desk_prohibited_markets)
    assert "HRR" in {item.market for item in policy.desk_prohibited_markets}


def test_unknown_key_raises(tmp_path):
    path = tmp_path / "verdict_policy.json"
    path.write_text(json.dumps({"mode": "shadow", "mystery": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown keys"):
        verdict_policy.load_verdict_policy(path)


def test_repo_policy_file_loads():
    policy = verdict_policy.load_verdict_policy()
    assert policy.mode in ("shadow", "enforce")
    assert 0 <= policy.reject_fail_ratio <= 1
    bb = [item for item in policy.desk_prohibited_markets if item.market == "BB"]
    assert bb and bb[0].scope == "PLAYER_PROP"
