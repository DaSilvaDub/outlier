"""Stdlib-only statistical primitives for the NCAA football pipeline.

Deliberately free of numpy/scipy, matching the house convention set by
``projections.py`` and ``team_strength.py``: every model primitive in this
repository is pure Python so a pack can be rebuilt on any machine without a
scientific stack.

Contents: normal CDF/quantile, logit/expit, Gauss-Hermite quadrature (used by
the parlay copula), and a rank-1 factor fit for correlation matrices.
"""

from __future__ import annotations

import math
from functools import lru_cache

__all__ = [
    "clamp",
    "expit",
    "gauss_hermite",
    "logit",
    "normal_cdf",
    "normal_pdf",
    "normal_ppf",
    "rank_one_factor",
    "standard_normal_expectation_nodes",
]

# Probabilities are clamped away from the open interval's endpoints before any
# logit/quantile transform. 1e-9 keeps |logit| under ~21, well inside float64.
_EPS = 1e-9


def clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to ``[low, high]``."""
    if high < low:
        raise ValueError("clamp(): high must be >= low")
    return low if value < low else high if value > high else value


def normal_pdf(x: float) -> float:
    """Standard normal density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def normal_cdf(x: float) -> float:
    """Standard normal CDF via ``math.erf`` (full double precision)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam's rational approximation to the standard normal quantile, refined by a
# single Halley step against ``normal_cdf``. Absolute error after refinement is
# below 1e-12 across the representable range, which is far tighter than any
# probability this pipeline actually carries.
_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_P_LOW = 0.02425
_P_HIGH = 1.0 - _P_LOW


def normal_ppf(p: float) -> float:
    """Standard normal quantile (inverse CDF)."""
    p = clamp(p, _EPS, 1.0 - _EPS)
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )
    elif p <= _P_HIGH:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (
            ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )
    # One Halley refinement.
    err = normal_cdf(x) - p
    density = normal_pdf(x)
    if density > 0.0:
        u = err / density
        x -= u / (1.0 + 0.5 * x * u)
    return x


def logit(p: float) -> float:
    """Log-odds of ``p``, clamped away from 0 and 1."""
    p = clamp(p, _EPS, 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def expit(x: float) -> float:
    """Inverse of :func:`logit`; numerically stable in both tails."""
    if x >= 0.0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


@lru_cache(maxsize=8)
def gauss_hermite(nodes: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Physicists' Gauss-Hermite nodes/weights for ``exp(-x^2)`` weighting.

    Newton iteration on the Hermite recurrence (the classical ``gauher``
    routine). Cached because the parlay optimizer calls it once per candidate
    combination.
    """
    if nodes < 2:
        raise ValueError("gauss_hermite(): need at least 2 nodes")
    eps = 3.0e-14
    pim4 = 1.0 / math.pi ** 0.25
    max_iter = 50
    x = [0.0] * nodes
    w = [0.0] * nodes
    z = 0.0
    pp = 1.0
    for i in range((nodes + 1) // 2):
        if i == 0:
            z = math.sqrt(2.0 * nodes + 1.0) - 1.85575 * (2.0 * nodes + 1.0) ** -0.16667
        elif i == 1:
            z -= 1.14 * nodes ** 0.426 / z
        elif i == 2:
            z = 1.86 * z - 0.86 * x[0]
        elif i == 3:
            z = 1.91 * z - 0.91 * x[1]
        else:
            z = 2.0 * z - x[i - 2]
        for _ in range(max_iter):
            p1 = pim4
            p2 = 0.0
            for j in range(1, nodes + 1):
                p3 = p2
                p2 = p1
                p1 = z * math.sqrt(2.0 / j) * p2 - math.sqrt((j - 1) / j) * p3
            pp = math.sqrt(2.0 * nodes) * p2
            z1 = z
            z = z1 - p1 / pp
            if abs(z - z1) <= eps:
                break
        x[i] = z
        x[nodes - 1 - i] = -z
        w[i] = 2.0 / (pp * pp)
        w[nodes - 1 - i] = w[i]
    return tuple(x), tuple(w)


@lru_cache(maxsize=8)
def standard_normal_expectation_nodes(nodes: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Nodes/weights ``(m_k, w_k)`` with ``sum(w_k f(m_k)) ~= E[f(M)]``, ``M~N(0,1)``.

    The change of variable ``m = sqrt(2) x`` maps the physicists' weight
    ``exp(-x^2)`` onto the standard normal density; weights are divided by
    ``sqrt(pi)`` so they sum to one.
    """
    x, w = gauss_hermite(nodes)
    scale = 1.0 / math.sqrt(math.pi)
    return (
        tuple(math.sqrt(2.0) * xi for xi in x),
        tuple(wi * scale for wi in w),
    )


def rank_one_factor(
    correlation: list[list[float]],
    *,
    max_loading: float = 0.95,
    iterations: int = 200,
    tolerance: float = 1e-10,
) -> list[float]:
    """Best rank-1 loadings ``a`` with ``a_i * a_j ~= rho_ij`` for ``i != j``.

    Principal-factor iteration: the diagonal of a correlation matrix carries no
    information about the shared factor, so it is replaced by the current
    communality estimate ``a_i ** 2`` and the leading eigenpair is recomputed
    until the loadings stop moving. Zeroing the diagonal instead (plain power
    iteration) systematically understates the loadings -- for an ``n``-leg block
    with common ``rho`` it converges to ``rho * (n - 1) / n`` rather than
    ``rho`` -- and understated correlation is exactly the error this pipeline
    must not make.

    The result feeds a single-factor Gaussian copula: an arbitrary tag-derived
    correlation structure is compressed into one common factor, which is the
    standard credit-risk simplification and keeps the joint-probability
    integral one-dimensional.

    Loadings are clipped to ``[0, max_loading]``. Negative correlations are not
    representable in a single non-negative factor; they are floored at zero,
    which is the conservative direction for a product whose failure mode is
    understating joint risk.
    """
    size = len(correlation)
    if size == 0:
        return []
    if size == 1:
        return [0.0]
    off = [
        [0.0 if i == j else clamp(correlation[i][j], 0.0, 1.0) for j in range(size)]
        for i in range(size)
    ]
    # Communality seed: the largest correlation in each row, the conventional
    # starting estimate for principal-factor analysis.
    diag = [max(off[i]) for i in range(size)]
    loadings = [0.0] * size
    for _ in range(iterations):
        matrix = [
            [diag[i] if i == j else off[i][j] for j in range(size)] for i in range(size)
        ]
        eigenvalue, vector = _leading_eigenpair(matrix, iterations=iterations, tolerance=tolerance)
        if eigenvalue <= tolerance:
            return [0.0] * size
        scale = math.sqrt(eigenvalue)
        nxt = [clamp(abs(v) * scale, 0.0, max_loading) for v in vector]
        delta = max(abs(nxt[i] - loadings[i]) for i in range(size))
        loadings = nxt
        diag = [a * a for a in loadings]
        if delta <= tolerance:
            break
    return loadings


def _leading_eigenpair(
    matrix: list[list[float]],
    *,
    iterations: int,
    tolerance: float,
) -> tuple[float, list[float]]:
    """Leading eigenvalue/unit eigenvector by power iteration."""
    size = len(matrix)
    vector = [1.0 / math.sqrt(size)] * size
    eigenvalue = 0.0
    for _ in range(iterations):
        nxt = [sum(matrix[i][j] * vector[j] for j in range(size)) for i in range(size)]
        norm = math.sqrt(sum(v * v for v in nxt))
        if norm <= tolerance:
            return 0.0, [0.0] * size
        nxt = [v / norm for v in nxt]
        delta = max(abs(nxt[i] - vector[i]) for i in range(size))
        vector = nxt
        eigenvalue = norm
        if delta <= tolerance:
            break
    return eigenvalue, vector
