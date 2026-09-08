"""Historical validation (spec Part 12).

The question this module exists to answer is the one the spec puts plainly:
*does a stated 90% probability historically win about 90% of the time?* Every
other metric here is subordinate to that.

Three things it refuses to do, all of them ways backtests usually flatter
themselves:

- It never reports a bucket hit rate as meaningful below ``min_bucket_sample``.
  A 3-for-3 record in the 95%+ bucket is not evidence of anything, and printing
  "100%" next to it invites exactly the wrong conclusion.
- It reports closing-line value separately from realised ROI. Over any sample a
  betting operation can plausibly collect, CLV is the lower-variance estimate of
  whether the edge is real, and ROI is mostly noise around it.
- It keeps pushes out of the win rate rather than counting them as half-wins,
  and states the push count alongside.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

from outlier_scrapers.ncaa.calibrate import log_loss
from outlier_scrapers.ncaa.config import BacktestConfig

__all__ = [
    "BacktestReport",
    "BucketRow",
    "MoneylineResult",
    "ParlayResult",
    "SegmentRow",
    "TotalsResult",
    "backtest_moneylines",
    "backtest_totals",
    "brier_score",
    "calibration_table",
    "parlay_survival",
]


@dataclass(frozen=True)
class MoneylineResult:
    """One settled moneyline wager."""

    game_id: str
    team: str
    model_prob: float
    market_prob: float | None
    decimal_price: float
    won: bool
    closing_prob: float | None = None
    tier: str | None = None
    season: int | None = None
    week: int | None = None


@dataclass(frozen=True)
class TotalsResult:
    """One settled total."""

    game_id: str
    projected_total: float
    market_total: float
    actual_total: float
    side: str
    decimal_price: float
    #: ``None`` means the bet pushed.
    won: bool | None
    closing_total: float | None = None
    conference: str | None = None
    weather_bucket: str | None = None
    season_period: str | None = None


@dataclass(frozen=True)
class ParlayResult:
    """One settled parlay."""

    leg_count: int
    legs_won: int
    estimated_probability: float
    decimal_price: float

    @property
    def won(self) -> bool:
        return self.legs_won == self.leg_count


@dataclass(frozen=True)
class BucketRow:
    """One probability bucket of the calibration curve."""

    low: float
    high: float
    count: int
    mean_predicted: float | None
    actual_rate: float | None
    calibration_gap: float | None
    #: False when ``count`` is under the configured floor. The rate is still
    #: shown, but it is descriptive, not evidence.
    sufficient_sample: bool


@dataclass(frozen=True)
class SegmentRow:
    """Performance within one slice (conference, weather, season period...)."""

    segment: str
    count: int
    win_rate: float | None
    roi: float | None
    mean_absolute_error: float | None


@dataclass(frozen=True)
class BacktestReport:
    """Everything spec Part 12 asks to be measured."""

    sample_size: int
    brier: float | None = None
    log_loss: float | None = None
    calibration: tuple[BucketRow, ...] = ()
    win_rate: float | None = None
    roi: float | None = None
    mean_closing_line_value: float | None = None
    closing_line_value_beat_rate: float | None = None
    upset_frequency: float | None = None
    mean_absolute_error: float | None = None
    rmse: float | None = None
    push_count: int = 0
    segments: tuple[SegmentRow, ...] = ()
    parlay_survival: tuple[SegmentRow, ...] = ()
    flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def brier_score(pairs: Sequence[tuple[float, int]]) -> float | None:
    """Mean squared error of probabilistic forecasts."""
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def calibration_table(
    pairs: Sequence[tuple[float, int]],
    config: BacktestConfig | None = None,
) -> tuple[BucketRow, ...]:
    """Bucketed reliability curve using the spec's probability bands."""
    cfg = config or BacktestConfig()
    rows: list[BucketRow] = []
    for low, high in cfg.probability_buckets:
        # Buckets are inclusive at both ends as the spec states them
        # (80-84.9, 85-89.9, ...), so the upper bound is compared with <=.
        members = [(p, y) for p, y in pairs if low <= p <= high]
        count = len(members)
        if count == 0:
            rows.append(BucketRow(low, high, 0, None, None, None, False))
            continue
        mean_predicted = sum(p for p, _ in members) / count
        actual = sum(y for _, y in members) / count
        rows.append(
            BucketRow(
                low=low,
                high=high,
                count=count,
                mean_predicted=round(mean_predicted, 6),
                actual_rate=round(actual, 6),
                calibration_gap=round(actual - mean_predicted, 6),
                sufficient_sample=count >= cfg.min_bucket_sample,
            )
        )
    return tuple(rows)


def _roi(stakes: Sequence[tuple[float, bool | None]]) -> float | None:
    """Return on turnover for ``(decimal_price, won)`` pairs; pushes refund."""
    settled = [(price, won) for price, won in stakes if won is not None]
    if not settled:
        return None
    profit = sum((price - 1.0) if won else -1.0 for price, won in settled)
    return profit / len(settled)


