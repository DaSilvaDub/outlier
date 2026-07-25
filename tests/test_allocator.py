import random
from outlier_scrapers.portfolio import PortfolioPolicy, allocate_portfolio_risk

def _make_policy():
    return PortfolioPolicy(
        stake_increment=0.1,
        max_wager_units=3.0,
        max_daily_units=100.0,
        max_event_units=10.0,
        max_player_units=5.0,
        max_team_units=10.0,
        max_market_type_units=15.0,
        max_correlated_cluster_units=8.0,
        max_book_units=50.0,
    )

def _make_row(wager_id, units, edge=0.05, event="evt1", player="p1", team="t1", mkt="m1", cluster="c1", book="b1", actionable="true", board="A"):
    return {
        "stable_wager_id": wager_id,
        "units": units,
        "edge_pct": edge,
        "event_id": event,
        "player_id": player,
        "team": team,
        "market_type": mkt,
        "cluster_id": cluster,
        "sportsbook": book,
        "actionable": actionable,
        "board": board,
        "market_id": f"mid_{wager_id}",
        "outcome_id": f"oid_{wager_id}"
    }

def test_exact_individual_caps():
    policy = _make_policy()
    # cap max_player_units is 5.0
    # we have two wagers with 3.0 units each on same player -> total 6.0
    # ratio = 5.0 / 6.0
    # scaled = 3.0 * (5/6) = 2.5
    rows = [
        _make_row("w1", 3.0, player="p1"),
        _make_row("w2", 3.0, player="p1"),
    ]
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units["w1"] == 2.5
    assert res.allocated_units["w2"] == 2.5
    assert "player:p1" in res.binding_constraints
    assert "player:p1" in res.cap_reasons["w1"]

def test_overlapping_caps():
    policy = _make_policy()
    # max_player=5.0, max_wager=3.0
    # If a wager wants 4.0, it gets capped by wager to 3.0
    rows = [_make_row("w1", 4.0)]
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units["w1"] == 3.0
    assert "wager:w1" in res.binding_constraints

def test_no_per_wager_over_allocation():
    policy = _make_policy()
    rows = [_make_row("w1", 1.25)]
    # shouldn't allocate more than 1.25, even with slack
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units["w1"] == 1.2

def test_no_divide_by_zero():
    policy = _make_policy()
    rows = [_make_row("w1", 0.0)]
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units["w1"] == 0.0

def test_residual_slack_recovery():
    policy = _make_policy()
    # sum = 5.3 units on a player cap of 5.0
    # 2.65 and 2.65
    # scaled = 2.5 and 2.5
    # increment = 0.1, sum is 5.0
    # wait, if sum = 5.2, ratio = 5/5.2 = 0.9615
    # pre = 2.6, scaled = 2.5. Total 5.0, exact.
    # What if we have slack?
    # Cap = 5.0, 3 wagers of 1.7
    # Sum = 5.1
    # ratio = 5.0 / 5.1 = 0.9803
    # scaled = 1.666
    # base = 1.6
    # 3 wagers of 1.6 = 4.8. Slack = 0.2
    # w1, w2 get +0.1
    rows = [
        _make_row("w1", 1.7, edge=0.1),
        _make_row("w2", 1.7, edge=0.08),
        _make_row("w3", 1.7, edge=0.05),
    ]
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units["w1"] == 1.7
    assert res.allocated_units["w2"] == 1.7
    assert res.allocated_units["w3"] == 1.6
    assert sum(res.allocated_units.values()) == 5.0

def test_reserved_capacity_deduction():
    policy = _make_policy()
    rows = [_make_row("w1", 5.0, player="p1")]
    res = allocate_portfolio_risk(rows, policy, reserved_exposure={"player:p1": 2.0})
    # player cap 5.0 - 2.0 = 3.0
    assert res.allocated_units["w1"] == 3.0

def test_input_immutability():
    policy = _make_policy()
    row = _make_row("w1", 2.0)
    rows = [row]
    allocate_portfolio_risk(rows, policy)
    assert row["units"] == 2.0

def test_input_order_invariance():
    policy = _make_policy()
    rows = [
        _make_row("w1", 1.7, edge=0.1),
        _make_row("w2", 1.7, edge=0.08),
        _make_row("w3", 1.7, edge=0.05),
    ]
    res1 = allocate_portfolio_risk(rows, policy)
    
    rows_shuffled = rows[::-1]
    res2 = allocate_portfolio_risk(rows_shuffled, policy)
    
    assert res1.allocated_units == res2.allocated_units
    assert res1.order_invariance_hash == res2.order_invariance_hash

def test_all_or_nothing_below_increment():
    policy = _make_policy()
    rows = [_make_row("w1", 0.05)]
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units["w1"] == 0.0

def test_defensive_house_rule_zeroing():
    policy = _make_policy()
    rows = [
        _make_row("w1", 1.0, actionable="false", board="A_FLAGGED"),
        _make_row("w2", 1.0, actionable="true", board="A")
    ]
    res = allocate_portfolio_risk(rows, policy)
    assert res.allocated_units.get("w1") == 0.0
    assert res.allocated_units.get("w2") == 1.0
