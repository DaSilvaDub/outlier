"""Price benchmarks for floor research; ranking scores are not probabilities.

No fitted, validated calibration artifact is admitted by this cold-start contract.
Integer lines require a push model before unconditional probability or EV output.
"""
from __future__ import annotations

import math
from typing import Any

RANKING_SCORE_VERSION = "nfl_alt_floor_rank_v1"


def odds_probability(value: Any) -> float | None:
    """American quote's break-even probability, absent for malformed prices."""
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or abs(value) < 100:
        return None
    return -value / (100 - value) if value < 0 else 100 / (100 + value)


def is_half_point_line(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value % 1 == 0.5
    )


def _book_key(value: Any) -> str:
    return "".join(c for c in str(value or "").upper() if c.isalnum())


def paired_market_probability(
    floor: dict[str, Any], props: list[dict[str, Any]],
) -> tuple[float | None, str]:
    """Proportional devig only for exact identity and same-book opposite quotes."""
    identity = ("event_id", "player_id", "team", "market", "scope", "line")
    if any(floor.get(k) in (None, "") for k in identity):
        return None, "missing_identity"
    if floor.get("position") != "OVER" or floor.get("scope") != "full_game":
        return None, "unsupported_side_or_scope"
    if not is_half_point_line(floor["line"]):
        return None, "push_probability_unmodeled"
    wanted = _book_key(floor.get("target_book"))
    if not wanted:
        return None, "missing_book"
    over = odds_probability(floor.get("target_odds"))
    if over is None:
        return None, "invalid_quote"
    selected: set[float] = set()
    opposite: set[float] = set()
    for row in props:
        if any(row.get(k) != floor.get(k) for k in identity):
            continue
        for quote in row.get("books") or []:
            if not isinstance(quote, dict) or _book_key(quote.get("book")) != wanted:
                continue
            probability = odds_probability(quote.get("odds"))
            if probability is None:
                continue
            if row.get("position") == "UNDER":
                opposite.add(probability)
            elif row.get("position") == "OVER":
                selected.add(probability)
    if selected != {over}:
        return None, "selected_quote_mismatch_or_conflict"
    if not opposite:
        return None, "no_exact_same_book_opposite_quote"
    if len(opposite) != 1:
        return None, "conflicting_opposite_quotes"
    return over / (over + next(iter(opposite))), "proportional_devig_market_benchmark"


def describe_floor(floor: dict[str, Any], props: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe quote evidence without inventing a calibrated model or stake."""
    market_probability, market_status = paired_market_probability(floor, props)
    no_push = is_half_point_line(floor.get("line"))
    break_even = odds_probability(floor.get("target_odds")) if no_push else None
    return {
        "ranking_score": floor.get("confidence_score"),
        "ranking_score_version": RANKING_SCORE_VERSION,
        "break_even_probability": break_even,
        "break_even_probability_status": (
            "push_probability_unmodeled" if not no_push else
            "invalid_quote" if break_even is None else "quoted_half_point_break_even"
        ),
        "market_probability": market_probability,
        "market_probability_status": market_status,
        "model_probability": None,
        "calibrated_probability": None,
        "probability_status": "insufficient_calibration_history",
        "fair_decimal_odds": None,
        "ev_per_unit": None,
        "actionable": False,
        "bet_actionable": False,
        "stake_fraction": 0.0,
    }
