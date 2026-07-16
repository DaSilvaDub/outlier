import pytest
from outlier_scrapers.sizing import compute_sizing, compute_full_kelly, compute_historical_edge


def test_no_push_even_money():
    sizing = compute_sizing(decimal_price=2.0, model_prob=0.55, push_prob=0.0)
    assert sizing.edge_pct == pytest.approx(0.10, abs=1e-9)
    assert sizing.recommended_units_pre_news == 2.5

    b = 2.0 - 1.0
    p_win = 0.55
    p_lose = 1.0 - p_win
    full_kelly = compute_full_kelly(b, p_win, p_lose)
    assert full_kelly == pytest.approx(0.10, abs=1e-9)


def test_push_normalization():
    # Positive-edge push market: normalized Kelly must exceed the unnormalized
    # (divide-by-b-only) value by a REAL margin, not floating-point noise.
    b, p_win, p_lose = 1.0, 0.50, 0.40  # implies push_prob = 0.10
    full_kelly = compute_full_kelly(b, p_win, p_lose)
    unnormalized = (b * p_win - p_lose) / b  # = 0.10
    assert full_kelly == pytest.approx(0.10 / 0.90, abs=1e-9)  # 0.1111…
    assert full_kelly - unnormalized > 0.01  # real gap, not ~1e-17


def test_push_reduces_to_binary():
    # Push reduces to binary: with push_prob=0, full_kelly == p_win - p_lose/b.
    b = 1.5
    p_win = 0.5
    p_lose = 0.5
    full_kelly = compute_full_kelly(b, p_win, p_lose)
    assert full_kelly == pytest.approx(p_win - p_lose / b, abs=1e-9)


def test_edge_gate():
    # a positive-Kelly case with edge_pct < min_edge -> recommended_units_pre_news == 0.0
    # b=1.0. edge_pct = 2*p_win - 1
    # p_win = 0.505 => edge_pct = 0.01 < 0.02
    sizing = compute_sizing(decimal_price=2.0, model_prob=0.505, min_edge=0.02)
    assert sizing.edge_pct == pytest.approx(0.01, abs=1e-9)
    # full_kelly > 0 because edge > 0
    assert sizing.recommended_units_pre_news == 0.0


def test_cap_binds():
    # large edge case -> max_units
    sizing = compute_sizing(decimal_price=2.0, model_prob=0.90, max_units=3.0)
    assert sizing.recommended_units_pre_news == 3.0


def test_ineligible_model_prob_none():
    sizing = compute_sizing(decimal_price=2.0, model_prob=None)
    assert sizing.recommended_units_pre_news is None
    assert sizing.kelly_025_units is None
    assert sizing.edge_pct is None
    assert sizing.implied_prob == 0.5
    assert sizing.decimal_price == 2.0


def test_invalid_decimal_price():
    # decimal_price <= 1.0
    sizing = compute_sizing(decimal_price=1.0, model_prob=0.6)
    assert sizing.recommended_units_pre_news is None
    assert sizing.kelly_025_units is None
    assert sizing.edge_pct is None


def test_divide_by_zero_guard():
    # p_win + p_lose <= 0 -> push_prob = 1.0
    sizing = compute_sizing(decimal_price=2.0, model_prob=0.0, push_prob=1.0)
    assert sizing.recommended_units_pre_news is None
    assert sizing.kelly_025_units is None
    assert sizing.edge_pct is None


def test_push_plus_model_over_one_is_ineligible():
    # push_prob + model_prob > 1 is not a valid probability partition (p_lose < 0)
    # and must not inflate edge/Kelly -> fall back to null sizing.
    sizing = compute_sizing(decimal_price=2.0, model_prob=0.6, push_prob=0.6)
    assert sizing.edge_pct is None
    assert sizing.kelly_025_units is None
    assert sizing.recommended_units_pre_news is None


def test_negative_push_prob_is_ineligible():
    sizing = compute_sizing(decimal_price=2.0, model_prob=0.55, push_prob=-0.2)
    assert sizing.edge_pct is None
    assert sizing.recommended_units_pre_news is None


def test_historical_edge_known_value():
    # 60% hit rate at even money: 0.6 * 1.0 - 0.4 = +0.20 (fraction, like edge_pct)
    assert compute_historical_edge(60.0, 2.0) == pytest.approx(0.2)


def test_historical_edge_push_aware():
    # Push mass shrinks p_lose: 0.6 * 1.0 - (1 - 0.6 - 0.1) = +0.30
    assert compute_historical_edge(60.0, 2.0, push_prob=0.1) == pytest.approx(0.3)


def test_historical_edge_missing_hit_rate_is_none():
    # Regression guard: missing recency data must yield None, never a
    # neutral-50 fabricated edge.
    assert compute_historical_edge(None, 2.0) is None


def test_historical_edge_missing_price_is_none():
    assert compute_historical_edge(60.0, None) is None


def test_historical_edge_degenerate_price_is_none():
    assert compute_historical_edge(60.0, 1.0) is None
    assert compute_historical_edge(60.0, 0.5) is None


def test_historical_edge_inconsistent_push_is_none():
    # p_win + push > 1 is an invalid partition (same rule as compute_sizing).
    assert compute_historical_edge(95.0, 2.0, push_prob=0.10) is None


def test_historical_edge_out_of_range_hit_is_none():
    assert compute_historical_edge(120.0, 2.0) is None
    assert compute_historical_edge(-5.0, 2.0) is None
