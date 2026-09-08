"""Probability calibration: Platt scaling and isotonic regression.

Spec Part 13 asks for calibrated production probabilities "using methods such
as isotonic regression or Platt scaling *when validation demonstrates
improvement*". That last clause is the whole design here. Both methods are
implemented, neither is applied on faith, and :func:`fit_calibrator` returns
the identity map unless a chronological holdout shows a real gain in log loss.

This mirrors the promotion pattern the rest of this repository already uses for
blend weights and the fundamentals model: a fitted artifact does not become
production behaviour until out-of-sample skill is demonstrated.

Stdlib only, consistent with the other model primitives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from outlier_scrapers.ncaa.stats import clamp, expit, logit

__all__ = [
    "Calibrator",
    "IDENTITY",
    "fit_calibrator",
    "fit_isotonic",
    "fit_platt",
    "log_loss",
]

_EPS = 1e-12


def log_loss(pairs: Sequence[tuple[float, int]]) -> float | None:
    """Mean negative log likelihood of ``(probability, outcome)`` pairs."""
    if not pairs:
        return None
    total = 0.0
    for probability, outcome in pairs:
        p = clamp(probability, _EPS, 1.0 - _EPS)
        total -= math.log(p) if outcome else math.log(1.0 - p)
    return total / len(pairs)


@dataclass(frozen=True)
class Calibrator:
    """A fitted probability map, plus the evidence that justified it."""

    method: str
    #: Platt parameters, empty for other methods.
    slope: float = 1.0
    intercept: float = 0.0
    #: Isotonic step function as sorted ``(x_upper, y)`` breakpoints.
    breakpoints: tuple[tuple[float, float], ...] = ()
    train_size: int = 0
    holdout_size: int = 0
    holdout_log_loss: float | None = None
    baseline_log_loss: float | None = None
    improvement: float | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    def apply(self, probability: float) -> float:
        """Map a raw model probability to its calibrated value."""
        if self.method == "identity":
            return probability
        if self.method == "platt":
            return expit(self.slope * logit(probability) + self.intercept)
        if self.method == "isotonic":
            return self._isotonic(probability)
        raise ValueError(f"unknown calibration method {self.method!r}")

    def _isotonic(self, probability: float) -> float:
        if not self.breakpoints:
            return probability
        # Linear interpolation between breakpoint centres keeps the map
        # monotone and continuous; a bare step function would make nearby
        # inputs jump, which reads badly on a probability board.
        xs = [x for x, _ in self.breakpoints]
        ys = [y for _, y in self.breakpoints]
        if probability <= xs[0]:
            return ys[0]
        if probability >= xs[-1]:
            return ys[-1]
        for index in range(1, len(xs)):
            if probability <= xs[index]:
                span = xs[index] - xs[index - 1]
                if span <= 0:
                    return ys[index]
                weight = (probability - xs[index - 1]) / span
                return ys[index - 1] + weight * (ys[index] - ys[index - 1])
        return ys[-1]


IDENTITY = Calibrator(method="identity")


def fit_platt(
    pairs: Sequence[tuple[float, int]],
    *,
    iterations: int = 500,
    learning_rate: float = 0.05,
) -> tuple[float, float]:
    """Fit ``sigmoid(a * logit(p) + b)`` by gradient descent on log loss.

    Gradient descent rather than Newton: the design matrix is one-dimensional,
    the loss is convex, and avoiding a matrix inverse keeps this dependency-free
    and numerically boring.
    """
    if not pairs:
        return 1.0, 0.0
    slope, intercept = 1.0, 0.0
    n = len(pairs)
    features = [(logit(p), float(y)) for p, y in pairs]
    for _ in range(iterations):
        grad_slope = 0.0
        grad_intercept = 0.0
        for x, y in features:
            predicted = expit(slope * x + intercept)
            error = predicted - y
            grad_slope += error * x
            grad_intercept += error
        slope -= learning_rate * grad_slope / n
        intercept -= learning_rate * grad_intercept / n
    return slope, intercept


def fit_isotonic(pairs: Sequence[tuple[float, int]]) -> tuple[tuple[float, float], ...]:
    """Pool-adjacent-violators isotonic regression of outcome on probability."""
    if not pairs:
        return ()
    ordered = sorted(pairs, key=lambda item: item[0])
    # Each block carries (sum of outcomes, count, max x in block).
    blocks: list[list[float]] = []
    for probability, outcome in ordered:
        blocks.append([float(outcome), 1.0, probability])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            last = blocks.pop()
            previous = blocks.pop()
            blocks.append([previous[0] + last[0], previous[1] + last[1], last[2]])
    return tuple((block[2], block[0] / block[1]) for block in blocks)


def fit_calibrator(
    pairs: Sequence[tuple[float, int]],
    *,
    holdout_fraction: float = 0.25,
    min_samples: int = 200,
    min_improvement: float = 0.001,
) -> Calibrator:
    """Fit the best calibration map, or the identity when neither earns its place.

    ``pairs`` must already be in chronological order: the holdout is the tail,
    never a random split. Randomly splitting time-ordered betting data leaks
    future information into training and reliably overstates calibration gains.
    """
    flags: list[str] = []
    if len(pairs) < min_samples:
        return Calibrator(
            method="identity",
            train_size=len(pairs),
            flags=("insufficient_samples",),
        )

    split = int(len(pairs) * (1.0 - clamp(holdout_fraction, 0.05, 0.5)))
    train, holdout = list(pairs[:split]), list(pairs[split:])
    if not train or not holdout:
        return Calibrator(method="identity", train_size=len(pairs), flags=("split_failed",))

    baseline = log_loss(holdout)
    if baseline is None:
        return Calibrator(method="identity", train_size=len(train), flags=("no_holdout_loss",))

    candidates: list[Calibrator] = []

    slope, intercept = fit_platt(train)
    platt = Calibrator(
        method="platt",
        slope=slope,
        intercept=intercept,
        train_size=len(train),
        holdout_size=len(holdout),
    )
    candidates.append(platt)

    breakpoints = fit_isotonic(train)
    isotonic = Calibrator(
        method="isotonic",
        breakpoints=breakpoints,
        train_size=len(train),
        holdout_size=len(holdout),
    )
    candidates.append(isotonic)

    scored: list[tuple[float, Calibrator]] = []
    for candidate in candidates:
        mapped = [(candidate.apply(p), y) for p, y in holdout]
        loss = log_loss(mapped)
        if loss is None:
            continue
        scored.append((loss, candidate))

    if not scored:
        return Calibrator(method="identity", train_size=len(train), flags=("no_candidate_loss",))

    scored.sort(key=lambda item: item[0])
    best_loss, best = scored[0]
    improvement = baseline - best_loss

    if improvement < min_improvement:
        flags.append("no_out_of_sample_improvement")
        return Calibrator(
            method="identity",
            train_size=len(train),
            holdout_size=len(holdout),
            holdout_log_loss=round(baseline, 6),
            baseline_log_loss=round(baseline, 6),
            improvement=round(improvement, 6),
            flags=tuple(flags),
        )

    return Calibrator(
        method=best.method,
        slope=best.slope,
        intercept=best.intercept,
        breakpoints=best.breakpoints,
        train_size=len(train),
        holdout_size=len(holdout),
        holdout_log_loss=round(best_loss, 6),
        baseline_log_loss=round(baseline, 6),
        improvement=round(improvement, 6),
        flags=tuple(flags),
    )