def backtest_moneylines(
    results: Sequence[MoneylineResult],
    config: BacktestConfig | None = None,
    *,
    segment_by: Callable[[MoneylineResult], str] | None = None,
) -> BacktestReport:
    """Score a settled moneyline history."""
    cfg = config or BacktestConfig()
    if not results:
        return BacktestReport(sample_size=0, flags=("no_results",))

    pairs = [(r.model_prob, 1 if r.won else 0) for r in results]
    stakes = [(r.decimal_price, r.won) for r in results]

    clv_values = [
        r.closing_prob - r.model_prob for r in results if r.closing_prob is not None
    ]
    # CLV against the *market* we bet into, not our own model: the useful
    # question is whether the closing market moved toward our side.
    market_clv = [
        r.closing_prob - r.market_prob
        for r in results
        if r.closing_prob is not None and r.market_prob is not None
    ]

    segments: list[SegmentRow] = []
    if segment_by is not None:
        grouped: dict[str, list[MoneylineResult]] = {}
        for result in results:
            grouped.setdefault(segment_by(result), []).append(result)
        for name in sorted(grouped):
            members = grouped[name]
            segments.append(
                SegmentRow(
                    segment=name,
                    count=len(members),
                    win_rate=round(sum(1 for m in members if m.won) / len(members), 6),
                    roi=_round(_roi([(m.decimal_price, m.won) for m in members])),
                    mean_absolute_error=_round(
                        sum(abs(m.model_prob - (1 if m.won else 0)) for m in members)
                        / len(members)
                    ),
                )
            )

    flags: list[str] = []
    table = calibration_table(pairs, cfg)
    if not any(row.sufficient_sample for row in table):
        flags.append("no_bucket_meets_sample_floor")

    favorites = [r for r in results if r.model_prob >= 0.5]
    upsets = sum(1 for r in favorites if not r.won)

    return BacktestReport(
        sample_size=len(results),
        brier=_round(brier_score(pairs)),
        log_loss=_round(log_loss(pairs)),
        calibration=table,
        win_rate=round(sum(1 for r in results if r.won) / len(results), 6),
        roi=_round(_roi(stakes)),
        mean_closing_line_value=_round(
            sum(market_clv) / len(market_clv) if market_clv else None
        ),
        closing_line_value_beat_rate=_round(
            sum(1 for v in clv_values if v > 0) / len(clv_values) if clv_values else None
        ),
        upset_frequency=_round(upsets / len(favorites) if favorites else None),
        segments=tuple(segments),
        flags=tuple(flags),
    )


def backtest_totals(
    results: Sequence[TotalsResult],
    *,
    segment_by: Callable[[TotalsResult], str] | None = None,
) -> BacktestReport:
    """Score a settled totals history (projection error plus betting record)."""
    if not results:
        return BacktestReport(sample_size=0, flags=("no_results",))

    errors = [r.projected_total - r.actual_total for r in results]
    settled = [r for r in results if r.won is not None]
    pushes = len(results) - len(settled)

    segments: list[SegmentRow] = []
    if segment_by is not None:
        grouped: dict[str, list[TotalsResult]] = {}
        for result in results:
            grouped.setdefault(segment_by(result), []).append(result)
        for name in sorted(grouped):
            members = grouped[name]
            decided = [m for m in members if m.won is not None]
            segments.append(
                SegmentRow(
                    segment=name,
                    count=len(members),
                    win_rate=(
                        round(sum(1 for m in decided if m.won) / len(decided), 6)
                        if decided
                        else None
                    ),
                    roi=_round(_roi([(m.decimal_price, m.won) for m in members])),
                    mean_absolute_error=round(
                        sum(abs(m.projected_total - m.actual_total) for m in members)
                        / len(members),
                        6,
                    ),
                )
            )

    return BacktestReport(
        sample_size=len(results),
        win_rate=(
            round(sum(1 for r in settled if r.won) / len(settled), 6) if settled else None
        ),
        roi=_round(_roi([(r.decimal_price, r.won) for r in results])),
        mean_absolute_error=round(sum(abs(e) for e in errors) / len(errors), 6),
        rmse=round(math.sqrt(sum(e * e for e in errors) / len(errors)), 6),
        push_count=pushes,
        mean_closing_line_value=_round(
            _mean(
                [
                    r.closing_total - r.market_total
                    for r in results
                    if r.closing_total is not None
                ]
            )
        ),
        segments=tuple(segments),
    )


def parlay_survival(results: Sequence[ParlayResult]) -> tuple[SegmentRow, ...]:
    """Realised parlay hit rate by leg count, against what was estimated.

    ``mean_absolute_error`` here is the calibration gap: how far the estimated
    parlay probability sat from the rate actually achieved at that leg count.
    """
    grouped: dict[int, list[ParlayResult]] = {}
    for result in results:
        grouped.setdefault(result.leg_count, []).append(result)
    rows: list[SegmentRow] = []
    for count in sorted(grouped):
        members = grouped[count]
        actual = sum(1 for m in members if m.won) / len(members)
        estimated = sum(m.estimated_probability for m in members) / len(members)
        rows.append(
            SegmentRow(
                segment=f"{count}-leg",
                count=len(members),
                win_rate=round(actual, 6),
                roi=_round(_roi([(m.decimal_price, m.won) for m in members])),
                mean_absolute_error=round(abs(actual - estimated), 6),
            )
        )
    return tuple(rows)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)
