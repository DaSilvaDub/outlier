"""Deterministic, provider-independent projection primitives.

Core distribution contracts stay local and training-free. Optional MLB Stats API
adapters attach starter game-log BF/K features for SO projections; league-average
stubs remain audit-only and must not fill independent_model_prob.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError

from outlier_scrapers.utils import _local_date

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionDistribution:
    """A bounded discrete distribution with explicit omitted tail mass."""

    pmf: dict[int, float]
    tail_mass: float
    model_version: str = "projection-v1"

    def __post_init__(self) -> None:
        if any(outcome < 0 or not math.isfinite(prob) or prob < 0 for outcome, prob in self.pmf.items()):
            raise ValueError("PMF outcomes must be nonnegative and probabilities must be finite")
        if not math.isfinite(self.tail_mass) or self.tail_mass < 0:
            raise ValueError("tail_mass must be finite and nonnegative")
        total = sum(self.pmf.values()) + self.tail_mass
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"PMF plus tail mass must sum to one, got {total}")

    @property
    def mean(self) -> float:
        return sum(outcome * probability for outcome, probability in self.pmf.items())

    @property
    def variance(self) -> float:
        mean = self.mean
        return sum((outcome - mean) ** 2 * probability for outcome, probability in self.pmf.items())

    def quantile(self, probability: float) -> int:
        if not 0 <= probability <= 1:
            raise ValueError("quantile probability must be between zero and one")
        cumulative = 0.0
        for outcome in sorted(self.pmf):
            cumulative += self.pmf[outcome]
            if cumulative >= probability:
                return outcome
        return max(self.pmf, default=0)

    def partition(self, line: float, side: str) -> dict[str, float]:
        """Return selected-side win, push, and loss probabilities."""

        line = float(line)
        normalized_side = side.strip().upper()
        if normalized_side not in {"OVER", "UNDER"}:
            raise ValueError("side must be OVER or UNDER")
        push = self.pmf.get(int(line), 0.0) if line.is_integer() else 0.0
        maximum = max(self.pmf, default=0)
        if self.tail_mass > 1e-12 and line > maximum:
            raise ValueError("line exceeds bounded PMF support while tail mass remains")
        if normalized_side == "OVER":
            win = self.tail_mass + sum(
                probability for outcome, probability in self.pmf.items() if outcome > line
            )
        else:
            win = sum(probability for outcome, probability in self.pmf.items() if outcome < line)
        return {"win_prob": win, "push_prob": push, "loss_prob": max(0.0, 1.0 - win - push)}

    def to_record(self, *, line: float | None = None, side: str | None = None) -> dict[str, object]:
        record: dict[str, object] = {
            "pmf": {str(outcome): probability for outcome, probability in sorted(self.pmf.items())},
            "tail_mass": self.tail_mass,
            "mean": self.mean,
            "variance": self.variance,
            "quantiles": {str(level): self.quantile(level) for level in (0.5, 0.9, 0.95)},
            "model_version": self.model_version,
        }
        if line is not None and side is not None:
            record["line"] = line
            record["side"] = side.upper()
            record.update(self.partition(line, side))
        return record


def _bounded_distribution(weights: Mapping[int, float], *, model_version: str = "projection-v1") -> ProjectionDistribution:
    cleaned = {int(outcome): max(0.0, float(weight)) for outcome, weight in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError("distribution has no positive mass")
    normalized = {outcome: weight / total for outcome, weight in cleaned.items()}
    return ProjectionDistribution(normalized, 0.0, model_version)


def _negative_binomial_pmf(mean: float, dispersion: float, maximum: int) -> dict[int, float]:
    if mean < 0 or dispersion <= 0 or maximum < 0:
        raise ValueError("mean must be nonnegative, dispersion positive, and maximum nonnegative")
    if mean == 0:
        return {0: 1.0}
    p = dispersion / (dispersion + mean)
    return {
        count: math.exp(
            math.lgamma(count + dispersion)
            - math.lgamma(dispersion)
            - math.lgamma(count + 1)
            + dispersion * math.log(p)
            + count * math.log1p(-p)
        )
        for count in range(maximum + 1)
    }


def negative_binomial_distribution(
    mean: float, dispersion: float, *, maximum: int | None = None
) -> ProjectionDistribution:
    """Create a bounded NB2 distribution and retain omitted tail mass."""

    variance = mean + mean * mean / dispersion
    maximum = maximum or max(20, math.ceil(mean + 10 * math.sqrt(max(variance, 1e-12))))
    weights = _negative_binomial_pmf(mean, dispersion, maximum)
    mass = sum(weights.values())
    return ProjectionDistribution(weights, max(0.0, 1.0 - mass))


def poisson_distribution(mean: float, *, maximum: int | None = None) -> ProjectionDistribution:
    """Create a bounded Poisson distribution using only the stdlib."""

    if mean < 0:
        raise ValueError("mean must be nonnegative")
    maximum = maximum or max(20, math.ceil(mean + 10 * math.sqrt(max(mean, 1e-12))))
    if mean == 0:
        return ProjectionDistribution({0: 1.0}, 0.0)
    weights = {0: math.exp(-mean)}
    for outcome in range(1, maximum + 1):
        weights[outcome] = weights[outcome - 1] * mean / outcome
    mass = sum(weights.values())
    return ProjectionDistribution(weights, max(0.0, 1.0 - mass))


def mlb_first_inning_run_distribution(
    home_run_mean: float, away_run_mean: float
) -> ProjectionDistribution:
    """Project total first-inning runs from the two starter/top-order means."""

    home = poisson_distribution(home_run_mean)
    away = poisson_distribution(away_run_mean)
    total: dict[int, float] = {}
    for home_runs, home_probability in home.pmf.items():
        for away_runs, away_probability in away.pmf.items():
            total[home_runs + away_runs] = total.get(home_runs + away_runs, 0.0) + (
                home_probability * away_probability
            )
    mass = sum(total.values())
    return ProjectionDistribution(total, max(0.0, 1.0 - mass))


def binomial_distribution(trials: int, probability: float) -> ProjectionDistribution:
    if trials < 0 or not 0 <= probability <= 1:
        raise ValueError("trials must be nonnegative and probability must be in [0, 1]")
    weights = {
        successes: math.comb(trials, successes)
        * probability**successes
        * (1.0 - probability) ** (trials - successes)
        for successes in range(trials + 1)
    }
    return _bounded_distribution(weights)


def mlb_strikeout_distribution(
    projected_bf: float,
    strikeout_rate: float,
    *,
    workload_dispersion: float = 20.0,
    maximum_bf: int | None = None,
) -> ProjectionDistribution:
    """Mix conditional binomial strikeouts over a stochastic BF workload."""

    if projected_bf < 0 or not 0 <= strikeout_rate <= 1:
        raise ValueError("projected_bf must be nonnegative and strikeout_rate must be in [0, 1]")
    bf_variance = projected_bf + projected_bf**2 / workload_dispersion
    maximum_bf = maximum_bf or max(1, math.ceil(projected_bf + 10 * math.sqrt(max(bf_variance, 1e-12))))
    workload = _negative_binomial_pmf(projected_bf, workload_dispersion, maximum_bf)
    strikeouts: dict[int, float] = {}
    for bf, workload_probability in workload.items():
        for strikeout, conditional_probability in binomial_distribution(bf, strikeout_rate).pmf.items():
            strikeouts[strikeout] = strikeouts.get(strikeout, 0.0) + workload_probability * conditional_probability
    mass = sum(strikeouts.values())
    return ProjectionDistribution(strikeouts, max(0.0, 1.0 - mass))


def mlb_hits_allowed_distribution(
    projected_bf: float, hit_rate: float, *, dispersion: float = 8.0
) -> ProjectionDistribution:
    """Project hits allowed with an NB2 mean driven by workload."""

    if projected_bf < 0 or hit_rate < 0:
        raise ValueError("projected_bf and hit_rate must be nonnegative")
    return negative_binomial_distribution(projected_bf * hit_rate, dispersion)


def mlb_total_bases_distribution(
    plate_appearances: int,
    zero_rate: float,
    positive_outcome_probs: Mapping[int, float],
) -> ProjectionDistribution:
    """Project total bases with a zero hurdle and compound PA outcomes."""

    if plate_appearances < 0 or not 0 <= zero_rate <= 1:
        raise ValueError("plate_appearances must be nonnegative and zero_rate must be in [0, 1]")
    outcomes = {int(value): float(probability) for value, probability in positive_outcome_probs.items()}
    if not outcomes or any(value <= 0 or probability < 0 for value, probability in outcomes.items()):
        raise ValueError("positive outcomes must have positive values and nonnegative probabilities")
    positive_total = sum(outcomes.values())
    if not math.isclose(positive_total, 1.0, abs_tol=1e-9):
        raise ValueError("positive outcome probabilities must sum to one")
    pa = {0: zero_rate, **{value: (1.0 - zero_rate) * probability for value, probability in outcomes.items()}}
    aggregate = {0: 1.0}
    for _ in range(plate_appearances):
        next_aggregate: dict[int, float] = {}
        for current, current_probability in aggregate.items():
            for value, probability in pa.items():
                next_aggregate[current + value] = next_aggregate.get(current + value, 0.0) + current_probability * probability
        aggregate = next_aggregate
    return _bounded_distribution(aggregate)


# NB2 dispersion for full-game MLB run totals. Not fit from data: it is a
# literature-typical placeholder (variance moderately above the Poisson mean,
# consistent with well-documented run-total overdispersion) pending an
# empirical method-of-moments refit from settled ``actual_result`` totals
# once enough accumulate (see ``empirical_game_total_dispersion``). Anything
# derived from it is a market-implied diagnostic, never an independent
# forecast, until it is replaced with a fitted value.
DEFAULT_MLB_GAME_TOTAL_DISPERSION = 12.0


def mlb_game_total_runs_distribution(
    mean: float, *, dispersion: float = DEFAULT_MLB_GAME_TOTAL_DISPERSION, maximum: int | None = None
) -> ProjectionDistribution:
    """Project full-game combined run total as a single NB2 around ``mean``.

    ``mean`` is expected to come from the market itself (e.g. the ladder's
    interpolated fair total) rather than a fundamentals forecast — this
    pipeline has no independent run-scoring model yet. The distribution
    exists to give that market-implied point a shape (sigma, quantiles) so
    edges can be standardized and multi-line probabilities stay internally
    consistent, not to claim new predictive power.
    """
    if mean < 0:
        raise ValueError("mean must be nonnegative")
    return negative_binomial_distribution(mean, dispersion, maximum=maximum)


def standardized_edge(distribution: ProjectionDistribution, line: float) -> float:
    """(mean - line) / sigma for a projection distribution at a totals line.

    A dimensionless alternative to a raw probability-edge threshold: it
    expresses how many standard deviations the distribution's mean sits from
    the line, so the same gate is comparable across totals with very
    different scales (e.g. a game total vs. a team total) instead of a flat
    percentage-point cutoff like ``MIN_EDGE_TOTALS``.
    """
    variance = distribution.variance
    if variance <= 0:
        raise ValueError("distribution variance must be positive")
    return (distribution.mean - float(line)) / math.sqrt(variance)


def fit_negative_binomial_dispersion(mean: float, sample_variance: float) -> float | None:
    """Method-of-moments NB2 dispersion from an observed mean/variance.

    Returns None when the sample is not overdispersed relative to Poisson
    (``sample_variance <= mean``): NB2 cannot represent that, and the caller
    should fall back to a Poisson distribution or the documented default.
    """
    if mean <= 0 or sample_variance <= mean:
        return None
    return mean * mean / (sample_variance - mean)


def empirical_game_total_dispersion(actual_totals: Iterable[float]) -> dict[str, float | int | None]:
    """Method-of-moments NB2 dispersion fit from settled game-total outcomes.

    Pure, DB-free: callers pass in the actual settled totals (e.g. from
    ``settlements.actual_result``) they've already fetched. Returns n/mean/
    variance alongside the fitted dispersion (None when underdispersed or
    n < 2) so a caller can decide whether to trust it over the placeholder
    default.
    """
    values = [float(value) for value in actual_totals]
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "variance": None, "dispersion": None}
    mean = sum(values) / n
    if n < 2:
        return {"n": n, "mean": mean, "variance": None, "dispersion": None}
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    return {
        "n": n,
        "mean": mean,
        "variance": variance,
        "dispersion": fit_negative_binomial_dispersion(mean, variance),
    }


STARTER_PROJECTED_BF = 22.0
LEAGUE_STRIKEOUT_RATE = 0.225
LEAGUE_AVG_SO_HASH = "so-starter-league-avg-v1"
GAMELOG_SO_HASH = "so-starter-gamelog-v2"  # v2 = EB shrink + thin-sample soften
WNBA_MINUTES_HASH = "wnba-minutes-ppm-v1"  # legacy points-only scaffold
WNBA_GAMELOG_HASH = "wnba-gamelog-stat-rates-v2"
AUDIT_ONLY_PROJECTION_HASHES = frozenset(
    {LEAGUE_AVG_SO_HASH, WNBA_MINUTES_HASH, WNBA_GAMELOG_HASH}
)
WNBA_MARKET_COMPONENTS: dict[str, tuple[str, ...]] = {
    "PTS": ("points",),
    "REB": ("rebounds",),
    "AST": ("assists",),
    "PR": ("points", "rebounds"),
    "PA": ("points", "assists"),
    "RA": ("rebounds", "assists"),
    "PRA": ("points", "rebounds", "assists"),
}
WNBA_MARKET_ALIASES = {
    "POINT": "PTS",
    "POINTS": "PTS",
    "REBOUND": "REB",
    "REBOUNDS": "REB",
    "ASSIST": "AST",
    "ASSISTS": "AST",
    "POINTS_REBOUNDS": "PR",
    "POINTSREBOUNDS": "PR",
    "POINTS_ASSISTS": "PA",
    "POINTSASSISTS": "PA",
    "REBOUNDS_ASSISTS": "RA",
    "REBOUNDSASSISTS": "RA",
    "POINTS_REBOUNDS_ASSISTS": "PRA",
    "POINTSREBOUNDSASSISTS": "PRA",
}
WNBA_GAMELOG_STAT_ALIASES: dict[str, tuple[str, ...]] = {
    "points": ("points", "pts"),
    "rebounds": ("rebounds", "totalrebounds", "reb", "rebs"),
    "assists": ("assists", "ast"),
}
MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1"
ESPN_SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
ESPN_WNBA_GAMELOG_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/wnba/athletes"
)
DEFAULT_SO_MIN_STARTS = 3
DEFAULT_SO_MAX_STARTS = 8
DEFAULT_SO_MIN_TOTAL_BF = 45
# Empirical-Bayes prior strength for thin recent-start samples. Diagnosis of
# settled gamelog SO (n=7) showed 7/7 independents more extreme than market and
# mean K sometimes absurd (e.g. 8.25 vs a 5.5 line) because raw L3-L8 averages
# were used without shrinkage or extra uncertainty.
SO_RATE_PRIOR_BF = 90.0  # ~4 league-average starts of PA/BF strength
SO_BF_PRIOR_STARTS = 4.0
SO_BASE_WORKLOAD_DISPERSION = 12.0
SO_THIN_START_DISPERSION_STEP = 5.0

# statsapi.mlb.com teamId keyed by Outlier canonical abbreviations (2026).
MLB_TEAM_STATS_IDS: dict[str, int] = {
    "ATH": 133,
    "ATL": 144,
    "AZ": 109,
    "BAL": 110,
    "BOS": 111,
    "CHC": 112,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "CWS": 145,
    "DET": 116,
    "HOU": 117,
    "KC": 118,
    "LAA": 108,
    "LAD": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYM": 121,
    "NYY": 147,
    "PHI": 143,
    "PIT": 134,
    "SD": 135,
    "SEA": 136,
    "SF": 137,
    "STL": 138,
    "TB": 139,
    "TEX": 140,
    "TOR": 141,
    "WSH": 120,
}
# Curated home-park SO multipliers (1.0 = league average). Source: Statcast-style
# SO park factors compressed to multipliers; missing teams default to 1.0.
HOME_PARK_K_FACTORS: dict[str, float] = {
    "ATH": 1.00,
    "ATL": 1.02,
    "AZ": 0.99,
    "BAL": 1.01,
    "BOS": 0.98,
    "CHC": 1.01,
    "CIN": 1.03,
    "CLE": 1.02,
    "COL": 0.93,
    "CWS": 1.01,
    "DET": 1.02,
    "HOU": 1.01,
    "KC": 0.99,
    "LAA": 1.00,
    "LAD": 1.01,
    "MIA": 1.03,
    "MIL": 1.02,
    "MIN": 1.01,
    "NYM": 1.02,
    "NYY": 1.01,
    "PHI": 1.02,
    "PIT": 1.00,
    "SD": 1.05,
    "SEA": 1.03,
    "SF": 1.04,
    "STL": 0.99,
    "TB": 1.01,
    "TEX": 1.00,
    "TOR": 1.00,
    "WSH": 1.01,
}


def independent_projection_eligible(projection: Mapping[str, object] | None) -> bool:
    """Audit-only digests must not fill independent_model_prob.

    League-avg SO stubs and WNBA gamelog-rate scaffolds stay shadow/audit until
    calibrated. Other projection artifacts remain eligible.
    """

    if not isinstance(projection, Mapping):
        return False
    digest = str(projection.get("feature_snapshot_hash") or "")
    return digest not in AUDIT_ONLY_PROJECTION_HASHES


SO_MODEL_PARAM_KEYS = (
    "league_strikeout_rate",
    "starter_projected_bf",
    "rate_prior_bf",
    "bf_prior_starts",
    "base_workload_dispersion",
    "thin_start_dispersion_step",
)
CALIBRATED_SO_HASH_PREFIX = "so-starter-calibrated-"
SO_MODEL_SCHEMA_VERSION = 1
# The starter-SO feature contract, hashed. Training records it on every sample
# and artifact; inference refuses a promoted artifact fitted on anything else.
SO_FEATURE_SCHEMA: tuple[str, ...] = (
    "projected_bf",
    "strikeout_rate",
    "starts",
    "total_bf",
    "total_k",
)
SO_FEATURE_SCHEMA_HASH = hashlib.sha256(
    "|".join(SO_FEATURE_SCHEMA).encode("utf-8")
).hexdigest()[:16]


def is_current_gamelog_so_hash(digest: object) -> bool:
    """Is this feature hash the current starter-SO generation?

    The v2 gamelog hash and any calibrated refit of it are the same feature
    family — a promotion swaps the fitted parameters, not the inputs. Gates that
    compared against the v2 literal alone would silently drop every row once a
    calibrated model is promoted, disabling restaking and starving the SO
    promotion gate. v1 stays excluded: it was the overconfident pre-shrinkage
    generation.
    """

    text = str(digest or "")
    return text == GAMELOG_SO_HASH or text.startswith(CALIBRATED_SO_HASH_PREFIX)


def default_so_model_params() -> dict[str, float]:
    """Hand-set priors used until a trained artifact is promoted."""

    return {
        "league_strikeout_rate": LEAGUE_STRIKEOUT_RATE,
        "starter_projected_bf": STARTER_PROJECTED_BF,
        "rate_prior_bf": SO_RATE_PRIOR_BF,
        "bf_prior_starts": SO_BF_PRIOR_STARTS,
        "base_workload_dispersion": SO_BASE_WORKLOAD_DISPERSION,
        "thin_start_dispersion_step": SO_THIN_START_DISPERSION_STEP,
    }


def resolve_so_model_params(params: Mapping[str, object] | None = None) -> dict[str, float]:
    """Overlay a trained artifact's parameters on the defaults, fail-safe."""

    resolved = default_so_model_params()
    if not isinstance(params, Mapping):
        return resolved
    for key in SO_MODEL_PARAM_KEYS:
        value = _float_stat(params.get(key))
        if value is None or value < 0:
            continue
        resolved[key] = value
    # Guard rails: a corrupt artifact must not produce a degenerate model.
    resolved["league_strikeout_rate"] = min(0.45, max(0.08, resolved["league_strikeout_rate"]))
    resolved["starter_projected_bf"] = min(40.0, max(1.0, resolved["starter_projected_bf"]))
    resolved["base_workload_dispersion"] = max(1e-3, resolved["base_workload_dispersion"])
    return resolved


