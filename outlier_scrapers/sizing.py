from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite


@dataclass(frozen=True)
class Sizing:
    decimal_price: float
    model_prob: float | None
    push_prob: float
    implied_prob: float
    edge_pct: float | None
    kelly_025_units: float | None
    max_units: float
    recommended_units_pre_news: float | None


def shrink_probability(
    k: float,
    n: float,
    p_mkt: float,
    alpha: float = 20.0,
) -> float | None:
    """Empirical-Bayes shrinkage toward market-implied probability.

    p_hat = (k + alpha * p_mkt) / (n + alpha)

    Parameters
    ----------
    k     : observed hits (successes) in the recency window.
    n     : total trials in the recency window.
    p_mkt : market-implied probability (the prior mean).
    alpha : prior strength (pseudo-count); 15-25 is typical.

    Returns None when inputs are degenerate.
    """
    if n + alpha <= 0 or not isfinite(p_mkt) or p_mkt < 0 or p_mkt > 1:
        return None
    if not isfinite(k) or not isfinite(n) or k < 0 or n < 0:
        return None
    return (k + alpha * p_mkt) / (n + alpha)


def compute_full_kelly(b: float, p_win: float, p_lose: float) -> float:
    if b <= 0 or (p_win + p_lose) <= 0:
        return 0.0
    return (b * p_win - p_lose) / (b * (p_win + p_lose))


def _round_to_half(value: float) -> float:
    # Use decimal for strict half-up rounding
    d = Decimal(str(value)) * Decimal("2")
    rounded = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(rounded) / 2.0


def compute_sizing(
    decimal_price: float,
    model_prob: float | None = None,
    push_prob: float = 0.0,
    kelly_fraction: float = 0.25,
    unit_bankroll: float = 100.0,
    max_units: float = 3.0,
    min_edge: float = 0.02,
    max_edge: float = 0.10,
) -> Sizing:
    if decimal_price <= 1.0:
        implied_prob = 1.0 / decimal_price if decimal_price > 0 else 0.0
        return Sizing(
            decimal_price=decimal_price,
            model_prob=model_prob,
            push_prob=push_prob,
            implied_prob=implied_prob,
            edge_pct=None,
            kelly_025_units=None,
            max_units=max_units,
            recommended_units_pre_news=None,
        )

    implied_prob = 1.0 / decimal_price

    if model_prob is None:
        return Sizing(
            decimal_price=decimal_price,
            model_prob=model_prob,
            push_prob=push_prob,
            implied_prob=implied_prob,
            edge_pct=None,
            kelly_025_units=None,
            max_units=max_units,
            recommended_units_pre_news=None,
        )

    b = decimal_price - 1.0
    p_win = model_prob
    p_lose = 1.0 - p_win - push_prob

    # Probabilities must form a valid partition. A push_prob that overlaps p_win
    # (push_prob + model_prob > 1) drives p_lose negative and would otherwise
    # inflate edge/Kelly, so treat inconsistent inputs as ineligible.
    if push_prob < 0 or p_lose < 0 or (p_win + p_lose) <= 0:
        return Sizing(
            decimal_price=decimal_price,
            model_prob=model_prob,
            push_prob=push_prob,
            implied_prob=implied_prob,
            edge_pct=None,
            kelly_025_units=None,
            max_units=max_units,
            recommended_units_pre_news=None,
        )

    edge_pct = p_win * b - p_lose
    full_kelly = compute_full_kelly(b, p_win, p_lose)

    kelly_025_units = _round_to_half(kelly_fraction * full_kelly * unit_bankroll)

    # Tolerance avoids floating-point noise at the boundary (e.g. 0.10 + 9e-17).
    if full_kelly <= 0 or edge_pct < min_edge or edge_pct > max_edge + 1e-9:
        recommended_units_pre_news = 0.0
    else:
        recommended_units_pre_news = min(kelly_025_units, max_units)

    return Sizing(
        decimal_price=decimal_price,
        model_prob=model_prob,
        push_prob=push_prob,
        implied_prob=implied_prob,
        edge_pct=edge_pct,
        kelly_025_units=kelly_025_units,
        max_units=max_units,
        recommended_units_pre_news=recommended_units_pre_news,
    )


def compute_historical_edge(
    hit_rate_prob: float | None,
    decimal_price: float | None,
    push_prob: float | None = None,
) -> float | None:
    """Edge implied by the raw recency hit rate alone. Descriptive only.

    Same convention as compute_sizing: probability inputs are fractions
    and the result is a fraction (p_win * b - p_lose). Returns None when
    inputs are missing, non-finite, or form an invalid partition.

    Callers must convert the raw nullable hit rate (signal["hit_pct"],
    expressed from 0 to 100) to a probability before calling. Never use
    hit_rate_component: its 50.0 default stands in for missing data and
    would fabricate an edge. This value must never feed sizing.
    """
    if hit_rate_prob is None or decimal_price is None:
        return None
    push = push_prob if push_prob is not None else 0.0
    if not all(isfinite(value) for value in (hit_rate_prob, decimal_price, push)):
        return None
    if decimal_price <= 1.0:
        return None
    p_win = hit_rate_prob
    if p_win < 0.0 or p_win > 1.0:
        return None
    p_lose = 1.0 - p_win - push
    if push < 0.0 or p_lose < 0.0:
        return None
    return p_win * (decimal_price - 1.0) - p_lose
