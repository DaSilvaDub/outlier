import pytest
from outlier_scrapers.sizing import compute_sizing, compute_full_kelly


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