def so_model_artifact_path(sport: str = "MLB") -> Path:
    from . import paths as _paths

    return _paths.PROJECT_ROOT / "calibration" / "projections" / f"{sport.upper()}_so_model.json"


_PROMOTED_SO_MODEL_CACHE: dict[str, tuple[float, dict[str, object] | None]] = {}


def load_promoted_so_model(path: Path | None = None) -> dict[str, object] | None:
    """Return the promoted, schema-compatible calibrated SO model, else None.

    Training and validation write artifacts continuously; only an explicitly
    promoted one changes live inference (docs/plans/independent-projection-layer.md).
    """

    artifact_path = Path(path) if path is not None else so_model_artifact_path("MLB")
    try:
        stamp = artifact_path.stat().st_mtime
    except OSError:
        _PROMOTED_SO_MODEL_CACHE.pop(str(artifact_path), None)
        return None
    cached = _PROMOTED_SO_MODEL_CACHE.get(str(artifact_path))
    if cached is not None and cached[0] == stamp:
        # Hand out a copy: a caller mutating the artifact must not poison the
        # cache for every later row in the same process.
        return dict(cached[1]) if cached[1] is not None else None
    payload: dict[str, object] | None = None
    try:
        loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable SO model artifact %s: %s", artifact_path, exc)
        loaded = None
    if isinstance(loaded, Mapping):
        compatible = (
            bool(loaded.get("promoted"))
            and str(loaded.get("status") or "") == "trained"
            and int(_float_stat(loaded.get("schema_version")) or 0) == SO_MODEL_SCHEMA_VERSION
            and bool(str(loaded.get("model_version") or ""))
            # An artifact with no/other feature schema was fitted on different
            # inputs than inference feeds it; refuse rather than guess.
            and str(loaded.get("feature_schema_hash") or "") == SO_FEATURE_SCHEMA_HASH
        )
        if compatible:
            payload = dict(loaded)
        elif loaded.get("promoted"):
            logger.warning(
                "Promoted SO model artifact %s is incompatible; staying on defaults.",
                artifact_path,
            )
    _PROMOTED_SO_MODEL_CACHE[str(artifact_path)] = (stamp, payload)
    return dict(payload) if payload is not None else None


