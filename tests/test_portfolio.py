"""Portfolio policy regression tests."""

from __future__ import annotations

import json

from outlier_scrapers import paths
from outlier_scrapers.portfolio import load_portfolio_policy


def test_shipped_policy_keeps_learned_multipliers_shadow_neutral():
    """Caps stay enforced while unvalidated learned stake multipliers stay neutral."""
    policy_path = paths.PROJECT_ROOT / "config" / "portfolio_risk.json"
    policy = load_portfolio_policy(policy_path)
    raw_policy = json.loads(policy_path.read_text(encoding="utf-8"))

    assert policy.mode == "enforce"
    assert raw_policy["shadow_multipliers_neutral"] is True
    assert policy.shadow_multipliers_neutral is True
    assert raw_policy["calibration"]["enabled"] is True
    assert raw_policy["uncertainty"]["enabled"] is True
    assert raw_policy["drawdown"]["enabled"] is True