def _default_stats_fetch_json(request_url: str) -> dict[str, object]:
    from urllib.request import Request, urlopen

    request = Request(
        request_url,
        headers={"User-Agent": "outlier-projections/1.0", "Accept": "application/json"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stats payload must be an object")
    return payload


def _normalize_person_name(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


_SELECTION_NAME_RE = re.compile(
    r"^(.*?)\s+(?:OVER|UNDER)\b",
    re.IGNORECASE,
)
_TRAILING_MARKET_RE = re.compile(
    r"\s+(?:SO|STRIKEOUTS?|K|PTS|REB|AST|PRA|PR|PA|RA)$",
    re.IGNORECASE,
)


def _player_name_from_row(row: Mapping[str, object]) -> str:
    player = str(row.get("player") or "").strip()
    if player:
        return player
    selection = str(row.get("selection") or "")
    if " - " in selection:
        return selection.split(" - ", 1)[0].strip()
    match = _SELECTION_NAME_RE.match(selection)
    if not match:
        return ""
    return _TRAILING_MARKET_RE.sub("", match.group(1).strip()).strip()


def _float_stat(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def compute_starter_so_features_from_logs(
    splits: Iterable[Mapping[str, object]],
    *,
    min_starts: int = DEFAULT_SO_MIN_STARTS,
    max_starts: int = DEFAULT_SO_MAX_STARTS,
    min_total_bf: int = DEFAULT_SO_MIN_TOTAL_BF,
) -> dict[str, object] | None:
    """Derive starter BF / K-rate from MLB Stats API pitching game logs.

    Uses only gamesStarted appearances. Newest splits first when dates exist.
    Returns None when the sample is too thin — callers must fail closed.
    """
    starts: list[tuple[str, float, float]] = []
    for split in splits:
        if not isinstance(split, Mapping):
            continue
        stat = split.get("stat")
        if not isinstance(stat, Mapping):
            continue
        if int(_float_stat(stat.get("gamesStarted")) or 0) < 1:
            continue
        bf = _float_stat(stat.get("battersFaced"))
        so = _float_stat(stat.get("strikeOuts"))
        if bf is None or so is None or bf <= 0 or so < 0 or so > bf:
            continue
        starts.append((str(split.get("date") or ""), bf, so))
    starts.sort(key=lambda item: item[0], reverse=True)
    kept = starts[: max(0, max_starts)]
    if len(kept) < min_starts:
        return None
    total_bf = sum(item[1] for item in kept)
    total_k = sum(item[2] for item in kept)
    if total_bf < min_total_bf:
        return None
    return {
        "projected_bf": total_bf / len(kept),
        "strikeout_rate": total_k / total_bf,
        "starts": len(kept),
        "total_bf": total_bf,
        "total_k": total_k,
        "feature_source": "mlb_stats_gamelog",
    }


def fetch_pitcher_pitching_game_logs(
    pitcher_id: int | str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> list[dict[str, object]]:
    """Fetch one pitcher's pitching gameLog splits for ``season``."""
    person_id = str(pitcher_id).strip()
    if not person_id.isdigit():
        return []
    url = (
        f"{MLB_STATS_API_BASE}/people/{person_id}/stats"
        f"?stats=gameLog&group=pitching&season={int(season)}"
    )
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    stats = payload.get("stats")
    if not isinstance(stats, list) or not stats:
        return []
    splits = stats[0].get("splits") if isinstance(stats[0], Mapping) else None
    if not isinstance(splits, list):
        return []
    return [dict(split) for split in splits if isinstance(split, Mapping)]


def fetch_team_batter_k_rate(
    team_code: str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> float | None:
    """Season batter K rate (SO/PA) for one MLB team abbreviation."""
    team_id = MLB_TEAM_STATS_IDS.get(str(team_code or "").strip().upper())
    if team_id is None:
        return None
    url = (
        f"{MLB_STATS_API_BASE}/teams/{team_id}/stats"
        f"?stats=season&group=hitting&season={int(season)}"
    )
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    stats = payload.get("stats")
    if not isinstance(stats, list) or not stats:
        return None
    splits = stats[0].get("splits") if isinstance(stats[0], Mapping) else None
    if not isinstance(splits, list) or not splits:
        return None
    stat = splits[0].get("stat") if isinstance(splits[0], Mapping) else None
    if not isinstance(stat, Mapping):
        return None
    strikeouts = _float_stat(stat.get("strikeOuts"))
    plate_appearances = _float_stat(stat.get("plateAppearances"))
    if (
        strikeouts is None
        or plate_appearances is None
        or plate_appearances <= 0
        or strikeouts < 0
        or strikeouts > plate_appearances
    ):
        return None
    return strikeouts / plate_appearances


def park_k_factor_for_venue_team(team_code: str | None) -> float | None:
    """Return curated home-park SO multiplier for a team abbreviation."""
    code = str(team_code or "").strip().upper()
    if not code:
        return None
    factor = HOME_PARK_K_FACTORS.get(code)
    if factor is None:
        return 1.0
    return factor


def enrich_probable_with_so_features(
    by_team: Mapping[str, Mapping[str, object]],
    *,
    season: int,
    fetch_json: Any | None = None,
    min_starts: int = DEFAULT_SO_MIN_STARTS,
    max_starts: int = DEFAULT_SO_MAX_STARTS,
    min_total_bf: int = DEFAULT_SO_MIN_TOTAL_BF,
) -> dict[str, dict[str, object]]:
    """Attach starter SO + opponent/park context onto a probable-pitcher lookup."""
    enriched: dict[str, dict[str, object]] = {}
    cache: dict[str, dict[str, object] | None] = {}
    opponent_cache: dict[str, float | None] = {}
    for team, info in by_team.items():
        row = dict(info) if isinstance(info, Mapping) else {}
        pitcher_id = str(row.get("pitcher_id") or "").strip()
        if row.get("confirmed") and pitcher_id:
            if pitcher_id not in cache:
                try:
                    logs = fetch_pitcher_pitching_game_logs(
                        pitcher_id, season=season, fetch_json=fetch_json
                    )
                    cache[pitcher_id] = compute_starter_so_features_from_logs(
                        logs,
                        min_starts=min_starts,
                        max_starts=max_starts,
                        min_total_bf=min_total_bf,
                    )
                except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "SO gamelog features failed for pitcher_id=%s season=%s: %s",
                        pitcher_id,
                        season,
                        exc,
                    )
                    cache[pitcher_id] = None
            features = cache[pitcher_id]
            if features:
                row.update(features)

            opponent = str(row.get("opponent") or "").strip().upper()
            if opponent:
                if opponent not in opponent_cache:
                    try:
                        opponent_cache[opponent] = fetch_team_batter_k_rate(
                            opponent, season=season, fetch_json=fetch_json
                        )
                    except (
                        HTTPError,
                        URLError,
                        TimeoutError,
                        ValueError,
                        OSError,
                        json.JSONDecodeError,
                    ) as exc:
                        logger.warning(
                            "Opponent K%% fetch failed for team=%s season=%s: %s",
                            opponent,
                            season,
                            exc,
                        )
                        opponent_cache[opponent] = None
                if opponent_cache[opponent] is not None:
                    row["opponent_k_rate"] = opponent_cache[opponent]
                    row["opponent_k_source"] = "mlb_stats_team_hitting"

            home_away = str(row.get("home_away") or "").strip().upper()
            venue_team = str(team).strip().upper() if home_away == "HOME" else opponent
            park_factor = park_k_factor_for_venue_team(venue_team)
            if park_factor is not None:
                row["park_k_factor"] = park_factor
                row["park_k_source"] = "curated_home_park_so"
                row["park_team"] = venue_team
        enriched[str(team)] = row
    return enriched


def mlb_so_projection_record(
    row: Mapping[str, object], probable_by_team: Mapping[str, Mapping[str, object]] | None
) -> dict[str, object] | None:
    """Build a starter-SO projection from probable pitchers.

    Confirmed starters with pitcher-specific BF/K features emit an
    independent-eligible hash. Confirmed starters without features fall back to
    the league-average audit stub. Relievers/openers stay blank.
    The betting line is never used as a feature.
    """
    sport = str(row.get("sport") or row.get("league") or "").upper()
    market = str(row.get("market_type") or row.get("market") or row.get("proposition") or "").upper()
    if sport != "MLB":
        return None
    if market not in {"SO", "STRIKEOUTS", "PITCHER_STRIKEOUTS", "K"} and "STRIKEOUT" not in market:
        selection = str(row.get("selection") or "").upper()
        if "STRIKEOUT" not in selection:
            return None
    player = _normalize_person_name(_player_name_from_row(row))
    if not player or not probable_by_team:
        return None
    listed = None
    for info in probable_by_team.values():
        if not isinstance(info, Mapping):
            continue
        if _normalize_person_name(info.get("pitcher")) == player:
            listed = info
            break
    if listed is None or not listed.get("confirmed"):
        return None
    line = row.get("line")
    side = str(row.get("headline_side") or row.get("position") or "").upper()
    if "OVER" in str(row.get("selection") or "").upper():
        side = "OVER"
    elif "UNDER" in str(row.get("selection") or "").upper():
        side = "UNDER"
    if side not in {"OVER", "UNDER"} or line in (None, ""):
        return None
    try:
        line_value = float(str(line).replace("+", ""))
    except (TypeError, ValueError):
        return None

    projected_bf = _float_stat(listed.get("projected_bf"))
    strikeout_rate = _float_stat(listed.get("strikeout_rate"))
    feature_source = str(listed.get("feature_source") or "")
    if (
        feature_source == "mlb_stats_gamelog"
        and projected_bf is not None
        and strikeout_rate is not None
        and projected_bf > 0
        and 0.0 < strikeout_rate < 1.0
    ):
        starts = int(_float_stat(listed.get("starts")) or 0)
        total_bf = _float_stat(listed.get("total_bf"))
        total_k = _float_stat(listed.get("total_k"))
        calibrated = load_promoted_so_model()
        calibrated_params = calibrated.get("parameters") if calibrated else None
        model_params = resolve_so_model_params(
            calibrated_params if isinstance(calibrated_params, Mapping) else None
        )
        shrunk = shrink_starter_so_features(
            projected_bf,
            strikeout_rate,
            starts=starts,
            total_bf=total_bf,
            total_k=total_k,
            params=model_params,
        )
        adjusted_bf, adjusted_rate = apply_so_context_adjustments(
            float(shrunk["projected_bf"]),
            float(shrunk["strikeout_rate"]),
            opponent_k_rate=_float_stat(listed.get("opponent_k_rate")),
            park_k_factor=_float_stat(listed.get("park_k_factor")),
        )
        distribution = mlb_strikeout_distribution(
            adjusted_bf,
            adjusted_rate,
            workload_dispersion=float(shrunk["workload_dispersion"]),
        )
        feature_hash = GAMELOG_SO_HASH
        if calibrated is not None:
            feature_hash = f"{CALIBRATED_SO_HASH_PREFIX}{calibrated['model_version']}"
        record = distribution.to_record(line=line_value, side=side)
        record = soften_so_win_probability(
            record,
            reliability=float(shrunk["reliability"]),
        )
    else:
        distribution = mlb_strikeout_distribution(STARTER_PROJECTED_BF, LEAGUE_STRIKEOUT_RATE)
        feature_hash = LEAGUE_AVG_SO_HASH
        record = distribution.to_record(line=line_value, side=side)
    return {
        "status": "eligible",
        "sport": "MLB",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "line": line_value,
        "side": side,
        "feature_snapshot_hash": feature_hash,
        "distribution": record,
    }


def shrink_starter_so_features(
    projected_bf: float,
    strikeout_rate: float,
    *,
    starts: int = 0,
    total_bf: float | None = None,
    total_k: float | None = None,
    params: Mapping[str, object] | None = None,
) -> dict[str, float]:
    """Empirical-Bayes shrink recent-start BF/K toward league priors.

    Thin samples produced overconfident independent probs (settled gamelog
    diagnosis: 7/7 more extreme than market). Shrinkage + wider dispersion
    flattens those tails without using the betting line as a feature.
    """
    settings = resolve_so_model_params(params)
    prior_bf = settings["rate_prior_bf"]
    prior_rate = settings["league_strikeout_rate"]
    prior_starts = settings["bf_prior_starts"]
    prior_workload = settings["starter_projected_bf"]
    starts = max(0, int(starts))
    observed_bf = float(total_bf) if total_bf is not None and total_bf > 0 else projected_bf * max(starts, 1)
    if total_k is not None and total_k >= 0 and observed_bf > 0:
        rate_numer = float(total_k) + prior_bf * prior_rate
        rate_denom = float(observed_bf) + prior_bf
    else:
        rate_numer = strikeout_rate * observed_bf + prior_bf * prior_rate
        rate_denom = observed_bf + prior_bf
    shrunk_rate = min(0.45, max(0.08, rate_numer / rate_denom))
    shrunk_bf = (starts * projected_bf + prior_starts * prior_workload) / max(
        1e-9, starts + prior_starts
    )
    shrunk_bf = max(1.0, shrunk_bf)
    dispersion = settings["base_workload_dispersion"] + settings[
        "thin_start_dispersion_step"
    ] * max(0, DEFAULT_SO_MAX_STARTS - max(starts, 1))
    reliability = observed_bf / (observed_bf + prior_bf) if observed_bf + prior_bf > 0 else 0.0
    return {
        "projected_bf": shrunk_bf,
        "strikeout_rate": shrunk_rate,
        "workload_dispersion": dispersion,
        "reliability": min(1.0, max(0.0, reliability)),
    }


def soften_so_win_probability(
    record: Mapping[str, object],
    *,
    reliability: float,
) -> dict[str, object]:
    """Pull thin-sample win probs toward 0.5 while preserving the partition."""
    payload = dict(record)
    win = _float_stat(payload.get("win_prob"))
    push = _float_stat(payload.get("push_prob")) or 0.0
    loss = _float_stat(payload.get("loss_prob"))
    if win is None:
        return payload
    if loss is None:
        loss = max(0.0, 1.0 - win - push)
    weight = min(1.0, max(0.0, reliability))
    # Keep push mass; shrink only the decisive win/loss split toward a coin flip.
    decisive = max(0.0, 1.0 - push)
    shrunk_win = weight * win + (1.0 - weight) * (0.5 * decisive)
    shrunk_loss = max(0.0, decisive - shrunk_win)
    payload["win_prob"] = shrunk_win
    payload["loss_prob"] = shrunk_loss
    payload["push_prob"] = push
    return payload


def apply_so_context_adjustments(
    projected_bf: float,
    strikeout_rate: float,
    *,
    opponent_k_rate: float | None = None,
    park_k_factor: float | None = None,
) -> tuple[float, float]:
    """Blend optional opponent/park context into starter SO features.

    Missing context leaves the gamelog rate unchanged (fail open on enrichment,
    fail closed on thin gamelog samples upstream).
    """
    rate = strikeout_rate
    if opponent_k_rate is not None and 0.0 < opponent_k_rate < 1.0:
        rate = 0.7 * rate + 0.3 * opponent_k_rate
    if park_k_factor is not None and 0.5 <= park_k_factor <= 1.5:
        rate *= park_k_factor
    rate = min(0.45, max(0.08, rate))
    bf = max(1.0, projected_bf)
    return bf, rate


def compute_wnba_player_features(
    recent_minutes: Iterable[float],
    *,
    min_games: int = 3,
    max_games: int = 10,
    recent_stats: Mapping[str, Iterable[float]] | None = None,
) -> dict[str, object] | None:
    """Derive bounded WNBA minutes and per-minute rates from aligned game logs.

    Every modeled stat must have one finite non-negative value per retained
    minute observation. Thin or misaligned samples fail closed. These features
    are audit-only until a market-family validation artifact is promoted.
    """
    minute_entries = [_float_stat(value) for value in recent_minutes]
    kept_indices = [
        index
        for index, minute in enumerate(minute_entries)
        if minute is not None and 0.0 <= float(minute) <= 48.0
    ]
    if len(kept_indices) < min_games:
        return None
    kept_indices = kept_indices[: max(0, max_games)]
    kept_minutes: list[float] = []
    for index in kept_indices:
        minute = minute_entries[index]
        if minute is None:
            continue
        kept_minutes.append(float(minute))
    mean_minutes = sum(kept_minutes) / len(kept_minutes)
    features: dict[str, object] = {
        "projected_minutes": mean_minutes,
        "games": len(kept_minutes),
        "feature_source": "wnba_minutes_recent",
    }
    if recent_stats is not None and sum(kept_minutes) > 0:
        for stat_name in WNBA_GAMELOG_STAT_ALIASES:
            raw_values = recent_stats.get(stat_name)
            if raw_values is None:
                continue
            stat_entries = list(raw_values)
            kept_stats: list[float] = []
            for index in kept_indices:
                if index >= len(stat_entries):
                    kept_stats = []
                    break
                stat_value = _float_stat(stat_entries[index])
                if stat_value is None or float(stat_value) < 0:
                    kept_stats = []
                    break
                kept_stats.append(float(stat_value))
            if len(kept_stats) != len(kept_minutes):
                continue
            features[f"{stat_name}_per_minute"] = sum(kept_stats) / sum(kept_minutes)
            features["feature_source"] = "wnba_espn_gamelog"
    return features


def compute_wnba_minutes_features(
    recent_minutes: Iterable[float],
    *,
    min_games: int = 3,
    max_games: int = 10,
    recent_points: Iterable[float] | None = None,
) -> dict[str, object] | None:
    """Backward-compatible points-only wrapper for the v1 scaffold."""

    stats = {"points": recent_points} if recent_points is not None else None
    return compute_wnba_player_features(
        recent_minutes,
        min_games=min_games,
        max_games=max_games,
        recent_stats=stats,
    )


def resolve_wnba_athlete_id(
    player_name: str,
    *,
    fetch_json: Any | None = None,
) -> str | None:
    """Resolve a WNBA player display name to an ESPN athlete id."""
    from urllib.parse import quote

    name = str(player_name or "").strip()
    if not name:
        return None
    url = f"{ESPN_SEARCH_URL}?region=us&lang=en&query={quote(name)}&limit=8&type=player"
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    target = _normalize_person_name(name)
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("league") or "").casefold() != "wnba":
            continue
        if str(item.get("type") or "").casefold() != "player":
            continue
        display = _normalize_person_name(item.get("displayName"))
        if display == target or target in display or display in target:
            athlete_id = str(item.get("id") or "").strip()
            if athlete_id.isdigit():
                return athlete_id
    return None


def fetch_wnba_athlete_gamelog(
    athlete_id: str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> tuple[list[float], dict[str, list[float]]]:
    """Return aligned minutes and base-stat lists newest-first from ESPN."""
    person_id = str(athlete_id).strip()
    if not person_id.isdigit():
        return [], {}
    url = f"{ESPN_WNBA_GAMELOG_URL}/{person_id}/gamelog?season={int(season)}"
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    names = payload.get("names")
    if not isinstance(names, list):
        return [], {}
    normalized_names = {
        re.sub(r"[^a-z0-9]", "", str(name).casefold()): index
        for index, name in enumerate(names)
    }
    minutes_idx = normalized_names.get("minutes")
    stat_indices: dict[str, int] = {}
    for stat_name, aliases in WNBA_GAMELOG_STAT_ALIASES.items():
        for alias in aliases:
            index = normalized_names.get(re.sub(r"[^a-z0-9]", "", alias.casefold()))
            if index is not None:
                stat_indices[stat_name] = index
                break
    if minutes_idx is None or not stat_indices:
        return [], {}
    minutes: list[float] = []
    stats_by_name: dict[str, list[float]] = {stat_name: [] for stat_name in stat_indices}
    season_types = payload.get("seasonTypes")
    if not isinstance(season_types, list):
        return [], {}
    for season_type in season_types:
        if not isinstance(season_type, Mapping):
            continue
        categories = season_type.get("categories")
        if not isinstance(categories, list):
            continue
        for category in categories:
            if not isinstance(category, Mapping):
                continue
            events = category.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                stats = event.get("stats")
                if not isinstance(stats, list):
                    continue
                required_indices = [minutes_idx, *stat_indices.values()]
                if max(required_indices) >= len(stats):
                    continue
                minute_value = _float_stat(stats[minutes_idx])
                stat_values = {
                    stat_name: _float_stat(stats[index])
                    for stat_name, index in stat_indices.items()
                }
                if minute_value is None or any(value is None for value in stat_values.values()):
                    continue
                minutes.append(minute_value)
                for stat_name, value in stat_values.items():
                    assert value is not None
                    stats_by_name[stat_name].append(float(value))
    return minutes, stats_by_name


def fetch_wnba_athlete_gamelog_stats(
    athlete_id: str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> tuple[list[float], list[float]]:
    """Backward-compatible points-only view of the generalized gamelog."""

    minutes, stats = fetch_wnba_athlete_gamelog(
        athlete_id, season=season, fetch_json=fetch_json
    )
    return minutes, stats.get("points", [])


_WNBA_FEATURE_CACHE: dict[str, dict[str, object] | None] = {}


def get_wnba_player_features(
    player_name: str,
    *,
    season: int,
    fetch_json: Any | None = None,
    cache: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    """Fetch/cache WNBA minutes and base-stat rates for one player."""
    key = f"{season}:{_normalize_person_name(player_name)}"
    store = cache if cache is not None else _WNBA_FEATURE_CACHE
    if key in store:
        return store[key]
    try:
        athlete_id = resolve_wnba_athlete_id(player_name, fetch_json=fetch_json)
        if not athlete_id:
            store[key] = None
            return None
        minutes, stats = fetch_wnba_athlete_gamelog(
            athlete_id, season=season, fetch_json=fetch_json
        )
        features = compute_wnba_player_features(minutes, recent_stats=stats)
        store[key] = features
        return features
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        logger.warning("WNBA minutes features failed for %s: %s", player_name, exc)
        store[key] = None
        return None


def get_wnba_points_features(
    player_name: str,
    *,
    season: int,
    fetch_json: Any | None = None,
    cache: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    """Backward-compatible alias for callers of the points-only scaffold."""

    return get_wnba_player_features(
        player_name, season=season, fetch_json=fetch_json, cache=cache
    )


def _wnba_market_code(row: Mapping[str, object]) -> str | None:
    for field in ("market", "proposition", "market_type"):
        raw = str(row.get(field) or "").upper().strip()
        token = re.sub(r"[^A-Z0-9]+", "_", raw).strip("_")
        compact = token.replace("_", "")
        candidate = WNBA_MARKET_ALIASES.get(token, WNBA_MARKET_ALIASES.get(compact, token))
        if candidate in WNBA_MARKET_COMPONENTS:
            return candidate
    return None


def wnba_projection_record(
    row: Mapping[str, object],
    *,
    features: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Shadow-only WNBA stat projection from minutes times component rates.

    Returns None unless features are complete. Never writes league averages.
    Audit-only hash: does not fill independent_model_prob.
    """
    sport = str(row.get("sport") or row.get("league") or "").upper()
    if sport != "WNBA":
        return None
    market = _wnba_market_code(row)
    if market is None:
        return None
    if not isinstance(features, Mapping):
        return None
    minutes = _float_stat(features.get("projected_minutes"))
    component_rates = [
        _float_stat(features.get(f"{component}_per_minute"))
        for component in WNBA_MARKET_COMPONENTS[market]
    ]
    if (
        minutes is None
        or minutes <= 0
        or any(rate is None or rate < 0 for rate in component_rates)
    ):
        return None
    line = row.get("line")
    side = str(row.get("headline_side") or row.get("position") or "").upper()
    if "OVER" in str(row.get("selection") or "").upper():
        side = "OVER"
    elif "UNDER" in str(row.get("selection") or "").upper():
        side = "UNDER"
    if side not in {"OVER", "UNDER"} or line in (None, ""):
        return None
    try:
        line_value = float(str(line).replace("+", ""))
    except (TypeError, ValueError):
        return None
    mean_stat = minutes * sum(float(rate) for rate in component_rates if rate is not None)
    if mean_stat <= 0:
        return None
    # Poisson-like discrete approximation via NB2 with light overdispersion.
    distribution = negative_binomial_distribution(mean_stat, dispersion=8.0)
    record = distribution.to_record(line=line_value, side=side)
    return {
        "status": "eligible",
        "sport": "WNBA",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "market": market,
        "line": line_value,
        "side": side,
        "feature_snapshot_hash": WNBA_GAMELOG_HASH,
        "distribution": record,
    }


def wnba_points_projection_record(
    row: Mapping[str, object],
    *,
    features: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Backward-compatible points-only wrapper."""

    if _wnba_market_code(row) != "PTS":
        return None
    record = wnba_projection_record(row, features=features)
    if record is None:
        return None
    return {**record, "feature_snapshot_hash": WNBA_MINUTES_HASH}


def project_mlb_row(row: Mapping[str, object]) -> dict[str, object]:
    """Project one normalized MLB row when its feature snapshot is present."""

    features = row.get("projection_features") or row.get("features") or {}
    if not isinstance(features, Mapping):
        features = {}
    proposition = str(row.get("proposition") or row.get("market") or "").upper()
    try:
        if "STRIKEOUT" in proposition:
            distribution = mlb_strikeout_distribution(
                float(features["projected_bf"]),
                float(features["strikeout_rate"]),
                workload_dispersion=float(features.get("workload_dispersion", 20.0)),
            )
        elif "HITS_ALLOWED" in proposition or proposition in {"PITCHER_HITS", "HITS"}:
            distribution = mlb_hits_allowed_distribution(
                float(features["projected_bf"]), float(features["hit_rate"]), dispersion=float(features.get("dispersion", 8.0))
            )
        elif "TOTAL_BASE" in proposition:
            distribution = mlb_total_bases_distribution(
                int(features["plate_appearances"]),
                float(features["zero_rate"]),
                features["positive_outcome_probs"],
            )
        elif "FIRST_INNING" in proposition or "NRFI" in proposition or "YRFI" in proposition:
            distribution = mlb_first_inning_run_distribution(
                float(features["home_run_mean"]), float(features["away_run_mean"])
            )
        else:
            return {"status": "ineligible", "reason": "unsupported_market", "row_id": row.get("outcome_id")}
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "status": "ineligible",
            "reason": "missing_or_invalid_features",
            "detail": str(exc),
            "row_id": row.get("outcome_id"),
        }

    record = {
        "status": "eligible",
        "sport": row.get("sport") or row.get("league") or "MLB",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "market": row.get("market") or row.get("proposition"),
        "line": row.get("line"),
        "side": row.get("position"),
        "distribution": distribution.to_record(),
        "feature_snapshot_hash": row.get("feature_snapshot_hash"),
    }
    if row.get("line") is not None and row.get("position"):
        partition_side = str(row["position"])
        if "FIRST_INNING" in proposition or "NRFI" in proposition or "YRFI" in proposition:
            partition_side = {"YES": "OVER", "NO": "UNDER"}.get(
                partition_side.upper(), partition_side
            )
        record["distribution"] = distribution.to_record(
            line=float(str(row["line"])), side=partition_side
        )
    return record


def project_rows(rows: Iterable[Mapping[str, object]], sport: str) -> list[dict[str, object]]:
    if sport.upper() != "MLB":
        return [
            {
                "status": "shadow_only",
                "reason": "WNBA_model_phase_two",
                "sport": sport.upper(),
                "row_id": row.get("outcome_id"),
                "event_id": row.get("event_id"),
                "market_id": row.get("market_id"),
            }
            for row in rows
        ]
    return [project_mlb_row(row) for row in rows]


def build_mlb_so_projections(
    props_rows: Iterable[Mapping[str, object]],
    probable_by_team: Mapping[str, Mapping[str, object]] | None,
) -> list[dict[str, object]]:
    """Compute starter-SO projections for every eligible row in normalized props.

    Reuses ``mlb_so_projection_record`` (the same function pack.py falls back to
    inline) so the file-based artifact and the inline fallback never disagree.
    """

    records = []
    for row in props_rows:
        record = mlb_so_projection_record(row, probable_by_team)
        if record is not None:
            records.append(record)
    return records


def build_wnba_projections(
    props_rows: Iterable[Mapping[str, object]],
    *,
    season: int,
    fetch_json: Any | None = None,
) -> list[dict[str, object]]:
    """Compute shadow WNBA projections for supported player-stat families.

    Feature lookups are cached per player so one slate costs one ESPN call per
    player, not one per priced outcome. Unsupported market rows never
    trigger a fetch.
    """

    cache: dict[str, dict[str, object] | None] = {}
    records: list[dict[str, object]] = []
    for row in props_rows:
        if not isinstance(row, Mapping):
            continue
        if _wnba_market_code(row) is None:
            continue
        player = _player_name_from_row(row)
        if not player:
            continue
        features = get_wnba_player_features(
            player, season=season, fetch_json=fetch_json, cache=cache
        )
        record = wnba_projection_record(row, features=features)
        if record is not None:
            records.append(record)
    return records


def build_wnba_points_projections(
    props_rows: Iterable[Mapping[str, object]],
    *,
    season: int,
    fetch_json: Any | None = None,
) -> list[dict[str, object]]:
    """Backward-compatible points-only builder."""

    points_rows = [row for row in props_rows if _wnba_market_code(row) == "PTS"]
    return build_wnba_projections(points_rows, season=season, fetch_json=fetch_json)


def summarize_wnba_projection_coverage(
    props_rows: Iterable[Mapping[str, object]], records: Iterable[Mapping[str, object]]
) -> dict[str, object]:
    """Report supported-row shadow coverage by normalized WNBA market family."""

    opportunities = {market: 0 for market in WNBA_MARKET_COMPONENTS}
    projected = {market: 0 for market in WNBA_MARKET_COMPONENTS}
    for row in props_rows:
        market = _wnba_market_code(row)
        if market is not None:
            opportunities[market] += 1
    for record in records:
        market = str(record.get("market") or "")
        if market in projected and record.get("status") == "eligible":
            projected[market] += 1
    return {
        "mode": "audit_only",
        "supported_opportunities": sum(opportunities.values()),
        "projected_opportunities": sum(projected.values()),
        "by_market": {
            market: {"opportunities": opportunities[market], "projected": projected[market]}
            for market in WNBA_MARKET_COMPONENTS
        },
    }


def _projection_season(props_rows: Iterable[Mapping[str, object]]) -> int:
    """Season year for feature lookups, taken from the feed's own as_of stamp."""

    for row in props_rows:
        if not isinstance(row, Mapping):
            continue
        as_of = str(row.get("as_of") or "")
        if len(as_of) >= 4 and as_of[:4].isdigit():
            return int(as_of[:4])
    return datetime.now().astimezone().year


def _row_slate_date(row: Mapping[str, object]) -> str | None:
    context = row.get("sport_context")
    context = context if isinstance(context, Mapping) else {}
    starts_at = (
        context.get("event_starts_at")
        or row.get("event_starts_at")
        or row.get("starts_at")
    )
    return _local_date(str(starts_at) if starts_at else None)


def _projection_slate_date(
    props_payload: Mapping[str, object],
    props_rows: Iterable[Mapping[str, object]],
    target_date: date | str | None,
) -> str:
    """Resolve one auditable slate date without trusting wall-clock time."""

    explicit_target: str | None = None
    if target_date is not None:
        raw_target = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        try:
            explicit_target = date.fromisoformat(raw_target).isoformat()
        except ValueError as exc:
            raise ValueError(f"invalid projection target_date {raw_target!r}") from exc

    candidates: set[str] = set()
    payload_date = props_payload.get("date")
    if payload_date not in (None, ""):
        try:
            candidates.add(date.fromisoformat(str(payload_date)).isoformat())
        except ValueError as exc:
            raise ValueError(f"invalid props artifact date {payload_date!r}") from exc
    for row in props_rows:
        if not isinstance(row, Mapping):
            continue
        if local_date := _row_slate_date(row):
            candidates.add(local_date)

    if explicit_target is not None:
        if candidates and explicit_target not in candidates:
            detail = ", ".join(sorted(candidates))
            raise ValueError(
                f"props feed slate date(s) {detail} do not match projection "
                f"target_date {explicit_target}"
            )
        return explicit_target
    if len(candidates) != 1:
        detail = "none" if not candidates else ", ".join(sorted(candidates))
        raise ValueError(
            "projection slate date is not safely derivable from the props feed "
            f"(found {detail}); pass target_date explicitly"
        )
    return next(iter(candidates))


def export_projections(
    league: str = "MLB", *, target_date: date | str | None = None
) -> dict[str, object]:
    """Compute and persist independent projections for one league's props.

    Reads the already-refreshed ``<league>_props_latest.json`` (and, for MLB,
    ``<league>_probable_pitchers_latest.json``) — both auto-advance to the
    correct slate on their own, so this step just needs to run after them.
    ``target_date`` is authoritative when supplied; otherwise the date is
    derived from the feed's own slate timestamps and ambiguous/undated inputs
    fail closed instead of being stamped with the current wall-clock date.
    MLB emits starter pitcher-SO projections from game logs; WNBA emits the
    shadow points scaffold. Both are written to
    ``<sport>_projections_latest.json`` so ``pack`` reads a file instead of
    re-deriving every projection inline.
    """

    sport = league.strip().upper()
    if sport not in {"MLB", "WNBA"}:
        return {
            "status": "skipped",
            "reason": f"no independent projection model for {league}",
            "record_count": 0,
        }

    from .paths import league_paths

    paths_for_league = league_paths(sport)
    props_path = paths_for_league.props_normalized_latest()
    if not props_path.exists():
        return {"status": "error", "reason": "props_normalized_latest missing", "record_count": 0}
    try:
        props_payload = json.loads(props_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "reason": str(exc)[:200], "record_count": 0}
    props_rows = props_payload.get("records") if isinstance(props_payload, Mapping) else None
    if not isinstance(props_rows, list):
        props_rows = []
    try:
        artifact_date = _projection_slate_date(props_payload, props_rows, target_date)
    except ValueError as exc:
        return {"status": "error", "reason": str(exc), "record_count": 0}

    dated_rows = [
        row
        for row in props_rows
        if isinstance(row, Mapping) and _row_slate_date(row) == artifact_date
    ]
    if any(_row_slate_date(row) for row in props_rows if isinstance(row, Mapping)):
        props_rows = dated_rows

    if sport == "MLB":
        from .probable_pitchers import load_probable_pitcher_lookup

        probable_by_team = load_probable_pitcher_lookup("MLB")
        records = build_mlb_so_projections(props_rows, probable_by_team)
    else:
        records = build_wnba_projections(
            props_rows, season=_projection_season(props_rows)
        )

    result: dict[str, object] = {
        "sport": sport,
        "date": artifact_date,
        "generated_at": datetime.now().astimezone().isoformat(),
        "projections": records,
    }
    if sport == "WNBA":
        result["coverage"] = summarize_wnba_projection_coverage(props_rows, records)
    out_path = paths_for_league.projections_latest()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return {"status": "ok", "record_count": len(records)}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent deterministic projection layer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("backfill", "train", "project", "validate", "export", "promote"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--sport", required=True, choices=("MLB", "WNBA"))
        subparser.add_argument("--date", default=date.today().isoformat())
        subparser.add_argument("--output", type=Path)
        if command == "project":
            subparser.add_argument("--input", type=Path)
        if command in {"backfill", "train", "validate"}:
            subparser.add_argument("--from", dest="from_date")
            subparser.add_argument("--to", dest="to_date")
        if command == "backfill":
            subparser.add_argument(
                "--team",
                action="append",
                dest="teams",
                help="Restrict the roster crawl to these team codes (repeatable).",
            )
            subparser.add_argument(
                "--pitcher-limit",
                type=int,
                help="Cap pitchers per season; useful for smoke runs.",
            )
        if command in {"train", "validate"}:
            subparser.add_argument("--dataset", type=Path)
            subparser.add_argument("--min-samples", type=int)
        if command == "train":
            subparser.add_argument("--as-of", dest="as_of")
        if command in {"validate", "promote"}:
            subparser.add_argument("--artifact", type=Path)
        if command == "promote":
            subparser.add_argument("--actor", default="")
            subparser.add_argument("--validation", type=Path)
            subparser.add_argument(
                "--skip-validation",
                action="store_true",
                help="Promote without a passing validation report (audited, discouraged).",
            )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "export":
        status = export_projections(args.sport, target_date=args.date)
        if status["status"] == "skipped":
            print(f"{args.sport} projections: skipped ({status['reason']})")
            return 0
        if status["status"] == "error":
            print(f"{args.sport} projections: error ({status['reason']})")
            return 1
        print(f"{args.sport} projections: exported {status['record_count']} records")
        return 0
    if args.command == "project":
        if args.input:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            rows = payload.get("records", payload) if isinstance(payload, Mapping) else payload
            if not isinstance(rows, list):
                raise ValueError(
                    "projection input must be a JSON list or an object containing records"
                )
            result = {
                "sport": args.sport,
                "date": args.date,
                "generated_at": datetime.now().astimezone().isoformat(),
                "projections": project_rows(rows, args.sport),
            }
            rendered = json.dumps(result, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
            else:
                print(rendered, end="")
            return 0
        # No explicit input: project the league's refreshed feed in place.
        status = export_projections(args.sport)
        print(f"{args.sport} projections: {json.dumps(status, sort_keys=True)}")
        return 1 if status["status"] == "error" else 0

    from . import projection_training

    try:
        if args.command == "backfill":
            if not args.from_date or not args.to_date:
                print("backfill requires --from and --to (YYYY-MM-DD).")
                return 2
            status = projection_training.backfill(
                args.sport,
                start=args.from_date,
                end=args.to_date,
                output=args.output,
                team_codes=args.teams,
                pitcher_limit=args.pitcher_limit,
            )
        elif args.command == "train":
            kwargs: dict[str, Any] = {
                "as_of": args.as_of or args.date,
                "dataset": args.dataset,
                "output": args.output,
            }
            if args.min_samples is not None:
                kwargs["min_samples"] = args.min_samples
            status = projection_training.train(args.sport, **kwargs)
        elif args.command == "validate":
            kwargs = {
                "artifact": args.artifact,
                "dataset": args.dataset,
                "output": args.output,
                "start": args.from_date,
                "end": args.to_date,
            }
            if args.min_samples is not None:
                kwargs["min_samples"] = args.min_samples
            status = projection_training.validate(args.sport, **kwargs)
        else:
            status = projection_training.promote(
                args.sport,
                artifact=args.artifact,
                actor=args.actor,
                require_validation=not args.skip_validation,
                validation=args.validation,
            )
    except projection_training.TrainingError as exc:
        print(f"{args.sport} {args.command}: {exc}")
        return 1

    print(json.dumps(status, indent=2, sort_keys=True))
    if args.command == "validate":
        return 0 if status.get("verdict") in {"pass", "insufficient_data"} else 1
    if status.get("status") in {"error"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
